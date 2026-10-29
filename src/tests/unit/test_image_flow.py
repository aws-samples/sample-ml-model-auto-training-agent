# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import io
import json
import zipfile
from typing import Any

from api.image_repository import (
    IMAGE_STATUS_BUILDING,
    IMAGE_STATUS_FAILED,
    IMAGE_STATUS_SUCCEEDED,
)
from api.images import handle_image_build_request, list_images
from image_oncomplete import handler as image_oncomplete


class FakeImageRepo:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def put_image(self, record: dict[str, Any]) -> None:
        self.items[record["tag"]] = dict(record)

    def get_image(self, tag: str):
        return self.items.get(tag)

    def list_images(self):
        return sorted(
            self.items.values(), key=lambda i: i.get("created_at", ""), reverse=True
        )

    def get_image_by_build_id(self, build_id: str):
        for record in self.items.values():
            if record.get("build_id") == build_id:
                return record
        return None


class FakeCodeBuild:
    def __init__(self, build_id: str) -> None:
        self._build_id = build_id
        self.started = []

    def start_build(self, **kwargs):
        self.started.append(kwargs)
        return {"build": {"id": self._build_id, "buildStatus": "IN_PROGRESS"}}


class FakeS3:
    """In-memory S3 stub holding the deployed agent baseline under
    agent-baseline/training_runtime/ and capturing repackaged uploads."""

    def __init__(self, baseline: dict[str, bytes]) -> None:
        # keyed by object key -> bytes
        self.objects: dict[str, bytes] = dict(baseline)
        self.put_calls: list[dict[str, Any]] = []

    def get_paginator(self, name: str):
        assert name == "list_objects_v2"
        objects = self.objects

        class _Paginator:
            def paginate(self, Bucket, Prefix):  # noqa: N803 - boto3 kwarg names
                contents = [
                    {"Key": key}
                    for key in objects
                    if key.startswith(Prefix)
                ]
                return [{"Contents": contents}]

        return _Paginator()

    def get_object(self, Bucket, Key):  # noqa: N803
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"]


BASELINE_PREFIX = "agent-baseline/training_runtime/"


def _baseline() -> dict[str, bytes]:
    return {
        f"{BASELINE_PREFIX}Dockerfile": b"FROM python\n",
        f"{BASELINE_PREFIX}runner.py": b"print('run')\n",
        f"{BASELINE_PREFIX}skills/general-agent-spec.md": b"OLD GENERAL\n",
        f"{BASELINE_PREFIX}skills/timeseries-forecast-train.md": b"OLD DOMAIN\n",
    }


def _event(body: dict) -> dict:
    return {"body": json.dumps(body)}


def test_skill_templates_returns_all_baseline_skills():
    repo = FakeImageRepo()
    s3 = FakeS3(_baseline())
    status, result = handle_image_build_request(
        event=_event({"action": "skill-templates"}),
        source_bucket="src-bucket",
        project_name="proj",
        repository=repo,
        s3_client=s3,
    )
    assert status == 200
    assert result["general_skill"] == "OLD GENERAL\n"
    # Every domain skill (all *.md under skills/ except the general one).
    domains = {d["file_name"]: d["content"] for d in result["domain_skills"]}
    assert domains == {"timeseries-forecast-train.md": "OLD DOMAIN\n"}


def test_skill_templates_lists_multiple_domain_skills():
    baseline = _baseline()
    baseline["agent-baseline/training_runtime/skills/noise-train.md"] = b"NOISE\n"
    repo = FakeImageRepo()
    s3 = FakeS3(baseline)
    _, result = handle_image_build_request(
        event=_event({"action": "skill-templates"}),
        source_bucket="src-bucket",
        project_name="proj",
        repository=repo,
        s3_client=s3,
    )
    names = sorted(d["file_name"] for d in result["domain_skills"])
    assert names == ["noise-train.md", "timeseries-forecast-train.md"]


