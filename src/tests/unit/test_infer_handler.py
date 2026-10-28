import io
import json
from typing import Any

import pytest

from infer import handler as infer_handler
from infer.sagemaker_infer import SageMakerInferenceAdapter, resolve_s3_uri
from infer.validators import parse_infer_submission
from shared.constants import (
    TASK_STATUS_FAILED,
    TASK_STATUS_SUCCEEDED,
)


class FakeRepository:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def create_task(self, record: dict[str, Any]) -> None:
        self.items[record["task_id"]] = dict(record)

    def update_task(self, record: dict[str, Any]) -> None:
        self.items[record["task_id"]] = dict(record)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.items.get(task_id)

    def list_tasks(self) -> list[dict[str, Any]]:
        return sorted(
            self.items.values(), key=lambda i: i.get("created_at", ""), reverse=True
        )


def _event(method: str, body: dict | None = None, query: dict | None = None):
    return {
        "httpMethod": method,
        "resource": "/infer-tasks",
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": query,
    }


VALID_BODY = {
    "task_type": "timeseries_forecast",
    "task_name": "推理-合同校验",
    "data_location": "inference_data/predict.csv",
    "output_location": "inference_output/infer-1",
    "model_name": "timeseries_model",
    "target_endpoint": "timeseries-endpoint",
    "submitted_by": "demo-operator",
    "description": "预测未来 7 天目标指标",
    "confirmation_flag": True,
}


def test_parse_infer_submission_accepts_valid_payload():
    normalized = parse_infer_submission(_event("POST", VALID_BODY))
    assert normalized["target_endpoint"] == "timeseries-endpoint"
    assert normalized["confirmation_flag"] is True


def test_parse_infer_submission_rejects_missing_endpoint():
    body = dict(VALID_BODY)
    body.pop("target_endpoint")
    from api.validators import TaskValidationError

    with pytest.raises(TaskValidationError):
        parse_infer_submission(_event("POST", body))


def test_post_infer_runs_sync_and_marks_succeeded(monkeypatch):
    repo = FakeRepository()
    monkeypatch.setattr(infer_handler, "build_repository", lambda: repo)
    monkeypatch.setenv("BUCKET_NAME", "demo-bucket")

    seen = {}

    class FakeAdapter:
        def run_sync(self, *, endpoint_name, input_uri, output_uri, target_column="target"):
            seen["endpoint"] = endpoint_name
            seen["input"] = input_uri
            seen["output"] = output_uri
            return {
                "output_uri": "s3://demo-bucket/output/infer-1/predictions.json",
                "prediction_count": 42,
                "instance_count": 42,
            }

    monkeypatch.setattr(infer_handler, "SageMakerInferenceAdapter", FakeAdapter)

    response = infer_handler.handler(_event("POST", VALID_BODY), None)
    assert response["statusCode"] == 201
    body = json.loads(response["body"])
    assert body["status"] == TASK_STATUS_SUCCEEDED
    assert seen["input"] == "s3://demo-bucket/inference_data/predict.csv"
    assert seen["endpoint"] == "timeseries-endpoint"
    stored = repo.items[body["task_id"]]
    assert stored["status"] == TASK_STATUS_SUCCEEDED
    assert stored["prediction_count"] == 42
    assert stored["result_location"] == "s3://demo-bucket/output/infer-1/predictions.json"


def test_post_infer_marks_failed_on_endpoint_error(monkeypatch):
    from infer.sagemaker_infer import InferenceInvocationError

    repo = FakeRepository()
    monkeypatch.setattr(infer_handler, "build_repository", lambda: repo)
    monkeypatch.setenv("BUCKET_NAME", "demo-bucket")

    class FakeAdapter:
        def run_sync(self, *, endpoint_name, input_uri, output_uri, target_column="target"):
            raise InferenceInvocationError("boom")

    monkeypatch.setattr(infer_handler, "SageMakerInferenceAdapter", FakeAdapter)

    response = infer_handler.handler(_event("POST", VALID_BODY), None)
    assert response["statusCode"] == 502
    # the failed record is persisted
    stored = list(repo.items.values())[0]
    assert stored["status"] == TASK_STATUS_FAILED
    assert "boom" in stored["error_message"]


