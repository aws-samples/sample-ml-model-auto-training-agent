import json
from types import SimpleNamespace

import oncomplete.handler as oncomplete_handler


class FakeRepository:
    def __init__(self, task):
        self.task = task
        self.updated = []

    def get_task_by_batch_job_id(self, batch_job_id):
        return self.task

    def update_task(self, task):
        self.updated.append(task)


class FakeModelRegistry:
    def __init__(self, accepted_record=None, accepted=True):
        self.accepted_record = accepted_record or {"model_record_id": "task-1", "status": "DEPLOYING"}
        self.accepted = accepted
        self.accept_calls = []
        self.dispatch_failures = []

    def accept_deployment_attempt(self, **kwargs):
        self.accept_calls.append(kwargs)
        return self.accepted_record, self.accepted

    def record_dispatch_failure(self, **kwargs):
        self.dispatch_failures.append(kwargs)


class FakeLambdaClient:
    def __init__(self, status_code=202, error=None):
        self.status_code = status_code
        self.error = error
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"StatusCode": self.status_code}


class FakeBody:
    def __init__(self, payload: str):
        self._payload = payload

    def read(self):
        return self._payload.encode("utf-8")


class FakeS3Client:
    def __init__(self, objects=None):
        self.objects = objects or {}

    def get_object(self, Bucket, Key):
        return {"Body": FakeBody(self.objects[(Bucket, Key)])}


def _verification_report(
    *,
    source="independent",
    achieved=True,
    metric_value=11.3,
    threshold=12.5,
    direction="lower_is_better",
):
    return json.dumps(
        {
            "source": source,
            "achieved": achieved,
            "verified_metric": metric_value,
            "threshold": threshold,
            "direction": direction,
        }
    )


def _eligible_manifest():
    return {
        "task_id": "task-1",
        "attempts": [
            {
                "verification": {
                    "achieved": True,
                    "metric_name": "rmse",
                    "metric_value": 11.3,
                    "threshold": 12.5,
                    "threshold_direction": "lower_is_better",
                    "reason": "verified",
                    "missing_artifacts": [],
                    "source": "independent",
                    "report_path": "attempt-1/runner_verification.json",
                }
            }
        ],
        "terminal_outcome": {
            "achieved": True,
            "reason": "verified",
            "final_metric_value": 11.3,
        },
        "published_artifacts": [
            {"bucket": "bucket-name", "key": "output/task-1/model.tar.gz"},
            {"bucket": "bucket-name", "key": "output/task-1/runner_verification.json"},
        ],
        "output_prefix": "output/task-1/",
    }


def _task(status="RUNNING"):
    return {
        "task_id": "task-1",
        "status": status,
        "batch_job_id": "job-1",
        "target_endpoint": "demo-endpoint-01",
        "resolved_image_tag": "stable",
        "model_name": "demo-model-01",
        "updated_at": "old",
        "error_message": "",
        "deployment_skip_reason": "",
    }


def _set_env(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "TaskTable")
    monkeypatch.setenv("TASK_BATCH_JOB_INDEX", "batch-job-id-index")
    monkeypatch.setenv("MODEL_REGISTRY_TABLE_NAME", "ModelRegistryTable")
    monkeypatch.setenv("MODEL_REGISTRY_TARGET_ENDPOINT_INDEX", "target-endpoint-index")
    monkeypatch.setenv("BUCKET_NAME", "bucket-name")
    monkeypatch.setenv("DEPLOYMENT_FUNCTION_NAME", "DeploymentFn")


def test_oncomplete_updates_nonterminal_task_status(monkeypatch):
    repository = FakeRepository(_task(status="SUBMITTED"))
    _set_env(monkeypatch)
    monkeypatch.setattr(
        oncomplete_handler,
        "TaskRepository",
        lambda table_name, batch_job_index: repository,
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "ModelRegistryRepository",
        lambda table_name, target_endpoint_index: FakeModelRegistry(),
    )

    response = oncomplete_handler.handler(
        {"detail": {"jobId": "job-1", "status": "RUNNING"}},
        SimpleNamespace(aws_request_id="req-1"),
    )

    assert response["statusCode"] == 200
    assert repository.updated[0]["status"] == "RUNNING"


