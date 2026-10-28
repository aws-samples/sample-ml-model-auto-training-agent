# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from typing import Any

import boto3

from shared.dynamo_utils import to_dynamo_safe


class InferTaskRepository:
    """DynamoDB access for inference tasks (infer-tasks table, task_id key)."""

    def __init__(
        self,
        table_name: str,
        dynamodb_resource: Any | None = None,
    ) -> None:
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)

    def create_task(self, task_record: dict[str, Any]) -> None:
        self._table.put_item(Item=to_dynamo_safe(task_record))

    def update_task(self, task_record: dict[str, Any]) -> None:
        self._table.put_item(Item=to_dynamo_safe(task_record))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        response = self._table.get_item(Key={"task_id": task_id})
        return response.get("Item")

    def list_tasks(self) -> list[dict[str, Any]]:
        items = self._scan_all_items()
        return sorted(items, key=lambda item: item.get("created_at", ""), reverse=True)

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
