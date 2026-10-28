# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

from shared.dynamo_utils import to_dynamo_safe
from shared.time_utils import utc_timestamp


# Messages self-expire after 7 days via DynamoDB TTL on the ``expires_at``
# attribute (a Unix epoch second count, as TTL requires).
MESSAGE_TTL_DAYS = 7


def _expires_at_epoch(days: int = MESSAGE_TTL_DAYS) -> int:
    return int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())


class MessageRepository:
    """DynamoDB access for user notification messages.

    Each row is one message addressed to a recipient (the task submitter). A
    ``recipient-index`` GSI (partition ``recipient``, sort ``created_at``) lets
    the API list a user's recent messages newest-first. Rows carry an
    ``expires_at`` epoch used by the table's TTL to purge messages after 7 days.
    """

    def __init__(
        self,
        table_name: str,
        recipient_index: str = "recipient-index",
        dynamodb_resource: Any | None = None,
    ) -> None:
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)
        self._recipient_index = recipient_index

    def add_message(
        self,
        *,
        recipient: str,
        content: str,
        task_id: str = "",
        task_name: str = "",
        submitted_by: str = "",
        category: str = "task_status",
    ) -> dict[str, Any]:
        """Persist a message for ``recipient`` and return the stored record."""
        now = utc_timestamp()
        record = {
            "message_id": str(uuid.uuid4()),
            "recipient": recipient,
            "content": content,
            "task_id": task_id,
            "task_name": task_name,
            # The originator/submitter of the underlying task (may equal
            # recipient). Kept explicit per the message schema.
            "submitted_by": submitted_by or recipient,
            "category": category,
            # New messages start unread; the UI marks them read on demand.
            "read": False,
            "created_at": now,
            "expires_at": _expires_at_epoch(),
        }
        self._table.put_item(Item=to_dynamo_safe(record))
        return record

    def list_messages(
        self, recipient: str, limit: int = 50, unread_only: bool = False
    ) -> list[dict[str, Any]]:
        """Return a recipient's messages, newest first. When ``unread_only`` is
        set, messages whose ``read`` flag is true are excluded."""
        response = self._table.query(
            IndexName=self._recipient_index,
            KeyConditionExpression=Key("recipient").eq(recipient),
            ScanIndexForward=False,  # newest first (created_at descending)
            Limit=limit,
        )
        items = response.get("Items", [])
        if unread_only:
            items = [item for item in items if not item.get("read", False)]
        return items

    def mark_read(self, message_id: str) -> None:
        """Mark a single message as read (idempotent)."""
        self._table.update_item(
            Key={"message_id": message_id},
            UpdateExpression="SET #r = :true",
            ExpressionAttributeNames={"#r": "read"},
            ExpressionAttributeValues={":true": True},
        )
