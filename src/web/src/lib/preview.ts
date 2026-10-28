// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { resolveSubmittedBy } from "./auth";
import type {
  FrontendRuntimeConfiguration,
  SubmissionDraft,
  TrainingPlanPreview,
} from "./types";

export function resolveImageTag(imageTag: string): string {
  return imageTag.trim() || "stable";
}

export function buildPreview(
  draft: SubmissionDraft,
  config: FrontendRuntimeConfiguration,
): TrainingPlanPreview {
  const submittedBy = resolveSubmittedBy(config);
  const resolvedImageTag = resolveImageTag(draft.imageTag);
  return {
    resolvedImageTag,
    submittedBy,
    payload: {
      task_name: draft.taskName.trim() || draft.modelName.trim(),
      task_type: draft.taskType,
      // Optional human-facing note; not injected into the training prompt.
      comment: draft.comment.trim(),
      data_location: draft.dataLocation.trim(),
      output_location: draft.outputLocation.trim(),
      model_name: draft.modelName.trim(),
      target_endpoint: draft.targetEndpoint.trim(),
      submitted_by: submittedBy,
      image_tag: resolvedImageTag,
      deployment_mode: draft.deploymentMode,
      confirmation_flag: true,
      // Recurring fields are only sent when enabled; the backend treats the
      // schedule_start value as UTC.
      ...(draft.isRecurring
        ? {
            is_recurring: true,
            schedule_start: draft.scheduleStart.trim(),
            interval_value: Number(draft.intervalValue),
            interval_unit: draft.intervalUnit,
          }
        : {}),
    },
  };
}