def test_oncomplete_dispatches_deployment_for_eligible_success(monkeypatch):
    repository = FakeRepository(_task())
    model_registry = FakeModelRegistry(
        accepted_record={"model_record_id": "task-1", "status": "DEPLOYING"}
    )
    lambda_client = FakeLambdaClient()
    _set_env(monkeypatch)
    monkeypatch.setattr(
        oncomplete_handler,
        "TaskRepository",
        lambda table_name, batch_job_index: repository,
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "ModelRegistryRepository",
        lambda table_name, target_endpoint_index: model_registry,
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "load_runtime_manifest",
        lambda bucket_name, task_record, s3_client=None: _eligible_manifest(),
    )
    monkeypatch.setattr(oncomplete_handler, "_build_lambda_client", lambda: lambda_client)
    monkeypatch.setattr(
        oncomplete_handler,
        "_build_s3_client",
        lambda: FakeS3Client(
            {
                ("bucket-name", "output/task-1/runner_verification.json"): _verification_report(),
            }
        ),
    )

    response = oncomplete_handler.handler(
        {"detail": {"jobId": "job-1", "status": "SUCCEEDED"}},
        SimpleNamespace(aws_request_id="req-1"),
    )

    assert response["statusCode"] == 200
    assert response["body"] == "DEPLOYING"
    assert repository.updated[-1]["status"] == "DEPLOYING"
    assert repository.updated[-1]["deployment_skip_reason"] == ""
    payload = json.loads(lambda_client.calls[0]["Payload"].decode("utf-8"))
    assert payload["model_record_id"] == "task-1"
    assert model_registry.accept_calls


def test_oncomplete_defers_deploy_for_manual_mode(monkeypatch):
    # A deployable, SUCCEEDED task in manual mode must NOT auto-dispatch; it
    # stays SUCCEEDED and no model record is accepted / lambda invoked.
    task = _task()
    task["deployment_mode"] = "manual"
    repository = FakeRepository(task)
    model_registry = FakeModelRegistry()
    lambda_client = FakeLambdaClient()
    _set_env(monkeypatch)
    monkeypatch.setattr(
        oncomplete_handler,
        "TaskRepository",
        lambda table_name, batch_job_index: repository,
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "ModelRegistryRepository",
        lambda table_name, target_endpoint_index: model_registry,
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "load_runtime_manifest",
        lambda bucket_name, task_record, s3_client=None: _eligible_manifest(),
    )
    monkeypatch.setattr(oncomplete_handler, "_build_lambda_client", lambda: lambda_client)
    monkeypatch.setattr(
        oncomplete_handler,
        "_build_s3_client",
        lambda: FakeS3Client(
            {
                ("bucket-name", "output/task-1/runner_verification.json"): _verification_report(),
            }
        ),
    )

    response = oncomplete_handler.handler(
        {"detail": {"jobId": "job-1", "status": "SUCCEEDED"}},
        SimpleNamespace(aws_request_id="req-1"),
    )

    assert response["statusCode"] == 200
    assert response["body"] == "SUCCEEDED"
    assert repository.updated[-1]["status"] == "SUCCEEDED"
    assert model_registry.accept_calls == []
    assert lambda_client.calls == []


def test_oncomplete_keeps_terminal_success_visible_when_manifest_is_ineligible(monkeypatch):
    repository = FakeRepository(_task())
    lambda_client = FakeLambdaClient()
    _set_env(monkeypatch)
    monkeypatch.setattr(
        oncomplete_handler,
        "TaskRepository",
        lambda table_name, batch_job_index: repository,
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "ModelRegistryRepository",
        lambda table_name, target_endpoint_index: FakeModelRegistry(),
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "load_runtime_manifest",
        lambda bucket_name, task_record, s3_client=None: {
            **_eligible_manifest(),
            "attempts": [
                {
                    "verification": {
                        **_eligible_manifest()["attempts"][0]["verification"],
                        "achieved": False,
                        "metric_value": 13.1,
                        "reason": "threshold not met",
                    }
                }
            ],
            "terminal_outcome": {
                "achieved": False,
                "reason": "threshold not met",
                "final_metric_value": 13.1,
            },
        },
    )
    monkeypatch.setattr(oncomplete_handler, "_build_lambda_client", lambda: lambda_client)
    monkeypatch.setattr(
        oncomplete_handler,
        "_build_s3_client",
        lambda: FakeS3Client(
            {
                ("bucket-name", "output/task-1/runner_verification.json"): _verification_report(
                    achieved=False,
                    metric_value=13.1,
                ),
            }
        ),
    )

    response = oncomplete_handler.handler(
        {"detail": {"jobId": "job-1", "status": "SUCCEEDED"}},
        SimpleNamespace(aws_request_id="req-1"),
    )

    assert response["statusCode"] == 200
    assert response["body"] == "SUCCEEDED"
    assert repository.updated[-1]["status"] == "SUCCEEDED"
    assert repository.updated[-1]["error_message"] == ""
    assert repository.updated[-1]["deployment_skip_reason"] == "threshold not met"
    assert lambda_client.calls == []