def test_list_infer_tasks_returns_stored_status(monkeypatch):
    repo = FakeRepository()
    repo.create_task(
        {
            "task_id": "t1",
            "task_name": "推理-1",
            "task_type": "timeseries_forecast",
            "model_name": "timeseries_model",
            "target_endpoint": "timeseries-endpoint",
            "data_location": "data/predict.csv",
            "output_location": "inference_output/infer-1",
            "submitted_by": "op",
            "description": "",
            "status": TASK_STATUS_SUCCEEDED,
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-01T00:00:00Z",
        }
    )
    monkeypatch.setattr(infer_handler, "build_repository", lambda: repo)

    response = infer_handler.handler(_event("GET"), None)
    assert response["statusCode"] == 200
    tasks = json.loads(response["body"])["tasks"]
    assert tasks[0]["status"] == TASK_STATUS_SUCCEEDED


def test_download_result_returns_inline_base64(monkeypatch):
    import base64
    import io

    repo = FakeRepository()
    repo.create_task(
        {
            "task_id": "t-dl",
            "status": TASK_STATUS_SUCCEEDED,
            "result_location": "s3://demo-bucket/output/infer-1/predictions.json",
            "created_at": "2026-07-01T00:00:00Z",
        }
    )
    monkeypatch.setattr(infer_handler, "build_repository", lambda: repo)

    class FakeS3:
        def __init__(self):
            self.get_args = None

        def get_object(self, Bucket, Key):  # noqa: N803
            self.get_args = {"Bucket": Bucket, "Key": Key}
            return {"Body": io.BytesIO(b'{"pred": 1}'), "ContentType": "application/json"}

    fake_s3 = FakeS3()

    result = infer_handler.handle_download_result(
        "t-dl", repo, s3_client=fake_s3
    )
    assert result["statusCode"] == 200
    body = json.loads(result["body"])
    # China cannot use presigned S3 URLs; the result is returned inline as base64.
    assert base64.b64decode(body["content_base64"]) == b'{"pred": 1}'
    assert body["file_name"] == "predictions.json"
    assert fake_s3.get_args["Bucket"] == "demo-bucket"
    assert fake_s3.get_args["Key"] == "output/infer-1/predictions.json"


def test_download_result_rejects_non_succeeded():
    from api.validators import TaskValidationError

    repo = FakeRepository()
    repo.create_task(
        {
            "task_id": "t-fail",
            "status": TASK_STATUS_FAILED,
            "result_location": "",
            "created_at": "2026-07-01T00:00:00Z",
        }
    )
    with pytest.raises(TaskValidationError):
        infer_handler.handle_download_result("t-fail", repo, s3_client=object())


def test_download_result_rejects_missing_result_location():
    from api.validators import TaskValidationError

    repo = FakeRepository()
    repo.create_task(
        {
            "task_id": "t-noloc",
            "status": TASK_STATUS_SUCCEEDED,
            "result_location": "",
            "created_at": "2026-07-01T00:00:00Z",
        }
    )
    with pytest.raises(TaskValidationError):
        infer_handler.handle_download_result("t-noloc", repo, s3_client=object())


def test_download_result_missing_task_raises():
    repo = FakeRepository()
    with pytest.raises(infer_handler.InferTaskNotFoundError):
        infer_handler.handle_download_result("nope", repo, s3_client=object())


def test_get_infer_task_download_routes_to_inline_content(monkeypatch):
    import base64
    import io

    repo = FakeRepository()
    repo.create_task(
        {
            "task_id": "t-route",
            "status": TASK_STATUS_SUCCEEDED,
            "result_location": "s3://demo-bucket/output/x/predictions.json",
            "created_at": "2026-07-01T00:00:00Z",
        }
    )
    monkeypatch.setattr(infer_handler, "build_repository", lambda: repo)
    monkeypatch.setattr(
        infer_handler.boto3,
        "client",
        lambda name: type(
            "S3",
            (),
            {
                "get_object": lambda self, Bucket, Key: {
                    "Body": io.BytesIO(b"ROUTED"),
                    "ContentType": "application/json",
                }
            },
        )(),
    )
    event = _event("GET", query={"task_id": "t-route", "download": "1"})
    response = infer_handler.handler(event, None)
    assert response["statusCode"] == 200
    assert base64.b64decode(json.loads(response["body"])["content_base64"]) == b"ROUTED"


