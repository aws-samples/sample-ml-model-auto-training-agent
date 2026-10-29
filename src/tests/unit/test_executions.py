# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from shared.executions import (
    logs_key,
    make_execution_id,
    model_record_id,
    start_execution,
    sync_active_execution,
    training_output_prefix,
)


def test_make_execution_id_from_iso():
    assert make_execution_id("2026-07-06T09:00:00+00:00") == "20260706090000Z"


def test_prefixes_flat_for_oneoff_and_scoped_for_periodic():
    assert training_output_prefix("t1") == "output/t1/"
    assert training_output_prefix("t1", "e1") == "output/t1/e1/"
    assert logs_key("t1") == "logs/t1/events.jsonl"
    assert logs_key("t1", "e1") == "logs/t1/e1/events.jsonl"
    assert model_record_id("t1") == "t1"
    assert model_record_id("t1", "e1") == "t1::e1"


def test_start_and_sync_execution():
    task = {"task_id": "t1", "executions": []}
    start_execution(task, "e1", "2026-07-06T09:00:00+00:00")
    assert task["active_execution_id"] == "e1"
    assert task["executions"][0]["status"] == "SUBMITTED"

    # a status change mirrors onto the active execution entry
    task["status"] = "RUNNING"
    task["batch_job_id"] = "job-1"
    sync_active_execution(task, "2026-07-06T09:05:00+00:00")
    assert task["executions"][0]["status"] == "RUNNING"
    assert task["executions"][0]["batch_job_id"] == "job-1"

    # terminal status stamps finished_at
    task["status"] = "SUCCEEDED"
    sync_active_execution(task, "2026-07-06T09:30:00+00:00")
    assert task["executions"][0]["finished_at"] == "2026-07-06T09:30:00+00:00"


def test_sync_is_noop_without_active_execution():
    task = {"task_id": "t1", "status": "RUNNING"}
    sync_active_execution(task, "2026-07-06T09:00:00+00:00")  # must not raise
    assert "executions" not in task
