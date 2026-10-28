# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
import math
from typing import Any

import boto3

from shared.constants import OUTPUT_PREFIX_ROOT, RUNTIME_MANIFEST_FILE_NAME
from shared.task_types import get_task_type


VERIFICATION_ARTIFACT_NAMES = {
    "runner_verification.json",
    "evaluation_report.json",
}
INDEPENDENT_VERIFICATION_SOURCE = "independent"
# Written by the training runtime's fallback path when independent verification
# is DISABLED for the task type (cdk.json taskTypes[].enforceVerification=false,
# e.g. a startup-duration regression). Such runs have no ground truth /
# MAPE threshold, so the deployment gate accepts them on engineering
# completeness (training achieved + a deployable model artifact) instead of
# requiring independent-verification / threshold evidence.
LOCAL_REPORT_SOURCE = "local-report"
VALID_THRESHOLD_DIRECTIONS = {
    "higher_is_better",
    "lower_is_better",
}
DEPLOYABLE_SUFFIXES = (
    ".tar.gz",
    ".tgz",
    ".zip",
    ".joblib",
    ".pkl",
    ".pickle",
    ".onnx",
    ".pt",
    ".bin",
    ".model",
)
NON_DEPLOYABLE_SUFFIXES = (
    ".json",
    ".csv",
    ".jsonl",
    ".txt",
    ".log",
)
# Bring-your-own payloads (a model trained outside this platform, transferred by
# a file-only training task) name their weights whatever the algorithm team used
# — rf.sav, final.dat, ... — which matches neither DEPLOYABLE_SUFFIXES nor the
# "model" substring rule, so the gate would reject an otherwise valid payload.
# Their inference entry point IS a reliable marker, and resolving to it is
# harmless: SageMakerAdapter._package_model_tarball tars EVERY object under the
# artifact's models/ directory, so which single object we return only has to
# identify the right directory.
CUSTOM_INFERENCE_ENTRYPOINTS = ("inference.py",)


class RuntimeManifestError(RuntimeError):
    """Raised when runtime outputs are missing or malformed."""


def runtime_output_prefix(task_record: dict[str, Any]) -> str:
    # Periodic executions carry an ``active_execution_id`` so their artifacts
    # land under output/<task>/<execution>/; one-off tasks stay flat.
    from shared.executions import training_output_prefix

    return training_output_prefix(
        task_record["task_id"], task_record.get("active_execution_id")
    )


def manifest_key_for_task(task_record: dict[str, Any]) -> str:
    return f"{runtime_output_prefix(task_record).rstrip('/')}/{RUNTIME_MANIFEST_FILE_NAME}"


def build_manifest_reference(bucket_name: str, task_record: dict[str, Any]) -> dict[str, str]:
    return {
        "bucket": bucket_name,
        "key": manifest_key_for_task(task_record),
    }


def load_runtime_manifest(
    *,
    bucket_name: str,
    task_record: dict[str, Any],
    s3_client: Any | None = None,
) -> dict[str, Any]:
    client = s3_client or boto3.client("s3")
    manifest_reference = build_manifest_reference(bucket_name, task_record)
    try:
        response = client.get_object(
            Bucket=manifest_reference["bucket"],
            Key=manifest_reference["key"],
        )
    except Exception as exc:  # pragma: no cover - boto3 specific failures
        raise RuntimeManifestError("runtime manifest is unavailable") from exc

    body = response.get("Body")
    payload = body.read() if body is not None else b""
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise RuntimeManifestError("runtime manifest is malformed") from exc

    if not isinstance(manifest, dict):
        raise RuntimeManifestError("runtime manifest must be a JSON object")
    if manifest.get("task_id") != task_record["task_id"]:
        raise RuntimeManifestError("runtime manifest task_id does not match the task record")
    return manifest


def latest_verification_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    attempts = manifest.get("attempts") or []
    if not attempts:
        return {}
    latest_attempt = attempts[-1] or {}
    verification = latest_attempt.get("verification") or {}
    return {
        "achieved": bool(verification.get("achieved")),
        "metric_name": verification.get("metric_name"),
        "metric_value": verification.get("metric_value"),
        "threshold": verification.get("threshold"),
        "threshold_direction": verification.get("threshold_direction"),
        "reason": verification.get("reason"),
        "missing_artifacts": list(verification.get("missing_artifacts") or []),
        "report_path": verification.get("report_path"),
        "source": verification.get("source"),
    }


def resolve_verification_artifact(manifest: dict[str, Any]) -> dict[str, Any] | None:
    published_artifacts = manifest.get("published_artifacts") or []
    for artifact_name in ("runner_verification.json", "evaluation_report.json"):
        for artifact in published_artifacts:
            key = (artifact.get("key") or "").rsplit("/", 1)[-1]
            if key == artifact_name:
                return artifact
    return None


