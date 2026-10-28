# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

from copy import deepcopy
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from shared.constants import (
    MODEL_PROVIDER_STATUS_ACCEPTED,
    MODEL_PROVIDER_STATUS_DISPATCH_FAILED,
    MODEL_REGISTRY_TARGET_ENDPOINT_INDEX,
    TASK_STATUS_DEPLOYED,
    TASK_STATUS_DEPLOYING,
    TASK_STATUS_FAILED,
)
from shared.dynamo_utils import to_dynamo_safe


MODEL_LIST_FIELDS = (
    "model_record_id",
    "task_id",
    "model_name",
    "target_endpoint",
    "resolved_image_tag",
    "status",
    "provider_status",
    "provider_status_detail",
    "failure_reason",
    "artifact_reference",
    "endpoint_arn",
    "endpoint_config_name",
    "sagemaker_model_name",
    "is_current_endpoint_target",
    "created_at",
    "updated_at",
)


def build_pending_model_record(
    *,
    task_record: dict[str, Any],
    eligibility_decision: dict[str, Any],
    manifest_reference: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    from shared.executions import model_record_id

    return {
        # Periodic executions must not overwrite prior runs' model records, so
        # scope the id by the active execution when present.
        "model_record_id": model_record_id(
            task_record["task_id"], task_record.get("active_execution_id")
        ),
        "task_id": task_record["task_id"],
        "model_name": task_record["model_name"],
        "target_endpoint": task_record["target_endpoint"],
        "resolved_image_tag": task_record["resolved_image_tag"],
        "status": TASK_STATUS_DEPLOYING,
        "provider_status": MODEL_PROVIDER_STATUS_ACCEPTED,
        "provider_status_detail": {
            "dispatch_state": "accepted",
            "verification_artifact_reference": eligibility_decision.get(
                "verification_artifact_reference"
            ),
            "verification_source": eligibility_decision.get("verification_source"),
        },
        "failure_reason": "",
        "artifact_reference": eligibility_decision.get("deployable_artifact_reference"),
        "manifest_reference": manifest_reference,
        "verification_summary": eligibility_decision.get("verification_summary"),
        "endpoint_arn": "",
        "endpoint_config_name": "",
        "sagemaker_model_name": "",
        "is_current_endpoint_target": False,
        "created_at": now,
        "updated_at": now,
    }


def sort_model_records_newest_first(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # ISO 8601 UTC timestamps sort correctly in descending lexical order.
    return sorted(
        records,
        key=lambda record: (
            record.get("created_at", ""),
            record.get("updated_at", ""),
            record.get("model_record_id", ""),
        ),
        reverse=True,
    )


def project_model_list_item(record: dict[str, Any]) -> dict[str, Any]:
    return {field: record.get(field) for field in MODEL_LIST_FIELDS}


class ModelRegistryRepository:
    def __init__(
        self,
        table_name: str,
        target_endpoint_index: str = MODEL_REGISTRY_TARGET_ENDPOINT_INDEX,
        dynamodb_resource: Any | None = None,
    ) -> None:
        resource = dynamodb_resource or boto3.resource("dynamodb")
        self._table = resource.Table(table_name)
        self._target_endpoint_index = target_endpoint_index

    def get_model_record(self, model_record_id: str) -> dict[str, Any] | None:
        response = self._table.get_item(Key={"model_record_id": model_record_id})
        return response.get("Item")

    def list_model_records(self) -> list[dict[str, Any]]:
        return sort_model_records_newest_first(self._scan_all_items())

    def accept_deployment_attempt(
        self,
        *,
        task_record: dict[str, Any],
        eligibility_decision: dict[str, Any],
        manifest_reference: dict[str, Any],
        now: str,
    ) -> tuple[dict[str, Any], bool]:
        record = build_pending_model_record(
            task_record=task_record,
            eligibility_decision=eligibility_decision,
            manifest_reference=manifest_reference,
            now=now,
        )
        try:
            self._table.put_item(
                Item=to_dynamo_safe(record),
                ConditionExpression="attribute_not_exists(model_record_id)",
            )
            return record, True
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code != "ConditionalCheckFailedException":
                raise
        existing = self.get_model_record(record["model_record_id"])
        if existing is None:  # pragma: no cover - defensive race fallback
            raise RuntimeError("model registry acceptance failed without an existing record")
        return existing, False

    def claim_deployment_execution(
        self,
        *,
        model_record_id: str,
        now: str,
    ) -> tuple[dict[str, Any], bool]:
        existing = self.get_model_record(model_record_id)
        if existing is None:
            raise RuntimeError(f"model record {model_record_id} not found")
        if existing.get("status") != TASK_STATUS_DEPLOYING:
            return existing, False
        if existing.get("execution_claimed_at"):
            return existing, False

        updated = deepcopy(existing)
        updated["execution_claimed_at"] = now
        updated["updated_at"] = now
        detail = deepcopy(updated.get("provider_status_detail") or {})
        detail["execution_claimed_at"] = now
        updated["provider_status_detail"] = detail
        try:
            self._table.put_item(
                Item=to_dynamo_safe(updated),
                ConditionExpression="attribute_not_exists(execution_claimed_at)",
            )
            return updated, True
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code != "ConditionalCheckFailedException":
                raise
        existing = self.get_model_record(model_record_id)
        if existing is None:  # pragma: no cover - defensive race fallback
            raise RuntimeError("deployment claim failed without an existing record")
        return existing, False

    def record_dispatch_failure(
        self,
        *,
        model_record_id: str,
        failure_reason: str,
        now: str,
    ) -> dict[str, Any]:
        return self.record_deployment_result(
            model_record_id=model_record_id,
            status=TASK_STATUS_FAILED,
            provider_status=MODEL_PROVIDER_STATUS_DISPATCH_FAILED,
            provider_status_detail={"dispatch_state": "failed"},
            failure_reason=failure_reason,
            now=now,
        )

    def record_deployment_result(
        self,
        *,
        model_record_id: str,
        status: str,
        provider_status: str,
        provider_status_detail: dict[str, Any] | None,
        failure_reason: str,
        endpoint_arn: str | None = None,
        endpoint_config_name: str | None = None,
        sagemaker_model_name: str | None = None,
        now: str,
    ) -> dict[str, Any]:
        existing = self.get_model_record(model_record_id)
        if existing is None:
            raise RuntimeError(f"model record {model_record_id} not found")

        updated = deepcopy(existing)
        updated["status"] = status
        updated["provider_status"] = provider_status
        updated["provider_status_detail"] = provider_status_detail or {}
        updated["failure_reason"] = failure_reason
        if endpoint_arn is not None:
            updated["endpoint_arn"] = endpoint_arn
        if endpoint_config_name is not None:
            updated["endpoint_config_name"] = endpoint_config_name
        if sagemaker_model_name is not None:
            updated["sagemaker_model_name"] = sagemaker_model_name
        updated["updated_at"] = now

        if status == TASK_STATUS_DEPLOYED:
            self._clear_current_flag_for_endpoint(
                target_endpoint=updated["target_endpoint"],
                except_model_record_id=model_record_id,
            )
            updated["is_current_endpoint_target"] = True
        else:
            updated["is_current_endpoint_target"] = False

        self._table.put_item(Item=to_dynamo_safe(updated))
        return updated

    def _clear_current_flag_for_endpoint(
        self,
        *,
        target_endpoint: str,
        except_model_record_id: str,
    ) -> None:
        for record in self._list_records_for_target_endpoint(target_endpoint):
            if record["model_record_id"] == except_model_record_id:
                continue
            if not record.get("is_current_endpoint_target"):
                continue
            updated = deepcopy(record)
            updated["is_current_endpoint_target"] = False
            self._table.put_item(Item=to_dynamo_safe(updated))

    def _list_records_for_target_endpoint(self, target_endpoint: str) -> list[dict[str, Any]]:
        try:
            response = self._table.query(
                IndexName=self._target_endpoint_index,
                KeyConditionExpression=Key("target_endpoint").eq(target_endpoint),
            )
            return response.get("Items", [])
        except Exception:  # pragma: no cover - fallback for local doubles
            return self._scan_all_items(
                FilterExpression=Attr("target_endpoint").eq(target_endpoint)
            )

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
