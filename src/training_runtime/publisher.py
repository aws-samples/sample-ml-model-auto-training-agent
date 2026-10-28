# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

from pathlib import Path
from typing import Any

from training_runtime.contracts import RuntimeContract, split_s3_uri


class OutputPublisher:
    def __init__(self, s3_client: Any) -> None:
        self._s3_client = s3_client

    def upload_file(self, local_path: Path, destination_uri: str) -> dict[str, str]:
        bucket, prefix = split_s3_uri(destination_uri)
        key = f"{prefix.rstrip('/')}/{local_path.name}"
        self._s3_client.upload_file(str(local_path), bucket, key)
        return {"bucket": bucket, "key": key, "local_path": str(local_path)}

    def upload_directory(self, local_dir: Path, destination_uri: str) -> list[dict[str, str]]:
        bucket, prefix = split_s3_uri(destination_uri)
        uploaded: list[dict[str, str]] = []
        for path in sorted(local_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(local_dir).as_posix()
            key = f"{prefix.rstrip('/')}/{relative}"
            self._s3_client.upload_file(str(path), bucket, key)
            uploaded.append({"bucket": bucket, "key": key, "local_path": str(path)})
        return uploaded

    def publish_results(
        self,
        *,
        workspace_output_dir: Path,
        contract: RuntimeContract,
    ) -> list[dict[str, str]]:
        return self.upload_directory(workspace_output_dir, contract.output_uri)
