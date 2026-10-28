# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import os
import uuid
from typing import Any

from api.batch_adapter import BatchAdapter, BatchSubmissionError
from api.log_reader import LogReader
from api.repository import TaskRepository
from api.scheduler_adapter import ScheduleError, SchedulerAdapter, build_rate_expression
from api.validators import ConcurrencyLimitExceeded, TaskValidationError, parse_submission
from deployment.model_registry import ModelRegistryRepository, project_model_list_item
from deployment.runtime_outputs import (
    RuntimeManifestError,
    evaluate_deployment_eligibility,
    load_runtime_manifest,
)
from shared.deployment_dispatch import DeploymentNotEligibleError, dispatch_deployment
from shared.logging_utils import log_event
from shared.task_types import load_task_types
from shared.notifications import build_message_repository, record_status_change
from shared.responses import (
    error_response,
    models_response,
    task_log_response,
    task_submission_response,
    tasks_response,
)
from shared.constants import (
    ACTIVE_TASK_STATUSES,
    CANCELLABLE_TASK_STATUSES,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_SCHEDULED,
)
from shared.executions import make_execution_id, start_execution
from shared.responses import json_response
from shared.task_status import (
    TASK_STATUS_FAILED,
    TASK_STATUS_SUBMITTED,
    TASK_STATUS_SUCCEEDED,
    apply_status_transition,
)
from shared.time_utils import utc_timestamp


# EventBridge Scheduler invokes this same Lambda with this marker in the event
# payload to fire one run of a recurring (parent) task.
SCHEDULED_TRIGGER_KEY = "scheduled_trigger"


class TaskNotFoundError(RuntimeError):
    pass


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    request_id = getattr(context, "aws_request_id", None)

    # EventBridge Scheduler fire: not an API Gateway request. Launch one run of
    # the recurring parent task, then return.
    if isinstance(event, dict) and event.get(SCHEDULED_TRIGGER_KEY):
        return handle_scheduled_trigger(event, request_id=request_id)

    method = (event.get("httpMethod") or "").upper()
    path = event.get("resource") or event.get("path") or ""

    log_event(
        "api-handler",
        "request-received",
        request_id=request_id,
        method=method,
        path=path,
    )

    try:
        if method == "POST" and path == "/tasks":
            return handle_post_tasks(event, request_id=request_id)
        if method == "POST" and path == "/tasks/cancel":
            return handle_cancel_task(event, request_id=request_id)
        if method == "POST" and path == "/tasks/deploy":
            return handle_deploy_task(event, request_id=request_id)
        if method == "GET" and path == "/tasks":
            return handle_get_tasks(request_id=request_id)
        if method == "GET" and path == "/models":
            return handle_get_models(request_id=request_id)
        if method == "GET" and path == "/task-types":
            return handle_get_task_types(request_id=request_id)
        if method == "GET" and path == "/logs":
            return handle_get_logs(event, request_id=request_id)
        if method == "GET" and path == "/tasks/evaluation":
            return handle_get_evaluation(event, request_id=request_id)
        if method == "GET" and path == "/messages":
            return handle_get_messages(event, request_id=request_id)
        if method == "POST" and path == "/messages":
            return handle_mark_message_read(event, request_id=request_id)
        return error_response(404, "Route not found", "ROUTE_NOT_FOUND")
    except TaskValidationError as exc:
        log_event(
            "api-handler",
            "validation-failed",
            level="WARN",
            request_id=request_id,
            details=exc.details,
        )
        return error_response(
            400,
            str(exc),
            "VALIDATION_ERROR",
            details=exc.details,
        )
    except ConcurrencyLimitExceeded as exc:
        log_event(
            "api-handler",
            "concurrency-rejected",
            level="WARN",
            request_id=request_id,
            active_task_count=exc.active_task_count,
            limit_value=exc.limit_value,
        )
        return error_response(
            409,
            "Too many active tasks",
            "CONCURRENCY_LIMIT_EXCEEDED",
            details={
                "active_task_count": exc.active_task_count,
                "limit_value": exc.limit_value,
            },
        )
    except TaskNotFoundError as exc:
        return error_response(404, str(exc), "TASK_NOT_FOUND")
    except BatchSubmissionError as exc:
        return error_response(
            502,
            str(exc),
            "BATCH_SUBMISSION_FAILED",
        )
    except Exception as exc:  # pragma: no cover - defensive runtime path
        log_event(
            "api-handler",
            "unexpected-error",
            level="ERROR",
            request_id=request_id,
            error=str(exc),
        )
        return error_response(
            500,
            "Unexpected backend error",
            "UNEXPECTED_BACKEND_ERROR",
        )


