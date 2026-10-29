# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from api.repository import TaskRepository


class FakeTable:
    def __init__(self, scan_responses):
        self.scan_responses = list(scan_responses)
        self.scan_calls = []

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        return self.scan_responses.pop(0)


class FakeResource:
    def __init__(self, table):
        self._table = table

    def Table(self, table_name):
        return self._table


def test_count_active_tasks_accumulates_paginated_scan_results():
    table = FakeTable(
        [
            {
                "Items": [{"task_id": "1"}, {"task_id": "2"}],
                "LastEvaluatedKey": {"task_id": "2"},
            },
            {
                "Items": [{"task_id": "3"}],
            },
        ]
    )
    repository = TaskRepository(
        table_name="TaskTable",
        batch_job_index="batch-job-id-index",
        dynamodb_resource=FakeResource(table),
    )

    assert repository.count_active_tasks() == 3
    assert len(table.scan_calls) == 2


def test_list_tasks_accumulates_paginated_scan_results():
    table = FakeTable(
        [
            {
                "Items": [{"task_id": "1", "created_at": "2026-01-01T00:00:00Z"}],
                "LastEvaluatedKey": {"task_id": "1"},
            },
            {
                "Items": [{"task_id": "2", "created_at": "2026-01-02T00:00:00Z"}],
            },
        ]
    )
    repository = TaskRepository(
        table_name="TaskTable",
        batch_job_index="batch-job-id-index",
        dynamodb_resource=FakeResource(table),
    )

    tasks = repository.list_tasks()
    assert [task["task_id"] for task in tasks] == ["2", "1"]
