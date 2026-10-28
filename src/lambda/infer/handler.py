# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import base64
import os
import uuid
from typing import Any

import boto3

from api.validators import TaskValidationError
from infer.repository import InferTaskRepository
from infer.sagemaker_infer import (
    InferenceInvocationError,
    SageMakerInferenceAdapter,
    _split_s3_uri,
    resolve_s3_uri,
)
from infer.validators import parse_infer_submission
from shared.logging_utils import log_event
from shared.responses import error_response, json_response
from shared.constants import (
    TASK_STATUS_FAILED,
    TASK_STATUS_SUCCEEDED,
)
from shared.time_utils import utc_timestamp


class InferTaskNotFoundError(RuntimeError):
    pass


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    method = (event.get("httpMethod") or "").upper()
    path = event.get("resource") or event.get("path") or ""
    request_id = getattr(context, "aws_request_id", None)

    log_event(
        "infer-handler",
        "request-received",
        request_id=request_id,
        method=method,
        path=path,
    )

    try:
        if method == "POST" and path == "/infer-tasks":
            return handle_post_infer(event, request_id=request_id)
        if method == "GET" and path == "/infer-tasks":
            return handle_get_infer_tasks(event, request_id=request_id)
        return error_response(404, "Route not found", "ROUTE_NOT_FOUND")
    except TaskValidationError as exc:
        log_event(
            "infer-handler",
            "validation-failed",
            level="WARN",
            request_id=request_id,
            details=exc.details,
        )
        return error_response(400, str(exc), "VALIDATION_ERROR", details=exc.details)
    except InferTaskNotFoundError as exc:
        return error_response(404, str(exc), "TASK_NOT_FOUND")
    except InferenceInvocationError as exc:
        return error_response(502, str(exc), "INFERENCE_DISPATCH_FAILED")
    except Exception as exc:  # pragma: no cover - defensive runtime path
        log_event(
            "infer-handler",
            "unexpected-error",
            level="ERROR",
            request_id=request_id,
            error=str(exc),
        )
        return error_response(500, "Unexpected backend error", "UNEXPECTED_BACKEND_ERROR")


