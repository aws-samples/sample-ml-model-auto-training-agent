# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

import os
from typing import Any

from shared.logging_utils import log_event
from shared.message_repository import MessageRepository


# Human-readable Chinese labels for task statuses used in message content.
STATUS_LABELS = {
    "SCHEDULED": "已排期",
    "SUBMITTED": "已提交",
    "PENDING": "等待中",
    "QUEUED": "已入队",
    "RUNNING": "训练中",
    "SUCCEEDED": "训练成功",
    "DEPLOYING": "部署中",
    "DEPLOYED": "已部署",
    "FAILED": "失败",
    "CANCELLED": "已取消",
}


def _label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def build_message_repository() -> MessageRepository | None:
    """Build the message repository if the table is configured; otherwise None
    so notification recording degrades gracefully (never blocks the caller)."""
    table_name = os.environ.get("MESSAGE_TABLE_NAME")
    if not table_name:
        return None
    return MessageRepository(table_name=table_name)


def record_status_change(
    task: dict[str, Any],
    old_status: str,
    new_status: str,
    *,
    repository: MessageRepository | None = None,
    request_id: str | None = None,
) -> None:
    """Record a notification message when a training task's status changes.

    The recipient is the task submitter. Recording is best-effort: any failure
    is logged but never propagated, so a messaging problem cannot break the task
    status update that triggered it.
    """
    if not old_status or not new_status or old_status == new_status:
        return
    repo = repository or build_message_repository()
    if repo is None:
        return

    submitted_by = (task.get("submitted_by") or "").strip()
    if not submitted_by:
        return

    task_name = task.get("task_name") or task.get("model_name") or task.get("task_id", "")
    content = (
        f"训练任务「{task_name}」状态由 {_label(old_status)} 变为 {_label(new_status)}"
    )
    try:
        repo.add_message(
            recipient=submitted_by,
            content=content,
            task_id=task.get("task_id", ""),
            task_name=task_name,
            submitted_by=submitted_by,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort, never break the caller
        log_event(
            "notifications",
            "record-status-change-failed",
            level="WARN",
            request_id=request_id,
            task_id=task.get("task_id", ""),
            error=str(exc),
        )
