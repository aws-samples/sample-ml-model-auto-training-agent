# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import base64
import io
import json
import re
import zipfile
from typing import Any

import boto3

from api.image_repository import (
    IMAGE_STATUS_BUILDING,
    ImageRepository,
)
from api.validators import TaskValidationError
from shared.time_utils import utc_timestamp


IMAGE_TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Uploaded/repackaged agent build sources live under this prefix in the
# build-source bucket. CodeBuild reads its source from here.
BUILD_SOURCE_PREFIX = "agent-uploads/"
MAX_PRESIGN_EXPIRY = 3600

# The agent code baseline is deployed (by CDK BucketDeployment) under this
# prefix in the build-source bucket. Orchestration downloads it, overlays the
# customer-edited skills, and repackages it into a build source zip — the
# customer never uploads a zip.
DEFAULT_BASELINE_PREFIX = "agent-baseline/training_runtime/"

# The general (task-agnostic) skill is always this file. Every OTHER *.md file
# under skills/ in the baseline is a domain skill; the built image bakes in ALL
# of them so a single image serves every task type. The runtime still selects
# one domain skill per run via DOMAIN_SKILL_NAME (see training_runtime/config.py).
GENERAL_SKILL_NAME = "general-agent-spec.md"
# Skills live under this subdirectory inside the baseline.
BASELINE_SKILLS_SUBDIR = "skills/"
# Upper bound on a single skill document to keep the repackaged zip small and
# reject pathological input.
MAX_SKILL_CHARS = 200_000


def _s3() -> Any:
    return boto3.client("s3")


def _codebuild() -> Any:
    return boto3.client("codebuild")


def list_images(
    *,
    repository: ImageRepository,
) -> dict[str, Any]:
    """List images from the image DynamoDB table (the source of truth). Rows are
    created in BUILDING state at build start and reconciled to SUCCEEDED/FAILED
    by the CodeBuild completion handler."""
    records = repository.list_images()
    images = [
        {
            "tag": record.get("tag", ""),
            "size": int(record.get("size", 0) or 0),
            "status": record.get("status", ""),
            "build_id": record.get("build_id", ""),
            "skills": record.get("skills", ""),
            "created_at": record.get("created_at", ""),
            "updated_at": record.get("updated_at", ""),
        }
        for record in records
    ]
    return {"images": images}


def _validate_tag(raw_tag: str | None) -> str:
    tag = (raw_tag or "").strip()
    if not IMAGE_TAG_PATTERN.match(tag):
        raise TaskValidationError("Image tag contains unsupported characters")
    return tag


