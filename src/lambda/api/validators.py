# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import base64
import json
import re
from typing import Any

from shared.constants import DEFAULT_IMAGE_TAG
from shared.task_types import task_type_ids


S3_LOCATION_PATTERN = re.compile(r"^s3://[A-Za-z0-9._/\-]+$")
PREFIX_LOCATION_PATTERN = re.compile(r"^[A-Za-z0-9._/\-]+$")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,62}$")
IMAGE_TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class TaskValidationError(ValueError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class ConcurrencyLimitExceeded(RuntimeError):
    def __init__(self, active_task_count: int, limit_value: int) -> None:
        super().__init__("Too many active tasks")
        self.active_task_count = active_task_count
        self.limit_value = limit_value


def parse_submission(event: dict[str, Any]) -> dict[str, Any]:
    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode("utf-8")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise TaskValidationError("Request body must be valid JSON") from exc
    normalized = normalize_submission(payload)
    validate_submission(normalized)
    normalized["resolved_image_tag"] = resolve_image_tag(normalized.get("image_tag"))
    return normalized


def normalize_submission(payload: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            normalized[key] = value.strip()
        else:
            normalized[key] = value

    confirmation_flag = normalized.get("confirmation_flag")
    if isinstance(confirmation_flag, str):
        normalized["confirmation_flag"] = confirmation_flag.strip().lower() in {
            "true",
            "1",
            "yes",
        }
    else:
        normalized["confirmation_flag"] = bool(confirmation_flag)

    if not normalized.get("image_tag"):
        normalized["image_tag"] = None
    if not normalized.get("task_name"):
        normalized["task_name"] = None
    if not normalized.get("comment"):
        normalized["comment"] = None

    # Recurring/scheduled task fields (all optional; absent => one-off task).
    is_recurring = normalized.get("is_recurring")
    if isinstance(is_recurring, str):
        normalized["is_recurring"] = is_recurring.strip().lower() in {"true", "1", "yes"}
    else:
        normalized["is_recurring"] = bool(is_recurring)
    if not normalized.get("schedule_start"):
        normalized["schedule_start"] = None
    interval_value = normalized.get("interval_value")
    if isinstance(interval_value, str) and interval_value.strip():
        try:
            normalized["interval_value"] = int(interval_value.strip())
        except ValueError:
            normalized["interval_value"] = interval_value  # let validation reject
    elif interval_value is None:
        normalized["interval_value"] = None
    if not normalized.get("interval_unit"):
        normalized["interval_unit"] = None

    # Deployment mode: "auto" (deploy on successful training, default) or
    # "manual" (operator triggers deployment from the UI). Any unrecognized or
    # missing value falls back to "auto" to preserve prior behaviour.
    deployment_mode = (normalized.get("deployment_mode") or "").strip().lower()
    normalized["deployment_mode"] = (
        "manual" if deployment_mode == "manual" else "auto"
    )
    return normalized


def resolve_image_tag(image_tag: str | None) -> str:
    if image_tag is None:
        return DEFAULT_IMAGE_TAG
    cleaned = image_tag.strip()
    return cleaned or DEFAULT_IMAGE_TAG


def validate_submission(payload: dict[str, Any]) -> None:
    required_fields = [
        "task_type",
        "data_location",
        "output_location",
        "model_name",
        "target_endpoint",
        "submitted_by",
    ]
    missing_fields = [
        field for field in required_fields if not isinstance(payload.get(field), str) or not payload[field]
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
    if not is_valid_location(payload["data_location"], allowed_prefixes=("data/", "s3://")):
        raise TaskValidationError(
            "data_location format is invalid",
            details={"field": "data_location"},
        )
    if not is_valid_location(
        payload["output_location"], allowed_prefixes=("output/", "s3://")
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
    image_tag = payload.get("image_tag")
    if image_tag is not None and not IMAGE_TAG_PATTERN.match(image_tag):
        raise TaskValidationError(
            "image_tag format is invalid",
            details={"field": "image_tag"},
        )
    # task_name is an optional human-friendly label; allow Chinese/Unicode but
    # keep it bounded and free of control characters.
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
    # comment is an optional free-text note describing the task. It does NOT
    # feed into the training agent prompt — the agent's flow relies only on the
    # two skills. Allow Chinese/Unicode but keep it bounded.
    comment = payload.get("comment")
    if comment is not None:
        if not isinstance(comment, str) or len(comment) > 1000:
            raise TaskValidationError(
                "comment format is invalid",
                details={"field": "comment"},
            )
        if any(ord(ch) < 0x20 and ch not in "\n\r\t" for ch in comment):
            raise TaskValidationError(
                "comment must not contain control characters",
                details={"field": "comment"},
            )
    # Recurring task: when enabled, require a valid interval and start time.
    if payload.get("is_recurring"):
        interval_value = payload.get("interval_value")
        if not isinstance(interval_value, int) or isinstance(interval_value, bool) or interval_value < 1:
            raise TaskValidationError(
                "interval_value must be a positive integer for a recurring task",
                details={"field": "interval_value"},
            )
        if interval_value > 1000:
            raise TaskValidationError(
                "interval_value is too large",
                details={"field": "interval_value"},
            )
        if payload.get("interval_unit") not in ("minutes", "hours", "days"):
            raise TaskValidationError(
                "interval_unit must be one of minutes/hours/days",
                details={"field": "interval_unit"},
            )
        if not _is_valid_schedule_start(payload.get("schedule_start")):
            raise TaskValidationError(
                "schedule_start must be an ISO-8601 datetime like 2026-07-02T09:00[:00]",
                details={"field": "schedule_start"},
            )


def _is_valid_schedule_start(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip().replace("Z", "")
    from datetime import datetime

    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    return False


def is_valid_location(value: str, allowed_prefixes: tuple[str, ...]) -> bool:
    if ".." in value:
        return False
    if value.startswith("s3://"):
        return bool(S3_LOCATION_PATTERN.match(value))
    if not value.startswith(allowed_prefixes):
        return False
    return bool(PREFIX_LOCATION_PATTERN.match(value))
