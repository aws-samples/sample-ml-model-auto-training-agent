# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from copy import deepcopy
from typing import Any

from shared.constants import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_DEPLOYED,
    TASK_STATUS_DEPLOYING,
    TASK_STATUS_FAILED,
    TASK_STATUS_PENDING,
    TASK_STATUS_QUEUED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SCHEDULED,
    TASK_STATUS_SUBMITTED,
    TASK_STATUS_SUCCEEDED,
)


LEGAL_TRANSITIONS = {
    # A recurring parent stays SCHEDULED until cancelled (or marked FAILED if the
    # schedule could not be created). It never itself runs Batch.
    TASK_STATUS_SCHEDULED: {
        TASK_STATUS_SCHEDULED,
        TASK_STATUS_CANCELLED,
        TASK_STATUS_FAILED,
    },
    TASK_STATUS_SUBMITTED: {
        TASK_STATUS_SUBMITTED,
        TASK_STATUS_PENDING,
        TASK_STATUS_QUEUED,
        TASK_STATUS_RUNNING,
        TASK_STATUS_SUCCEEDED,
        TASK_STATUS_FAILED,
        TASK_STATUS_CANCELLED,
    },
    TASK_STATUS_PENDING: {
        TASK_STATUS_PENDING,
        TASK_STATUS_QUEUED,
        TASK_STATUS_RUNNING,
        TASK_STATUS_SUCCEEDED,
        TASK_STATUS_FAILED,
        TASK_STATUS_CANCELLED,
    },
    TASK_STATUS_QUEUED: {
        TASK_STATUS_QUEUED,
        TASK_STATUS_RUNNING,
        TASK_STATUS_SUCCEEDED,
        TASK_STATUS_FAILED,
        TASK_STATUS_CANCELLED,
    },
    TASK_STATUS_RUNNING: {
        TASK_STATUS_RUNNING,
        TASK_STATUS_SUCCEEDED,
        TASK_STATUS_FAILED,
        TASK_STATUS_DEPLOYING,
        TASK_STATUS_CANCELLED,
    },
    TASK_STATUS_SUCCEEDED: {
        TASK_STATUS_SUCCEEDED,
        TASK_STATUS_DEPLOYING,
        TASK_STATUS_DEPLOYED,
    },
    TASK_STATUS_DEPLOYING: {
        TASK_STATUS_DEPLOYING,
        TASK_STATUS_DEPLOYED,
        TASK_STATUS_FAILED,
    },
    TASK_STATUS_DEPLOYED: {TASK_STATUS_DEPLOYED},
    TASK_STATUS_FAILED: {TASK_STATUS_FAILED},
    TASK_STATUS_CANCELLED: {TASK_STATUS_CANCELLED},
}


def apply_status_transition(
    task_record: dict[str, Any],
    new_status: str,
    now: str | None = None,
    batch_job_id: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    current_status = task_record["status"]
    if new_status not in LEGAL_TRANSITIONS.get(current_status, set()):
        raise ValueError(f"Illegal task status transition: {current_status} -> {new_status}")

    updated = deepcopy(task_record)
    if new_status != current_status:
        updated["status"] = new_status
        if now is not None:
            updated["updated_at"] = now
    if batch_job_id:
        updated["batch_job_id"] = batch_job_id
    if error_message is not None:
        updated["error_message"] = error_message
    return updated
