# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any, Callable

from training_runtime.contracts import RuntimeContract, split_s3_uri


@dataclass(frozen=True)
class WorkspaceContext:
    root_dir: Path
    input_dir: Path
    skills_dir: Path
    logs_dir: Path
    attempts_dir: Path
    output_dir: Path
    verify_dir: Path

    def attempt_dir(self, attempt_number: int) -> Path:
        path = self.attempts_dir / f"attempt-{attempt_number}"
        path.mkdir(parents=True, exist_ok=True)
        return path


def prepare_workspace(root_dir: str, contract: RuntimeContract) -> WorkspaceContext:
    task_root = Path(root_dir).expanduser().resolve() / contract.task_id
    context = WorkspaceContext(
        root_dir=task_root,
        input_dir=task_root / "input",
        skills_dir=task_root / "skills",
        logs_dir=task_root / "logs",
        attempts_dir=task_root / "attempts",
        output_dir=task_root / "output",
        verify_dir=task_root / "verify",
    )
    for path in (
        context.root_dir,
        context.input_dir,
        context.skills_dir,
        context.logs_dir,
        context.attempts_dir,
        context.output_dir,
        context.verify_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return context


def clear_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def local_data_target(contract: RuntimeContract, workspace: WorkspaceContext) -> Path:
    _, key = split_s3_uri(contract.data_uri)
    return workspace.input_dir / Path(key).name


def materialize_s3_reference(
    *,
    s3_client: Any,
    source_uri: str,
    destination_dir: Path,
) -> list[Path]:
    bucket, key = split_s3_uri(source_uri)
    destination_dir.mkdir(parents=True, exist_ok=True)

    if not key.endswith("/"):
        target = destination_dir / Path(key).name
        s3_client.download_file(bucket, key, str(target))
        return [target]

    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=key)
    files: list[Path] = []
    for item in response.get("Contents", []):
        object_key = item.get("Key") or ""
        if not object_key or object_key.endswith("/"):
            continue
        relative = Path(object_key[len(key) :])
        target = destination_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        s3_client.download_file(bucket, object_key, str(target))
        files.append(target)
    return files


def copy_directory_contents(
    source_dir: Path,
    destination_dir: Path,
    *,
    include: Callable[[Path], bool] | None = None,
) -> list[Path]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for path in source_dir.rglob("*"):
        if not path.is_file():
            continue
        if include is not None and not include(path):
            continue
        relative = path.relative_to(source_dir)
        target = destination_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append(target)
    return copied
