# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

from copy import deepcopy

from botocore.exceptions import ClientError

from deployment.model_registry import ModelRegistryRepository


class FakeModelRegistryTable:
    def __init__(self):
        self.items: dict[str, dict[str, object]] = {}

    def get_item(self, Key):
        item = self.items.get(Key["model_record_id"])
        return {"Item": deepcopy(item)} if item is not None else {}

    def put_item(self, Item, ConditionExpression=None):
        existing = self.items.get(Item["model_record_id"])
        if ConditionExpression == "attribute_not_exists(model_record_id)" and existing is not None:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}},
                "PutItem",
            )
        if (
            ConditionExpression == "attribute_not_exists(execution_claimed_at)"
            and existing is not None
            and existing.get("execution_claimed_at")
        ):
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "claimed"}},
                "PutItem",
            )
        self.items[Item["model_record_id"]] = deepcopy(Item)
        return {}

    def query(self, IndexName, KeyConditionExpression):
        field, value = _extract_equals_expression(KeyConditionExpression)
        return {
            "Items": [
                deepcopy(item)
                for item in self.items.values()
                if item.get(field) == value
            ]
        }

    def scan(self, FilterExpression=None, ExclusiveStartKey=None):
        items = list(self.items.values())
        if FilterExpression is not None:
            field, value = _extract_equals_expression(FilterExpression)
            items = [item for item in items if item.get(field) == value]
        return {"Items": [deepcopy(item) for item in items]}


class FakeDynamoResource:
    def __init__(self, table):
        self._table = table

    def Table(self, table_name):
        return self._table


def _extract_equals_expression(expression):
    field = expression._values[0].name
    value = expression._values[1]
    return field, value


def _task(task_id: str, endpoint: str, created_at: str) -> dict[str, str]:
    return {
        "task_id": task_id,
        "model_name": f"model-{task_id}",
        "target_endpoint": endpoint,
        "resolved_image_tag": "stable",
        "status": "SUCCEEDED",
        "created_at": created_at,
        "updated_at": created_at,
    }


def _decision(task_id: str) -> dict[str, object]:
    return {
        "verification_summary": {
            "achieved": True,
            "reason": "verified",
            "source": "independent",
            "threshold_direction": "lower_is_better",
        },
        "verification_source": "independent",
        "verification_artifact_reference": {
            "bucket": "bucket-name",
            "key": f"output/{task_id}/runner_verification.json",
        },
        "deployable_artifact_reference": {
            "bucket": "bucket-name",
            "key": f"output/{task_id}/model.tar.gz",
            "artifact_type": "tarball",
        },
    }


def _repository() -> ModelRegistryRepository:
    table = FakeModelRegistryTable()
    return ModelRegistryRepository(
        table_name="ModelRegistryTable",
        dynamodb_resource=FakeDynamoResource(table),
    )


def test_accept_deployment_attempt_is_idempotent():
    repository = _repository()
    task = _task("task-1", "endpoint-a", "2026-01-01T00:00:00Z")
    manifest_reference = {"bucket": "bucket-name", "key": "output/task-1/runtime_manifest.json"}

    first_record, first_accepted = repository.accept_deployment_attempt(
        task_record=task,
        eligibility_decision=_decision("task-1"),
        manifest_reference=manifest_reference,
        now="2026-01-01T00:00:00Z",
    )
    second_record, second_accepted = repository.accept_deployment_attempt(
        task_record=task,
        eligibility_decision=_decision("task-1"),
        manifest_reference=manifest_reference,
        now="2026-01-01T00:01:00Z",
    )

    assert first_accepted is True
    assert second_accepted is False
    assert second_record["model_record_id"] == first_record["model_record_id"]


