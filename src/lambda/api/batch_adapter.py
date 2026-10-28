# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import os
import re
import time
from typing import Any

import boto3

from shared.constants import (
    DEFAULT_SKILLS_PREFIX,
)
from shared.task_types import get_task_type
from shared.executions import logs_prefix as _logs_prefix
from shared.executions import training_output_prefix as _training_output_prefix


class BatchSubmissionError(RuntimeError):
    pass


def _derive_package_root(data_location: str) -> str:
    """Map a submitted data_location to the evaluation package root prefix.

    The eval package is laid out as ``<root>/data/...`` and ``<root>/eval/...``.
    Operators usually point data_location at ``<root>/data/`` (or ``<root>/``),
    so strip a trailing slash and a trailing ``data`` segment to recover the
    package root used to locate the ground truth and compare script.
    """
    cleaned = (data_location or "").strip()
    # Ignore s3:// scheme/bucket if present; verification keys are bucket-relative.
    if cleaned.startswith("s3://"):
        without_scheme = cleaned[len("s3://"):]
        cleaned = without_scheme.split("/", 1)[1] if "/" in without_scheme else ""
    cleaned = cleaned.strip("/")
    if not cleaned:
        return ""
    segments = cleaned.split("/")
    if segments and segments[-1] == "data":
        segments = segments[:-1]
    return "/".join(segments)


