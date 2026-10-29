# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
from types import SimpleNamespace

import deployment.handler as deployment_handler
from deployment.sagemaker_adapter import SageMakerDeploymentError


class FakeTaskRepository:
    def __init__(self, task):
        self.task = task
        self.updated = []

    def get_task(self, task_id):
        return self.task

    def update_task(self, task_record):
        self.updated.append(task_record)


class FakeModelRegistryRepository:
    def __init__(self, record, claim_succeeds=True):
        self.record = record
        self.claim_succeeds = claim_succeeds
        self.claims = []
        self.updates = []

    def get_model_record(self, model_record_id):
        return self.record

    def claim_deployment_execution(self, **kwargs):
        self.claims.append(kwargs)
        if not self.claim_succeeds:
            return self.record, False
        self.record = {
            **self.record,
            "execution_claimed_at": kwargs["now"],
        }
        return self.record, True

    def record_deployment_result(self, **kwargs):
        self.updates.append(kwargs)
        self.record = {**self.record, **kwargs}
        return self.record


class FakeSageMakerAdapter:
    def __init__(self, response=None, error=None):
        self.response = response or {
            "provider_status": "Updating",
            "provider_status_detail": {"operation": "update"},
            "endpoint_arn": "arn:aws-cn:sagemaker:cn-north-1:123456789012:endpoint/demo-endpoint-01",
            "endpoint_config_name": "demo-endpoint-01-config",
            "sagemaker_model_name": "demo-endpoint-01-model",
        }
        self.error = error

    def deploy_model(self, **kwargs):
        if self.error:
            raise self.error
        return self.response


def _task():
    return {
        "task_id": "task-1",
        "status": "DEPLOYING",
        "target_endpoint": "demo-endpoint-01",
        "resolved_image_tag": "stable",
        "batch_job_id": "job-1",
        "error_message": "",
        "updated_at": "old",
    }


def _record():
    return {
        "model_record_id": "task-1",
        "status": "DEPLOYING",
        "artifact_reference": {
            "bucket": "bucket-name",
            "key": "output/task-1/model.tar.gz",
            "artifact_type": "tarball",
        },
    }


def test_deployment_handler_marks_task_deployed(monkeypatch):
    task_repository = FakeTaskRepository(_task())
    registry = FakeModelRegistryRepository(_record())
    monkeypatch.setenv("IMAGE_REPO_URI", "123456789012.dkr.ecr.cn/test")
    monkeypatch.setenv("SAGEMAKER_EXECUTION_ROLE_ARN", "arn:aws-cn:iam::123456789012:role/SageMakerExecutionRole")
    monkeypatch.setenv("SAGEMAKER_INSTANCE_TYPE", "ml.m5.large")
    monkeypatch.setenv("SAGEMAKER_INITIAL_INSTANCE_COUNT", "1")
    monkeypatch.setattr(
        deployment_handler,
        "_build_task_repository",
        lambda: task_repository,
    )
    monkeypatch.setattr(
        deployment_handler,
        "_build_model_registry_repository",
        lambda: registry,
    )
    monkeypatch.setattr(
        deployment_handler,
        "SageMakerAdapter",
        lambda: FakeSageMakerAdapter(),
    )

    response = deployment_handler.handler(
        {"task_id": "task-1", "model_record_id": "task-1"},
        SimpleNamespace(aws_request_id="req-1"),
    )

    assert response["statusCode"] == 200
    assert response["body"] == "DEPLOYED"
    assert registry.claims
    assert registry.updates[-1]["status"] == "DEPLOYED"
    assert task_repository.updated[-1]["status"] == "DEPLOYED"


