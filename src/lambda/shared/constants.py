# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
TASK_TYPE_TIMESERIES_FORECAST = "timeseries_forecast"
DEFAULT_IMAGE_TAG = "stable"
DEFAULT_SKILLS_PREFIX = "skills/"
DEFAULT_LOG_OBJECT_KEY = "logs/{task_id}/events.jsonl"
# S3 layout convention (subfolders under the data bucket):
#   data/               -> uploaded training input datasets
#   inference_data/     -> uploaded prediction (inference) input datasets
#   output/             -> training run artifacts (models under models/, manifests, predictions)
#   inference_output/   -> inference/prediction result files
DATA_PREFIX_ROOT = "data"
INFERENCE_DATA_PREFIX_ROOT = "inference_data"
OUTPUT_PREFIX_ROOT = "output"
INFERENCE_OUTPUT_PREFIX_ROOT = "inference_output"
RUNTIME_MANIFEST_FILE_NAME = "runtime_manifest.json"
MODEL_REGISTRY_TARGET_ENDPOINT_INDEX = "target-endpoint-index"
MODEL_PROVIDER_STATUS_ACCEPTED = "ACCEPTED"
MODEL_PROVIDER_STATUS_DISPATCH_FAILED = "DISPATCH_FAILED"

TASK_STATUS_SUBMITTED = "SUBMITTED"
# A recurring "parent" task waiting for its EventBridge schedule to fire. It
# never runs Batch itself; each fire creates a child SUBMITTED task.
TASK_STATUS_SCHEDULED = "SCHEDULED"
TASK_STATUS_PENDING = "PENDING"
TASK_STATUS_QUEUED = "QUEUED"
TASK_STATUS_RUNNING = "RUNNING"
TASK_STATUS_SUCCEEDED = "SUCCEEDED"
TASK_STATUS_FAILED = "FAILED"
TASK_STATUS_DEPLOYING = "DEPLOYING"
TASK_STATUS_DEPLOYED = "DEPLOYED"
TASK_STATUS_CANCELLED = "CANCELLED"

# Statuses from which an operator may cancel the task (Batch job still pending
# or running). Terminal/deploying states cannot be cancelled.
CANCELLABLE_TASK_STATUSES = {
    TASK_STATUS_SUBMITTED,
    TASK_STATUS_SCHEDULED,
    TASK_STATUS_PENDING,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
}

ACTIVE_TASK_STATUSES = {
    TASK_STATUS_SUBMITTED,
    TASK_STATUS_RUNNING,
}
