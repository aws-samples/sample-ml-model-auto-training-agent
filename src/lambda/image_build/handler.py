# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import os
from typing import Any

from api.image_repository import ImageRepository
from api.images import handle_image_build_request
from api.validators import TaskValidationError
from shared.logging_utils import log_event
from shared.responses import error_response, json_response


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Two-step image build: issue a presigned URL for the agent zip, then start
    a CodeBuild run that builds and pushes the image under the requested tag."""
    request_id = getattr(context, "aws_request_id", None)
    try:
        repository = ImageRepository(table_name=os.environ["IMAGE_TABLE_NAME"])
        status_code, result = handle_image_build_request(
            event=event,
            source_bucket=os.environ["IMAGE_BUILD_SOURCE_BUCKET"],
            project_name=os.environ["IMAGE_BUILD_PROJECT_NAME"],
            repository=repository,
        )
    except TaskValidationError as exc:
        log_event(
            "image-build-handler",
            "validation-failed",
            level="WARN",
            request_id=request_id,
            details=exc.details,
        )
        return error_response(400, str(exc), "VALIDATION_ERROR", details=exc.details)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        log_event(
            "image-build-handler",
            "unexpected-error",
            level="ERROR",
            request_id=request_id,
            error=str(exc),
        )
        return error_response(
            500, "Unable to process image build request", "UNEXPECTED_BACKEND_ERROR"
        )

    log_event(
        "image-build-handler",
        "image-build-request-handled",
        request_id=request_id,
        status_code=status_code,
    )
    return json_response(status_code, result)
