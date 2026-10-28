# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
from typing import Any


def json_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            # Lambda proxy integration responses must carry CORS headers
            # themselves; the API Gateway CORS preflight config only handles
            # the OPTIONS request, not the actual GET/POST responses.
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Authorization,Content-Type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=str),
    }


def error_response(
    status_code: int,
    message: str,
    error_code: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = {
        "message": message,
        "error_code": error_code,
    }
    if details:
        body["details"] = details
    return json_response(status_code, body)


def task_submission_response(task_id: str, status: str, message: str) -> dict[str, Any]:
    return json_response(
        201,
        {
            "task_id": task_id,
            "status": status,
            "message": message,
        },
    )


def tasks_response(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return json_response(200, {"tasks": tasks})


def models_response(models: list[dict[str, Any]]) -> dict[str, Any]:
    return json_response(200, {"models": models})


def task_log_response(
    task_id: str,
    status: str,
    logs: list[dict[str, Any]],
) -> dict[str, Any]:
    return json_response(
        200,
        {
            "task_id": task_id,
            "status": status,
            "logs": logs,
        },
    )