def test_resolve_s3_uri_passthrough_and_prefix():
    assert resolve_s3_uri("s3://x/y.csv", "bucket") == "s3://x/y.csv"
    assert resolve_s3_uri("data/p.csv", "bucket") == "s3://bucket/data/p.csv"


def test_run_sync_reads_csv_invokes_and_writes_output():
    """run_sync reads CSV rows from S3, posts them as instances to the
    real-time endpoint, and writes the predictions back to the output URI."""

    class FakeS3:
        def __init__(self):
            self.put_calls = []

        def get_object(self, Bucket, Key):
            csv_bytes = b"item_id,timestamp,target\nd1,2026-07-01T00:00:00,1.5\n"
            return {"Body": io.BytesIO(csv_bytes)}

        def put_object(self, **kwargs):
            self.put_calls.append(kwargs)

    class FakeRuntime:
        def __init__(self):
            self.invoked = None

        def invoke_endpoint(self, **kwargs):
            self.invoked = kwargs
            payload = json.dumps(
                {"predictions": [{"item_id": "d1", "timestamp": "x", "mean": 2.0}]}
            ).encode("utf-8")
            return {"Body": io.BytesIO(payload)}

    s3 = FakeS3()
    runtime = FakeRuntime()
    adapter = SageMakerInferenceAdapter(runtime_client=runtime, s3_client=s3)
    result = adapter.run_sync(
        endpoint_name="timeseries-endpoint",
        input_uri="s3://b/data/predict.csv",
        output_uri="s3://b/output/infer-1",
    )
    # invoked synchronously with a JSON instances body
    sent = json.loads(runtime.invoked["Body"].decode("utf-8"))
    assert sent["instances"][0]["item_id"] == "d1"
    # target must be coerced to a number (AutoGluon rejects object-dtype target)
    assert sent["instances"][0]["target"] == 1.5
    assert isinstance(sent["instances"][0]["target"], (int, float))
    # timestamp is left as a string
    assert sent["instances"][0]["timestamp"] == "2026-07-01T00:00:00"
    # predictions written; output key defaults to predictions.json for a prefix
    assert result["prediction_count"] == 1
    assert result["output_uri"] == "s3://b/output/infer-1/predictions.json"
    assert s3.put_calls[0]["Key"] == "output/infer-1/predictions.json"


def test_run_sync_maps_target_column_alias():
    """A CSV whose target lives in a differently-named column (e.g. sensor_value)
    is mapped to `target` and the extra feature columns are dropped."""

    class FakeS3:
        def get_object(self, Bucket, Key):
            csv_bytes = (
                b"timestamp,pressure_diff,sensor_value,item_id\n"
                b"2026-05-20 00:00:00,0.0,13.08,dev-1\n"
            )
            return {"Body": io.BytesIO(csv_bytes)}

        def put_object(self, **kwargs):
            pass

    class FakeRuntime:
        def __init__(self):
            self.invoked = None

        def invoke_endpoint(self, **kwargs):
            self.invoked = kwargs
            return {"Body": io.BytesIO(b'{"predictions": []}')}

    runtime = FakeRuntime()
    adapter = SageMakerInferenceAdapter(runtime_client=runtime, s3_client=FakeS3())
    adapter.run_sync(
        endpoint_name="ep",
        input_uri="s3://b/in.csv",
        output_uri="s3://b/out",
        target_column="sensor_value",
    )
    sent = json.loads(runtime.invoked["Body"].decode("utf-8"))
    inst = sent["instances"][0]
    # sensor_value mapped to target (numeric), only the 3 required keys present
    assert inst["target"] == 13.08
    assert inst["item_id"] == "dev-1"
    assert set(inst.keys()) == {"item_id", "timestamp", "target"}


def test_run_sync_rejects_unknown_target_column():
    from infer.sagemaker_infer import InferenceInvocationError

    class FakeS3:
        def get_object(self, Bucket, Key):
            return {"Body": io.BytesIO(b"item_id,timestamp,sensor_value\nd1,t,1\n")}

    adapter = SageMakerInferenceAdapter(
        runtime_client=object(), s3_client=FakeS3()
    )
    with pytest.raises(InferenceInvocationError):
        adapter.run_sync(
            endpoint_name="ep",
            input_uri="s3://b/in.csv",
            output_uri="s3://b/out",
            target_column="nonexistent",
        )


