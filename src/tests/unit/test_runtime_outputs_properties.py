import json

from hypothesis import given, strategies as st

from deployment.runtime_outputs import evaluate_deployment_eligibility


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


@given(
    terminal_achieved=st.booleans(),
    verification_achieved=st.booleans(),
    independent_source=st.booleans(),
    matching_report=st.booleans(),
    deployable_artifact_present=st.booleans(),
    target_endpoint_present=st.booleans(),
)
def test_deployment_gate_only_opens_when_all_required_conditions_hold(
    terminal_achieved,
    verification_achieved,
    independent_source,
    matching_report,
    deployable_artifact_present,
    target_endpoint_present,
):
    metric_value = 11.3 if verification_achieved else 13.7
    source = "independent" if independent_source else "local-report"
    manifest = {
        "task_id": "task-1",
        "attempts": [
            {
                "verification": {
                    "achieved": verification_achieved,
                    "metric_name": "rmse",
                    "metric_value": metric_value,
                    "threshold": 12.5,
                    "threshold_direction": "lower_is_better",
                    "reason": "verified",
                    "missing_artifacts": [],
                    "source": source,
                }
            }
        ],
        "terminal_outcome": {
            "achieved": terminal_achieved,
            "reason": "verified" if terminal_achieved else "threshold not met",
            "final_metric_value": metric_value,
        },
        "published_artifacts": [
            {"bucket": "bucket-name", "key": "output/task-1/runner_verification.json"},
        ],
        "output_prefix": "output/task-1/",
    }
    if deployable_artifact_present:
        manifest["published_artifacts"].insert(
            0,
            {"bucket": "bucket-name", "key": "output/task-1/model.tar.gz"},
        )

    report_metric = metric_value if matching_report else metric_value + 1.0
    report = json.dumps(
        {
            "source": source,
            "achieved": verification_achieved,
            "verified_metric": report_metric,
            "threshold": 12.5,
            "direction": "lower_is_better",
        }
    )
    decision = evaluate_deployment_eligibility(
        task_record={
            "task_id": "task-1",
            "status": "SUCCEEDED",
            "target_endpoint": "demo-endpoint-01" if target_endpoint_present else "",
        },
        manifest=manifest,
        s3_client=FakeS3Client(
            {
                ("bucket-name", "output/task-1/runner_verification.json"): report,
            }
        ),
    )

    if independent_source:
        # Independent verification: full consistency gate (unchanged behavior).
        expected = (
            terminal_achieved
            and verification_achieved
            and matching_report
            and deployable_artifact_present
            and target_endpoint_present
        )
    else:
        # Verification disabled (source "local-report"): accepted on engineering
        # completeness — training achieved + deployable model + endpoint. The
        # independent-source / MAPE-threshold / report-consistency gates are
        # skipped, so verification_achieved and matching_report do not matter.
        expected = (
            terminal_achieved
            and deployable_artifact_present
            and target_endpoint_present
        )
    assert decision["eligible"] is expected
