# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
from typing import Any

import boto3

from shared.executions import logs_key


class LogReader:
    def __init__(self, s3_client: Any | None = None) -> None:
        self._s3_client = s3_client or boto3.client("s3")

    def read_task_logs(
        self,
        bucket_name: str,
        task_id: str,
        execution_id: str | None = None,
    ) -> list[dict[str, Any]]:
        # Periodic executions store logs under logs/<task>/<execution>/; one-off
        # tasks keep the flat logs/<task>/ layout.
        key = logs_key(task_id, execution_id)
        try:
            response = self._s3_client.get_object(Bucket=bucket_name, Key=key)
        except Exception as exc:  # pragma: no cover - boto3 service path
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if error_code in {"NoSuchKey", "404", "NotFound"}:
                return []
            if type(exc).__name__ == "NoSuchKey":
                return []
            raise

        body = response["Body"].read().decode("utf-8")
        logs: list[dict[str, Any]] = []
        for line in body.splitlines():
            if not line.strip():
                continue
            try:
                logs.append(json.loads(line))
            except json.JSONDecodeError:
                logs.append({"message": line})
        return logs
