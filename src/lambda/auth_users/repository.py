# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from typing import Any

import boto3

from shared.dynamo_utils import to_dynamo_safe


# Supported roles.
ROLE_ADMIN = "admin"
ROLE_USER = "user"


class UserRepository:
    """DynamoDB access for application users (users table, ``username`` key)."""

    def __init__(
        self,
        table_name: str,
        dynamodb_resource: Any | None = None,
    ) -> None:
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)

    def get_user(self, username: str) -> dict[str, Any] | None:
        response = self._table.get_item(Key={"username": username})
        return response.get("Item")

    def put_user(self, record: dict[str, Any]) -> None:
        self._table.put_item(Item=to_dynamo_safe(record))

    def create_user_if_absent(self, record: dict[str, Any]) -> bool:
        """Create the user only if it does not already exist. Returns True when a
        new row was written, False if one was already present."""
        from botocore.exceptions import ClientError

        try:
            self._table.put_item(
                Item=to_dynamo_safe(record),
                ConditionExpression="attribute_not_exists(username)",
            )
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
