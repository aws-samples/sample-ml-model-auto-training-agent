# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import base64
import json
import os
import re
from typing import Any

import boto3
from botocore.config import Config

from api.validators import TaskValidationError


# Object keys are confined to safe characters and one of the allowed areas to
# reject path traversal. The four S3 areas (see shared/constants.py):
#   data/              -> training input datasets       (browsable + uploadable)
#   inference_data/    -> prediction input datasets      (browsable + uploadable)
#   output/            -> training run artifacts         (browsable only)
#   inference_output/  -> prediction result files        (browsable only)
DATA_PREFIX = "data/"
# Listing is allowed across all four areas so the UI can browse each one.
LIST_ALLOWED_PREFIXES = (
    "data/",
    "inference_data/",
    "output/",
    "inference_output/",
)
# Uploads are restricted to the two input areas; output areas are written only
# by the training/inference backends, never by user uploads.
UPLOAD_ALLOWED_PREFIXES = ("data/", "inference_data/")
# Deletion is likewise restricted to the two user-writable input areas. Output
# areas (output/, inference_output/) hold training/inference artifacts and are
# read-only from the UI, so a stray click can never destroy a model or result.
DELETE_ALLOWED_PREFIXES = ("data/", "inference_data/")
KEY_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_PRESIGN_EXPIRY = 3600
# Max object size returned inline (base64) through the API. China blocks
# browser access to presigned S3 URLs (未备案 domain edge rule), so downloads
# are proxied through the signed execute-api instead of a presigned S3 URL.
# API Gateway caps the response payload at ~10MB and base64 inflates by ~33%,
# so keep the raw object well under that. This is only used for small result
# files (predictions / evaluation reports), not multi-hundred-MB datasets.
MAX_INLINE_DOWNLOAD_BYTES = 6 * 1024 * 1024


def _client() -> Any:
    # SigV4 + the regional endpoint. The default (SigV2 on the global
    # s3.amazonaws.com host) answers 307 Temporary Redirect for a freshly
    # created bucket outside us-east-1, which a browser PUT cannot follow.
    region = os.environ.get("AWS_REGION")
    return boto3.client(
        "s3",
        region_name=region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def _normalize_prefix(
    raw_prefix: str | None,
    allowed_prefixes: tuple[str, ...] = LIST_ALLOWED_PREFIXES,
) -> str:
    prefix = (raw_prefix or DATA_PREFIX).strip()
    if not prefix:
        prefix = DATA_PREFIX
    if ".." in prefix:
        raise TaskValidationError("Prefix must not contain parent traversal")
    if not any(prefix.startswith(root) for root in allowed_prefixes):
        raise TaskValidationError(
            "Prefix must stay within an allowed data area"
        )
    if not prefix.endswith("/"):
        prefix = prefix + "/"
    return prefix


def list_data_files(
    *,
    bucket_name: str,
    prefix: str | None,
    s3_client: Any | None = None,
) -> dict[str, Any]:
    client = s3_client or _client()
    normalized = _normalize_prefix(prefix)
    paginator = client.get_paginator("list_objects_v2")
    folders: list[str] = []
    files: list[dict[str, Any]] = []
    for page in paginator.paginate(
        Bucket=bucket_name, Prefix=normalized, Delimiter="/"
    ):
        for common_prefix in page.get("CommonPrefixes", []):
            folder_key = common_prefix.get("Prefix", "")
            if folder_key and folder_key != normalized:
                folders.append(folder_key)
        for item in page.get("Contents", []):
            key = item.get("Key", "")
            # Skip the folder placeholder object that equals the prefix itself.
            if not key or key == normalized:
                continue
            files.append(
                {
                    "key": key,
                    "name": key[len(normalized):],
                    "size": item.get("Size", 0),
                    "last_modified": item.get("LastModified"),
                }
            )
    return {
        "prefix": normalized,
        "folders": sorted(set(folders)),
        "files": sorted(files, key=lambda entry: entry["name"]),
    }


def _validate_object_key(prefix: str, file_name: str) -> str:
    name = (file_name or "").strip()
    if not name or "/" in name or ".." in name:
        raise TaskValidationError("File name must be a single safe path segment")
    if not KEY_SEGMENT_PATTERN.match(name):
        raise TaskValidationError("File name contains unsupported characters")
    return f"{prefix}{name}"


def create_upload_url(
    *,
    bucket_name: str,
    event: dict[str, Any],
    s3_client: Any | None = None,
) -> dict[str, Any]:
    client = s3_client or _client()
    payload = _parse_body(event)
    # Uploads may only target the input areas (data/ or inference_data/).
    prefix = _normalize_prefix(
        payload.get("prefix"), allowed_prefixes=UPLOAD_ALLOWED_PREFIXES
    )

    # Optionally place the file inside a (new or existing) child subdirectory.
    subdirectory = (payload.get("subdirectory") or "").strip().strip("/")
    if subdirectory:
        for segment in subdirectory.split("/"):
            if not KEY_SEGMENT_PATTERN.match(segment):
                raise TaskValidationError("Subdirectory contains unsupported characters")
        prefix = f"{prefix}{subdirectory}/"

    object_key = _validate_object_key(prefix, payload.get("file_name"))
    content_type = (payload.get("content_type") or "application/octet-stream").strip()

    upload_url = client.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": bucket_name,
            "Key": object_key,
            "ContentType": content_type,
        },
        ExpiresIn=MAX_PRESIGN_EXPIRY,
    )
    return {
        "upload_url": upload_url,
        "key": object_key,
        "content_type": content_type,
        "expires_in": MAX_PRESIGN_EXPIRY,
    }


