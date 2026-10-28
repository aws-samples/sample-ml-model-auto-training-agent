# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

import io
import os
import re
import tarfile
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class SageMakerDeploymentError(RuntimeError):
    """Raised when a SageMaker deployment attempt fails."""


def _model_dir_prefix(artifact_key: str) -> str:
    """Given a deployable artifact key (e.g. ``output/<task>/output/models/predictor.pkl``
    or ``.../models/``), return the AutoGluon model directory prefix ending in
    ``models/``."""
    key = artifact_key.strip()
    marker = "models/"
    idx = key.find(marker)
    if idx != -1:
        return key[: idx + len(marker)]
    # Fall back to the artifact's parent directory.
    return key.rsplit("/", 1)[0] + "/" if "/" in key else ""


class SageMakerAdapter:
    def __init__(
        self,
        client: Any | None = None,
        s3_client: Any | None = None,
        ecr_client: Any | None = None,
    ) -> None:
        self._client = client or boto3.client("sagemaker")
        self._s3 = s3_client or boto3.client("s3")
        self._ecr = ecr_client

    def _resolve_serve_tag(self, image_repo_uri: str, tag: str) -> str:
        """Prefer the non-root ``<tag>-serve`` image variant for the SageMaker
        endpoint (deployed containers must not run as root). Falls back to the
        base tag for images built before the serve stage existed, or when ECR
        cannot be queried."""
        serve_tag = f"{tag}-serve"
        repo_name = image_repo_uri.split("/", 1)[-1]
        try:
            if self._ecr is None:
                self._ecr = boto3.client("ecr")
            self._ecr.describe_images(
                repositoryName=repo_name,
                imageIds=[{"imageTag": serve_tag}],
            )
            return serve_tag
        except (ClientError, BotoCoreError):
            return tag

    def _package_model_tarball(self, bucket: str, model_dir_prefix: str) -> str:
        """Package every object under the AutoGluon model directory into a
        ``model.tar.gz`` (SageMaker requires a single tarball) and upload it next
        to the model dir. Returns the s3:// URL of the tarball."""
        paginator = self._s3.get_paginator("list_objects_v2")
        buffer = io.BytesIO()
        member_count = 0
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for page in paginator.paginate(Bucket=bucket, Prefix=model_dir_prefix):
                for obj in page.get("Contents", []):
                    obj_key = obj["Key"]
                    if obj_key.endswith("/"):
                        continue
                    body = self._s3.get_object(Bucket=bucket, Key=obj_key)["Body"].read()
                    # Store paths relative to the model dir so the predictor
                    # loads from the tar root (SageMaker extracts to /opt/ml/model).
                    arcname = obj_key[len(model_dir_prefix):]
                    info = tarfile.TarInfo(name=arcname)
                    info.size = len(body)
                    tar.addfile(info, io.BytesIO(body))
                    member_count += 1
        if member_count == 0:
            raise SageMakerDeploymentError(
                f"no model objects found under s3://{bucket}/{model_dir_prefix}"
            )
        buffer.seek(0)
        tarball_key = f"{model_dir_prefix}model.tar.gz"
        self._s3.put_object(Bucket=bucket, Key=tarball_key, Body=buffer.getvalue())
        return f"s3://{bucket}/{tarball_key}"

    def deploy_model(
        self,
        *,
        task_record: dict[str, Any],
        artifact_reference: dict[str, Any],
        image_repo_uri: str,
        execution_role_arn: str,
        instance_type: str,
        initial_instance_count: int,
    ) -> dict[str, Any]:
        target_endpoint = task_record["target_endpoint"]
        resolved_tag = self._resolve_serve_tag(
            image_repo_uri, task_record["resolved_image_tag"]
        )
        image_uri = f"{image_repo_uri}:{resolved_tag}"
        model_name = _bounded_name(f"{target_endpoint}-{task_record['task_id']}-model")
        endpoint_config_name = _bounded_name(
            f"{target_endpoint}-{task_record['task_id']}-config"
        )

        # AutoGluon stores a model *directory*; SageMaker needs a single
        # model.tar.gz. Package the model dir derived from the deployable
        # artifact key and point ModelDataUrl at the resulting tarball.
        model_dir_prefix = _model_dir_prefix(artifact_reference["key"])
        model_data_url = self._package_model_tarball(
            bucket=artifact_reference["bucket"],
            model_dir_prefix=model_dir_prefix,
        )
        self._create_model_if_missing(
            model_name=model_name,
            execution_role_arn=execution_role_arn,
            image_uri=image_uri,
            model_data_url=model_data_url,
        )
        self._create_endpoint_config_if_missing(
            endpoint_config_name=endpoint_config_name,
            model_name=model_name,
            instance_type=instance_type,
            initial_instance_count=initial_instance_count,
        )

        operation = "update" if self._endpoint_exists(target_endpoint) else "create"
        # Wrap create/update_endpoint ClientErrors (e.g. ResourceLimitExceeded
        # when the g5 endpoint quota is exhausted) as SageMakerDeploymentError so
        # the handler's except clause catches it and marks the record FAILED,
        # instead of letting it bubble up and leave the task stuck in DEPLOYING.
        try:
            if operation == "update":
                self._client.update_endpoint(
                    EndpointName=target_endpoint,
                    EndpointConfigName=endpoint_config_name,
                )
            else:
                self._client.create_endpoint(
                    EndpointName=target_endpoint,
                    EndpointConfigName=endpoint_config_name,
                )
        except (ClientError, BotoCoreError) as exc:
            raise SageMakerDeploymentError(
                f"unable to {operation} SageMaker endpoint '{target_endpoint}': {exc}"
            ) from exc

        description = self._describe_endpoint(target_endpoint)
        return {
            "operation": operation,
            "provider_status": description.get("EndpointStatus", "Unknown"),
            "provider_status_detail": {
                "image_uri": image_uri,
                "artifact_type": artifact_reference.get("artifact_type"),
                "last_modified_time": str(description.get("LastModifiedTime", "")),
            },
            "endpoint_arn": description.get("EndpointArn", ""),
            "endpoint_config_name": endpoint_config_name,
            "sagemaker_model_name": model_name,
        }

    def _inference_environment(self) -> dict[str, str]:
        """Environment for the inference container. Chronos is a zero-shot model
        whose weights are NOT stored in the AutoGluon predictor (model.tar.gz is
        only ~180KB of metadata). The training image PRE-BAKES those weights into
        the in-image HuggingFace cache at /opt/hf-cache (see training_runtime/
        Dockerfile), so the endpoint loads Chronos OFFLINE and never depends on a
        runtime HF download — which on SageMaker real-time endpoints was
        unreliable and surfaced as "Following models failed to predict".

        Point HF_HOME at the baked cache and force offline mode so the loader
        resolves the local snapshot instead of reaching out to the network."""
        return {
            "HF_HOME": "/opt/hf-cache",
            "HF_HUB_CACHE": "/opt/hf-cache/hub",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }

    def _create_model_if_missing(
        self,
        *,
        model_name: str,
        execution_role_arn: str,
        image_uri: str,
        model_data_url: str,
    ) -> None:
        if self._model_exists(model_name):
            return
        try:
            self._client.create_model(
                ModelName=model_name,
                ExecutionRoleArn=execution_role_arn,
                PrimaryContainer={
                    "Image": image_uri,
                    "ModelDataUrl": model_data_url,
                    "Environment": self._inference_environment(),
                },
            )
        except ClientError as exc:
            if not _is_already_exists_error(exc):
                raise SageMakerDeploymentError("unable to create SageMaker model") from exc

    def _create_endpoint_config_if_missing(
        self,
        *,
        endpoint_config_name: str,
        model_name: str,
        instance_type: str,
        initial_instance_count: int,
    ) -> None:
        if self._endpoint_config_exists(endpoint_config_name):
            return
        try:
            self._client.create_endpoint_config(
                EndpointConfigName=endpoint_config_name,
                ProductionVariants=[
                    {
                        "VariantName": "primary",
                        "ModelName": model_name,
                        "InitialInstanceCount": initial_instance_count,
                        "InstanceType": instance_type,
                        "InitialVariantWeight": 1.0,
                    }
                ],
            )
        except ClientError as exc:
            if not _is_already_exists_error(exc):
                raise SageMakerDeploymentError(
                    "unable to create SageMaker endpoint configuration"
                ) from exc

    def _model_exists(self, model_name: str) -> bool:
        try:
            self._client.describe_model(ModelName=model_name)
            return True
        except ClientError as exc:
            if _is_not_found_error(exc):
                return False
            raise SageMakerDeploymentError("unable to inspect SageMaker model") from exc

    def _endpoint_config_exists(self, endpoint_config_name: str) -> bool:
        try:
            self._client.describe_endpoint_config(EndpointConfigName=endpoint_config_name)
            return True
        except ClientError as exc:
            if _is_not_found_error(exc):
                return False
            raise SageMakerDeploymentError(
                "unable to inspect SageMaker endpoint configuration"
            ) from exc

    def _endpoint_exists(self, endpoint_name: str) -> bool:
        try:
            self._client.describe_endpoint(EndpointName=endpoint_name)
            return True
        except ClientError as exc:
            if _is_not_found_error(exc):
                return False
            raise SageMakerDeploymentError("unable to inspect SageMaker endpoint") from exc

    def _describe_endpoint(self, endpoint_name: str) -> dict[str, Any]:
        try:
            return self._client.describe_endpoint(EndpointName=endpoint_name)
        except ClientError as exc:
            raise SageMakerDeploymentError("unable to describe SageMaker endpoint") from exc


def _bounded_name(raw_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9-]+", "-", raw_name).strip("-")
    compact = re.sub(r"-{2,}", "-", normalized)
    return compact[:63] or "deployment-artifact"


def _is_already_exists_error(error: ClientError) -> bool:
    code = error.response.get("Error", {}).get("Code", "")
    message = (error.response.get("Error", {}).get("Message") or "").lower()
    return code in {"ResourceInUse", "ValidationException", "ConflictException"} and (
        "already" in message or "exists" in message
    )


def _is_not_found_error(error: ClientError) -> bool:
    code = error.response.get("Error", {}).get("Code", "")
    message = (error.response.get("Error", {}).get("Message") or "").lower()
    if code in {"ResourceNotFound", "ResourceNotFoundException"}:
        return True
    return code == "ValidationException" and ("not found" in message or "could not find" in message)
