# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlparse


class ContractError(ValueError):
    """Raised when the canonical Batch runtime contract is invalid."""


# TRAINING_PROMPT is intentionally NOT required: the training agent's flow
# relies only on the two skills, so the (optional) task comment does not drive
# the prompt. It is retained as an optional field for backward compatibility.
_REQUIRED_FIELDS = (
    "TASK_ID",
    "BUCKET",
    "DATA_LOCATION",
    "SKILLS_PREFIX",
    "LOGS_PREFIX",
    "OUTPUT_PREFIX",
    "AWS_REGION",
)


def _strip(value: str | None) -> str:
    return (value or "").strip()


def _ensure_no_traversal(value: str, field_name: str) -> str:
    if ".." in value:
        raise ContractError(f"{field_name} must not contain path traversal segments")
    return value


def join_bucket_path(bucket: str, reference: str) -> str:
    if reference.startswith("s3://"):
        return reference
    normalized = reference.lstrip("/")
    return f"s3://{bucket}/{normalized}"


def split_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ContractError(f"Invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


@dataclass(frozen=True)
class RuntimeContract:
    task_id: str
    bucket: str
    data_location: str
    skills_prefix: str
    logs_prefix: str
    output_prefix: str
    training_prompt: str
    aws_region: str

    @property
    def data_uri(self) -> str:
        return join_bucket_path(self.bucket, self.data_location)

    @property
    def skills_uri(self) -> str:
        return join_bucket_path(self.bucket, self.skills_prefix)

    @property
    def logs_uri(self) -> str:
        return join_bucket_path(self.bucket, self.logs_prefix)

    @property
    def output_uri(self) -> str:
        return join_bucket_path(self.bucket, self.output_prefix)


def load_runtime_contract(env: dict[str, str] | None = None) -> RuntimeContract:
    env_map = env or dict(os.environ)
    missing = [field for field in _REQUIRED_FIELDS if not _strip(env_map.get(field))]
    if missing:
        raise ContractError(f"Missing required runtime fields: {', '.join(missing)}")

    contract = RuntimeContract(
        task_id=_strip(env_map["TASK_ID"]),
        bucket=_strip(env_map["BUCKET"]),
        data_location=_ensure_no_traversal(_strip(env_map["DATA_LOCATION"]), "DATA_LOCATION"),
        skills_prefix=_ensure_no_traversal(_strip(env_map["SKILLS_PREFIX"]), "SKILLS_PREFIX"),
        logs_prefix=_ensure_no_traversal(_strip(env_map["LOGS_PREFIX"]), "LOGS_PREFIX"),
        output_prefix=_ensure_no_traversal(_strip(env_map["OUTPUT_PREFIX"]), "OUTPUT_PREFIX"),
        training_prompt=_strip(env_map.get("TRAINING_PROMPT")),
        aws_region=_strip(env_map["AWS_REGION"]),
    )

    if not contract.bucket:
        raise ContractError("BUCKET must not be empty")
    split_s3_uri(contract.data_uri)
    split_s3_uri(contract.skills_uri)
    split_s3_uri(contract.logs_uri)
    split_s3_uri(contract.output_uri)
    return contract
