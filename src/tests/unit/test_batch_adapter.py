# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
from types import SimpleNamespace

from api.batch_adapter import BatchAdapter


class FakeBatchClient:
    def __init__(self):
        self.submit_calls = []
        self.register_calls = []
        self.describe_by_arn_calls = []
        self.describe_by_name_calls = []

    def submit_job(self, **kwargs):
        self.submit_calls.append(kwargs)
        return {"jobId": "job-123", "jobName": kwargs["jobName"]}

    def describe_job_definitions(self, **kwargs):
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

    def register_job_definition(self, **kwargs):
        self.register_calls.append(kwargs)
        return {"jobDefinitionArn": "arn:aws:batch:tagged"}


def test_submit_task_registers_tagged_job_definition_for_non_stable_image(monkeypatch):
    client = FakeBatchClient()
    adapter = BatchAdapter(batch_client=client, sleeper=lambda _: None)
    monkeypatch.setenv("AWS_REGION", "cn-north-1")

    result = adapter.submit_task(
        task_record={
            "task_id": "task-1",
            "resolved_image_tag": "candidate",
            "data_location": "data/train.csv",
            "training_prompt": "train it",
        },
        bucket_name="bucket-name",
        job_queue_arn="queue-arn",
        job_definition_arn="base-job-def-arn",
        image_repo_uri="123456789012.dkr.ecr.cn/test",
    )

    assert client.register_calls
    assert client.register_calls[0]["containerProperties"]["image"].endswith(":candidate")
    assert client.submit_calls[0]["jobDefinition"] == "arn:aws:batch:tagged"
    assert "image" not in client.submit_calls[0]["containerOverrides"]
    assert result["job_definition_arn"] == "arn:aws:batch:tagged"


def test_submit_task_uses_base_job_definition_for_stable(monkeypatch):
    client = FakeBatchClient()
    adapter = BatchAdapter(batch_client=client, sleeper=lambda _: None)
    monkeypatch.setenv("AWS_REGION", "cn-north-1")

    adapter.submit_task(
        task_record={
            "task_id": "task-1",
            "resolved_image_tag": "stable",
            "data_location": "data/train.csv",
            "training_prompt": "train it",
        },
        bucket_name="bucket-name",
        job_queue_arn="queue-arn",
        job_definition_arn="base-job-def-arn",
        image_repo_uri="123456789012.dkr.ecr.cn/test",
    )

    assert not client.register_calls
    assert client.submit_calls[0]["jobDefinition"] == "base-job-def-arn"


def _submitted_env(client):
    return {
        item["name"]: item["value"]
        for item in client.submit_calls[0]["containerOverrides"]["environment"]
    }


def _submit(client, monkeypatch, task_type):
    monkeypatch.setenv("AWS_REGION", "cn-north-1")
    BatchAdapter(batch_client=client, sleeper=lambda _: None).submit_task(
        task_record={
            "task_id": "task-1",
            "resolved_image_tag": "stable",
            "data_location": "data/pkg/",
            "task_type": task_type,
        },
        bucket_name="bucket-name",
        job_queue_arn="queue-arn",
        job_definition_arn="arn:aws:batch:base",
        image_repo_uri="123456789012.dkr.ecr.cn/test",
    )


def test_submit_task_injects_required_prediction_count_for_transfer_types(monkeypatch):
    """byo_deploy produces no predictions; the runtime must be told so, or every
    attempt fails with "Missing required artifacts: prediction csv"."""
    monkeypatch.setenv(
        "TASK_TYPES_JSON",
        json.dumps(
            [
                {
                    "id": "byo_deploy",
                    "skill": "byo-model-deploy.md",
                    "enforceVerification": False,
                    "requiredPredictionCount": 0,
                }
            ]
        ),
    )
    client = FakeBatchClient()
    _submit(client, monkeypatch, "byo_deploy")

    env = _submitted_env(client)
    assert env["REQUIRED_PREDICTION_COUNT"] == "0"
    assert env["ENFORCE_VERIFICATION"] == "0"


def test_submit_task_omits_required_prediction_count_when_unconfigured(monkeypatch):
    """Training types don't set it, so the runtime keeps its default of 1."""
    monkeypatch.setenv(
        "TASK_TYPES_JSON",
        json.dumps([{"id": "tabular_regression", "skill": "tabular-regression-train.md"}]),
    )
    client = FakeBatchClient()
    _submit(client, monkeypatch, "tabular_regression")

    assert "REQUIRED_PREDICTION_COUNT" not in _submitted_env(client)
