import json
from types import SimpleNamespace

import pytest

import api.handler as api_handler
from api.batch_adapter import BatchSubmissionError


class FakeRepository:
    def __init__(self, count=0, task=None, tasks=None):
        self.count = count
        self.created = []
        self.updated = []
        self.task = task
        self.tasks = tasks or []

    def count_active_tasks(self):
        return self.count

    def create_task(self, task_record):
        self.created.append(task_record)

    def update_task(self, task_record):
        self.updated.append(task_record)

    def get_task(self, task_id):
        return self.task

    def list_tasks(self):
        return self.tasks


class FakeBatchAdapter:
    def __init__(self, response=None, error=None):
        self.response = response or {"job_id": "job-123", "job_name": "job-name"}
        self.error = error

    def submit_task(self, **kwargs):
        if self.error:
            raise self.error
        return self.response


class FakeLogReader:
    def __init__(self, logs=None):
        self.logs = logs if logs is not None else []

    def read_task_logs(self, bucket_name, task_id, execution_id=None):
        return self.logs


class FakeModelRegistryRepository:
    def __init__(self, records=None):
        self.records = records or []

    def list_model_records(self):
        return self.records


def _context():
    return SimpleNamespace(aws_request_id="req-123")


def _submission_event(body):
    return {
        "httpMethod": "POST",
        "resource": "/tasks",
        "body": json.dumps(body),
    }


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "TaskTable")
    monkeypatch.setenv("BUCKET_NAME", "bucket-name")
    monkeypatch.setenv("JOB_QUEUE_ARN", "queue-arn")
    monkeypatch.setenv("JOB_DEF_ARN", "job-def-arn")
    monkeypatch.setenv("IMAGE_REPO_URI", "123456789012.dkr.ecr.cn/test")
    monkeypatch.setenv("MAX_CONCURRENT", "5")
    monkeypatch.setenv("TASK_BATCH_JOB_INDEX", "batch-job-id-index")
    monkeypatch.setenv("MODEL_REGISTRY_TABLE_NAME", "ModelRegistryTable")
    monkeypatch.setenv("MODEL_REGISTRY_TARGET_ENDPOINT_INDEX", "target-endpoint-index")
    monkeypatch.setenv("DEPLOYMENT_FUNCTION_NAME", "DeploymentFn")


def test_post_tasks_happy_path(monkeypatch):
    repository = FakeRepository()
    monkeypatch.setattr(api_handler, "build_repository", lambda: repository)
    monkeypatch.setattr(api_handler, "BatchAdapter", lambda: FakeBatchAdapter())

    response = api_handler.handler(
        _submission_event(
            {
                "task_type": "timeseries_forecast",
                "training_prompt": "train this",
                "data_location": "data/train.csv",
                "output_location": "output/run-1",
                "model_name": "demo-model-01",
                "target_endpoint": "demo-endpoint-01",
                "submitted_by": "demoops01",
                "confirmation_flag": True,
            }
        ),
        _context(),
    )

    body = json.loads(response["body"])
    assert response["statusCode"] == 201
    assert body["status"] == "SUBMITTED"
    assert repository.created[0]["resolved_image_tag"] == "stable"
    assert repository.updated[0]["batch_job_id"] == "job-123"


def test_post_tasks_validation_failure(monkeypatch):
    monkeypatch.setattr(api_handler, "build_repository", lambda: FakeRepository())

    response = api_handler.handler(
        _submission_event(
            {
                "task_type": "timeseries_forecast",
                "training_prompt": "train this",
                "data_location": "data/train.csv",
                "output_location": "output/run-1",
                "model_name": "demo-model-01",
                "target_endpoint": "demo-endpoint-01",
                "confirmation_flag": True,
            }
        ),
        _context(),
    )

    body = json.loads(response["body"])
    assert response["statusCode"] == 400
    assert body["error_code"] == "VALIDATION_ERROR"


def test_post_tasks_rejects_path_traversal_data_location(monkeypatch):
    monkeypatch.setattr(api_handler, "build_repository", lambda: FakeRepository())

    response = api_handler.handler(
        _submission_event(
            {
                "task_type": "timeseries_forecast",
                "training_prompt": "train this",
                "data_location": "../secret/train.csv",
                "output_location": "output/run-1",
                "model_name": "demo-model-01",
                "target_endpoint": "demo-endpoint-01",
                "submitted_by": "demoops01",
                "confirmation_flag": True,
            }
        ),
        _context(),
    )

    body = json.loads(response["body"])
    assert response["statusCode"] == 400
    assert body["details"]["field"] == "data_location"


