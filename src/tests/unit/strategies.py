from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hypothesis import strategies as st


_SAFE_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789-"
_MODEL_FILES = (
    "model.tar.gz",
    "predictor.joblib",
    "weights.onnx",
)


def identifier_strategy(prefix: str) -> st.SearchStrategy[str]:
    return st.text(alphabet=_SAFE_CHARS, min_size=4, max_size=12).map(
        lambda value: f"{prefix}-{value.strip('-') or 'id'}"
    )


@st.composite
def iso_timestamp_strategy(draw: st.DrawFn) -> str:
    offset_seconds = draw(st.integers(min_value=0, max_value=7 * 24 * 60 * 60))
    return (
        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat()


@st.composite
def artifact_reference_strategy(draw: st.DrawFn) -> dict[str, str]:
    task_id = draw(identifier_strategy("task"))
    filename = draw(st.sampled_from(_MODEL_FILES))
    artifact_type = "tarball" if filename.endswith(".tar.gz") else "model-artifact"
    return {
        "bucket": "bucket-name",
        "key": f"output/{task_id}/{filename}",
        "artifact_type": artifact_type,
        "output_prefix": f"output/{task_id}/",
    }


@st.composite
def task_record_strategy(draw: st.DrawFn) -> dict[str, str]:
    task_id = draw(identifier_strategy("task"))
    created_at = draw(iso_timestamp_strategy())
    return {
        "task_id": task_id,
        "status": draw(st.sampled_from(["RUNNING", "SUCCEEDED", "DEPLOYING"])),
        "task_type": "timeseries_forecast",
        "model_name": draw(identifier_strategy("model")),
        "target_endpoint": draw(identifier_strategy("endpoint")),
        "resolved_image_tag": draw(st.sampled_from(["stable", "candidate", "blue"])),
        "batch_job_id": draw(identifier_strategy("job")),
        "error_message": "",
        "created_at": created_at,
        "updated_at": created_at,
    }


@st.composite
def deployment_decision_strategy(draw: st.DrawFn) -> dict[str, object]:
    artifact_reference = draw(artifact_reference_strategy())
    metric_value = draw(
        st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)
    )
    return {
        "eligible": True,
        "verification_summary": {
            "achieved": True,
            "metric_name": "rmse",
            "metric_value": metric_value,
            "threshold": 12.5,
            "reason": "independent verification passed",
            "missing_artifacts": [],
        },
        "verification_artifact_reference": {
            "bucket": artifact_reference["bucket"],
            "key": artifact_reference["key"].rsplit("/", 1)[0] + "/runner_verification.json",
        },
        "deployable_artifact_reference": artifact_reference,
    }


@st.composite
def model_record_strategy(draw: st.DrawFn) -> dict[str, object]:
    task = draw(task_record_strategy())
    created_at = draw(iso_timestamp_strategy())
    updated_at = draw(iso_timestamp_strategy())
    status = draw(st.sampled_from(["DEPLOYING", "DEPLOYED", "FAILED"]))
    return {
        "model_record_id": task["task_id"],
        "task_id": task["task_id"],
        "model_name": task["model_name"],
        "target_endpoint": task["target_endpoint"],
        "resolved_image_tag": task["resolved_image_tag"],
        "status": status,
        "provider_status": draw(st.sampled_from(["ACCEPTED", "Creating", "InService", "FAILED"])),
        "provider_status_detail": {"source": "property-test"},
        "failure_reason": "" if status != "FAILED" else "deployment failed",
        "artifact_reference": draw(artifact_reference_strategy()),
        "endpoint_arn": f"arn:aws-cn:sagemaker:cn-north-1:123456789012:endpoint/{task['target_endpoint']}",
        "endpoint_config_name": draw(identifier_strategy("config")),
        "sagemaker_model_name": draw(identifier_strategy("sm-model")),
        "is_current_endpoint_target": draw(st.booleans()) if status == "DEPLOYED" else False,
        "created_at": created_at,
        "updated_at": updated_at,
    }


@st.composite
def deployment_command_strategy(draw: st.DrawFn) -> dict[str, object]:
    task = draw(task_record_strategy())
    command_time = draw(iso_timestamp_strategy())
    return {
        "task_record": {
            **task,
            "status": "SUCCEEDED",
            "created_at": command_time,
            "updated_at": command_time,
        },
        "decision": draw(deployment_decision_strategy()),
        "now": command_time,
        "succeeds": draw(st.booleans()),
    }