def test_orchestrate_overlays_all_skills_and_starts_build():
    baseline = _baseline()
    baseline["agent-baseline/training_runtime/skills/noise-train.md"] = b"OLD NOISE\n"
    repo = FakeImageRepo()
    s3 = FakeS3(baseline)
    cb = FakeCodeBuild("proj:build-123")
    status, result = handle_image_build_request(
        event=_event(
            {
                "action": "orchestrate",
                "tag": "v9",
                "general_skill": "NEW GENERAL",
                "domain_skills": [
                    {"file_name": "timeseries-forecast-train.md", "content": "NEW CONTENT"},
                    {"file_name": "noise-train.md", "content": "NEW NOISE"},
                ],
            }
        ),
        source_bucket="src-bucket",
        project_name="proj",
        repository=repo,
        s3_client=s3,
        codebuild_client=cb,
    )
    assert status == 202
    assert result["build_id"] == "proj:build-123"

    # The repackaged zip was uploaded to the agent-uploads/ prefix.
    put = next(c for c in s3.put_calls if c["Key"].startswith("agent-uploads/"))
    assert put["Key"] == "agent-uploads/v9/source.zip"
    with zipfile.ZipFile(io.BytesIO(put["Body"])) as archive:
        names = set(archive.namelist())
        # Entries keep the training_runtime/ top-level so the buildspec finds it.
        assert "training_runtime/Dockerfile" in names
        # General + ALL domain skills are overlaid with the customer-edited text.
        assert archive.read("training_runtime/skills/general-agent-spec.md") == b"NEW GENERAL"
        assert archive.read("training_runtime/skills/timeseries-forecast-train.md") == b"NEW CONTENT"
        assert archive.read("training_runtime/skills/noise-train.md") == b"NEW NOISE"

    # Row is BUILDING, carries the build id and JSON skills (no task_type).
    row = repo.items["v9"]
    assert row["status"] == IMAGE_STATUS_BUILDING
    assert row["build_id"] == "proj:build-123"
    assert "task_type" not in row
    skills = json.loads(row["skills"])
    assert skills["general_skill"] == "NEW GENERAL"
    assert {d["file_name"] for d in skills["domain_skills"]} == {
        "timeseries-forecast-train.md",
        "noise-train.md",
    }
    assert len(cb.started) == 1


def test_orchestrate_rejects_empty_domain_skills():
    from api.validators import TaskValidationError

    repo = FakeImageRepo()
    s3 = FakeS3(_baseline())
    try:
        handle_image_build_request(
            event=_event(
                {
                    "action": "orchestrate",
                    "tag": "v9",
                    "general_skill": "g",
                    "domain_skills": [],
                }
            ),
            source_bucket="src-bucket",
            project_name="proj",
            repository=repo,
            s3_client=s3,
            codebuild_client=FakeCodeBuild("b"),
        )
        assert False, "expected TaskValidationError"
    except TaskValidationError:
        pass


def test_orchestrate_rejects_empty_skill():
    from api.validators import TaskValidationError

    repo = FakeImageRepo()
    s3 = FakeS3(_baseline())
    try:
        handle_image_build_request(
            event=_event(
                {
                    "action": "orchestrate",
                    "tag": "v9",
                    "general_skill": "   ",
                    "domain_skills": [
                        {"file_name": "timeseries-forecast-train.md", "content": "d"}
                    ],
                }
            ),
            source_bucket="src-bucket",
            project_name="proj",
            repository=repo,
            s3_client=s3,
            codebuild_client=FakeCodeBuild("b"),
        )
        assert False, "expected TaskValidationError"
    except TaskValidationError:
        pass


def test_orchestrate_never_writes_empty_build_id():
    """build_id is a GSI key; DynamoDB rejects an empty string for it. When
    CodeBuild returns no id, the field must be absent, not empty."""
    repo = FakeImageRepo()
    s3 = FakeS3(_baseline())

    class NoIdCodeBuild:
        def start_build(self, **kwargs):
            return {"build": {"id": "", "buildStatus": "IN_PROGRESS"}}

    handle_image_build_request(
        event=_event(
            {
                "action": "orchestrate",
                "tag": "v10",
                "general_skill": "g",
                "domain_skills": [
                    {"file_name": "timeseries-forecast-train.md", "content": "d"}
                ],
            }
        ),
        source_bucket="src-bucket",
        project_name="proj",
        repository=repo,
        s3_client=s3,
        codebuild_client=NoIdCodeBuild(),
    )
    row = repo.items["v10"]
    assert "build_id" not in row or row["build_id"]  # never an empty string