def test_post_tasks_concurrency_rejection(monkeypatch):
    repository = FakeRepository(count=5)
    monkeypatch.setattr(api_handler, "build_repository", lambda: repository)
    monkeypatch.setenv("MAX_CONCURRENT", "5")

    response = api_handler.handler(
        _submission_event(
            {
                "task_type": "timeseries_forecast",
                "training_prompt": "train this",
                "data_location": "data/train.csv",
                "output_location": "output/run-1",
                "model_name": "demo-model-01",
                "target_endpoint": "demo-endpoint-01",
                "submitted_by": "demoops01",
                "confirmation_flag": True,
            }
        ),
        _context(),
    )

    assert response["statusCode"] == 409


def test_post_tasks_marks_failed_when_batch_submission_fails(monkeypatch):
    repository = FakeRepository()
    monkeypatch.setattr(api_handler, "build_repository", lambda: repository)
    monkeypatch.setattr(
        api_handler,
        "BatchAdapter",
        lambda: FakeBatchAdapter(error=BatchSubmissionError("batch down")),
    )

    response = api_handler.handler(
        _submission_event(
            {
                "task_type": "timeseries_forecast",
                "training_prompt": "train this",
                "data_location": "data/train.csv",
                "output_location": "output/run-1",
                "model_name": "demo-model-01",
                "target_endpoint": "demo-endpoint-01",
                "submitted_by": "demoops01",
                "confirmation_flag": True,
            }
        ),
        _context(),
    )

    assert response["statusCode"] == 502
    assert repository.updated[-1]["status"] == "FAILED"


def test_get_tasks_returns_summary(monkeypatch):
    monkeypatch.setattr(
        api_handler,
        "build_repository",
        lambda: FakeRepository(
            tasks=[
                {
                    "task_id": "task-1",
                    "status": "RUNNING",
                    "task_type": "timeseries_forecast",
                    "model_name": "demo-model-01",
                    "target_endpoint": "demo-endpoint-01",
                    "submitted_by": "demoops01",
                    "resolved_image_tag": "stable",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:05:00Z",
                }
            ]
        ),
    )

    response = api_handler.handler(
        {"httpMethod": "GET", "resource": "/tasks"},
        _context(),
    )

    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["tasks"][0]["task_id"] == "task-1"
    assert body["tasks"][0]["submitted_by"] == "demoops01"


def test_get_models_returns_newest_first_registry_projection(monkeypatch):
    monkeypatch.setattr(
        api_handler,
        "build_model_registry_repository",
        lambda: FakeModelRegistryRepository(
            records=[
                {
                    "model_record_id": "task-2",
                    "task_id": "task-2",
                    "model_name": "demo-model-02",
                    "target_endpoint": "demo-endpoint-01",
                    "resolved_image_tag": "candidate",
                    "status": "FAILED",
                    "provider_status": "FAILED",
                    "provider_status_detail": {"stage": "deploy"},
                    "failure_reason": "endpoint update failed",
                    "artifact_reference": {
                        "bucket": "bucket-name",
                        "key": "output/task-2/model.tar.gz",
                    },
                    "endpoint_arn": "",
                    "endpoint_config_name": "",
                    "sagemaker_model_name": "",
                    "is_current_endpoint_target": False,
                    "created_at": "2026-01-02T00:00:00Z",
                    "updated_at": "2026-01-02T00:05:00Z",
                }
            ]
        ),
    )

    response = api_handler.handler(
        {"httpMethod": "GET", "resource": "/models"},
        _context(),
    )

    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["models"][0]["model_record_id"] == "task-2"
    assert body["models"][0]["failure_reason"] == "endpoint update failed"


def test_get_logs_returns_empty_array_before_first_log(monkeypatch):
    monkeypatch.setattr(
        api_handler,
        "build_repository",
        lambda: FakeRepository(task={"task_id": "task-1", "status": "RUNNING"}),
    )
    monkeypatch.setattr(api_handler, "LogReader", lambda: FakeLogReader(logs=[]))

    response = api_handler.handler(
        {
            "httpMethod": "GET",
            "resource": "/logs",
            "queryStringParameters": {"task_id": "task-1"},
        },
        _context(),
    )

    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["status"] == "RUNNING"
    assert body["logs"] == []


