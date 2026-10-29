# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import api.handler as api_handler
from tests.contracts import load_contract_fixture


class FakeRepository:
    def __init__(self, count: int = 0) -> None:
        self.count = count
        self.created: list[dict[str, object]] = []
        self.updated: list[dict[str, object]] = []

    def count_active_tasks(self) -> int:
        return self.count

    def create_task(self, task_record: dict[str, object]) -> None:
        self.created.append(task_record)

    def update_task(self, task_record: dict[str, object]) -> None:
        self.updated.append(task_record)


class FakeBatchAdapter:
    def submit_task(self, **_: object) -> dict[str, str]:
        return {"job_id": "job-123", "job_name": "training-task-contract-1"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_NAME", "TaskTable")
    monkeypatch.setenv("BUCKET_NAME", "bucket-name")
    monkeypatch.setenv("JOB_QUEUE_ARN", "queue-arn")
    monkeypatch.setenv("JOB_DEF_ARN", "job-def-arn")
    monkeypatch.setenv("IMAGE_REPO_URI", "123456789012.dkr.ecr.cn/test")
    monkeypatch.setenv("MAX_CONCURRENT", "5")
    monkeypatch.setenv("TASK_BATCH_JOB_INDEX", "batch-job-id-index")
    monkeypatch.setenv("MODEL_REGISTRY_TABLE_NAME", "ModelRegistryTable")
    monkeypatch.setenv("MODEL_REGISTRY_TARGET_ENDPOINT_INDEX", "target-endpoint-index")


def _context() -> SimpleNamespace:
    return SimpleNamespace(aws_request_id="req-contract")


def _submission_event(body: dict[str, object]) -> dict[str, object]:
    return {
        "httpMethod": "POST",
        "resource": "/tasks",
        "body": json.dumps(body),
    }


def test_confirmed_submission_fixture_round_trips_through_api_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_contract_fixture("confirmed_task_submission.json")
    repository = FakeRepository()
    monkeypatch.setattr(api_handler, "build_repository", lambda: repository)
    monkeypatch.setattr(api_handler, "BatchAdapter", lambda: FakeBatchAdapter())

    response = api_handler.handler(_submission_event(fixture["payload"]), _context())

    body = json.loads(response["body"])
    assert response["statusCode"] == 201
    assert body["status"] == fixture["success_response"]["status"]
    assert body["message"] == fixture["success_response"]["message"]
    assert repository.created[0]["submitted_by"] == fixture["payload"]["submitted_by"]
    assert repository.created[0]["resolved_image_tag"] == fixture["payload"]["image_tag"]
    assert repository.updated[0]["batch_job_id"] == "job-123"


def test_backend_validation_uses_stable_error_code_for_missing_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_contract_fixture("confirmed_task_submission.json")
    repository = FakeRepository()
    monkeypatch.setattr(api_handler, "build_repository", lambda: repository)

    invalid_payload = dict(fixture["payload"])
    invalid_payload.pop("submitted_by")
    response = api_handler.handler(_submission_event(invalid_payload), _context())

    body = json.loads(response["body"])
    assert response["statusCode"] == fixture["validation_error"]["status"]
    assert body["error_code"] == fixture["validation_error"]["error_code"]
    assert "submitted_by" in body["details"]["missing_fields"]


def test_backend_rejects_unconfirmed_submissions_for_checkpoint_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = load_contract_fixture("confirmed_task_submission.json")
    repository = FakeRepository()
    monkeypatch.setattr(api_handler, "build_repository", lambda: repository)

    invalid_payload = dict(fixture["payload"])
    invalid_payload["confirmation_flag"] = False
    response = api_handler.handler(_submission_event(invalid_payload), _context())

    body = json.loads(response["body"])
    assert response["statusCode"] == fixture["validation_error"]["status"]
    assert body["error_code"] == fixture["validation_error"]["error_code"]
    assert "explicit confirmation" in body["message"]
