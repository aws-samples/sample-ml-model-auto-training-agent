# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from shared.dynamo_utils import to_dynamo_safe


# Image build lifecycle statuses (kept local to the image domain).
IMAGE_STATUS_BUILDING = "BUILDING"
IMAGE_STATUS_SUCCEEDED = "SUCCEEDED"
IMAGE_STATUS_FAILED = "FAILED"


class ImageRepository:
    """DynamoDB access for training-agent images (image table, ``tag`` key).

    Each row tracks one build: its tag, byte size, status, and timestamps. A
    ``build-id-index`` GSI lets the CodeBuild completion handler find the row by
    the CodeBuild run id."""

    def __init__(
        self,
        table_name: str,
        build_id_index: str = "build-id-index",
        dynamodb_resource: Any | None = None,
    ) -> None:
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)
        self._build_id_index = build_id_index

    def put_image(self, record: dict[str, Any]) -> None:
        self._table.put_item(Item=to_dynamo_safe(record))

    def get_image(self, tag: str) -> dict[str, Any] | None:
        response = self._table.get_item(Key={"tag": tag})
        return response.get("Item")

    def list_images(self) -> list[dict[str, Any]]:
        items = self._scan_all_items()
        return sorted(items, key=lambda item: item.get("created_at", ""), reverse=True)

    def get_image_by_build_id(self, build_id: str) -> dict[str, Any] | None:
        response = self._table.query(
            IndexName=self._build_id_index,
            KeyConditionExpression=Key("build_id").eq(build_id),
        )
        items = response.get("Items", [])
        return items[0] if items else None

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
