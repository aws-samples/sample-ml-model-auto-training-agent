# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import base64
import json
from typing import Any

from api.validators import (
    NAME_PATTERN,
    TaskValidationError,
    is_valid_location,
)
from shared.task_types import task_type_ids


def parse_infer_submission(event: dict[str, Any]) -> dict[str, Any]:
    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode("utf-8")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise TaskValidationError("Request body must be valid JSON") from exc
    normalized = normalize_infer_submission(payload)
    validate_infer_submission(normalized)
    return normalized


def normalize_infer_submission(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        normalized[key] = value.strip() if isinstance(value, str) else value

    confirmation_flag = normalized.get("confirmation_flag")
    if isinstance(confirmation_flag, str):
        normalized["confirmation_flag"] = confirmation_flag.strip().lower() in {
            "true",
            "1",
            "yes",
        }
    else:
        normalized["confirmation_flag"] = bool(confirmation_flag)

    if not normalized.get("task_name"):
        normalized["task_name"] = None
    if not normalized.get("description"):
        normalized["description"] = None
    # Column in the prediction CSV that holds the historical target values. The
    # endpoint requires a `target` column, so this column is renamed to
    # `target` at inference time. Defaults to "target".
    if not normalized.get("target_column"):
        normalized["target_column"] = "target"
    return normalized


def validate_infer_submission(payload: dict[str, Any]) -> None:
    required_fields = [
        "task_type",
        "data_location",
        "output_location",
        "model_name",
        "target_endpoint",
        "submitted_by",
    ]
    missing_fields = [
        field
        for field in required_fields
        if not isinstance(payload.get(field), str) or not payload[field]
    ]
    if missing_fields:
        raise TaskValidationError(
            "Missing or empty required fields",
            details={"missing_fields": missing_fields},
        )
    if payload["task_type"] not in task_type_ids():
        raise TaskValidationError(
            "Unsupported task type",
            details={"task_type": payload["task_type"]},
        )
    if not payload.get("confirmation_flag"):
        raise TaskValidationError("Task submission requires explicit confirmation")
    # Prediction input lives under inference_data/ (or a full s3:// URI).
    if not is_valid_location(
        payload["data_location"], allowed_prefixes=("inference_data/", "s3://")
    ):
        raise TaskValidationError(
            "data_location format is invalid",
            details={"field": "data_location"},
        )
    if not is_valid_location(
        payload["output_location"], allowed_prefixes=("inference_output/", "s3://")
    ):
        raise TaskValidationError(
            "output_location format is invalid",
            details={"field": "output_location"},
        )
    for field_name in ("model_name", "target_endpoint", "submitted_by"):
        if not NAME_PATTERN.match(payload[field_name]):
            raise TaskValidationError(
                f"{field_name} format is invalid",
                details={"field": field_name},
            )
    # description is an optional free-text note; allow Chinese/Unicode but keep
    # it bounded and free of control characters.
    description = payload.get("description")
    if description is not None:
        if not isinstance(description, str) or len(description) > 1000:
            raise TaskValidationError(
                "description format is invalid",
                details={"field": "description"},
            )
        if any(ord(ch) < 0x20 and ch not in "\n\r\t" for ch in description):
            raise TaskValidationError(
                "description must not contain control characters",
                details={"field": "description"},
            )
    task_name = payload.get("task_name")
    if task_name is not None:
        if not isinstance(task_name, str) or len(task_name) > 100:
            raise TaskValidationError(
                "task_name format is invalid",
                details={"field": "task_name"},
            )
        if any(ord(ch) < 0x20 for ch in task_name):
            raise TaskValidationError(
                "task_name must not contain control characters",
                details={"field": "task_name"},
            )
    # target_column is a CSV header name; keep it a bounded, sane token.
    target_column = payload.get("target_column")
    if not isinstance(target_column, str) or not target_column or len(target_column) > 128:
        raise TaskValidationError(
            "target_column is invalid",
            details={"field": "target_column"},
        )
    if any(ord(ch) < 0x20 for ch in target_column):
        raise TaskValidationError(
            "target_column must not contain control characters",
            details={"field": "target_column"},
        )