def _new_task_record(payload: dict[str, Any], now: str) -> dict[str, Any]:
    """Build a base training-task record from a validated submission payload."""
    return {
        "task_id": str(uuid.uuid4()),
        "task_name": payload.get("task_name") or payload["model_name"],
        "task_type": payload["task_type"],
        # comment is a human-facing note only; it does not participate in the
        # training agent prompt (the agent relies on the two skills).
        "comment": payload.get("comment") or "",
        "data_location": payload["data_location"],
        "output_location": payload["output_location"],
        "model_name": payload["model_name"],
        "target_endpoint": payload["target_endpoint"],
        "submitted_by": payload["submitted_by"],
        "resolved_image_tag": payload["resolved_image_tag"],
        "status": TASK_STATUS_SUBMITTED,
        # batch_job_id is intentionally omitted until a real Batch job id is
        # known: it is the partition key of the batch-job-id-index GSI, and
        # DynamoDB rejects an empty string for an indexed key attribute.
        "error_message": "",
        "deployment_skip_reason": "",
        # "auto": deploy automatically once training succeeds (default);
        # "manual": operator triggers deployment from the UI.
        "deployment_mode": payload.get("deployment_mode") or "auto",
        # One-off task by default; periodic tasks flip this and carry an
        # executions[] history managed on the same record.
        "is_periodical": False,
        "created_at": now,
        "updated_at": now,
    }


def _submit_training_run(
    *,
    repository: TaskRepository,
    task_record: dict[str, Any],
    request_id: str | None,
) -> dict[str, Any]:
    """Persist a task record and submit it to Batch. On Batch failure the record
    is marked FAILED and the error re-raised. Returns the updated record."""
    repository.create_task(task_record)
    adapter = BatchAdapter()
    try:
        submission = adapter.submit_task(
            task_record=task_record,
            bucket_name=os.environ["BUCKET_NAME"],
            job_queue_arn=os.environ["JOB_QUEUE_ARN"],
            job_definition_arn=os.environ["JOB_DEF_ARN"],
            image_repo_uri=os.environ["IMAGE_REPO_URI"],
            # Periodic tasks scope each run's S3 output/logs by execution id.
            execution_id=task_record.get("active_execution_id"),
        )
    except BatchSubmissionError as exc:
        failed_record = apply_status_transition(
            task_record,
            TASK_STATUS_FAILED,
            now=utc_timestamp(),
            error_message=str(exc),
        )
        repository.update_task(failed_record)
        log_event(
            "api-handler",
            "task-submission-failed",
            level="ERROR",
            request_id=request_id,
            task_id=task_record["task_id"],
            error=str(exc),
        )
        raise

    task_record["batch_job_id"] = submission["job_id"]
    task_record["updated_at"] = utc_timestamp()
    repository.update_task(task_record)
    log_event(
        "api-handler",
        "task-submitted",
        request_id=request_id,
        task_id=task_record["task_id"],
        batch_job_id=submission["job_id"],
    )
    return task_record