def test_deployment_handler_marks_task_failed_when_adapter_errors(monkeypatch):
    task_repository = FakeTaskRepository(_task())
    registry = FakeModelRegistryRepository(_record())
    monkeypatch.setenv("IMAGE_REPO_URI", "123456789012.dkr.ecr.cn/test")
    monkeypatch.setenv("SAGEMAKER_EXECUTION_ROLE_ARN", "arn:aws-cn:iam::123456789012:role/SageMakerExecutionRole")
    monkeypatch.setattr(
        deployment_handler,
        "_build_task_repository",
        lambda: task_repository,
    )
    monkeypatch.setattr(
        deployment_handler,
        "_build_model_registry_repository",
        lambda: registry,
    )
    monkeypatch.setattr(
        deployment_handler,
        "SageMakerAdapter",
        lambda: FakeSageMakerAdapter(
            error=SageMakerDeploymentError("endpoint update failed")
        ),
    )

    response = deployment_handler.handler(
        {"task_id": "task-1", "model_record_id": "task-1"},
        SimpleNamespace(aws_request_id="req-1"),
    )

    assert response["statusCode"] == 200
    assert response["body"] == "FAILED"
    assert registry.updates[-1]["status"] == "FAILED"
    assert task_repository.updated[-1]["status"] == "FAILED"


def test_deployment_handler_ignores_duplicate_claimed_execution(monkeypatch):
    task_repository = FakeTaskRepository(_task())
    registry = FakeModelRegistryRepository(_record(), claim_succeeds=False)
    monkeypatch.setenv("IMAGE_REPO_URI", "123456789012.dkr.ecr.cn/test")
    monkeypatch.setenv(
        "SAGEMAKER_EXECUTION_ROLE_ARN",
        "arn:aws-cn:iam::123456789012:role/SageMakerExecutionRole",
    )
    monkeypatch.setattr(
        deployment_handler,
        "_build_task_repository",
        lambda: task_repository,
    )
    monkeypatch.setattr(
        deployment_handler,
        "_build_model_registry_repository",
        lambda: registry,
    )
    monkeypatch.setattr(
        deployment_handler,
        "SageMakerAdapter",
        lambda: FakeSageMakerAdapter(),
    )

    response = deployment_handler.handler(
        {"task_id": "task-1", "model_record_id": "task-1"},
        SimpleNamespace(aws_request_id="req-1"),
    )

    assert response["statusCode"] == 200
    assert response["body"] == "ignored"
    assert registry.updates == []
    assert task_repository.updated == []


def _instance_type_task(task_type):
    return {"task_id": "task-1", "status": "SUCCEEDED", "task_type": task_type}


def test_resolve_instance_type_prefers_the_task_types_override(monkeypatch):
    """byo_deploy deploys a CPU-only model, so it must NOT land on the stack-wide
    GPU default — that would burn the (quota-1) GPU endpoint slot."""
    monkeypatch.setenv("SAGEMAKER_INSTANCE_TYPE", "ml.g5.4xlarge")
    monkeypatch.setenv(
        "TASK_TYPES_JSON",
        json.dumps(
            [
                {"id": "timeseries_forecast", "skill": "timeseries-forecast-train.md"},
                {
                    "id": "byo_deploy",
                    "skill": "byo-model-deploy.md",
                    "instanceType": "ml.m5.xlarge",
                },
            ]
        ),
    )

    assert (
        deployment_handler._resolve_instance_type(_instance_type_task("byo_deploy"))
        == "ml.m5.xlarge"
    )


def test_resolve_instance_type_falls_back_to_env_without_override(monkeypatch):
    monkeypatch.setenv("SAGEMAKER_INSTANCE_TYPE", "ml.g5.4xlarge")
    monkeypatch.setenv(
        "TASK_TYPES_JSON",
        json.dumps([{"id": "timeseries_forecast", "skill": "timeseries-forecast-train.md"}]),
    )

    assert (
        deployment_handler._resolve_instance_type(
            _instance_type_task("timeseries_forecast")
        )
        == "ml.g5.4xlarge"
    )


def test_resolve_instance_type_falls_back_for_unknown_task_type(monkeypatch):
    """An unrecognised task_type must not crash the deployment path."""
    monkeypatch.setenv("SAGEMAKER_INSTANCE_TYPE", "ml.g5.4xlarge")
    monkeypatch.setenv(
        "TASK_TYPES_JSON",
        json.dumps([{"id": "timeseries_forecast", "skill": "timeseries-forecast-train.md"}]),
    )

    assert (
        deployment_handler._resolve_instance_type(_instance_type_task("nope"))
        == "ml.g5.4xlarge"
    )
