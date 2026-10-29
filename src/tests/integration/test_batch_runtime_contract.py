# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

from api.batch_adapter import BatchAdapter
from tests.contracts import load_contract_fixture
from training_runtime.contracts import load_runtime_contract


class FakeBatchClient:
    def __init__(self) -> None:
        self.submit_calls: list[dict[str, object]] = []
        self.register_calls: list[dict[str, object]] = []
        self.describe_by_arn_calls: list[dict[str, object]] = []
        self.describe_by_name_calls: list[dict[str, object]] = []

    def submit_job(self, **kwargs: object) -> dict[str, str]:
        self.submit_calls.append(kwargs)
        return {"jobId": "job-123", "jobName": str(kwargs["jobName"])}

    def describe_job_definitions(self, **kwargs: object) -> dict[str, object]:
        if "jobDefinitions" in kwargs:
            self.describe_by_arn_calls.append(kwargs)
            return {
                "jobDefinitions": [
                    {
                        "jobDefinitionArn": "arn:aws:batch:base",
                        "jobDefinitionName": "training-base",
                        "type": "container",
                        "containerProperties": {
                            "image": "123456789012.dkr.ecr.cn/test:stable",
                            "jobRoleArn": "arn:aws:iam::123456789012:role/JobRole",
                            "resourceRequirements": [{"type": "GPU", "value": "1"}],
                        },
                        "platformCapabilities": ["EC2"],
                    }
                ]
            }
        self.describe_by_name_calls.append(kwargs)
        return {"jobDefinitions": []}

    def register_job_definition(self, **kwargs: object) -> dict[str, str]:
        self.register_calls.append(kwargs)
        return {"jobDefinitionArn": "arn:aws:batch:tagged"}


def test_batch_adapter_emits_fixture_aligned_runtime_contract(monkeypatch) -> None:
    fixture = load_contract_fixture("batch_runtime_contract.json")
    client = FakeBatchClient()
    adapter = BatchAdapter(batch_client=client, sleeper=lambda _: None)
    monkeypatch.setenv("AWS_REGION", fixture["submit_context"]["aws_region"])

    result = adapter.submit_task(
        task_record=fixture["task_record"],
        bucket_name=fixture["submit_context"]["bucket_name"],
        job_queue_arn=fixture["submit_context"]["job_queue_arn"],
        job_definition_arn=fixture["submit_context"]["job_definition_arn"],
        image_repo_uri=fixture["submit_context"]["image_repo_uri"],
    )

    environment = {
        item["name"]: item["value"]
        for item in client.submit_calls[0]["containerOverrides"]["environment"]
    }
    contract = load_runtime_contract(environment)

    assert environment == fixture["expected_runtime_environment"]
    assert contract.task_id == fixture["task_record"]["task_id"]
    assert contract.logs_uri == "s3://bucket-name/logs/task-contract-1/"
    assert contract.output_uri == "s3://bucket-name/output/task-contract-1/"
    assert client.register_calls[0]["containerProperties"]["image"] == fixture["expected_job_definition"]["resolved_image"]
    assert result["job_definition_arn"] == fixture["expected_job_definition"]["resolved_arn"]


def test_batch_runtime_contract_keeps_image_tag_traceability(monkeypatch) -> None:
    fixture = load_contract_fixture("batch_runtime_contract.json")
    client = FakeBatchClient()
    adapter = BatchAdapter(batch_client=client, sleeper=lambda _: None)
    monkeypatch.setenv("AWS_REGION", fixture["submit_context"]["aws_region"])

    adapter.submit_task(
        task_record=fixture["task_record"],
        bucket_name=fixture["submit_context"]["bucket_name"],
        job_queue_arn=fixture["submit_context"]["job_queue_arn"],
        job_definition_arn=fixture["submit_context"]["job_definition_arn"],
        image_repo_uri=fixture["submit_context"]["image_repo_uri"],
    )

    assert client.register_calls[0]["jobDefinitionName"].startswith(
        fixture["expected_job_definition"]["base_name"]
    )
    assert client.register_calls[0]["jobDefinitionName"].endswith(
        fixture["task_record"]["resolved_image_tag"]
    )