def test_successful_deployments_keep_only_latest_current_per_endpoint():
    repository = _repository()
    first_record, _ = repository.accept_deployment_attempt(
        task_record=_task("task-1", "endpoint-a", "2026-01-01T00:00:00Z"),
        eligibility_decision=_decision("task-1"),
        manifest_reference={"bucket": "bucket-name", "key": "output/task-1/runtime_manifest.json"},
        now="2026-01-01T00:00:00Z",
    )
    repository.record_deployment_result(
        model_record_id=first_record["model_record_id"],
        status="DEPLOYED",
        provider_status="InService",
        provider_status_detail={"stage": "ready"},
        failure_reason="",
        endpoint_arn="arn:first",
        endpoint_config_name="config-first",
        sagemaker_model_name="model-first",
        now="2026-01-01T00:05:00Z",
    )
    second_record, _ = repository.accept_deployment_attempt(
        task_record=_task("task-2", "endpoint-a", "2026-01-02T00:00:00Z"),
        eligibility_decision=_decision("task-2"),
        manifest_reference={"bucket": "bucket-name", "key": "output/task-2/runtime_manifest.json"},
        now="2026-01-02T00:00:00Z",
    )
    repository.record_deployment_result(
        model_record_id=second_record["model_record_id"],
        status="DEPLOYED",
        provider_status="Updating",
        provider_status_detail={"stage": "update"},
        failure_reason="",
        endpoint_arn="arn:second",
        endpoint_config_name="config-second",
        sagemaker_model_name="model-second",
        now="2026-01-02T00:05:00Z",
    )

    records = {record["model_record_id"]: record for record in repository.list_model_records()}
    assert records["task-1"]["is_current_endpoint_target"] is False
    assert records["task-2"]["is_current_endpoint_target"] is True


def test_claim_deployment_execution_is_idempotent():
    repository = _repository()
    record, _ = repository.accept_deployment_attempt(
        task_record=_task("task-1", "endpoint-a", "2026-01-01T00:00:00Z"),
        eligibility_decision=_decision("task-1"),
        manifest_reference={"bucket": "bucket-name", "key": "output/task-1/runtime_manifest.json"},
        now="2026-01-01T00:00:00Z",
    )

    first, first_claimed = repository.claim_deployment_execution(
        model_record_id=record["model_record_id"],
        now="2026-01-01T00:01:00Z",
    )
    second, second_claimed = repository.claim_deployment_execution(
        model_record_id=record["model_record_id"],
        now="2026-01-01T00:02:00Z",
    )

    assert first_claimed is True
    assert second_claimed is False
    assert first["execution_claimed_at"] == "2026-01-01T00:01:00Z"
    assert second["execution_claimed_at"] == "2026-01-01T00:01:00Z"


def test_failed_deployment_preserves_existing_endpoint_context():
    repository = _repository()
    record, _ = repository.accept_deployment_attempt(
        task_record=_task("task-1", "endpoint-a", "2026-01-01T00:00:00Z"),
        eligibility_decision=_decision("task-1"),
        manifest_reference={"bucket": "bucket-name", "key": "output/task-1/runtime_manifest.json"},
        now="2026-01-01T00:00:00Z",
    )
    repository.record_deployment_result(
        model_record_id=record["model_record_id"],
        status="DEPLOYED",
        provider_status="Creating",
        provider_status_detail={"stage": "create"},
        failure_reason="",
        endpoint_arn="arn:first",
        endpoint_config_name="config-first",
        sagemaker_model_name="model-first",
        now="2026-01-01T00:03:00Z",
    )

    failed = repository.record_deployment_result(
        model_record_id=record["model_record_id"],
        status="FAILED",
        provider_status="FAILED",
        provider_status_detail={"stage": "update"},
        failure_reason="update failed",
        now="2026-01-01T00:04:00Z",
    )

    assert failed["endpoint_arn"] == "arn:first"
    assert failed["endpoint_config_name"] == "config-first"
    assert failed["sagemaker_model_name"] == "model-first"


def test_list_model_records_returns_newest_first():
    repository = _repository()
    repository.accept_deployment_attempt(
        task_record=_task("task-1", "endpoint-a", "2026-01-01T00:00:00Z"),
        eligibility_decision=_decision("task-1"),
        manifest_reference={"bucket": "bucket-name", "key": "output/task-1/runtime_manifest.json"},
        now="2026-01-01T00:00:00Z",
    )
    repository.accept_deployment_attempt(
        task_record=_task("task-2", "endpoint-b", "2026-01-02T00:00:00Z"),
        eligibility_decision=_decision("task-2"),
        manifest_reference={"bucket": "bucket-name", "key": "output/task-2/runtime_manifest.json"},
        now="2026-01-02T00:00:00Z",
    )

    records = repository.list_model_records()
    assert [record["model_record_id"] for record in records] == ["task-2", "task-1"]
