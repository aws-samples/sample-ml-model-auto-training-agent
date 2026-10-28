# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.exceptions import ClientError


class ScheduleError(RuntimeError):
    """Raised when creating or deleting an EventBridge schedule fails."""


# EventBridge Scheduler rate() units. We map the frontend's day/hour/minute
# choice to the plural unit rate() expects.
_RATE_UNITS = {
    "minutes": "minute",
    "hours": "hour",
    "days": "day",
}


def build_rate_expression(interval_value: int, interval_unit: str) -> str:
    """Return an EventBridge Scheduler rate() expression, e.g. rate(2 hours).
    rate() uses the singular unit only when the value is 1."""
    unit = _RATE_UNITS.get(interval_unit)
    if unit is None:
        raise ScheduleError(f"unsupported interval unit: {interval_unit}")
    if interval_value < 1:
        raise ScheduleError("interval value must be >= 1")
    plural = unit if interval_value == 1 else f"{unit}s"
    return f"rate({interval_value} {plural})"


class SchedulerAdapter:
    """Manages EventBridge Scheduler schedules that periodically trigger the API
    Lambda to launch a recurring training run."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client or boto3.client("scheduler")

    def create_schedule(
        self,
        *,
        name: str,
        group_name: str,
        rate_expression: str,
        start_time: str | None,
        target_arn: str,
        role_arn: str,
        input_payload: dict[str, Any],
    ) -> str:
        """Create a recurring schedule. ``start_time`` is an ISO-8601 string
        (UTC, no offset, seconds precision e.g. 2026-07-02T09:00:00); the
        schedule fires first at/after that time and then every rate() interval.
        Returns the schedule ARN."""
        kwargs: dict[str, Any] = {
            "Name": name,
            "GroupName": group_name,
            "ScheduleExpression": rate_expression,
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "Target": {
                "Arn": target_arn,
                "RoleArn": role_arn,
                "Input": json.dumps(input_payload),
            },
            "State": "ENABLED",
        }
        if start_time:
            # Scheduler wants a naive UTC datetime string; the value has already
            # been validated/normalized upstream.
            kwargs["ScheduleExpressionTimezone"] = "UTC"
            kwargs["StartDate"] = _to_epoch_naive(start_time)
        try:
            response = self._client.create_schedule(**kwargs)
        except ClientError as exc:
            raise ScheduleError(f"could not create schedule {name}: {exc}") from exc
        return response.get("ScheduleArn", "")

    def delete_schedule(self, *, name: str, group_name: str) -> None:
        """Delete a schedule. Missing schedules are treated as already gone."""
        try:
            self._client.delete_schedule(Name=name, GroupName=group_name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("ResourceNotFoundException", "NotFoundException"):
                return
            raise ScheduleError(f"could not delete schedule {name}: {exc}") from exc


def _to_epoch_naive(start_time: str) -> Any:
    """Convert an ISO-8601 string to a datetime for boto3. boto3 accepts a
    datetime for StartDate; parse leniently and treat as UTC."""
    from datetime import datetime, timezone

    text = start_time.strip().replace("Z", "")
    # Accept 'YYYY-MM-DDTHH:MM' or with seconds.
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ScheduleError(f"invalid schedule start time: {start_time}")
