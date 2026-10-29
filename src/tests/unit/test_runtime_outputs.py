# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json

from deployment.runtime_outputs import (
    evaluate_deployment_eligibility,
    resolve_deployable_artifact,
)


class FakeBody:
    def __init__(self, payload: str):
        self._payload = payload

    def read(self):
        return self._payload.encode("utf-8")


class FakeS3Client:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, Bucket, Key):
        return {"Body": FakeBody(self.objects[(Bucket, Key)])}


def _task(target_endpoint="demo-endpoint-01"):
    return {
        "task_id": "task-1",
        "status": "SUCCEEDED",
        "target_endpoint": target_endpoint,
    }


def _manifest(source="independent", metric_value=11.3):
    return {
        "task_id": "task-1",
        "attempts": [
            {
                "verification": {
                    "achieved": True,
                    "metric_name": "rmse",
                    "metric_value": metric_value,
                    "threshold": 12.5,
                    "threshold_direction": "lower_is_better",
                    "reason": "verified",
                    "missing_artifacts": [],
                    "source": source,
                    "report_path": "attempt-1/runner_verification.json",
                }
            }
        ],
        "terminal_outcome": {
            "achieved": True,
            "reason": "verified",
            "final_metric_value": metric_value,
        },
        "published_artifacts": [
            {"bucket": "bucket-name", "key": "output/task-1/model.tar.gz"},
            {"bucket": "bucket-name", "key": "output/task-1/runner_verification.json"},
        ],
        "output_prefix": "output/task-1/",
    }


def _report(source="independent", metric_value=11.3, threshold=12.5):
    return json.dumps(
        {
            "source": source,
            "achieved": True,
            "verified_metric": metric_value,
            "threshold": threshold,
            "direction": "lower_is_better",
        }
    )


def test_evaluate_deployment_eligibility_accepts_local_report_when_verification_disabled():
    # Task types with independent verification disabled (enforceVerification=
    # false) write source "local-report". The deployment gate must accept them
    # on engineering completeness (training achieved + a deployable model +
    # endpoint), skipping the independent-source / MAPE-threshold checks.
    decision = evaluate_deployment_eligibility(
        task_record=_task(),
        manifest=_manifest(source="local-report"),
        s3_client=FakeS3Client({}),
    )

    assert decision["eligible"] is True
    assert decision["ineligible_reason"] == ""


def test_evaluate_deployment_eligibility_rejects_local_report_without_model():
    # Even with verification disabled, a run that produced no deployable model
    # artifact must not be deployable.
    manifest = _manifest(source="local-report")
    manifest["published_artifacts"] = [
        {"bucket": "bucket-name", "key": "output/task-1/evaluation_report.json"},
    ]
    decision = evaluate_deployment_eligibility(
        task_record=_task(),
        manifest=manifest,
        s3_client=FakeS3Client({}),
    )

    assert decision["eligible"] is False
    assert "deployable model artifact" in decision["ineligible_reason"]


def test_evaluate_deployment_eligibility_rejects_mismatched_verification_report():
    decision = evaluate_deployment_eligibility(
        task_record=_task(),
        manifest=_manifest(metric_value=11.3),
        s3_client=FakeS3Client(
            {
                ("bucket-name", "output/task-1/runner_verification.json"): _report(
                    metric_value=15.0
                )
            }
        ),
    )

    assert decision["eligible"] is False
    assert decision["ineligible_reason"] == "verification artifact metric does not match the runtime manifest"


def test_evaluate_deployment_eligibility_requires_strict_model_artifact_match():
    manifest = _manifest()
    manifest["published_artifacts"] = [
        {"bucket": "bucket-name", "key": "output/task-1/events.jsonl"},
        {"bucket": "bucket-name", "key": "output/task-1/runner_verification.json"},
    ]

    decision = evaluate_deployment_eligibility(
        task_record=_task(),
        manifest=manifest,
        s3_client=FakeS3Client(
            {
                ("bucket-name", "output/task-1/runner_verification.json"): _report(),
            }
        ),
    )

    assert decision["eligible"] is False
    assert (
        decision["ineligible_reason"]
        == "deployable model artifact could not be resolved from published artifacts"
    )


def test_resolve_deployable_artifact_accepts_bring_your_own_inference_entrypoint():
    """A payload transferred by a byo_deploy task names its weights whatever the
    algorithm team used (here rf.sav), which matches neither DEPLOYABLE_SUFFIXES
    nor the "model" substring rule. Its inference.py must make it resolvable,
    otherwise the deployment gate rejects a perfectly valid payload."""
    manifest = {
        "published_artifacts": [
            {"bucket": "bucket-name", "key": "output/task-1/output/models/inference.py"},
            {"bucket": "bucket-name", "key": "output/task-1/output/models/model/rf.sav"},
        ]
    }

    artifact = resolve_deployable_artifact(manifest)

    assert artifact is not None
    assert artifact["key"] == "output/task-1/output/models/inference.py"
    assert artifact["artifact_type"] == "custom-inference-payload"


def test_resolve_deployable_artifact_prefers_predictor_over_inference_entrypoint():
    """The inference.py rule is a SECOND pass, so an AutoGluon run that happens to
    ship an inference.py alongside its predictor still resolves to the predictor
    (which also yields the correct artifact_type)."""
    manifest = {
        "published_artifacts": [
            {"bucket": "bucket-name", "key": "output/task-1/output/models/inference.py"},
            {"bucket": "bucket-name", "key": "output/task-1/output/models/predictor.pkl"},
        ]
    }

    artifact = resolve_deployable_artifact(manifest)

    assert artifact["key"] == "output/task-1/output/models/predictor.pkl"
    assert artifact["artifact_type"] == "python-serialized-model"


def test_resolve_deployable_artifact_returns_none_without_model_or_entrypoint():
    manifest = {
        "published_artifacts": [
            {"bucket": "bucket-name", "key": "output/task-1/output/prediction_machine.csv"},
            {"bucket": "bucket-name", "key": "output/task-1/output/evaluation_report.json"},
        ]
    }

    assert resolve_deployable_artifact(manifest) is None