class BatchAdapter:
    def __init__(
        self,
        batch_client: Any | None = None,
        sleeper: Any | None = None,
    ) -> None:
        self._batch_client = batch_client or boto3.client("batch")
        self._sleep = sleeper or time.sleep

    def terminate_task(self, batch_job_id: str, reason: str = "Cancelled by operator") -> None:
        # terminate_job cancels both queued (RUNNABLE) and running jobs.
        self._call_with_retries(
            lambda: self._batch_client.terminate_job(
                jobId=batch_job_id,
                reason=reason,
            )
        )

    def submit_task(
        self,
        task_record: dict[str, Any],
        bucket_name: str,
        job_queue_arn: str,
        job_definition_arn: str,
        image_repo_uri: str,
        max_attempts: int = 3,
        execution_id: str | None = None,
    ) -> dict[str, Any]:
        task_id = task_record["task_id"]
        image_tag = task_record["resolved_image_tag"]
        resolved_job_definition_arn = self._resolve_job_definition_arn(
            base_job_definition_arn=job_definition_arn,
            image_repo_uri=image_repo_uri,
            image_tag=image_tag,
        )
        # Derive the evaluation-package keys from the submitted data_location so
        # independent verification can locate the ground truth + compare script
        # regardless of where the operator uploaded the package (e.g. under a
        # data/ prefix). data_location like "data/<pkg>/data/" -> package root
        # "data/<pkg>"; ground truth at "<root>/data/<groundTruthFile>" and
        # compare script at "<root>/eval/<compareScript>".
        #
        # The per-task-type file names and domain skill come from the task-type
        # registry (cdk.json context "taskTypes", injected as TASK_TYPES_JSON);
        # get_task_type falls back to the default task type when unconfigured.
        package_root = _derive_package_root(task_record["data_location"])
        task_type = get_task_type(task_record.get("task_type", "")) or {}
        # Independent anti-cheat verification is per task type (default on). When
        # off, the run has no ground-truth/compare package — success is decided
        # by "produced predictions + non-empty model" — so those keys are NOT
        # injected and the container falls back to that lenient check.
        enforce_verification = bool(task_type.get("enforceVerification", True))
        # Number of prediction*.csv files the run must produce to be considered
        # successful (see verification._collect_prediction_files). Training task
        # types leave this at the default 1. FILE-TRANSFER types (byo_deploy)
        # must set it to 0 in cdk.json: they deliberately do not run inference —
        # their skill forbids it, because any metric computed there would be on
        # data the external model was trained on, and would be mistaken for real
        # accuracy. Without this, such a run fails verification with
        # "Missing required artifacts: prediction csv" on every attempt.
        required_prediction_count = task_type.get("requiredPredictionCount")
        environment = [
            {"name": "TASK_ID", "value": task_id},
            {"name": "BUCKET", "value": bucket_name},
            {"name": "DATA_LOCATION", "value": task_record["data_location"]},
            {
                "name": "ENFORCE_VERIFICATION",
                "value": "1" if enforce_verification else "0",
            },
            {"name": "SKILLS_PREFIX", "value": DEFAULT_SKILLS_PREFIX},
            {
                "name": "LOGS_PREFIX",
                # For periodic executions this carries an execution-id directory
                # layer (logs/<task>/<exec>/); one-off tasks stay flat.
                "value": _logs_prefix(task_id, execution_id),
            },
            {
                "name": "OUTPUT_PREFIX",
                "value": _training_output_prefix(task_id, execution_id),
            },
            # TRAINING_PROMPT is intentionally a neutral constant, NOT the task
            # comment. The training agent's flow relies only on the two skills;
            # the user's comment is a human-facing note and must not enter the
            # agent prompt. This constant is sent purely to satisfy the current
            # runtime image's contract (which still lists TRAINING_PROMPT as a
            # required env var); rebuilt images treat it as optional/ignored.
            {
                "name": "TRAINING_PROMPT",
                "value": "Run the training per the provided skills.",
            },
            {"name": "AWS_REGION", "value": os.environ.get("AWS_REGION", "")},
        ]
        # Only sent when the registry configures it, so task types that omit it
        # keep the runtime's default of 1.
        if required_prediction_count is not None:
            environment.append(
                {
                    "name": "REQUIRED_PREDICTION_COUNT",
                    "value": str(int(required_prediction_count)),
                }
            )
        # Ground-truth + compare-script keys only matter when verification is
        # on. Derive them from the data package: <root>/data/<groundTruthFile>
        # and <root>/eval/<compareScript>.
        if enforce_verification:
            ground_truth_file = (
                task_type.get("groundTruthFile") or "ground_truth_hourly.csv"
            )
            compare_script = task_type.get("compareScript") or "compare_timeseries.py"
            environment.extend(
                [
                    {
                        "name": "GROUND_TRUTH_KEY",
                        "value": f"{package_root}/data/{ground_truth_file}",
                    },
                    {
                        "name": "COMPARE_SCRIPT_KEY",
                        "value": f"{package_root}/eval/{compare_script}",
                    },
                ]
            )
        # Select the domain skill for this task type. The image bakes in every
        # skill; DOMAIN_SKILL_NAME picks which one drives THIS run. Only sent
        # when the registry names a skill, so unconfigured task types keep the
        # image's default (timeseries-forecast-train.md).
        domain_skill = task_type.get("skill")
        if domain_skill:
            environment.append(
                {"name": "DOMAIN_SKILL_NAME", "value": domain_skill}
            )
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                response = self._batch_client.submit_job(
                    jobName=f"training-{task_id}",
                    jobQueue=job_queue_arn,
                    jobDefinition=resolved_job_definition_arn,
                    containerOverrides={
                        "environment": environment,
                    },
                )
                return {
                    "job_id": response["jobId"],
                    "job_name": response["jobName"],
                    "job_definition_arn": resolved_job_definition_arn,
                }
            except Exception as exc:  # pragma: no cover - boto3 specific failures
                last_error = exc
                if not self._is_retryable_exception(exc) or attempt == max_attempts - 1:
                    break
                self._sleep(2**attempt)
        raise BatchSubmissionError("Unable to submit AWS Batch job") from last_error

    def _resolve_job_definition_arn(
        self,
        base_job_definition_arn: str,
        image_repo_uri: str,
        image_tag: str,
    ) -> str:
        if image_tag == "stable":
            return base_job_definition_arn

        base_job_definition = self._call_with_retries(
            lambda: self._batch_client.describe_job_definitions(
                jobDefinitions=[base_job_definition_arn]
            )
        )
        definitions = base_job_definition.get("jobDefinitions", [])
        if not definitions:
            raise BatchSubmissionError("Base job definition could not be described")
        source = definitions[0]
        target_image = f"{image_repo_uri}:{image_tag}"
        definition_name = self._definition_name_for_tag(
            source["jobDefinitionName"], image_tag
        )
        existing = self._call_with_retries(
            lambda: self._batch_client.describe_job_definitions(
                jobDefinitionName=definition_name,
                status="ACTIVE",
            )
        )
        for definition in existing.get("jobDefinitions", []):
            container_image = definition.get("containerProperties", {}).get("image")
            if container_image == target_image:
                return definition["jobDefinitionArn"]

        container_properties = dict(source["containerProperties"])
        container_properties["image"] = target_image
        register_kwargs = {
            "jobDefinitionName": definition_name,
            "type": source["type"],
            "containerProperties": container_properties,
            "platformCapabilities": source.get("platformCapabilities", ["EC2"]),
        }
        if "retryStrategy" in source:
            register_kwargs["retryStrategy"] = source["retryStrategy"]
        if "timeout" in source:
            register_kwargs["timeout"] = source["timeout"]

        response = self._call_with_retries(
            lambda: self._batch_client.register_job_definition(**register_kwargs)
        )
        return response["jobDefinitionArn"]

    def _call_with_retries(self, callback: Any, max_attempts: int = 3) -> Any:
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                return callback()
            except Exception as exc:  # pragma: no cover - boto3 specific failures
                last_error = exc
                if not self._is_retryable_exception(exc) or attempt == max_attempts - 1:
                    break
                self._sleep(2**attempt)
        if last_error is None:
            raise BatchSubmissionError("AWS Batch call did not execute")
        raise last_error

    def _is_retryable_exception(self, exc: Exception) -> bool:
        response = getattr(exc, "response", None) or {}
        error = response.get("Error", {})
        code = error.get("Code", "")
        if code in {
            "ThrottlingException",
            "TooManyRequestsException",
            "RequestLimitExceeded",
            "InternalFailure",
            "ServiceUnavailableException",
        }:
            return True
        status_code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return isinstance(status_code, int) and status_code >= 500

    def _definition_name_for_tag(self, base_name: str, image_tag: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9_-]+", "-", image_tag).strip("-")
        return f"{base_name}-{sanitized or 'tagged'}"