def _list_baseline_domain_skill_names(
    *,
    s3_client: Any,
    source_bucket: str,
    baseline_prefix: str,
) -> list[str]:
    """List every domain skill file name under the baseline skills/ directory
    (all *.md except the general skill), sorted for stable ordering. These are
    all baked into the image so one image serves every task type."""
    skills_prefix = f"{baseline_prefix}{BASELINE_SKILLS_SUBDIR}"
    paginator = s3_client.get_paginator("list_objects_v2")
    names: set[str] = set()
    for page in paginator.paginate(Bucket=source_bucket, Prefix=skills_prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            file_name = key[len(skills_prefix):]
            # Only files directly under skills/ (no nested dirs), *.md only.
            if "/" in file_name or not file_name.endswith(".md"):
                continue
            if file_name == GENERAL_SKILL_NAME:
                continue
            names.add(file_name)
    if not names:
        raise TaskValidationError(
            "No domain skills found in the agent baseline; is the stack deployed?"
        )
    return sorted(names)


def _validate_skill(raw: str | None, label: str) -> str:
    text = raw if isinstance(raw, str) else ""
    if not text.strip():
        raise TaskValidationError(f"{label} skill content must not be empty")
    if len(text) > MAX_SKILL_CHARS:
        raise TaskValidationError(
            f"{label} skill content exceeds {MAX_SKILL_CHARS} characters"
        )
    return text


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode("utf-8")
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise TaskValidationError("Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise TaskValidationError("Request body must be a JSON object")
    return payload


def _read_baseline_skill(
    *,
    s3_client: Any,
    source_bucket: str,
    baseline_prefix: str,
    skill_name: str,
) -> str:
    """Read one baked-in skill document from the deployed agent baseline."""
    key = f"{baseline_prefix}skills/{skill_name}"
    try:
        body = s3_client.get_object(Bucket=source_bucket, Key=key)["Body"].read()
    except Exception as exc:  # noqa: BLE001 - surface as a validation-style error
        raise TaskValidationError(
            f"Unable to read baseline skill {skill_name}; is the agent baseline deployed?"
        ) from exc
    return body.decode("utf-8")


def get_skill_templates(
    *,
    source_bucket: str,
    baseline_prefix: str,
    s3_client: Any | None = None,
) -> dict[str, Any]:
    """Return the default general skill text plus EVERY domain skill so the UI
    can pre-fill the editors. The general skill is task-agnostic; the domain
    skills are all baked into a single image and the UI lets the user switch
    between them. Templates come straight from the deployed agent baseline
    (single source of truth)."""
    client = s3_client or _s3()
    general_text = _read_baseline_skill(
        s3_client=client,
        source_bucket=source_bucket,
        baseline_prefix=baseline_prefix,
        skill_name=GENERAL_SKILL_NAME,
    )
    domain_names = _list_baseline_domain_skill_names(
        s3_client=client,
        source_bucket=source_bucket,
        baseline_prefix=baseline_prefix,
    )
    domain_skills = [
        {
            "file_name": name,
            "content": _read_baseline_skill(
                s3_client=client,
                source_bucket=source_bucket,
                baseline_prefix=baseline_prefix,
                skill_name=name,
            ),
        }
        for name in domain_names
    ]
    return {
        "general_skill_name": GENERAL_SKILL_NAME,
        "general_skill": general_text,
        "domain_skills": domain_skills,
    }


def _repackage_baseline(
    *,
    s3_client: Any,
    source_bucket: str,
    baseline_prefix: str,
    overrides: dict[str, str],
) -> bytes:
    """Download the agent baseline and repackage it into a build-source zip,
    overlaying the customer-edited skills. Zip entries keep the ``training_runtime/``
    top-level directory so the CodeBuild buildspec finds training_runtime/Dockerfile."""
    paginator = s3_client.get_paginator("list_objects_v2")
    buffer = io.BytesIO()
    written: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for page in paginator.paginate(Bucket=source_bucket, Prefix=baseline_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue
                rel = key[len(baseline_prefix):]
                if not rel:
                    continue
                arcname = f"training_runtime/{rel}"
                if arcname in overrides:
                    archive.writestr(arcname, overrides[arcname])
                else:
                    body = s3_client.get_object(Bucket=source_bucket, Key=key)[
                        "Body"
                    ].read()
                    archive.writestr(arcname, body)
                written.add(arcname)
        # Ensure the overridden skills are present even if the baseline lacked
        # them (e.g. a newly introduced domain skill file name).
        for arcname, content in overrides.items():
            if arcname not in written:
                archive.writestr(arcname, content)
                written.add(arcname)
    if not written:
        raise TaskValidationError(
            "Agent baseline is empty; cannot orchestrate build. Redeploy the stack."
        )
    return buffer.getvalue()


def _start_build_row(
    *,
    project_name: str,
    source_bucket: str,
    tag: str,
    source_key: str,
    repository: ImageRepository,
    extra_fields: dict[str, Any] | None,
    codebuild_client: Any | None,
) -> dict[str, Any]:
    """Write the BUILDING row, start CodeBuild, then store the run id.

    The image table row is written FIRST with status BUILDING (so the tag shows
    up immediately as in-progress), then CodeBuild is started, then the row is
    updated with the CodeBuild run id. The completion handler later flips the
    status to SUCCEEDED/FAILED via the build-id-index."""
    client = codebuild_client or _codebuild()
    now = utc_timestamp()
    # NOTE: build_id is intentionally omitted here. It is the partition key of
    # the build-id-index GSI, and DynamoDB rejects an empty string for an
    # indexed key attribute. It is added below once CodeBuild returns a real id.
    building_record: dict[str, Any] = {
        "tag": tag,
        "status": IMAGE_STATUS_BUILDING,
        "size": 0,
        "source_key": source_key,
        "error_message": "",
        "created_at": now,
        "updated_at": now,
    }
    if extra_fields:
        building_record.update(extra_fields)
    repository.put_image(building_record)

    response = client.start_build(
        projectName=project_name,
        sourceLocationOverride=f"{source_bucket}/{source_key}",
        sourceTypeOverride="S3",
        environmentVariablesOverride=[
            {"name": "IMAGE_TAG", "value": tag, "type": "PLAINTEXT"},
        ],
    )
    build = response.get("build", {})
    build_id = build.get("id") or ""

    # Only set build_id when CodeBuild returned a non-empty id (GSI key cannot
    # be an empty string).
    if build_id:
        building_record["build_id"] = build_id
    building_record["updated_at"] = utc_timestamp()
    repository.put_image(building_record)

    return {
        "build_id": build_id,
        "build_status": build.get("buildStatus"),
        "tag": tag,
    }


def _validate_domain_skills(raw: Any) -> list[dict[str, str]]:
    """Validate the domain_skills payload: a non-empty list of
    ``{"file_name", "content"}`` objects, each a *.md file with non-empty text.
    Every task type's domain skill is edited and baked into one image."""
    if not isinstance(raw, list) or not raw:
        raise TaskValidationError("domain_skills must be a non-empty list")
    validated: list[dict[str, str]] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise TaskValidationError("Each domain skill must be an object")
        file_name = (entry.get("file_name") or "").strip()
        if not file_name.endswith(".md") or "/" in file_name:
            raise TaskValidationError(
                f"Invalid domain skill file name: {file_name or '(empty)'}"
            )
        if file_name == GENERAL_SKILL_NAME:
            raise TaskValidationError(
                f"{file_name} is the general skill, not a domain skill"
            )
        if file_name in seen:
            raise TaskValidationError(f"Duplicate domain skill: {file_name}")
        seen.add(file_name)
        content = _validate_skill(entry.get("content"), f"Domain ({file_name})")
        validated.append({"file_name": file_name, "content": content})
    return validated


def orchestrate_image_build(
    *,
    project_name: str,
    source_bucket: str,
    baseline_prefix: str,
    tag: str,
    general_skill: str,
    domain_skills: Any,
    repository: ImageRepository,
    s3_client: Any | None = None,
    codebuild_client: Any | None = None,
) -> dict[str, Any]:
    """Orchestrate a training-agent image: take the deployed agent code baseline,
    overlay the customer-edited general skill plus ALL domain skills (one per
    task type), repackage into a build-source zip, and start CodeBuild. The
    resulting image bakes in every domain skill. The customer never uploads a
    zip. The skills are also persisted (JSON) on the image row."""
    s3_client = s3_client or _s3()
    safe_tag = _validate_tag(tag)
    general_text = _validate_skill(general_skill, "General")
    validated_domains = _validate_domain_skills(domain_skills)

    overrides = {
        f"training_runtime/skills/{GENERAL_SKILL_NAME}": general_text,
    }
    for skill in validated_domains:
        overrides[f"training_runtime/skills/{skill['file_name']}"] = skill["content"]

    zip_bytes = _repackage_baseline(
        s3_client=s3_client,
        source_bucket=source_bucket,
        baseline_prefix=baseline_prefix,
        overrides=overrides,
    )
    source_key = f"{BUILD_SOURCE_PREFIX}{safe_tag}/source.zip"
    s3_client.put_object(
        Bucket=source_bucket,
        Key=source_key,
        Body=zip_bytes,
        ContentType="application/zip",
    )

    skills_json = json.dumps(
        {
            "general_skill_name": GENERAL_SKILL_NAME,
            "general_skill": general_text,
            "domain_skills": validated_domains,
        },
        ensure_ascii=False,
    )
    return _start_build_row(
        project_name=project_name,
        source_bucket=source_bucket,
        tag=safe_tag,
        source_key=source_key,
        repository=repository,
        extra_fields={"skills": skills_json},
        codebuild_client=codebuild_client,
    )


def handle_image_build_request(
    *,
    event: dict[str, Any],
    source_bucket: str,
    project_name: str,
    repository: ImageRepository,
    baseline_prefix: str = DEFAULT_BASELINE_PREFIX,
    s3_client: Any | None = None,
    codebuild_client: Any | None = None,
) -> tuple[int, dict[str, Any]]:
    """Orchestration contract (the customer never uploads a zip):
    - action=skill-templates -> return the default general skill text plus every
      domain skill, so the UI can pre-fill the editors (one image serves all
      task types)
    - action=orchestrate     -> overlay the edited general skill and all domain
      skills onto the agent code baseline, repackage, and start CodeBuild
    """
    payload = _parse_body(event)
    action = (payload.get("action") or "orchestrate").strip()

    if action == "skill-templates":
        return 200, get_skill_templates(
            source_bucket=source_bucket,
            baseline_prefix=baseline_prefix,
            s3_client=s3_client,
        )
    if action == "orchestrate":
        return 202, orchestrate_image_build(
            project_name=project_name,
            source_bucket=source_bucket,
            baseline_prefix=baseline_prefix,
            tag=payload.get("tag"),
            general_skill=payload.get("general_skill"),
            domain_skills=payload.get("domain_skills"),
            repository=repository,
            s3_client=s3_client,
            codebuild_client=codebuild_client,
        )
    raise TaskValidationError(
        "Unsupported action; expected orchestrate or skill-templates"
    )
