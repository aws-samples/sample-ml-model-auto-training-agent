# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import os
from typing import Any

from api.data_files import create_download_url, list_data_files
from api.validators import TaskValidationError
from shared.logging_utils import log_event
from shared.responses import error_response, json_response


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """GET /data-files: list files/folders under a prefix, or (action=download)
    return a presigned GET URL for a single object."""
    request_id = getattr(context, "aws_request_id", None)
    query = event.get("queryStringParameters") or {}
    prefix = query.get("prefix")
    action = (query.get("action") or "list").strip()

    try:
        if action == "download":
            download = create_download_url(
                bucket_name=os.environ["BUCKET_NAME"],
                key=query.get("key"),
            )
            log_event(
                "data-list-handler",
                "download-url-created",
                request_id=request_id,
                key=download["key"],
            )
            return json_response(200, download)

        result = list_data_files(
            bucket_name=os.environ["BUCKET_NAME"],
            prefix=prefix,
        )
    except TaskValidationError as exc:
        log_event(
            "data-list-handler",
            "validation-failed",
            level="WARN",
            request_id=request_id,
            details=exc.details,
        )
        return error_response(400, str(exc), "VALIDATION_ERROR", details=exc.details)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        log_event(
            "data-list-handler",
            "unexpected-error",
            level="ERROR",
            request_id=request_id,
            error=str(exc),
        )
        return error_response(500, "Unable to list data files", "UNEXPECTED_BACKEND_ERROR")

    log_event(
        "data-list-handler",
        "data-files-listed",
        request_id=request_id,
        prefix=result["prefix"],
        file_count=len(result["files"]),
        folder_count=len(result["folders"]),
    )
    return json_response(200, result)
