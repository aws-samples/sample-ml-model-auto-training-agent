# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any


@dataclass(frozen=True)
class LogReference:
    bucket: str
    key: str


class JsonlRuntimeLog:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_sync_time = 0.0

    def write(
        self,
        event_type: str,
        *,
        task_id: str,
        attempt_number: int | None = None,
        level: str = "INFO",
        **details: Any,
    ) -> None:
        entry = {
            "timestamp": time.time(),
            "level": level,
            "event_type": event_type,
            "task_id": task_id,
            "attempt_number": attempt_number,
            "details": details,
        }
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def maybe_sync(
        self,
        *,
        bucket: str,
        key: str,
        s3_client: Any,
        minimum_interval_seconds: int,
    ) -> LogReference | None:
        now = time.time()
        if now - self._last_sync_time < minimum_interval_seconds:
            return None
        self.sync(bucket=bucket, key=key, s3_client=s3_client)
        self._last_sync_time = now
        return LogReference(bucket=bucket, key=key)

    def sync(self, *, bucket: str, key: str, s3_client: Any) -> LogReference:
        s3_client.upload_file(str(self.file_path), bucket, key)
        return LogReference(bucket=bucket, key=key)