def load_json_artifact(
    *,
    artifact_reference: dict[str, Any],
    s3_client: Any | None = None,
) -> dict[str, Any]:
    client = s3_client or boto3.client("s3")
    bucket = (artifact_reference.get("bucket") or "").strip()
    key = (artifact_reference.get("key") or "").strip()
    if not bucket or not key:
        raise RuntimeManifestError("artifact reference is incomplete")

    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:  # pragma: no cover - boto3 specific failures
        raise RuntimeManifestError("verification artifact is unavailable") from exc

    body = response.get("Body")
    payload = body.read() if body is not None else b""
    try:
        artifact = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise RuntimeManifestError("verification artifact is malformed") from exc

    if not isinstance(artifact, dict):
        raise RuntimeManifestError("verification artifact must be a JSON object")
    return artifact


def resolve_deployable_artifact(manifest: dict[str, Any]) -> dict[str, Any] | None:
    published_artifacts = manifest.get("published_artifacts") or []
    # Pass 1: a recognisable model artifact. Preferred, because it also tells
    # _artifact_type_for_name what kind of model this is.
    for artifact in published_artifacts:
        key = artifact.get("key") or ""
        name = key.rsplit("/", 1)[-1].lower()
        if not key or name == RUNTIME_MANIFEST_FILE_NAME or name in VERIFICATION_ARTIFACT_NAMES:
            continue
        if "prediction" in name or "events" in name or name.endswith(NON_DEPLOYABLE_SUFFIXES):
            continue
        if name.endswith(DEPLOYABLE_SUFFIXES) or "model" in name:
            return {
                **artifact,
                "artifact_type": _artifact_type_for_name(name),
                "output_prefix": manifest.get("output_prefix"),
            }
    # Pass 2: a bring-your-own payload, identified by its inference entry point.
    # Runs only after pass 1 so a normal AutoGluon run keeps resolving to its
    # predictor, never to a stray inference.py.
    for artifact in published_artifacts:
        key = artifact.get("key") or ""
        name = key.rsplit("/", 1)[-1].lower()
        if key and name in CUSTOM_INFERENCE_ENTRYPOINTS:
            return {
                **artifact,
                "artifact_type": "custom-inference-payload",
                "output_prefix": manifest.get("output_prefix"),
            }
    return None


def evaluate_deployment_eligibility(
    *,
    task_record: dict[str, Any],
    manifest: dict[str, Any],
    s3_client: Any | None = None,
) -> dict[str, Any]:
    verification_summary = latest_verification_summary(manifest)
    verification_artifact = resolve_verification_artifact(manifest)
    artifact_reference = resolve_deployable_artifact(manifest)
    target_endpoint = (task_record.get("target_endpoint") or "").strip()
    terminal_outcome = manifest.get("terminal_outcome") or {}
    verification_report: dict[str, Any] | None = None

    reason = ""
    # Task types with independent verification disabled skip the independent-
    # verification / MAPE-threshold / verification-artifact consistency gates
    # (all specific to the ground-truth-based type) and are accepted on engineering completeness: training
    # achieved + a resolvable deployable model + a target endpoint.
    #
    # Two independent signals mark verification as disabled, EITHER suffices:
    #  1. the runtime wrote source "local-report" in the manifest, or
    #  2. the task's configured type has enforceVerification=false (cdk.json
    #     taskTypes, via TASK_TYPES_JSON).
    # Using the task-type config as a fallback means a run does not get wrongly
    # rejected just because the runtime image emitted a slightly different
    # source string — the deployment gate no longer depends solely on that
    # string matching.
    task_type_entry = get_task_type((task_record.get("task_type") or "").strip())
    task_type_verification_off = task_type_entry is not None and not bool(
        task_type_entry.get("enforceVerification", True)
    )
    verification_disabled = (
        verification_summary.get("source") == LOCAL_REPORT_SOURCE
        or task_type_verification_off
    )
    if verification_disabled:
        if not verification_summary:
            reason = "runtime manifest does not contain a latest verification summary"
        elif not bool(terminal_outcome.get("achieved")):
            reason = (
                terminal_outcome.get("reason") or "training did not complete successfully"
            ).strip()
        elif artifact_reference is None:
            reason = "deployable model artifact could not be resolved from published artifacts"
        elif not target_endpoint:
            reason = "target endpoint is missing"
    else:
        consistency_issue = _manifest_verification_consistency_issue(
            terminal_outcome=terminal_outcome,
            verification_summary=verification_summary,
        )
        if consistency_issue:
            reason = consistency_issue
        elif not bool(terminal_outcome.get("achieved")):
            reason = (terminal_outcome.get("reason") or "verification threshold not achieved").strip()
        elif verification_artifact is None:
            reason = "verification evidence is not traceable from published artifacts"
        elif artifact_reference is None:
            reason = "deployable model artifact could not be resolved from published artifacts"
        elif not target_endpoint:
            reason = "target endpoint is missing"
        else:
            verification_report = load_json_artifact(
                artifact_reference=verification_artifact,
                s3_client=s3_client,
            )
            report_issue = _verification_report_consistency_issue(
                verification_report=verification_report,
                verification_summary=verification_summary,
            )
            if report_issue:
                reason = report_issue

    return {
        "task_id": task_record["task_id"],
        "task_status": task_record["status"],
        "manifest_present": True,
        "verification_achieved": bool(verification_summary.get("achieved")),
        "verification_reason": verification_summary.get("reason", ""),
        "verification_source": verification_summary.get("source", ""),
        "verification_summary": verification_summary,
        "verification_artifact_reference": verification_artifact,
        "verification_artifact_report": verification_report,
        "deployable_artifact_reference": artifact_reference,
        "target_endpoint": target_endpoint,
        "eligible": reason == "",
        "ineligible_reason": reason,
    }


