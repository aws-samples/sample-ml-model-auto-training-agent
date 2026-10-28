# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from decimal import Decimal
from typing import Any


def to_dynamo_safe(value: Any) -> Any:
    """Recursively convert a value into something the boto3 DynamoDB resource
    accepts. The DynamoDB resource serializer rejects Python ``float`` (it
    raises "Float types are not supported. Use Decimal types instead."), so any
    float reaching it — e.g. metric values copied from a runtime manifest —
    must be converted to ``Decimal`` first. Floats are routed through ``str`` so
    values like 14.61 round-trip exactly instead of becoming 14.6099999...."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: to_dynamo_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dynamo_safe(item) for item in value]
    return value