def test_run_sync_raises_on_endpoint_error():
    from botocore.exceptions import ClientError
    from infer.sagemaker_infer import InferenceInvocationError

    class FakeS3:
        def get_object(self, Bucket, Key):
            return {"Body": io.BytesIO(b"item_id,timestamp,target\nd1,t,1\n")}

    class FakeRuntime:
        def invoke_endpoint(self, **kwargs):
            raise ClientError({"Error": {"Code": "ValidationError"}}, "InvokeEndpoint")

    adapter = SageMakerInferenceAdapter(
        runtime_client=FakeRuntime(), s3_client=FakeS3()
    )
    with pytest.raises(InferenceInvocationError):
        adapter.run_sync(
            endpoint_name="ep", input_uri="s3://b/in.csv", output_uri="s3://b/out"
        )


# --------------------------------------------------------------------------
# data_location may name a directory: every .json/.csv underneath is read and
# their rows concatenated into one batch.
# --------------------------------------------------------------------------


class FakeDirS3:
    """In-memory S3 holding a whole prefix, with list_objects_v2 pagination."""

    def __init__(self, objects: dict[str, bytes], page_size: int = 1000):
        self.objects = objects
        self.page_size = page_size
        self.put_calls: list[dict] = []
        self.list_calls = 0

    def get_object(self, Bucket, Key):  # noqa: N803
        from botocore.exceptions import ClientError

        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)

    def list_objects_v2(self, **kwargs):
        self.list_calls += 1
        prefix = kwargs.get("Prefix", "")
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        start = int(kwargs.get("ContinuationToken") or 0)
        page = keys[start:start + self.page_size]
        end = start + len(page)
        out = {
            "Contents": [{"Key": k, "Size": len(self.objects[k])} for k in page],
            "IsTruncated": end < len(keys),
        }
        if out["IsTruncated"]:
            out["NextContinuationToken"] = str(end)
        return out


class FakeEchoRuntime:
    """Echoes one prediction per received instance."""

    def __init__(self):
        self.invoked = None

    def invoke_endpoint(self, **kwargs):
        self.invoked = kwargs
        sent = json.loads(kwargs["Body"].decode("utf-8"))["instances"]
        payload = json.dumps(
            {"predictions": [{"ignite_at": f"p{i}"} for i, _ in enumerate(sent)]}
        ).encode("utf-8")
        return {"Body": io.BytesIO(payload)}


def _series_row(line: str) -> bytes:
    return json.dumps(
        {"instances": [{"line_id": line, "cutoff": "2026-08-20 04:00:00"}]}
    ).encode("utf-8")


def test_run_sync_reads_every_file_under_a_directory():
    s3 = FakeDirS3(
        {
            "inference_data/batch/b.json": _series_row("line-02"),
            "inference_data/batch/a.json": _series_row("line-01"),
        }
    )
    runtime = FakeEchoRuntime()
    adapter = SageMakerInferenceAdapter(runtime_client=runtime, s3_client=s3)
    result = adapter.run_sync(
        endpoint_name="ep",
        input_uri="s3://b/inference_data/batch/",
        output_uri="s3://b/inference_output/run1/",
    )
    sent = json.loads(runtime.invoked["Body"].decode("utf-8"))["instances"]
    # both files read, concatenated in sorted key order (a.json before b.json)
    assert [i["line_id"] for i in sent] == ["line-01", "line-02"]
    assert result["prediction_count"] == 2
    assert result["input_files"] == [
        "inference_data/batch/a.json",
        "inference_data/batch/b.json",
    ]


def test_run_sync_directory_without_trailing_slash_falls_back_to_listing():
    s3 = FakeDirS3({"inference_data/batch/a.json": _series_row("line-01")})
    adapter = SageMakerInferenceAdapter(
        runtime_client=FakeEchoRuntime(), s3_client=s3
    )
    result = adapter.run_sync(
        endpoint_name="ep",
        input_uri="s3://b/inference_data/batch",   # no trailing slash
        output_uri="s3://b/out/",
    )
    assert result["input_files"] == ["inference_data/batch/a.json"]