def handle_post_infer(
    event: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    repository = build_repository()
    payload = parse_infer_submission(event)

    now = utc_timestamp()
    task_id = str(uuid.uuid4())
    bucket_name = os.environ["BUCKET_NAME"]
    input_uri = resolve_s3_uri(payload["data_location"], bucket_name)
    output_uri = resolve_s3_uri(payload["output_location"], bucket_name)
    task_record = {
        "task_id": task_id,
        "task_name": payload.get("task_name") or payload["model_name"],
        "task_type": payload["task_type"],
        "data_location": payload["data_location"],
        "output_location": payload["output_location"],
        "model_name": payload["model_name"],
        "target_endpoint": payload["target_endpoint"],
        "submitted_by": payload["submitted_by"],
        "description": payload.get("description") or "",
        "target_column": payload.get("target_column") or "target",
        "status": TASK_STATUS_SUCCEEDED,
        "result_location": "",
        "prediction_count": 0,
        # data_location may be a directory; record how many objects were read so
        # a run against a prefix is traceable after the fact.
        "input_file_count": 0,
        "error_message": "",
        "created_at": now,
        "updated_at": now,
    }

    # The deployed model is a real-time endpoint, so run inference SYNCHRONOUSLY:
    # read the prediction CSV, invoke the endpoint, write predictions to S3.
    adapter = SageMakerInferenceAdapter()
    try:
        result = adapter.run_sync(
            endpoint_name=payload["target_endpoint"],
            input_uri=input_uri,
            output_uri=output_uri,
            target_column=task_record["target_column"],
        )
    except InferenceInvocationError as exc:
        task_record["status"] = TASK_STATUS_FAILED
        task_record["error_message"] = str(exc)
        task_record["updated_at"] = utc_timestamp()
        repository.create_task(task_record)
        log_event(
            "infer-handler",
            "infer-dispatch-failed",
            level="ERROR",
            request_id=request_id,
            task_id=task_id,
            target_endpoint=payload["target_endpoint"],
            error=str(exc),
        )
        raise

    input_files = result.get("input_files") or []
    task_record["result_location"] = result["output_uri"]
    task_record["prediction_count"] = result["prediction_count"]
    task_record["input_file_count"] = len(input_files)
    task_record["updated_at"] = utc_timestamp()
    repository.create_task(task_record)

    log_event(
        "infer-handler",
        "infer-task-succeeded",
        request_id=request_id,
        task_id=task_id,
        target_endpoint=payload["target_endpoint"],
        prediction_count=result["prediction_count"],
        input_file_count=len(input_files),
    )
    return json_response(
        201,
        {
            "task_id": task_id,
            "status": task_record["status"],
            "message": "Inference completed successfully",
        },
    )


def handle_get_infer_tasks(
    event: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    query = event.get("queryStringParameters") or {}
    task_id = (query.get("task_id") or "").strip()
    download = (query.get("download") or "").strip()
    repository = build_repository()

    if task_id and download:
        return handle_download_result(task_id, repository, request_id=request_id)

    if task_id:
        task = repository.get_task(task_id)
        if task is None:
            raise InferTaskNotFoundError(f"Infer task {task_id} not found")
        return json_response(200, {"task": task})

    # Inference runs synchronously, so stored statuses are already terminal —
    # no polling/reconciliation needed.
    tasks = repository.list_tasks()
    summaries = [
        {
            "task_id": task["task_id"],
            "task_name": task.get("task_name", task.get("model_name", "")),
            "status": task["status"],
            "task_type": task.get("task_type", ""),
            "model_name": task.get("model_name", ""),
            "target_endpoint": task.get("target_endpoint", ""),
            "data_location": task.get("data_location", ""),
            "output_location": task.get("output_location", ""),
            "submitted_by": task.get("submitted_by", ""),
            "description": task.get("description", ""),
            "target_column": task.get("target_column", ""),
            "result_location": task.get("result_location", ""),
            "prediction_count": int(task.get("prediction_count", 0) or 0),
            "input_file_count": int(task.get("input_file_count", 0) or 0),
            "created_at": task.get("created_at", ""),
            "updated_at": task.get("updated_at", ""),
        }
        for task in tasks
    ]
    log_event(
        "infer-handler",
        "infer-tasks-listed",
        request_id=request_id,
        task_count=len(summaries),
    )
    return json_response(200, {"tasks": summaries})


# Max result size returned inline (base64) through the API. China blocks
# browser access to presigned S3 URLs (未备案 domain edge rule), so the result
# is proxied through the signed API instead. API Gateway caps the response at
# ~10MB and base64 inflates by ~33%; prediction results are small JSON files.
_MAX_INLINE_DOWNLOAD_BYTES = 6 * 1024 * 1024


def handle_download_result(
    task_id: str,
    repository: InferTaskRepository,
    request_id: str | None = None,
    s3_client: Any | None = None,
) -> dict[str, Any]:
    """Return a succeeded inference task's result file inline as base64 so the
    browser can download it WITHOUT hitting a presigned S3 URL (China blocks
    browser access to the 未备案 S3 domain). The download is proxied through the
    signed API."""
    task = repository.get_task(task_id)
    if task is None:
        raise InferTaskNotFoundError(f"Infer task {task_id} not found")
    if task.get("status") != TASK_STATUS_SUCCEEDED:
        raise TaskValidationError("Only succeeded inference tasks have a downloadable result")
    result_location = (task.get("result_location") or "").strip()
    if not result_location.startswith("s3://"):
        raise TaskValidationError("This task has no downloadable result file")

    bucket, key = _split_s3_uri(result_location)
    file_name = key.rsplit("/", 1)[-1] or "predictions.json"
    client = s3_client or boto3.client("s3")
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    if len(body) > _MAX_INLINE_DOWNLOAD_BYTES:
        raise TaskValidationError(
            f"结果文件过大（{len(body) // (1024 * 1024)} MB），无法在线下载；"
            f"仅支持小于 {_MAX_INLINE_DOWNLOAD_BYTES // (1024 * 1024)} MB 的结果文件"
        )
    log_event(
        "infer-handler",
        "infer-result-downloaded",
        request_id=request_id,
        task_id=task_id,
    )
    return json_response(
        200,
        {
            "file_name": file_name,
            "content_type": response.get("ContentType") or "application/octet-stream",
            "content_base64": base64.b64encode(body).decode("ascii"),
            "size": len(body),
        },
    )


def build_repository() -> InferTaskRepository:
    return InferTaskRepository(table_name=os.environ["INFER_TABLE_NAME"])