def test_list_images_reads_from_table():
    repo = FakeImageRepo()
    repo.put_image(
        {
            "tag": "v1",
            "status": IMAGE_STATUS_SUCCEEDED,
            "size": 1234,
            "build_id": "b1",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-01T00:05:00Z",
        }
    )
    result = list_images(repository=repo)
    assert result["images"][0]["tag"] == "v1"
    assert result["images"][0]["status"] == IMAGE_STATUS_SUCCEEDED
    assert result["images"][0]["size"] == 1234
    # task_type is no longer part of the image record/listing.
    assert "task_type" not in result["images"][0]


def test_oncomplete_marks_succeeded(monkeypatch):
    repo = FakeImageRepo()
    repo.put_image(
        {
            "tag": "v2",
            "status": IMAGE_STATUS_BUILDING,
            "size": 0,
            "build_id": "proj:build-777",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-01T00:00:00Z",
        }
    )
    monkeypatch.setattr(image_oncomplete, "ImageRepository", lambda **_: repo)
    monkeypatch.setattr(image_oncomplete, "_resolve_image_size", lambda tag: 5555)
    monkeypatch.setenv("IMAGE_TABLE_NAME", "t")

    event = {
        "detail": {"build-id": "proj:build-777", "build-status": "SUCCEEDED"}
    }
    response = image_oncomplete.handler(event, None)
    assert response["body"] == IMAGE_STATUS_SUCCEEDED
    assert repo.items["v2"]["status"] == IMAGE_STATUS_SUCCEEDED
    assert repo.items["v2"]["size"] == 5555


def test_oncomplete_matches_full_arn_build_id(monkeypatch):
    """In aws-cn the CodeBuild state-change event carries build-id as a full ARN
    while the stored build_id is the short form; the handler must still match."""
    repo = FakeImageRepo()
    repo.put_image(
        {
            "tag": "v2",
            "status": IMAGE_STATUS_BUILDING,
            "size": 0,
            "build_id": "autogluon-agent-image-build:25dd102d-40c4",
            "created_at": "2026-07-14T00:00:00Z",
            "updated_at": "2026-07-14T00:00:00Z",
        }
    )
    monkeypatch.setattr(image_oncomplete, "ImageRepository", lambda **_: repo)
    monkeypatch.setattr(image_oncomplete, "_resolve_image_size", lambda tag: 4242)
    monkeypatch.setenv("IMAGE_TABLE_NAME", "t")

    event = {
        "detail": {
            "build-id": (
                "arn:aws-cn:codebuild:cn-northwest-1:123456789012:build/"
                "autogluon-agent-image-build:25dd102d-40c4"
            ),
            "build-status": "SUCCEEDED",
        }
    }
    response = image_oncomplete.handler(event, None)
    assert response["body"] == IMAGE_STATUS_SUCCEEDED
    assert repo.items["v2"]["status"] == IMAGE_STATUS_SUCCEEDED
    assert repo.items["v2"]["size"] == 4242


def test_oncomplete_marks_failed(monkeypatch):
    repo = FakeImageRepo()
    repo.put_image(
        {
            "tag": "v3",
            "status": IMAGE_STATUS_BUILDING,
            "size": 0,
            "build_id": "proj:build-888",
            "created_at": "2026-07-01T00:00:00Z",
            "updated_at": "2026-07-01T00:00:00Z",
        }
    )
    monkeypatch.setattr(image_oncomplete, "ImageRepository", lambda **_: repo)
    monkeypatch.setenv("IMAGE_TABLE_NAME", "t")

    event = {"detail": {"build-id": "proj:build-888", "build-status": "FAILED"}}
    response = image_oncomplete.handler(event, None)
    assert response["body"] == IMAGE_STATUS_FAILED
    assert repo.items["v3"]["status"] == IMAGE_STATUS_FAILED


def test_oncomplete_ignores_unknown_build(monkeypatch):
    repo = FakeImageRepo()
    monkeypatch.setattr(image_oncomplete, "ImageRepository", lambda **_: repo)
    monkeypatch.setenv("IMAGE_TABLE_NAME", "t")
    event = {"detail": {"build-id": "nope", "build-status": "SUCCEEDED"}}
    response = image_oncomplete.handler(event, None)
    assert response["body"] == "ignored"
