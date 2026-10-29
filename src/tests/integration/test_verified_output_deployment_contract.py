# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

import json
from copy import deepcopy

from botocore.exceptions import ClientError

from deployment.model_registry import ModelRegistryRepository, project_model_list_item
from deployment.runtime_outputs import build_manifest_reference, evaluate_deployment_eligibility
from tests.contracts import load_contract_fixture


class FakeBody:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload.encode("utf-8")


class FakeS3Client:
    def __init__(self, objects: dict[tuple[str, str], str]) -> None:
        self.objects = objects

    def get_object(self, Bucket: str, Key: str) -> dict[str, FakeBody]:
        return {"Body": FakeBody(self.objects[(Bucket, Key)])}


class FakeModelRegistryTable:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, object]] = {}

    def get_item(self, Key: dict[str, str]) -> dict[str, object]:
        item = self.items.get(Key["model_record_id"])
        return {"Item": deepcopy(item)} if item is not None else {}

    def put_item(self, Item: dict[str, object], ConditionExpression: str | None = None) -> dict[str, object]:
        existing = self.items.get(str(Item["model_record_id"]))
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
        self.items[str(Item["model_record_id"])] = deepcopy(Item)
        return {}

    def query(self, IndexName, KeyConditionExpression):  # noqa: N803
        field, value = _extract_equals_expression(KeyConditionExpression)
        return {
            "Items": [
                deepcopy(item)
                for item in self.items.values()
                if item.get(field) == value
            ]
        }

    def scan(self, FilterExpression=None, ExclusiveStartKey=None):  # noqa: N803
        items = list(self.items.values())
        if FilterExpression is not None:
            field, value = _extract_equals_expression(FilterExpression)
            items = [item for item in items if item.get(field) == value]
        return {"Items": [deepcopy(item) for item in items]}


class FakeDynamoResource:
    def __init__(self, table: FakeModelRegistryTable) -> None:
        self._table = table

    def Table(self, table_name: str) -> FakeModelRegistryTable:  # noqa: N802
        return self._table


def _extract_equals_expression(expression):
    field = expression._values[0].name
    value = expression._values[1]
    return field, value


def _repository() -> ModelRegistryRepository:
    return ModelRegistryRepository(
        table_name="ModelRegistryTable",
        dynamodb_resource=FakeDynamoResource(FakeModelRegistryTable()),
    )


def test_deployment_fixture_opens_gate_and_seeds_registry_projection() -> None:
    fixture = load_contract_fixture("deployment_contract.json")
    decision = evaluate_deployment_eligibility(
        task_record=fixture["task_record"],
        manifest=fixture["manifest"],
        s3_client=FakeS3Client(
            {
                ("bucket-name", "output/task-contract-1/runner_verification.json"): json.dumps(
                    fixture["verification_report"]
                )
            }
        ),
    )
    manifest_reference = build_manifest_reference("bucket-name", fixture["task_record"])
    repository = _repository()
    record, accepted = repository.accept_deployment_attempt(
        task_record=fixture["task_record"],
        eligibility_decision=decision,
        manifest_reference=manifest_reference,
        now="2026-01-01T00:00:00Z",
    )
    projection = project_model_list_item(record)

    assert decision["eligible"] is True
    assert manifest_reference == fixture["expected_manifest_reference"]
    assert accepted is True
    assert projection["model_record_id"] == fixture["expected_model_projection"]["model_record_id"]
    assert projection["status"] == fixture["expected_model_projection"]["status"]
    assert projection["provider_status"] == fixture["expected_model_projection"]["provider_status"]
    assert projection["artifact_reference"]["key"] == fixture["expected_model_projection"]["artifact_reference_key"]
    assert projection["provider_status_detail"]["verification_source"] == fixture["expected_model_projection"]["verification_source"]


def test_deployment_fixture_requires_independent_verification_before_registry_seed() -> None:
    fixture = load_contract_fixture("deployment_contract.json")
    manifest = deepcopy(fixture["manifest"])
    manifest["attempts"][0]["verification"]["source"] = "runtime-exception"

    decision = evaluate_deployment_eligibility(
        task_record=fixture["task_record"],
        manifest=manifest,
        s3_client=FakeS3Client(
            {
                ("bucket-name", "output/task-contract-1/runner_verification.json"): json.dumps(
                    {
                        **fixture["verification_report"],
                        "source": "runtime-exception",
                    }
                )
            }
        ),
    )

    assert decision["eligible"] is False
    assert decision["ineligible_reason"] == "deployment requires independent verification evidence"