def _artifact_type_for_name(name: str) -> str:
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "tarball"
    if name.endswith(".zip"):
        return "zip"
    if name.endswith(".onnx"):
        return "onnx"
    if name.endswith(".pt"):
        return "pytorch"
    if name.endswith(".joblib") or name.endswith(".pkl") or name.endswith(".pickle"):
        return "python-serialized-model"
    return "model-artifact"


def _manifest_verification_consistency_issue(
    *,
    terminal_outcome: dict[str, Any],
    verification_summary: dict[str, Any],
) -> str:
    if not verification_summary:
        return "runtime manifest does not contain a latest verification summary"

    metric_value = _coerce_float(verification_summary.get("metric_value"))
    threshold = _coerce_float(verification_summary.get("threshold"))
    direction = (verification_summary.get("threshold_direction") or "").strip()
    if verification_summary.get("source") != INDEPENDENT_VERIFICATION_SOURCE:
        return "deployment requires independent verification evidence"
    if metric_value is None:
        return "verification metric value is missing from the runtime manifest"
    if threshold is None:
        return "verification threshold is missing from the runtime manifest"
    if direction not in VALID_THRESHOLD_DIRECTIONS:
        return "verification threshold direction is missing from the runtime manifest"

    verification_achieved = bool(verification_summary.get("achieved"))
    threshold_achieved = _threshold_satisfied(
        metric_value=metric_value,
        threshold=threshold,
        direction=direction,
    )
    if verification_achieved != threshold_achieved:
        return "runtime manifest verification result is inconsistent with threshold metadata"

    terminal_achieved = bool(terminal_outcome.get("achieved"))
    if terminal_achieved != verification_achieved:
        return "runtime manifest terminal outcome is inconsistent with the latest verification result"

    final_metric_value = _coerce_float(terminal_outcome.get("final_metric_value"))
    if final_metric_value is not None and not math.isclose(
        final_metric_value,
        metric_value,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        return "runtime manifest terminal metric does not match the latest verification metric"
    return ""


def _verification_report_consistency_issue(
    *,
    verification_report: dict[str, Any],
    verification_summary: dict[str, Any],
) -> str:
    report_source = (verification_report.get("source") or "").strip()
    if report_source != INDEPENDENT_VERIFICATION_SOURCE:
        return "verification artifact does not prove an independent verification result"

    if bool(verification_report.get("achieved")) != bool(verification_summary.get("achieved")):
        return "verification artifact achieved flag is inconsistent with the runtime manifest"

    report_metric_value = _coerce_float(
        verification_report.get("verified_metric", verification_report.get("metric_value"))
    )
    manifest_metric_value = _coerce_float(verification_summary.get("metric_value"))
    if report_metric_value is None or manifest_metric_value is None:
        return "verification artifact is missing verified metric details"
    if not math.isclose(report_metric_value, manifest_metric_value, rel_tol=0.0, abs_tol=1e-9):
        return "verification artifact metric does not match the runtime manifest"

    report_threshold = _coerce_float(verification_report.get("threshold"))
    manifest_threshold = _coerce_float(verification_summary.get("threshold"))
    if report_threshold is None or manifest_threshold is None:
        return "verification artifact is missing threshold metadata"
    if not math.isclose(report_threshold, manifest_threshold, rel_tol=0.0, abs_tol=1e-9):
        return "verification artifact threshold does not match the runtime manifest"

    report_direction = (
        verification_report.get("direction")
        or verification_report.get("threshold_direction")
        or ""
    ).strip()
    manifest_direction = (verification_summary.get("threshold_direction") or "").strip()
    if report_direction not in VALID_THRESHOLD_DIRECTIONS:
        return "verification artifact is missing threshold direction metadata"
    if report_direction != manifest_direction:
        return "verification artifact threshold direction does not match the runtime manifest"
    return ""


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _threshold_satisfied(
    *,
    metric_value: float,
    threshold: float,
    direction: str,
) -> bool:
    if direction == "lower_is_better":
        return metric_value <= threshold
    return metric_value >= threshold