def _validate_full_key(
    raw_key: str | None,
    allowed_prefixes: tuple[str, ...],
) -> str:
    """Validate a full object key (prefix + optional subdirs + file name) for
    download/delete. Confines the key to an allowed area and rejects traversal."""
    key = (raw_key or "").strip()
    if not key:
        raise TaskValidationError("Object key must not be empty")
    if ".." in key or key.startswith("/") or key.endswith("/"):
        raise TaskValidationError("Object key is not a valid file path")
    if not any(key.startswith(root) for root in allowed_prefixes):
        raise TaskValidationError("Object key must stay within an allowed data area")
    # Every path segment must be a safe token (letters/digits/._-), so no control
    # characters, spaces, or shell-hostile names slip through.
    for segment in key.split("/"):
        if not KEY_SEGMENT_PATTERN.match(segment):
            raise TaskValidationError("Object key contains unsupported characters")
    return key


def create_download_url(
    *,
    bucket_name: str,
    key: str | None,
    s3_client: Any | None = None,
) -> dict[str, Any]:
    """Return the object's bytes inline as base64 so the browser can download it
    WITHOUT hitting a presigned S3 URL (China blocks browser access to the
    未备案 S3 domain). The download is proxied through the signed API. Only small
    result files are supported; larger objects are rejected with a clear error."""
    client = s3_client or _client()
    object_key = _validate_full_key(key, LIST_ALLOWED_PREFIXES)
    response = client.get_object(Bucket=bucket_name, Key=object_key)
    body = response["Body"].read()
    if len(body) > MAX_INLINE_DOWNLOAD_BYTES:
        raise TaskValidationError(
            f"文件过大（{len(body) // (1024 * 1024)} MB），无法在线下载；"
            f"仅支持小于 {MAX_INLINE_DOWNLOAD_BYTES // (1024 * 1024)} MB 的结果文件"
        )
    file_name = object_key.rsplit("/", 1)[-1] or "download"
    return {
        "key": object_key,
        "file_name": file_name,
        "content_type": response.get("ContentType") or "application/octet-stream",
        "content_base64": base64.b64encode(body).decode("ascii"),
        "size": len(body),
    }


def delete_data_file(
    *,
    bucket_name: str,
    event: dict[str, Any],
    s3_client: Any | None = None,
) -> dict[str, Any]:
    """Delete a single object from a user-writable input area (data/ or
    inference_data/). Output areas are not deletable from the UI."""
    client = s3_client or _client()
    payload = _parse_body(event)
    object_key = _validate_full_key(payload.get("key"), DELETE_ALLOWED_PREFIXES)
    client.delete_object(Bucket=bucket_name, Key=object_key)
    return {"deleted": True, "key": object_key}


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
