# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Unit tests for the data-files download/delete helpers, focused on the
validation guards that keep deletion inside user-writable input areas and reject
path traversal."""

import base64
import io
import json

import pytest

from api.data_files import (
    MAX_INLINE_DOWNLOAD_BYTES,
    create_download_url,
    delete_data_file,
)
from api.validators import TaskValidationError


class _FakeS3:
    def __init__(self, body: bytes = b"hello") -> None:
        self.deleted: list[tuple[str, str]] = []
        self.gets: list[dict] = []
        self._body = body

    def get_object(self, Bucket, Key):  # noqa: N803
        self.gets.append({"Bucket": Bucket, "Key": Key})
        return {"Body": io.BytesIO(self._body), "ContentType": "text/plain"}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.deleted.append((Bucket, Key))
        return {}


def _delete_event(key):
    return {"body": json.dumps({"key": key})}


def test_download_returns_inline_base64_for_any_browsable_area():
    # China cannot use presigned S3 URLs, so the file is returned inline as base64.
    s3 = _FakeS3(body=b"model-bytes")
    result = create_download_url(
        bucket_name="bucket", key="output/task-1/models/model.tar.gz", s3_client=s3
    )
    assert result["key"] == "output/task-1/models/model.tar.gz"
    assert result["file_name"] == "model.tar.gz"
    assert base64.b64decode(result["content_base64"]) == b"model-bytes"
    assert s3.gets[0]["Key"] == "output/task-1/models/model.tar.gz"


def test_download_rejects_oversized_file():
    s3 = _FakeS3(body=b"x" * (MAX_INLINE_DOWNLOAD_BYTES + 1))
    with pytest.raises(TaskValidationError):
        create_download_url(bucket_name="bucket", key="output/t/big.bin", s3_client=s3)


def test_download_url_rejects_traversal():
    s3 = _FakeS3()
    with pytest.raises(TaskValidationError):
        create_download_url(bucket_name="bucket", key="data/../secrets", s3_client=s3)


def test_download_url_rejects_area_outside_allowed():
    s3 = _FakeS3()
    with pytest.raises(TaskValidationError):
        create_download_url(bucket_name="bucket", key="skills/hack.md", s3_client=s3)


def test_delete_removes_object_in_input_area():
    s3 = _FakeS3()
    result = delete_data_file(
        bucket_name="bucket", event=_delete_event("data/train.csv"), s3_client=s3
    )
    assert result == {"deleted": True, "key": "data/train.csv"}
    assert s3.deleted == [("bucket", "data/train.csv")]


def test_delete_allows_inference_data_area():
    s3 = _FakeS3()
    delete_data_file(
        bucket_name="bucket",
        event=_delete_event("inference_data/sub/predict.csv"),
        s3_client=s3,
    )
    assert s3.deleted == [("bucket", "inference_data/sub/predict.csv")]


def test_delete_rejects_output_area():
    # Output areas hold artifacts and must not be deletable from the UI.
    s3 = _FakeS3()
    with pytest.raises(TaskValidationError):
        delete_data_file(
            bucket_name="bucket",
            event=_delete_event("output/task-1/models/model.tar.gz"),
            s3_client=s3,
        )
    assert s3.deleted == []


def test_delete_rejects_traversal_and_empty():
    s3 = _FakeS3()
    for bad in ("data/../etc/passwd", "", "data/", "data/evil name.csv"):
        with pytest.raises(TaskValidationError):
            delete_data_file(
                bucket_name="bucket", event=_delete_event(bad), s3_client=s3
            )
    assert s3.deleted == []
