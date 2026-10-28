# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import os
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from api.repository import TaskRepository
from deployment.model_registry import ModelRegistryRepository
from deployment.sagemaker_adapter import SageMakerAdapter, SageMakerDeploymentError
from shared.logging_utils import log_event
from shared.notifications import record_status_change
from shared.task_types import get_task_type
from shared.task_status import (
    TASK_STATUS_DEPLOYED,
    TASK_STATUS_FAILED,
    apply_status_transition,
)
from shared.time_utils import utc_timestamp


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    request_id = getattr(context, "aws_request_id", None)
    model_record_id = (event.get("model_record_id") or event.get("task_id") or "").strip()
    task_id = (event.get("task_id") or model_record_id).strip()
    if not model_record_id or not task_id:
        log_event(
            "deployment-handler",
            "deployment-event-ignored",
            level="WARN",
            request_id=request_id,
            reason="missing task_id or model_record_id",
        )
        return {"statusCode": 200, "body": "ignored"}

    task_repository = _build_task_repository()
    registry = _build_model_registry_repository()
    task = task_repository.get_task(task_id)
    model_record = registry.get_model_record(model_record_id)
    if task is None or model_record is None:
        log_event(
            "deployment-handler",
            "deployment-event-ignored",
            level="WARN",
            request_id=request_id,
            task_id=task_id,
            model_record_id=model_record_id,
            reason="missing task or model record",
        )
        return {"statusCode": 200, "body": "ignored"}

    if model_record.get("status") in {TASK_STATUS_DEPLOYED, TASK_STATUS_FAILED}:
        log_event(
            "deployment-handler",
            "deployment-event-ignored",
            request_id=request_id,
            task_id=task_id,
            model_record_id=model_record_id,
            reason="model record already terminal",
            status=model_record["status"],
        )
        return {"statusCode": 200, "body": "ignored"}

    model_record, claimed = registry.claim_deployment_execution(
        model_record_id=model_record_id,
        now=utc_timestamp(),
    )
    if not claimed:
        log_event(
            "deployment-handler",
            "deployment-event-ignored",
            request_id=request_id,
            task_id=task_id,
            model_record_id=model_record_id,
            reason="deployment execution already claimed",
            status=model_record.get("status", ""),
        )
        return {"statusCode": 200, "body": "ignored"}

    adapter = SageMakerAdapter()
    try:
        result = adapter.deploy_model(
            task_record=task,
            artifact_reference=model_record["artifact_reference"],
            image_repo_uri=os.environ["IMAGE_REPO_URI"],
            execution_role_arn=os.environ["SAGEMAKER_EXECUTION_ROLE_ARN"],
            instance_type=_resolve_instance_type(task),
            initial_instance_count=int(
                os.environ.get("SAGEMAKER_INITIAL_INSTANCE_COUNT", "1")
            ),
        )
        now = utc_timestamp()
        registry.record_deployment_result(
            model_record_id=model_record_id,
            status=TASK_STATUS_DEPLOYED,
            provider_status=result["provider_status"],
            provider_status_detail=result["provider_status_detail"],
            failure_reason="",
            endpoint_arn=result["endpoint_arn"],
            endpoint_config_name=result["endpoint_config_name"],
            sagemaker_model_name=result["sagemaker_model_name"],
            now=now,
        )
        deployed_task = apply_status_transition(
            task,
            TASK_STATUS_DEPLOYED,
            now=now,
            error_message="",
        )
        task_repository.update_task(deployed_task)
        record_status_change(
            deployed_task, task["status"], deployed_task["status"], request_id=request_id
        )
        log_event(
            "deployment-handler",
            "deployment-succeeded",
            request_id=request_id,
            task_id=task_id,
            model_record_id=model_record_id,
            endpoint=task["target_endpoint"],
            provider_status=result["provider_status"],
        )
        return {"statusCode": 200, "body": TASK_STATUS_DEPLOYED}
    # Catch raw boto errors (ClientError/BotoCoreError) too, as a safety net —
    # any deployment failure must mark the record FAILED with a reason rather
    # than bubbling up and leaving the task permanently stuck in DEPLOYING (a
    # re-invoke would then be dropped by the "already claimed" idempotency guard).
    except (
        SageMakerDeploymentError,
        ClientError,
        BotoCoreError,
        KeyError,
        ValueError,
    ) as exc:
        now = utc_timestamp()
        registry.record_deployment_result(
            model_record_id=model_record_id,
            status=TASK_STATUS_FAILED,
            provider_status="FAILED",
            provider_status_detail={"error_type": type(exc).__name__},
            failure_reason=str(exc),
            now=now,
        )
        failed_task = apply_status_transition(
            task,
            TASK_STATUS_FAILED,
            now=now,
            error_message=str(exc),
        )
        task_repository.update_task(failed_task)
        record_status_change(
            failed_task, task["status"], failed_task["status"], request_id=request_id
        )
        log_event(
            "deployment-handler",
            "deployment-failed",
            level="ERROR",
            request_id=request_id,
            task_id=task_id,
            model_record_id=model_record_id,
            error=str(exc),
        )
        return {"statusCode": 200, "body": TASK_STATUS_FAILED}


def _resolve_instance_type(task_record: dict[str, Any]) -> str:
    """Endpoint instance type for this task's type.

    The stack-wide default (SAGEMAKER_INSTANCE_TYPE) is a GPU instance, which the
    AutoGluon models need. Task types that deploy CPU-only models — notably
    byo_deploy, which just transfers an algorithm team's scikit-learn/XGBoost
    model — set "instanceType" in cdk.json taskTypes to avoid burning the (very
    small) GPU endpoint quota and paying for a GPU they never use.
    """
    configured = get_task_type((task_record.get("task_type") or "").strip()) or {}
    per_type = str(configured.get("instanceType") or "").strip()
    if per_type:
        return per_type
    return os.environ.get("SAGEMAKER_INSTANCE_TYPE", "ml.m5.large")


def _build_task_repository() -> TaskRepository:
    return TaskRepository(
        table_name=os.environ["TABLE_NAME"],
        batch_job_index=os.environ.get("TASK_BATCH_JOB_INDEX", "batch-job-id-index"),
    )


def _build_model_registry_repository() -> ModelRegistryRepository:
    return ModelRegistryRepository(
        table_name=os.environ["MODEL_REGISTRY_TABLE_NAME"],
        target_endpoint_index=os.environ.get(
            "MODEL_REGISTRY_TARGET_ENDPOINT_INDEX",
            "target-endpoint-index",
        ),
    )
