# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import os
from typing import Any

from api.repository import TaskRepository
from deployment.model_registry import ModelRegistryRepository
from deployment.runtime_outputs import (
    evaluate_deployment_eligibility,
    load_runtime_manifest,
)
from shared.deployment_dispatch import dispatch_deployment
from shared.logging_utils import log_event
from shared.notifications import build_message_repository, record_status_change
from shared.task_status import (
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
    apply_status_transition,
)
from shared.time_utils import utc_timestamp


STATUS_MAP = {
    "RUNNABLE": TASK_STATUS_QUEUED,
    "STARTING": TASK_STATUS_QUEUED,
    "RUNNING": TASK_STATUS_RUNNING,
    "FAILED": TASK_STATUS_FAILED,
    "SUCCEEDED": TASK_STATUS_SUCCEEDED,
}


def _update_and_notify(
    repository: TaskRepository,
    updated_task: dict[str, Any],
    old_status: str,
    request_id: str | None,
    message_repo: Any | None,
) -> None:
    """Persist the updated task and, if its status changed, record a
    notification message for the submitter (best-effort)."""
    repository.update_task(updated_task)
    record_status_change(
        updated_task,
        old_status,
        updated_task.get("status", ""),
        repository=message_repo,
        request_id=request_id,
    )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    detail = event.get("detail", {})
    batch_job_id = detail.get("jobId", "")
    batch_status = detail.get("status", "")
    request_id = getattr(context, "aws_request_id", None)

    repository = TaskRepository(
        table_name=os.environ["TABLE_NAME"],
        batch_job_index=os.environ.get("TASK_BATCH_JOB_INDEX", "batch-job-id-index"),
    )
    model_registry = ModelRegistryRepository(
        table_name=os.environ["MODEL_REGISTRY_TABLE_NAME"],
        target_endpoint_index=os.environ.get(
            "MODEL_REGISTRY_TARGET_ENDPOINT_INDEX",
            "target-endpoint-index",
        ),
    )
    message_repo = build_message_repository()
    task = repository.get_task_by_batch_job_id(batch_job_id)
    if task is None or batch_status not in STATUS_MAP:
        log_event(
            "oncomplete-handler",
            "completion-event-ignored",
            request_id=request_id,
            batch_job_id=batch_job_id,
            batch_status=batch_status,
        )
        return {"statusCode": 200, "body": "ignored"}

    if batch_status == "SUCCEEDED":
        return _handle_successful_completion(
            task=task,
            repository=repository,
            model_registry=model_registry,
            request_id=request_id,
            message_repo=message_repo,
        )

    try:
        updated_task = apply_status_transition(
            task,
            STATUS_MAP[batch_status],
            now=utc_timestamp(),
        )
    except ValueError:
        log_event(
            "oncomplete-handler",
            "completion-event-ignored",
            request_id=request_id,
            task_id=task["task_id"],
            batch_job_id=batch_job_id,
            batch_status=batch_status,
            current_status=task["status"],
        )
        return {"statusCode": 200, "body": "ignored"}
    _update_and_notify(
        repository, updated_task, task["status"], request_id, message_repo
    )
    log_event(
        "oncomplete-handler",
        "task-status-updated",
        request_id=request_id,
        task_id=task["task_id"],
        batch_job_id=batch_job_id,
        status=updated_task["status"],
    )
    return {"statusCode": 200, "body": updated_task["status"]}


def _handle_successful_completion(
    *,
    task: dict[str, Any],
    repository: TaskRepository,
    model_registry: ModelRegistryRepository,
    request_id: str | None,
    message_repo: Any | None = None,
) -> dict[str, Any]:
    original_status = task["status"]
    s3_client = _build_s3_client()
    try:
        succeeded_task = apply_status_transition(
            task,
            TASK_STATUS_SUCCEEDED,
            now=utc_timestamp(),
        )
    except ValueError:
        log_event(
            "oncomplete-handler",
            "completion-event-ignored",
            request_id=request_id,
            task_id=task["task_id"],
            batch_job_id=task["batch_job_id"],
            batch_status="SUCCEEDED",
            current_status=task["status"],
        )
        return {"statusCode": 200, "body": "ignored"}

    try:
        manifest = load_runtime_manifest(
            bucket_name=os.environ["BUCKET_NAME"],
            task_record=task,
            s3_client=s3_client,
        )
        decision = evaluate_deployment_eligibility(
            task_record=succeeded_task,
            manifest=manifest,
            s3_client=s3_client,
        )
    except Exception as exc:
        skipped_task = _record_non_deployable_success(
            task_record=succeeded_task,
            reason=str(exc),
        )
        _update_and_notify(
            repository, skipped_task, original_status, request_id, message_repo
        )
        log_event(
            "oncomplete-handler",
            "deployment-skipped",
            level="WARN",
            request_id=request_id,
            task_id=task["task_id"],
            reason=str(exc),
        )
        return {"statusCode": 200, "body": skipped_task["status"]}

    if not decision["eligible"]:
        skipped_task = _record_non_deployable_success(
            task_record=succeeded_task,
            reason=decision["ineligible_reason"],
        )
        _update_and_notify(
            repository, skipped_task, original_status, request_id, message_repo
        )
        log_event(
            "oncomplete-handler",
            "deployment-skipped",
            level="WARN",
            request_id=request_id,
            task_id=task["task_id"],
            reason=decision["ineligible_reason"],
        )
        return {"statusCode": 200, "body": skipped_task["status"]}

    # Manual deployment mode: training is deployable but the operator must
    # trigger deployment from the UI. Leave the task SUCCEEDED and do not
    # dispatch the deployment lambda; POST /tasks/deploy will do it on demand.
    if (task.get("deployment_mode") or "auto") == "manual":
        _update_and_notify(
            repository, succeeded_task, original_status, request_id, message_repo
        )
        log_event(
            "oncomplete-handler",
            "deployment-deferred-manual",
            request_id=request_id,
            task_id=task["task_id"],
        )
        return {"statusCode": 200, "body": succeeded_task["status"]}

    # Auto deployment: dispatch via the shared helper (same path the manual
    # POST /tasks/deploy trigger uses), so behaviour is identical.
    result = dispatch_deployment(
        task_record=succeeded_task,
        decision=decision,
        repository=repository,
        model_registry=model_registry,
        bucket_name=os.environ["BUCKET_NAME"],
        deployment_function_name=os.environ["DEPLOYMENT_FUNCTION_NAME"],
        lambda_client=_build_lambda_client(),
        update_and_notify=lambda updated, prev: _update_and_notify(
            repository, updated, prev, request_id, message_repo
        ),
        request_id=request_id,
    )
    # A duplicate (already-accepted) attempt is a no-op for the completion event.
    if not result.get("accepted", True):
        return {"statusCode": 200, "body": "ignored"}
    return {"statusCode": 200, "body": result["status"]}


def _build_lambda_client() -> Any:
    import boto3

    return boto3.client("lambda")


def _build_s3_client() -> Any:
    import boto3

    return boto3.client("s3")


def _record_non_deployable_success(
    *,
    task_record: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    updated = dict(task_record)
    updated["deployment_skip_reason"] = reason
    updated["error_message"] = ""
    return updated
