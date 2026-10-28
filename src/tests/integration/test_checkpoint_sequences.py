from __future__ import annotations

import json
import os
from copy import deepcopy

from hypothesis import given, strategies as st

from api.batch_adapter import BatchAdapter
from deployment.model_registry import ModelRegistryRepository, project_model_list_item
from deployment.runtime_outputs import build_manifest_reference, evaluate_deployment_eligibility
from tests.contracts import load_contract_fixture
from tests.integration.test_verified_output_deployment_contract import (
    FakeDynamoResource,
    FakeModelRegistryTable,
    FakeS3Client,
)
from tests.unit.strategies import identifier_strategy
from training_runtime.contracts import load_runtime_contract


class FakeBatchClient:
    def __init__(self) -> None:
        self.submit_calls: list[dict[str, object]] = []
        self.register_calls: list[dict[str, object]] = []

    def submit_job(self, **kwargs: object) -> dict[str, str]:
        self.submit_calls.append(kwargs)
        return {"jobId": "job-123", "jobName": str(kwargs["jobName"])}

    def describe_job_definitions(self, **kwargs: object) -> dict[str, object]:
        if "jobDefinitions" in kwargs:
            return {
                "jobDefinitions": [
                    {
                        "jobDefinitionArn": "arn:aws:batch:base",
                        "jobDefinitionName": "training-base",
                        "type": "container",
                        "containerProperties": {
                            "image": "123456789012.dkr.ecr.cn/test:stable",
                        },
                        "platformCapabilities": ["EC2"],
                    }
                ]
            }
        return {"jobDefinitions": []}

    def register_job_definition(self, **kwargs: object) -> dict[str, str]:
        self.register_calls.append(kwargs)
        return {"jobDefinitionArn": "arn:aws:batch:tagged"}


def _repository() -> ModelRegistryRepository:
    return ModelRegistryRepository(
        table_name="ModelRegistryTable",
        dynamodb_resource=FakeDynamoResource(FakeModelRegistryTable()),
    )


def _eligible_decision(fixture: dict[str, object], task_id: str) -> dict[str, object]:
    manifest = deepcopy(fixture["manifest"])
    manifest["task_id"] = task_id
    manifest["published_artifacts"][0]["key"] = f"output/{task_id}/model.tar.gz"
    manifest["published_artifacts"][1]["key"] = f"output/{task_id}/runner_verification.json"
    manifest["output_prefix"] = f"output/{task_id}/"

    return evaluate_deployment_eligibility(
        task_record={
            **fixture["task_record"],
            "task_id": task_id,
        },
        manifest=manifest,
        s3_client=FakeS3Client(
            {
                ("bucket-name", f"output/{task_id}/runner_verification.json"): json.dumps(
                    fixture["verification_report"]
                )
            }
        ),
    )


@given(task_id=identifier_strategy("task"))
def test_batch_runtime_environment_remains_task_scoped(task_id: str) -> None:
    fixture = load_contract_fixture("batch_runtime_contract.json")
    client = FakeBatchClient()
    adapter = BatchAdapter(batch_client=client, sleeper=lambda _: None)
    previous_region = os.environ.get("AWS_REGION")
    os.environ["AWS_REGION"] = fixture["submit_context"]["aws_region"]

    try:
        adapter.submit_task(
            task_record={
                **fixture["task_record"],
                "task_id": task_id,
            },
            bucket_name=fixture["submit_context"]["bucket_name"],
            job_queue_arn=fixture["submit_context"]["job_queue_arn"],
            job_definition_arn=fixture["submit_context"]["job_definition_arn"],
            image_repo_uri=fixture["submit_context"]["image_repo_uri"],
        )
    finally:
        if previous_region is None:
            os.environ.pop("AWS_REGION", None)
        else:
            os.environ["AWS_REGION"] = previous_region

    environment = {
        item["name"]: item["value"]
        for item in client.submit_calls[0]["containerOverrides"]["environment"]
    }
    contract = load_runtime_contract(environment)

    assert environment["LOGS_PREFIX"] == f"logs/{task_id}/"
    assert environment["OUTPUT_PREFIX"] == f"output/{task_id}/"
    assert contract.logs_uri == f"s3://bucket-name/logs/{task_id}/"
    assert contract.output_uri == f"s3://bucket-name/output/{task_id}/"


def test_deployment_sequence_stays_append_only_and_newest_first() -> None:
    fixture = load_contract_fixture("deployment_contract.json")
    repository = _repository()
    first_task = {
        **fixture["task_record"],
        "task_id": "task-contract-1",
        "status": "SUCCEEDED",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    second_task = {
        **fixture["task_record"],
        "task_id": "task-contract-2",
        "model_name": "demo-model-02",
        "resolved_image_tag": "stable",
        "status": "SUCCEEDED",
        "created_at": "2026-01-02T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
    }

    first_decision = _eligible_decision(fixture, "task-contract-1")
    second_decision = _eligible_decision(fixture, "task-contract-2")
    first_record, first_accepted = repository.accept_deployment_attempt(
        task_record=first_task,
        eligibility_decision=first_decision,
        manifest_reference=build_manifest_reference("bucket-name", first_task),
        now="2026-01-01T00:00:00Z",
    )
    duplicate_record, duplicate_accepted = repository.accept_deployment_attempt(
        task_record=first_task,
        eligibility_decision=first_decision,
        manifest_reference=build_manifest_reference("bucket-name", first_task),
        now="2026-01-01T00:01:00Z",
    )
    second_record, second_accepted = repository.accept_deployment_attempt(
        task_record=second_task,
        eligibility_decision=second_decision,
        manifest_reference=build_manifest_reference("bucket-name", second_task),
        now="2026-01-02T00:00:00Z",
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
    repository.record_deployment_result(
        model_record_id=second_record["model_record_id"],
        status="DEPLOYED",
        provider_status="InService",
        provider_status_detail={"stage": "ready"},
        failure_reason="",
        endpoint_arn="arn:second",
        endpoint_config_name="config-second",
        sagemaker_model_name="model-second",
        now="2026-01-02T00:05:00Z",
    )
    projections = [project_model_list_item(record) for record in repository.list_model_records()]

    assert first_accepted is True
    assert duplicate_accepted is False
    assert duplicate_record["model_record_id"] == first_record["model_record_id"]
    assert second_accepted is True
    assert [record["model_record_id"] for record in projections] == [
        second_record["model_record_id"],
        first_record["model_record_id"],
    ]
    assert projections[0]["is_current_endpoint_target"] is True
    assert projections[1]["is_current_endpoint_target"] is False