def handle_post_tasks(
    event: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    repository = build_repository()
    payload = parse_submission(event)

    if payload.get("is_recurring"):
        return _create_recurring_task(
            repository=repository, payload=payload, request_id=request_id
        )

    active_task_count = repository.count_active_tasks()
    max_concurrent = int(os.environ.get("MAX_CONCURRENT", "5"))
    if active_task_count >= max_concurrent:
        raise ConcurrencyLimitExceeded(active_task_count, max_concurrent)

    task_record = _new_task_record(payload, utc_timestamp())
    task_record = _submit_training_run(
        repository=repository, task_record=task_record, request_id=request_id
    )
    return task_submission_response(
        task_id=task_record["task_id"],
        status=task_record["status"],
        message="Task submitted successfully",
    )


def _create_recurring_task(
    *,
    repository: TaskRepository,
    payload: dict[str, Any],
    request_id: str | None,
) -> dict[str, Any]:
    """Create a single periodic task record (is_periodical=True) plus an
    EventBridge schedule that fires first at schedule_start and then every
    interval. The record stays SCHEDULED until its first fire; each fire runs
    Batch on THIS SAME record and appends to its executions[] history (see
    handle_scheduled_trigger)."""
    now = utc_timestamp()
    task_id = str(uuid.uuid4())
    schedule_name = f"training-{task_id}"
    rate_expression = build_rate_expression(
        int(payload["interval_value"]), payload["interval_unit"]
    )
    parent_record = {
        "task_id": task_id,
        "task_name": payload.get("task_name") or payload["model_name"],
        "task_type": payload["task_type"],
        "comment": payload.get("comment") or "",
        "data_location": payload["data_location"],
        "output_location": payload["output_location"],
        "model_name": payload["model_name"],
        "target_endpoint": payload["target_endpoint"],
        "submitted_by": payload["submitted_by"],
        "resolved_image_tag": payload["resolved_image_tag"],
        "status": TASK_STATUS_SCHEDULED,
        # "auto" (default) or "manual" — same semantics as one-off tasks; each
        # periodic execution honours this on completion.
        "deployment_mode": payload.get("deployment_mode") or "auto",
        # Single-record periodic model: this record manages all runs via its
        # executions[] history. `is_recurring` kept for backward compatibility.
        "is_periodical": True,
        "is_recurring": True,
        "schedule_name": schedule_name,
        "schedule_start": payload["schedule_start"],
        "interval_value": int(payload["interval_value"]),
        "interval_unit": payload["interval_unit"],
        "schedule_expression": rate_expression,
        "executions": [],
        "active_execution_id": "",
        "error_message": "",
        "deployment_skip_reason": "",
        "created_at": now,
        "updated_at": now,
    }
    repository.create_task(parent_record)

    scheduler = SchedulerAdapter()
    try:
        scheduler.create_schedule(
            name=schedule_name,
            group_name=os.environ["SCHEDULE_GROUP_NAME"],
            rate_expression=rate_expression,
            start_time=payload["schedule_start"],
            target_arn=os.environ["API_FUNCTION_ARN"],
            role_arn=os.environ["SCHEDULER_ROLE_ARN"],
            input_payload={SCHEDULED_TRIGGER_KEY: True, "parent_task_id": task_id},
        )
    except ScheduleError as exc:
        parent_record["status"] = TASK_STATUS_FAILED
        parent_record["error_message"] = str(exc)
        parent_record["updated_at"] = utc_timestamp()
        repository.update_task(parent_record)
        log_event(
            "api-handler",
            "schedule-create-failed",
            level="ERROR",
            request_id=request_id,
            task_id=task_id,
            error=str(exc),
        )
        raise
    log_event(
        "api-handler",
        "recurring-task-scheduled",
        request_id=request_id,
        task_id=task_id,
        schedule_name=schedule_name,
        schedule_expression=rate_expression,
        schedule_start=payload["schedule_start"],
    )
    return task_submission_response(
        task_id=task_id,
        status=parent_record["status"],
        message="Recurring task scheduled successfully",
    )


def handle_scheduled_trigger(
    event: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    """Fired by EventBridge Scheduler. Start a new execution ON THE SAME
    periodic task record: append an execution entry (timestamped S3 scope) and
    submit it to Batch. The record's executions[] history accumulates every run."""
    parent_task_id = (event.get("parent_task_id") or "").strip()
    repository = build_repository()
    task = repository.get_task(parent_task_id) if parent_task_id else None
    if task is None:
        log_event(
            "api-handler",
            "scheduled-trigger-task-missing",
            level="WARN",
            request_id=request_id,
            parent_task_id=parent_task_id,
        )
        return {"statusCode": 200, "body": "task not found"}

    # If cancelled, ignore the fire (schedule deletion may lag).
    if task.get("status") == TASK_STATUS_CANCELLED:
        return {"statusCode": 200, "body": "task cancelled"}

    # Skip if the previous execution is still running (avoid overlapping runs).
    if task.get("status") in ACTIVE_TASK_STATUSES:
        log_event(
            "api-handler",
            "scheduled-trigger-skipped-active",
            level="WARN",
            request_id=request_id,
            parent_task_id=parent_task_id,
            status=task.get("status"),
        )
        return {"statusCode": 200, "body": "previous execution still active"}

    now = utc_timestamp()
    execution_id = make_execution_id(now)
    start_execution(task, execution_id, now)
    # Reset the record for a fresh run. batch_job_id is dropped until Batch
    # returns a new one (it is the GSI key and cannot be empty).
    task["status"] = TASK_STATUS_SUBMITTED
    task["error_message"] = ""
    task["deployment_skip_reason"] = ""
    task.pop("batch_job_id", None)
    task["updated_at"] = now

    adapter = BatchAdapter()
    repository.update_task(task)
    try:
        submission = adapter.submit_task(
            task_record=task,
            bucket_name=os.environ["BUCKET_NAME"],
            job_queue_arn=os.environ["JOB_QUEUE_ARN"],
            job_definition_arn=os.environ["JOB_DEF_ARN"],
            image_repo_uri=os.environ["IMAGE_REPO_URI"],
            execution_id=execution_id,
        )
    except BatchSubmissionError as exc:
        task["status"] = TASK_STATUS_FAILED
        task["error_message"] = str(exc)
        task["updated_at"] = utc_timestamp()
        repository.update_task(task)
        return {"statusCode": 200, "body": "execution submission failed"}

    task["batch_job_id"] = submission["job_id"]
    task["updated_at"] = utc_timestamp()
    repository.update_task(task)
    log_event(
        "api-handler",
        "scheduled-execution-submitted",
        request_id=request_id,
        task_id=parent_task_id,
        execution_id=execution_id,
        batch_job_id=submission["job_id"],
    )
    return {"statusCode": 200, "body": execution_id}


def handle_cancel_task(
    event: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    import base64
    import json

    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode("utf-8")
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        raise TaskValidationError("Request body must be valid JSON") from exc
    task_id = (payload.get("task_id") or "").strip() if isinstance(payload, dict) else ""
    if not task_id:
        # also accept ?task_id= for convenience
        query = event.get("queryStringParameters") or {}
        task_id = (query.get("task_id") or "").strip()
    if not task_id:
        raise TaskValidationError("Missing task_id")

    repository = build_repository()
    task = repository.get_task(task_id)
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} not found")

    # Periodic tasks are cancellable from any non-cancelled state (to stop future
    # runs) even when idle between executions (e.g. SUCCEEDED/DEPLOYED). One-off
    # tasks follow the normal cancellable-status rule.
    is_periodical = bool(task.get("is_periodical"))
    if task["status"] == TASK_STATUS_CANCELLED or (
        not is_periodical and task["status"] not in CANCELLABLE_TASK_STATUSES
    ):
        raise TaskValidationError(
            "Task is not in a cancellable state",
            details={"status": task["status"]},
        )

    # Periodic task: delete the EventBridge schedule so no further runs fire.
    # The single record's own Batch job (if a run is active) is terminated below.
    if task.get("is_periodical") and task.get("schedule_name"):
        try:
            SchedulerAdapter().delete_schedule(
                name=task["schedule_name"],
                group_name=os.environ["SCHEDULE_GROUP_NAME"],
            )
        except ScheduleError as exc:  # pragma: no cover - boto3 specific failures
            log_event(
                "api-handler",
                "task-cancel-schedule-delete-failed",
                level="WARN",
                request_id=request_id,
                task_id=task_id,
                schedule_name=task.get("schedule_name"),
                error=str(exc),
            )

    # Best-effort terminate the Batch job if one was already submitted.
    batch_job_id = (task.get("batch_job_id") or "").strip()
    if batch_job_id:
        try:
            BatchAdapter().terminate_task(batch_job_id, reason="Cancelled by operator")
        except Exception as exc:  # pragma: no cover - boto3 specific failures
            log_event(
                "api-handler",
                "task-cancel-batch-terminate-failed",
                level="WARN",
                request_id=request_id,
                task_id=task_id,
                batch_job_id=batch_job_id,
                error=str(exc),
            )

    previous_status = task["status"]
    if is_periodical:
        # A periodic record may be idle-between-runs (SUCCEEDED/DEPLOYED), which
        # the linear transition table would reject. Set CANCELLED directly to
        # stop the schedule and freeze the record.
        cancelled = dict(task)
        cancelled["status"] = TASK_STATUS_CANCELLED
        cancelled["error_message"] = "Cancelled by operator"
        cancelled["updated_at"] = utc_timestamp()
    else:
        cancelled = apply_status_transition(
            task,
            TASK_STATUS_CANCELLED,
            now=utc_timestamp(),
            error_message="Cancelled by operator",
        )
    repository.update_task(cancelled)
    record_status_change(
        cancelled, previous_status, cancelled["status"], request_id=request_id
    )
    log_event(
        "api-handler",
        "task-cancelled",
        request_id=request_id,
        task_id=task_id,
        batch_job_id=batch_job_id,
    )
    return json_response(
        200,
        {
            "task_id": task_id,
            "status": cancelled["status"],
            "message": "Task cancelled",
        },
    )


def handle_deploy_task(
    event: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    """Manually deploy a SUCCEEDED task's model to its SageMaker endpoint.

    Only valid for tasks whose training has succeeded and that are not already
    deploying/deployed. Reuses the exact deploy-dispatch path as the automatic
    (on-success) flow."""
    import base64
    import json as _json

    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode("utf-8")
    try:
        payload = _json.loads(raw_body) if raw_body else {}
    except _json.JSONDecodeError as exc:
        raise TaskValidationError("Request body must be valid JSON") from exc
    task_id = (payload.get("task_id") or "").strip() if isinstance(payload, dict) else ""
    if not task_id:
        query = event.get("queryStringParameters") or {}
        task_id = (query.get("task_id") or "").strip()
    if not task_id:
        raise TaskValidationError("Missing task_id")

    repository = build_repository()
    task = repository.get_task(task_id)
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} not found")

    if task.get("status") != TASK_STATUS_SUCCEEDED:
        raise TaskValidationError(
            "Only succeeded tasks can be deployed",
            details={"status": task.get("status", "")},
        )

    model_registry = build_model_registry_repository()
    message_repo = build_message_repository()
    bucket_name = os.environ["BUCKET_NAME"]
    s3_client = _build_s3_client()
    try:
        manifest = load_runtime_manifest(
            bucket_name=bucket_name, task_record=task, s3_client=s3_client
        )
        decision = evaluate_deployment_eligibility(
            task_record=task, manifest=manifest, s3_client=s3_client
        )
        result = dispatch_deployment(
            task_record=task,
            decision=decision,
            repository=repository,
            model_registry=model_registry,
            bucket_name=bucket_name,
            deployment_function_name=os.environ["DEPLOYMENT_FUNCTION_NAME"],
            lambda_client=_build_lambda_client(),
            update_and_notify=lambda updated, prev: _deploy_update_and_notify(
                repository, updated, prev, request_id, message_repo
            ),
            request_id=request_id,
        )
    except DeploymentNotEligibleError as exc:
        raise TaskValidationError(
            f"Task is not deployable: {exc}",
            details={"reason": str(exc)},
        )
    except RuntimeManifestError as exc:
        raise TaskValidationError(
            f"Task is not deployable: {exc}",
            details={"reason": str(exc)},
        )

    log_event(
        "api-handler",
        "task-deploy-triggered",
        request_id=request_id,
        task_id=task_id,
        status=result["status"],
    )
    return json_response(
        200,
        {
            "task_id": task_id,
            "status": result["status"],
            "message": "Deployment triggered",
        },
    )


def _deploy_update_and_notify(
    repository: TaskRepository,
    updated_task: dict[str, Any],
    previous_status: str,
    request_id: str | None,
    message_repo: Any | None,
) -> None:
    repository.update_task(updated_task)
    record_status_change(
        updated_task,
        previous_status,
        updated_task.get("status", ""),
        repository=message_repo,
        request_id=request_id,
    )


def _build_lambda_client() -> Any:
    import boto3

    return boto3.client("lambda")


def _build_s3_client() -> Any:
    import boto3

    return boto3.client("s3")


def handle_get_tasks(request_id: str | None = None) -> dict[str, Any]:
    repository = build_repository()
    tasks = repository.list_tasks()
    summaries = []
    for task in tasks:
        summaries.append(
            {
                "task_id": task["task_id"],
                "task_name": task.get("task_name", task.get("model_name", "")),
                "status": task["status"],
                "task_type": task["task_type"],
                "model_name": task["model_name"],
                "target_endpoint": task["target_endpoint"],
                "submitted_by": task["submitted_by"],
                "resolved_image_tag": task["resolved_image_tag"],
                "created_at": task["created_at"],
                "updated_at": task["updated_at"],
                "deployment_mode": task.get("deployment_mode", "auto"),
                # Periodic/scheduled metadata (present only for periodic tasks).
                "is_periodical": bool(task.get("is_periodical")),
                "is_recurring": bool(task.get("is_recurring")),
                "schedule_expression": task.get("schedule_expression", ""),
                "schedule_start": task.get("schedule_start", ""),
                "interval_value": task.get("interval_value"),
                "interval_unit": task.get("interval_unit", ""),
                # Execution history (per-run time + status) for periodic tasks.
                "executions": task.get("executions", []),
            }
        )
    log_event(
        "api-handler",
        "tasks-listed",
        request_id=request_id,
        task_count=len(summaries),
    )
    return tasks_response(summaries)


def handle_get_task_types(request_id: str | None = None) -> dict[str, Any]:
    """GET /task-types: the configured task types (cdk.json "taskTypes"), so
    the frontend can render the type dropdown from the same single source of
    truth the backend validates against. Only id + label are exposed; the
    skill/eval-file fields are backend-internal."""
    task_types = [
        {"id": entry["id"], "label": entry["label"]}
        for entry in load_task_types()
    ]
    log_event(
        "api-handler",
        "task-types-listed",
        request_id=request_id,
        task_type_count=len(task_types),
    )
    from shared.responses import json_response

    return json_response(200, {"task_types": task_types})


def handle_get_models(request_id: str | None = None) -> dict[str, Any]:
    repository = build_model_registry_repository()
    models = [
        project_model_list_item(record)
        for record in repository.list_model_records()
    ]
    log_event(
        "api-handler",
        "models-listed",
        request_id=request_id,
        model_count=len(models),
    )
    return models_response(models)


def handle_get_logs(
    event: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    query = event.get("queryStringParameters") or {}
    task_id = (query.get("task_id") or "").strip()
    execution_id = (query.get("execution_id") or "").strip() or None
    if not task_id:
        raise TaskValidationError("Missing task_id query parameter")

    repository = build_repository()
    task = repository.get_task(task_id)
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} not found")

    reader = LogReader()
    logs = reader.read_task_logs(
        bucket_name=os.environ["BUCKET_NAME"],
        task_id=task_id,
        execution_id=execution_id,
    )
    log_event(
        "api-handler",
        "logs-read",
        request_id=request_id,
        task_id=task_id,
        execution_id=execution_id or "",
        status=task["status"],
        log_count=len(logs),
    )
    return task_log_response(task_id=task_id, status=task["status"], logs=logs)


def handle_get_evaluation(
    event: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    """Return the structured evaluation_report.json for a task's training run so
    the detail view can render it. Reads the artifact directly from the run's
    S3 output prefix (output/<task>/[<execution>/]evaluation_report.json)."""
    import json

    import boto3

    from shared.executions import training_output_prefix

    query = event.get("queryStringParameters") or {}
    task_id = (query.get("task_id") or "").strip()
    execution_id = (query.get("execution_id") or "").strip() or None
    if not task_id:
        raise TaskValidationError("Missing task_id query parameter")

    repository = build_repository()
    task = repository.get_task(task_id)
    if task is None:
        raise TaskNotFoundError(f"Task {task_id} not found")

    # For a one-off task, fall back to the task's active execution (periodic runs
    # store artifacts under output/<task>/<execution>/).
    if execution_id is None:
        execution_id = (task.get("active_execution_id") or "").strip() or None

    bucket_name = os.environ["BUCKET_NAME"]
    prefix = training_output_prefix(task_id, execution_id).rstrip("/")
    # The report lives under one of two layouts depending on when the run was
    # produced: the original flat layout wrote it directly under the run prefix
    # (output/<task>/evaluation_report.json), while runs since the "training
    # output goes under output/" change nest it one level deeper because the
    # runtime uploads the whole attempt tree, which carries its own output/
    # subdirectory (output/<task>/output/evaluation_report.json). Probe both,
    # preferring the nested (current) layout, so old and new runs both resolve.
    candidate_keys = [
        f"{prefix}/output/evaluation_report.json",
        f"{prefix}/evaluation_report.json",
    ]

    client = boto3.client("s3")
    report: dict[str, Any] | None = None
    for key in candidate_keys:
        try:
            response = client.get_object(Bucket=bucket_name, Key=key)
            body = response.get("Body")
            payload = body.read() if body is not None else b""
            parsed = json.loads(payload.decode("utf-8"))
            if isinstance(parsed, dict):
                report = parsed
                break
        except client.exceptions.NoSuchKey:
            continue
        except Exception:  # noqa: BLE001 - malformed/unreadable report is not fatal
            report = None
            break

    log_event(
        "api-handler",
        "evaluation-read",
        request_id=request_id,
        task_id=task_id,
        execution_id=execution_id or "",
        found=report is not None,
    )
    return json_response(
        200,
        {
            "task_id": task_id,
            "execution_id": execution_id or "",
            "status": task["status"],
            "report": report,
        },
    )


def handle_get_messages(
    event: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    """List a user's recent notification messages (newest first). The recipient
    is taken from the ``recipient`` query parameter (the logged-in user name)."""
    query = event.get("queryStringParameters") or {}
    recipient = (query.get("recipient") or "").strip()
    if not recipient:
        raise TaskValidationError("Missing recipient query parameter")

    message_repo = build_message_repository()
    messages: list[dict[str, Any]] = []
    if message_repo is not None:
        # Only unread messages are surfaced in the notification panel.
        for record in message_repo.list_messages(recipient, unread_only=True):
            messages.append(
                {
                    "message_id": record.get("message_id", ""),
                    "content": record.get("content", ""),
                    "task_id": record.get("task_id", ""),
                    "task_name": record.get("task_name", ""),
                    "submitted_by": record.get("submitted_by", ""),
                    "read": bool(record.get("read", False)),
                    "created_at": record.get("created_at", ""),
                }
            )
    log_event(
        "api-handler",
        "messages-listed",
        request_id=request_id,
        recipient=recipient,
        message_count=len(messages),
    )
    return json_response(200, {"messages": messages})


def handle_mark_message_read(
    event: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    """Mark one notification message as read."""
    import base64
    import json

    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode("utf-8")
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError as exc:
        raise TaskValidationError("Request body must be valid JSON") from exc
    message_id = (payload.get("message_id") or "").strip() if isinstance(payload, dict) else ""
    if not message_id:
        raise TaskValidationError("Missing message_id")

    message_repo = build_message_repository()
    if message_repo is not None:
        message_repo.mark_read(message_id)
    log_event(
        "api-handler",
        "message-marked-read",
        request_id=request_id,
        message_id=message_id,
    )
    return json_response(200, {"message_id": message_id, "read": True})


def build_repository() -> TaskRepository:
    return TaskRepository(
        table_name=os.environ["TABLE_NAME"],
        batch_job_index=os.environ.get("TASK_BATCH_JOB_INDEX", "batch-job-id-index"),
    )


def build_model_registry_repository() -> ModelRegistryRepository:
    return ModelRegistryRepository(
        table_name=os.environ["MODEL_REGISTRY_TABLE_NAME"],
        target_endpoint_index=os.environ.get(
            "MODEL_REGISTRY_TARGET_ENDPOINT_INDEX",
            "target-endpoint-index",
        ),
    )