def test_oncomplete_marks_failed_when_dispatch_is_not_accepted(monkeypatch):
    repository = FakeRepository(_task())
    model_registry = FakeModelRegistry()
    _set_env(monkeypatch)
    monkeypatch.setattr(
        oncomplete_handler,
        "TaskRepository",
        lambda table_name, batch_job_index: repository,
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "ModelRegistryRepository",
        lambda table_name, target_endpoint_index: model_registry,
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "load_runtime_manifest",
        lambda bucket_name, task_record, s3_client=None: _eligible_manifest(),
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "_build_lambda_client",
        lambda: FakeLambdaClient(status_code=500),
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "_build_s3_client",
        lambda: FakeS3Client(
            {
                ("bucket-name", "output/task-1/runner_verification.json"): _verification_report(),
            }
        ),
    )

    response = oncomplete_handler.handler(
        {"detail": {"jobId": "job-1", "status": "SUCCEEDED"}},
        SimpleNamespace(aws_request_id="req-1"),
    )

    assert response["statusCode"] == 200
    assert response["body"] == "FAILED"
    assert repository.updated[-1]["status"] == "FAILED"
    assert model_registry.dispatch_failures[0]["model_record_id"] == "task-1"


def test_oncomplete_ignores_duplicate_accepted_attempt(monkeypatch):
    repository = FakeRepository(_task())
    _set_env(monkeypatch)
    monkeypatch.setattr(
        oncomplete_handler,
        "TaskRepository",
        lambda table_name, batch_job_index: repository,
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "ModelRegistryRepository",
        lambda table_name, target_endpoint_index: FakeModelRegistry(accepted=False),
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "load_runtime_manifest",
        lambda bucket_name, task_record, s3_client=None: _eligible_manifest(),
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "_build_lambda_client",
        lambda: FakeLambdaClient(),
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "_build_s3_client",
        lambda: FakeS3Client(
            {
                ("bucket-name", "output/task-1/runner_verification.json"): _verification_report(),
            }
        ),
    )

    response = oncomplete_handler.handler(
        {"detail": {"jobId": "job-1", "status": "SUCCEEDED"}},
        SimpleNamespace(aws_request_id="req-1"),
    )

    assert response["statusCode"] == 200
    assert response["body"] == "ignored"
    assert repository.updated == []


def test_oncomplete_deploys_local_report_when_verification_disabled(monkeypatch):
    # Task types with independent verification disabled write source
    # "local-report". Such a successful run with a deployable model must now
    # DEPLOY (not be skipped), accepted on engineering completeness.
    repository = FakeRepository(_task())
    model_registry = FakeModelRegistry(
        accepted_record={"model_record_id": "task-1", "status": "DEPLOYING"}
    )
    lambda_client = FakeLambdaClient()
    _set_env(monkeypatch)
    monkeypatch.setattr(
        oncomplete_handler,
        "TaskRepository",
        lambda table_name, batch_job_index: repository,
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "ModelRegistryRepository",
        lambda table_name, target_endpoint_index: model_registry,
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "load_runtime_manifest",
        lambda bucket_name, task_record, s3_client=None: {
            **_eligible_manifest(),
            "attempts": [
                {
                    "verification": {
                        **_eligible_manifest()["attempts"][0]["verification"],
                        "source": "local-report",
                    }
                }
            ],
        },
    )
    monkeypatch.setattr(oncomplete_handler, "_build_lambda_client", lambda: lambda_client)
    monkeypatch.setattr(
        oncomplete_handler,
        "_build_s3_client",
        lambda: FakeS3Client({}),
    )

    response = oncomplete_handler.handler(
        {"detail": {"jobId": "job-1", "status": "SUCCEEDED"}},
        SimpleNamespace(aws_request_id="req-1"),
    )

    assert response["statusCode"] == 200
    assert response["body"] == "DEPLOYING"
    assert repository.updated[-1]["status"] == "DEPLOYING"
    assert repository.updated[-1]["deployment_skip_reason"] == ""
    assert model_registry.accept_calls


def test_oncomplete_ignores_illegal_repeated_transition(monkeypatch):
    repository = FakeRepository(_task(status="DEPLOYED"))
    _set_env(monkeypatch)
    monkeypatch.setattr(
        oncomplete_handler,
        "TaskRepository",
        lambda table_name, batch_job_index: repository,
    )
    monkeypatch.setattr(
        oncomplete_handler,
        "ModelRegistryRepository",
        lambda table_name, target_endpoint_index: FakeModelRegistry(),
    )

    response = oncomplete_handler.handler(
        {"detail": {"jobId": "job-1", "status": "SUCCEEDED"}},
        SimpleNamespace(aws_request_id="req-1"),
    )

    assert response["statusCode"] == 200
    assert response["body"] == "ignored"
    assert repository.updated == []
