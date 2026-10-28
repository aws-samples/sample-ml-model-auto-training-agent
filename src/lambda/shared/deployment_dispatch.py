# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Shared deployment-dispatch logic used by both the automatic path
(oncomplete handler, on training success) and the manual path (API
POST /tasks/deploy, operator-triggered).

Given a SUCCEEDED task record, this:
  1. Loads the runtime manifest and evaluates deployment eligibility.
  2. Accepts a deployment attempt in the model registry (idempotent).
  3. Marks the task DEPLOYING.
  4. Invokes the deployment Lambda asynchronously.

On any failure it records the reason and marks the task FAILED. The caller
supplies the repositories and an ``update_and_notify`` callback so the same
persistence/notification behaviour is reused verbatim.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from deployment.runtime_outputs import build_manifest_reference
from shared.logging_utils import log_event
from shared.task_status import (
    TASK_STATUS_DEPLOYING,
    TASK_STATUS_FAILED,
    apply_status_transition,
)
from shared.time_utils import utc_timestamp


class DeploymentNotEligibleError(RuntimeError):
    """Raised when a task cannot be deployed (manifest missing, threshold not
    met, artifact unresolved, etc.). Carries a human-readable reason."""


def dispatch_deployment(
    *,
    task_record: dict[str, Any],
    decision: dict[str, Any],
    repository: Any,
    model_registry: Any,
    bucket_name: str,
    deployment_function_name: str,
    lambda_client: Any,
    update_and_notify: Callable[[dict[str, Any], str], None],
    request_id: str | None = None,
) -> dict[str, Any]:
    """Run the deploy-dispatch sequence for a SUCCEEDED task, given a
    pre-computed eligibility ``decision`` (from evaluate_deployment_eligibility).

    ``update_and_notify(updated_task, previous_status)`` persists the record and
    emits a status-change notification (the caller owns message plumbing).

    Returns a dict: {"status": <task status>, "model_record_id": <id or "">}.
    Raises DeploymentNotEligibleError if the decision is not eligible.
    """
    previous_status = task_record["status"]

    if not decision["eligible"]:
        raise DeploymentNotEligibleError(decision["ineligible_reason"])

    now = utc_timestamp()
    manifest_reference = build_manifest_reference(bucket_name, task_record)
    model_record, accepted = model_registry.accept_deployment_attempt(
        task_record=task_record,
        eligibility_decision=decision,
        manifest_reference=manifest_reference,
        now=now,
    )
    if not accepted:
        # Another dispatch already accepted this attempt; treat as a no-op.
        log_event(
            "deployment-dispatch",
            "deployment-already-accepted",
            request_id=request_id,
            task_id=task_record["task_id"],
            model_record_id=model_record["model_record_id"],
            status=model_record["status"],
        )
        return {
            "status": task_record["status"],
            "model_record_id": model_record["model_record_id"],
            "accepted": False,
        }

    deployment_task = apply_status_transition(
        task_record,
        TASK_STATUS_DEPLOYING,
        now=now,
        error_message="",
    )
    deployment_task["deployment_skip_reason"] = ""
    update_and_notify(deployment_task, previous_status)

    payload = {
        "task_id": task_record["task_id"],
        "model_record_id": model_record["model_record_id"],
    }
    try:
        response = lambda_client.invoke(
            FunctionName=deployment_function_name,
            InvocationType="Event",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        if int(response.get("StatusCode", 0)) != 202:
            raise RuntimeError("deployment invocation was not accepted")
    except Exception as exc:  # noqa: BLE001 - convert to task failure below
        failure_now = utc_timestamp()
        model_registry.record_dispatch_failure(
            model_record_id=model_record["model_record_id"],
            failure_reason=str(exc),
            now=failure_now,
        )
        failed_task = apply_status_transition(
            deployment_task,
            TASK_STATUS_FAILED,
            now=failure_now,
            error_message=str(exc),
        )
        update_and_notify(failed_task, deployment_task["status"])
        log_event(
            "deployment-dispatch",
            "deployment-dispatch-failed",
            level="ERROR",
            request_id=request_id,
            task_id=task_record["task_id"],
            error=str(exc),
        )
        return {
            "status": failed_task["status"],
            "model_record_id": model_record["model_record_id"],
            "accepted": True,
        }

    log_event(
        "deployment-dispatch",
        "deployment-dispatched",
        request_id=request_id,
        task_id=task_record["task_id"],
        model_record_id=model_record["model_record_id"],
        target_endpoint=task_record.get("target_endpoint", ""),
    )
    return {
        "status": deployment_task["status"],
        "model_record_id": model_record["model_record_id"],
        "accepted": True,
    }
