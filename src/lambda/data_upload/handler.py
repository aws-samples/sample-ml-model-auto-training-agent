# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import os
from typing import Any

from api.data_files import create_upload_url, delete_data_file
from api.validators import TaskValidationError
from shared.logging_utils import log_event
from shared.responses import error_response, json_response


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Write operations on the data area:
    - POST   -> presigned PUT URL for a browser upload
    - DELETE -> delete a single object from a user-writable input area
    """
    request_id = getattr(context, "aws_request_id", None)
    method = (event.get("httpMethod") or "POST").upper()

    try:
        if method == "DELETE":
            result = delete_data_file(
                bucket_name=os.environ["BUCKET_NAME"],
                event=event,
            )
            log_event(
                "data-upload-handler",
                "data-file-deleted",
                request_id=request_id,
                key=result["key"],
            )
            return json_response(200, result)

        result = create_upload_url(
            bucket_name=os.environ["BUCKET_NAME"],
            event=event,
        )
    except TaskValidationError as exc:
        log_event(
            "data-upload-handler",
            "validation-failed",
            level="WARN",
            request_id=request_id,
            details=exc.details,
        )
        return error_response(400, str(exc), "VALIDATION_ERROR", details=exc.details)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        log_event(
            "data-upload-handler",
            "unexpected-error",
            level="ERROR",
            request_id=request_id,
            error=str(exc),
        )
        return error_response(
            500, "Unable to process data write request", "UNEXPECTED_BACKEND_ERROR"
        )

    log_event(
        "data-upload-handler",
        "upload-url-created",
        request_id=request_id,
        key=result["key"],
    )
    return json_response(201, result)
