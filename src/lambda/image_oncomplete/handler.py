# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import os
from typing import Any

import boto3

from api.image_repository import (
    IMAGE_STATUS_FAILED,
    IMAGE_STATUS_SUCCEEDED,
    ImageRepository,
)
from shared.logging_utils import log_event
from shared.time_utils import utc_timestamp


# CodeBuild build phase-change / state-change status -> image status.
_SUCCESS_STATES = {"SUCCEEDED"}
_FAILURE_STATES = {"FAILED", "FAULT", "TIMED_OUT", "STOPPED"}


def _normalize_build_id(raw: str) -> str:
    """CodeBuild's StartBuild returns a short build id
    (``<project>:<uuid>``), which is what we persist on the image row. But the
    EventBridge "CodeBuild Build State Change" event carries ``build-id`` as a
    FULL ARN in some partitions (notably aws-cn):
    ``arn:aws-cn:codebuild:<region>:<acct>:build/<project>:<uuid>``.
    Reduce the ARN to the short id (the part after ``build/``) so the GSI lookup
    matches the stored value. A value that is already short is returned as-is."""
    if raw.startswith("arn:") and ":build/" in raw:
        return raw.split(":build/", 1)[1]
    return raw


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """React to CodeBuild State Change events for the agent image build project.
    Finds the image row by CodeBuild build id and reconciles its status."""
    request_id = getattr(context, "aws_request_id", None)
    detail = event.get("detail", {})
    build_id = _normalize_build_id(detail.get("build-id", "") or "")
    build_status = (detail.get("build-status") or "").upper()

    repository = ImageRepository(
        table_name=os.environ["IMAGE_TABLE_NAME"],
        build_id_index=os.environ.get("IMAGE_BUILD_ID_INDEX", "build-id-index"),
    )
    record = repository.get_image_by_build_id(build_id) if build_id else None
    if record is None or build_status not in (_SUCCESS_STATES | _FAILURE_STATES):
        log_event(
            "image-oncomplete-handler",
            "event-ignored",
            request_id=request_id,
            build_id=build_id,
            build_status=build_status,
        )
        return {"statusCode": 200, "body": "ignored"}

    if build_status in _SUCCESS_STATES:
        record["status"] = IMAGE_STATUS_SUCCEEDED
        record["error_message"] = ""
        size = _resolve_image_size(record.get("tag", ""))
        if size is not None:
            record["size"] = size
    else:
        record["status"] = IMAGE_STATUS_FAILED
        record["error_message"] = f"CodeBuild build {build_status}"
    record["updated_at"] = utc_timestamp()
    repository.put_image(record)

    log_event(
        "image-oncomplete-handler",
        "image-status-updated",
        request_id=request_id,
        build_id=build_id,
        tag=record.get("tag", ""),
        status=record["status"],
    )
    return {"statusCode": 200, "body": record["status"]}


def _resolve_image_size(tag: str) -> int | None:
    """Best-effort: look up the pushed image's byte size from ECR so the list
    can show it. Returns None on any failure (size stays 0)."""
    repo_name = os.environ.get("IMAGE_REPO_NAME", "")
    if not tag or not repo_name:
        return None
    try:
        client = boto3.client("ecr")
        response = client.describe_images(
            repositoryName=repo_name,
            imageIds=[{"imageTag": tag}],
        )
        details = response.get("imageDetails", [])
        if details:
            return int(details[0].get("imageSizeInBytes", 0) or 0)
    except Exception:  # pragma: no cover - best-effort size enrichment
        return None
    return None