def test_run_sync_directory_mixes_json_and_csv_and_skips_other_extensions():
    s3 = FakeDirS3(
        {
            "d/a.json": json.dumps(
                [{"item_id": "d1", "timestamp": "t1", "target": 1.5}]
            ).encode("utf-8"),
            "d/b.csv": b"item_id,timestamp,target\nd2,t2,2.5\n",
            "d/README.md": b"# notes, must not be parsed",
            "d/model.parquet": b"\x00\x01binary",
        }
    )
    runtime = FakeEchoRuntime()
    adapter = SageMakerInferenceAdapter(runtime_client=runtime, s3_client=s3)
    result = adapter.run_sync(
        endpoint_name="ep", input_uri="s3://b/d/", output_uri="s3://b/out/"
    )
    sent = json.loads(runtime.invoked["Body"].decode("utf-8"))["instances"]
    assert [i["item_id"] for i in sent] == ["d1", "d2"]
    assert [i["target"] for i in sent] == [1.5, 2.5]   # CSV cell coerced to float
    assert result["input_files"] == ["d/a.json", "d/b.csv"]


def test_run_sync_directory_paginates_the_listing():
    from infer.sagemaker_infer import MAX_INPUT_FILES

    objects = {f"d/f{i:03d}.json": _series_row("line-01") for i in range(7)}
    s3 = FakeDirS3(objects, page_size=2)
    adapter = SageMakerInferenceAdapter(
        runtime_client=FakeEchoRuntime(), s3_client=s3
    )
    result = adapter.run_sync(
        endpoint_name="ep", input_uri="s3://b/d/", output_uri="s3://b/out/"
    )
    assert len(result["input_files"]) == 7
    assert s3.list_calls == 4          # 7 keys / 2 per page
    assert MAX_INPUT_FILES >= 7


def test_run_sync_directory_with_no_supported_files_reports_what_it_skipped():
    from infer.sagemaker_infer import InferenceInvocationError

    s3 = FakeDirS3({"d/README.md": b"x", "d/notes.txt": b"y"})
    adapter = SageMakerInferenceAdapter(runtime_client=object(), s3_client=s3)
    with pytest.raises(InferenceInvocationError) as exc:
        adapter.run_sync(
            endpoint_name="ep", input_uri="s3://b/d/", output_uri="s3://b/out/"
        )
    assert "no .json or .csv files" in str(exc.value)
    assert "README.md" in str(exc.value)


def test_run_sync_directory_rejects_too_many_files():
    from infer.sagemaker_infer import MAX_INPUT_FILES, InferenceInvocationError

    objects = {
        f"d/f{i:04d}.json": _series_row("line-01")
        for i in range(MAX_INPUT_FILES + 1)
    }
    adapter = SageMakerInferenceAdapter(
        runtime_client=object(), s3_client=FakeDirS3(objects)
    )
    with pytest.raises(InferenceInvocationError) as exc:
        adapter.run_sync(
            endpoint_name="ep", input_uri="s3://b/d/", output_uri="s3://b/out/"
        )
    assert str(MAX_INPUT_FILES) in str(exc.value)


def test_run_sync_rejects_batch_above_the_sagemaker_payload_limit():
    """A directory can easily expand past SageMaker's 6 MB request cap; the
    size is checked locally so the error names the real cause."""
    from infer.sagemaker_infer import InferenceInvocationError

    big = json.dumps(
        [{"line_id": "line-01", "values": [1.234567] * 60000}]
    ).encode("utf-8")
    objects = {f"d/f{i}.json": big for i in range(20)}
    adapter = SageMakerInferenceAdapter(
        runtime_client=object(), s3_client=FakeDirS3(objects)
    )
    with pytest.raises(InferenceInvocationError) as exc:
        adapter.run_sync(
            endpoint_name="ep", input_uri="s3://b/d/", output_uri="s3://b/out/"
        )
    assert "above the SageMaker 6 MB limit" in str(exc.value)


def test_malformed_json_object_does_not_fall_back_to_directory_listing():
    """A single object with broken JSON must report its parse error, not be
    silently retried as a prefix — that would replace the real cause with a
    misleading "directory contains no files"."""
    from infer.sagemaker_infer import InferenceInvocationError

    s3 = FakeDirS3({"d/bad.json": b"{not json"})
    adapter = SageMakerInferenceAdapter(runtime_client=object(), s3_client=s3)
    with pytest.raises(InferenceInvocationError) as exc:
        adapter.run_sync(
            endpoint_name="ep", input_uri="s3://b/d/bad.json", output_uri="s3://b/out/"
        )
    assert "not valid JSON" in str(exc.value)
    assert s3.list_calls == 0
