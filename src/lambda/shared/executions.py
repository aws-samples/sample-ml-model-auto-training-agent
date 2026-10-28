# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Helpers for periodic-task execution scoping.

A periodic (``is_periodical``) task is a single DynamoDB record that manages
many runs over time. Each run is one *execution* with its own id, timestamp,
status, and S3 output/log area. To keep runs from overwriting each other, S3
keys gain an execution-id directory layer:

    output/<task_id>/<execution_id>/...
    logs/<task_id>/<execution_id>/events.jsonl

One-off tasks pass ``execution_id=None`` and keep the original flat layout:

    output/<task_id>/...
    logs/<task_id>/events.jsonl
"""
from __future__ import annotations

import re

from shared.constants import OUTPUT_PREFIX_ROOT


def make_execution_id(now_iso: str) -> str:
    """Derive a filesystem/S3-safe execution id from an ISO-8601 timestamp,
    e.g. ``2026-07-06T09:00:00+00:00`` -> ``20260706T090000Z``."""
    digits = re.sub(r"[^0-9]", "", now_iso)
    # YYYYMMDDHHMMSS (first 14 digits); pad defensively.
    stamp = (digits + "00000000000000")[:14]
    return f"{stamp}Z"


def training_output_prefix(task_id: str, execution_id: str | None = None) -> str:
    """S3 prefix (bucket-relative) for a run's training artifacts."""
    if execution_id:
        return f"{OUTPUT_PREFIX_ROOT}/{task_id}/{execution_id}/"
    return f"{OUTPUT_PREFIX_ROOT}/{task_id}/"


def logs_prefix(task_id: str, execution_id: str | None = None) -> str:
    """S3 prefix (bucket-relative) for a run's event-log directory."""
    if execution_id:
        return f"logs/{task_id}/{execution_id}/"
    return f"logs/{task_id}/"


def logs_key(task_id: str, execution_id: str | None = None) -> str:
    """S3 object key for a run's events.jsonl log file."""
    return f"{logs_prefix(task_id, execution_id)}events.jsonl"


def model_record_id(task_id: str, execution_id: str | None = None) -> str:
    """Model-registry primary key for a run. Periodic executions must not
    collide, so scope the id by execution when present."""
    if execution_id:
        return f"{task_id}::{execution_id}"
    return task_id


# Statuses that end a single execution's lifecycle.
_TERMINAL_EXECUTION_STATUSES = {"SUCCEEDED", "DEPLOYED", "FAILED", "CANCELLED"}


def start_execution(task_record: dict, execution_id: str, now: str) -> dict:
    """Append a new execution entry to a periodic task record and mark it the
    active one. Returns the appended entry."""
    entry = {
        "execution_id": execution_id,
        "status": "SUBMITTED",
        "batch_job_id": "",
        "started_at": now,
        "finished_at": "",
        "error_message": "",
    }
    executions = list(task_record.get("executions") or [])
    executions.append(entry)
    task_record["executions"] = executions
    task_record["active_execution_id"] = execution_id
    return entry


def sync_active_execution(task_record: dict, now: str) -> None:
    """Mirror the record's top-level status/batch_job_id onto its active
    execution entry (for periodic tasks). No-op for one-off tasks."""
    execution_id = task_record.get("active_execution_id")
    if not execution_id:
        return
    status = task_record.get("status", "")
    executions = task_record.get("executions") or []
    for entry in executions:
        if entry.get("execution_id") == execution_id:
            entry["status"] = status
            if task_record.get("batch_job_id"):
                entry["batch_job_id"] = task_record["batch_job_id"]
            if status in _TERMINAL_EXECUTION_STATUSES and not entry.get("finished_at"):
                entry["finished_at"] = now
            if task_record.get("error_message"):
                entry["error_message"] = task_record["error_message"]
            break
