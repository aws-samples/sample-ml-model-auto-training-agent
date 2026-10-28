# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import os
from typing import Any

from api.image_repository import ImageRepository
from api.images import list_images
from shared.logging_utils import log_event
from shared.responses import error_response, json_response


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """List training-agent images from the image DynamoDB table."""
    request_id = getattr(context, "aws_request_id", None)
    try:
        repository = ImageRepository(table_name=os.environ["IMAGE_TABLE_NAME"])
        result = list_images(repository=repository)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        log_event(
            "image-list-handler",
            "unexpected-error",
            level="ERROR",
            request_id=request_id,
            error=str(exc),
        )
        return error_response(500, "Unable to list images", "UNEXPECTED_BACKEND_ERROR")

    log_event(
        "image-list-handler",
        "images-listed",
        request_id=request_id,
        image_count=len(result["images"]),
    )
    return json_response(200, result)