def test_get_evaluation_returns_report(monkeypatch):
    import io
    import json as _json

    monkeypatch.setattr(
        api_handler,
        "build_repository",
        lambda: FakeRepository(task={"task_id": "task-1", "status": "SUCCEEDED"}),
    )

    report = {"metric_name": "MAPE", "metric_value": 13.5, "is_achieved": True}

    class _FakeBody:
        def read(self):
            return _json.dumps(report).encode("utf-8")

    class _FakeS3:
        exceptions = type("exc", (), {"NoSuchKey": type("NoSuchKey", (Exception,), {})})

        def get_object(self, Bucket, Key):  # noqa: N803
            assert Key.endswith("evaluation_report.json")
            return {"Body": _FakeBody()}

    monkeypatch.setattr(__import__("boto3"), "client", lambda service: _FakeS3())

    response = api_handler.handler(
        {
            "httpMethod": "GET",
            "resource": "/tasks/evaluation",
            "queryStringParameters": {"task_id": "task-1"},
        },
        _context(),
    )
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["report"]["metric_name"] == "MAPE"
    assert body["report"]["is_achieved"] is True


def test_get_evaluation_reads_nested_output_layout(monkeypatch):
    # Runs produced since the "training output under output/" change store the
    # report at output/<task>/output/evaluation_report.json. The nested key must
    # be probed (and preferred) so those runs render instead of showing the
    # "no results yet" placeholder.
    import json as _json

    monkeypatch.setattr(
        api_handler,
        "build_repository",
        lambda: FakeRepository(task={"task_id": "task-1", "status": "DEPLOYED"}),
    )

    report = {"metric_name": "MAPE", "metric_value": 9.0, "is_achieved": True}
    requested_keys: list[str] = []

    class _FakeBody:
        def read(self):
            return _json.dumps(report).encode("utf-8")

    class _FakeS3:
        exceptions = type("exc", (), {"NoSuchKey": type("NoSuchKey", (Exception,), {})})

        def get_object(self, Bucket, Key):  # noqa: N803
            requested_keys.append(Key)
            return {"Body": _FakeBody()}

    monkeypatch.setattr(__import__("boto3"), "client", lambda service: _FakeS3())

    response = api_handler.handler(
        {
            "httpMethod": "GET",
            "resource": "/tasks/evaluation",
            "queryStringParameters": {"task_id": "task-1"},
        },
        _context(),
    )
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["report"]["metric_value"] == 9.0
    # The nested layout is probed first.
    assert requested_keys[0].endswith("/output/evaluation_report.json")


def test_get_evaluation_falls_back_to_flat_layout(monkeypatch):
    # Legacy runs wrote the report directly under output/<task>/. When the
    # nested key is absent, the handler must fall back to the flat key.
    import json as _json

    monkeypatch.setattr(
        api_handler,
        "build_repository",
        lambda: FakeRepository(task={"task_id": "task-1", "status": "SUCCEEDED"}),
    )

    report = {"metric_name": "MAPE", "metric_value": 12.0, "is_achieved": True}
    requested_keys: list[str] = []

    class _NoSuchKey(Exception):
        pass

    class _FakeBody:
        def read(self):
            return _json.dumps(report).encode("utf-8")

    class _FakeS3:
        exceptions = type("exc", (), {"NoSuchKey": _NoSuchKey})

        def get_object(self, Bucket, Key):  # noqa: N803
            requested_keys.append(Key)
            if Key.endswith("/output/evaluation_report.json"):
                raise _NoSuchKey()
            return {"Body": _FakeBody()}

    monkeypatch.setattr(__import__("boto3"), "client", lambda service: _FakeS3())

    response = api_handler.handler(
        {
            "httpMethod": "GET",
            "resource": "/tasks/evaluation",
            "queryStringParameters": {"task_id": "task-1"},
        },
        _context(),
    )
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["report"]["metric_value"] == 12.0
    assert len(requested_keys) == 2


