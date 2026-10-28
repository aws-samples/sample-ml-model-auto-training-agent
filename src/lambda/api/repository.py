# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key

from shared.constants import ACTIVE_TASK_STATUSES
from shared.dynamo_utils import to_dynamo_safe
from shared.executions import sync_active_execution


class TaskRepository:
    def __init__(
        self,
        table_name: str,
        batch_job_index: str,
        dynamodb_resource: Any | None = None,
    ) -> None:
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)
        self._batch_job_index = batch_job_index

    def create_task(self, task_record: dict[str, Any]) -> None:
        self._table.put_item(Item=to_dynamo_safe(task_record))

    def update_task(self, task_record: dict[str, Any]) -> None:
        # For periodic tasks, mirror the top-level status/batch_job_id onto the
        # active execution entry so the execution history stays in sync no matter
        # which handler updated the record. No-op for one-off tasks.
        sync_active_execution(
            task_record, datetime.now(timezone.utc).isoformat()
        )
        self._table.put_item(Item=to_dynamo_safe(task_record))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        response = self._table.get_item(Key={"task_id": task_id})
        return response.get("Item")

    def list_tasks(self) -> list[dict[str, Any]]:
        items = self._scan_all_items()
        return sorted(items, key=lambda item: item.get("created_at", ""), reverse=True)

    def count_active_tasks(self) -> int:
        response_items = self._scan_all_items(
            FilterExpression=Attr("status").is_in(list(ACTIVE_TASK_STATUSES))
        )
        return len(response_items)

    def list_children(self, parent_task_id: str) -> list[dict[str, Any]]:
        """Return all child tasks spawned by a recurring parent."""
        return self._scan_all_items(
            FilterExpression=Attr("parent_task_id").eq(parent_task_id)
        )

    def get_task_by_batch_job_id(self, batch_job_id: str) -> dict[str, Any] | None:
        response = self._table.query(
            IndexName=self._batch_job_index,
            KeyConditionExpression=Key("batch_job_id").eq(batch_job_id),
        )
        items = response.get("Items", [])
        if not items:
            return None
        return items[0]

    def _scan_all_items(self, **scan_kwargs: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        exclusive_start_key = None
        while True:
            if exclusive_start_key is not None:
                scan_kwargs["ExclusiveStartKey"] = exclusive_start_key
            response = self._table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            exclusive_start_key = response.get("LastEvaluatedKey")
            if exclusive_start_key is None:
                break
        return items