def test_get_evaluation_missing_report_returns_null(monkeypatch):
    monkeypatch.setattr(
        api_handler,
        "build_repository",
        lambda: FakeRepository(task={"task_id": "task-1", "status": "SUCCEEDED"}),
    )

    class _NoSuchKey(Exception):
        pass

    class _FakeS3:
        exceptions = type("exc", (), {"NoSuchKey": _NoSuchKey})

        def get_object(self, Bucket, Key):  # noqa: N803
            raise _NoSuchKey()

    monkeypatch.setattr(__import__("boto3"), "client", lambda service: _FakeS3())

    response = api_handler.handler(
        {
            "httpMethod": "GET",
            "resource": "/tasks/evaluation",
            "queryStringParameters": {"task_id": "task-1"},
        },
        _context(),
    )
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["report"] is None


def test_deploy_task_rejects_non_succeeded(monkeypatch):
    # Manual deploy is only valid for SUCCEEDED tasks.
    monkeypatch.setattr(
        api_handler,
        "build_repository",
        lambda: FakeRepository(task={"task_id": "task-1", "status": "RUNNING"}),
    )
    response = api_handler.handler(
        {
            "httpMethod": "POST",
            "resource": "/tasks/deploy",
            "body": json.dumps({"task_id": "task-1"}),
        },
        _context(),
    )
    body = json.loads(response["body"])
    assert response["statusCode"] == 400
    assert body["error_code"] == "VALIDATION_ERROR"


def test_deploy_task_dispatches_for_succeeded(monkeypatch):
    task = {
        "task_id": "task-1",
        "status": "SUCCEEDED",
        "target_endpoint": "demo-endpoint-01",
        "deployment_mode": "manual",
    }
    repository = FakeRepository(task=task)
    monkeypatch.setattr(api_handler, "build_repository", lambda: repository)
    monkeypatch.setattr(
        api_handler, "build_model_registry_repository", lambda: object()
    )
    monkeypatch.setattr(api_handler, "build_message_repository", lambda: None)
    monkeypatch.setattr(api_handler, "_build_s3_client", lambda: object())
    monkeypatch.setattr(api_handler, "_build_lambda_client", lambda: object())
    monkeypatch.setattr(
        api_handler, "load_runtime_manifest",
        lambda bucket_name, task_record, s3_client=None: {"task_id": "task-1"},
    )
    monkeypatch.setattr(
        api_handler, "evaluate_deployment_eligibility",
        lambda task_record, manifest, s3_client=None: {"eligible": True},
    )
    captured = {}

    def fake_dispatch(**kwargs):
        captured.update(kwargs)
        return {"status": "DEPLOYING", "model_record_id": "task-1", "accepted": True}

    monkeypatch.setattr(api_handler, "dispatch_deployment", fake_dispatch)

    response = api_handler.handler(
        {
            "httpMethod": "POST",
            "resource": "/tasks/deploy",
            "body": json.dumps({"task_id": "task-1"}),
        },
        _context(),
    )
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["status"] == "DEPLOYING"
    # the shared dispatch helper was invoked with the eligible decision
    assert captured["decision"] == {"eligible": True}


def test_deploy_task_rejects_ineligible(monkeypatch):
    from shared.deployment_dispatch import DeploymentNotEligibleError

    task = {"task_id": "task-1", "status": "SUCCEEDED", "target_endpoint": "e"}
    monkeypatch.setattr(api_handler, "build_repository", lambda: FakeRepository(task=task))
    monkeypatch.setattr(api_handler, "build_model_registry_repository", lambda: object())
    monkeypatch.setattr(api_handler, "build_message_repository", lambda: None)
    monkeypatch.setattr(api_handler, "_build_s3_client", lambda: object())
    monkeypatch.setattr(api_handler, "_build_lambda_client", lambda: object())
    monkeypatch.setattr(
        api_handler, "load_runtime_manifest",
        lambda bucket_name, task_record, s3_client=None: {"task_id": "task-1"},
    )
    monkeypatch.setattr(
        api_handler, "evaluate_deployment_eligibility",
        lambda task_record, manifest, s3_client=None: {
            "eligible": False, "ineligible_reason": "threshold not met"
        },
    )

    def fake_dispatch(**kwargs):
        raise DeploymentNotEligibleError(kwargs["decision"]["ineligible_reason"])

    monkeypatch.setattr(api_handler, "dispatch_deployment", fake_dispatch)

    response = api_handler.handler(
        {
            "httpMethod": "POST",
            "resource": "/tasks/deploy",
            "body": json.dumps({"task_id": "task-1"}),
        },
        _context(),
    )
    body = json.loads(response["body"])
    assert response["statusCode"] == 400
    assert body["error_code"] == "VALIDATION_ERROR"
