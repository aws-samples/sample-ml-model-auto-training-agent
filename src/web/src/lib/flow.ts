// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { SubmissionFlowStage } from "./types";

const ALLOWED_TRANSITIONS: Record<
  SubmissionFlowStage,
  readonly SubmissionFlowStage[]
> = {
  bootstrap: ["welcome", "submit-failure"],
  welcome: ["task-type-selection", "draft-edit", "submit-failure"],
  "task-type-selection": ["draft-edit", "preview"],
  "draft-edit": ["preview", "submit-failure"],
  preview: ["draft-edit", "submitting"],
  submitting: ["submit-success", "submit-failure"],
  "submit-success": ["draft-edit", "task-type-selection", "preview"],
  "submit-failure": ["draft-edit", "preview"],
};

export function nextStageAfterPreviewSubmit(
  success: boolean,
): SubmissionFlowStage {
  return success ? "submit-success" : "submit-failure";
}

export function canTransitionToPreview(
  current: SubmissionFlowStage,
): boolean {
  return (
    current === "draft-edit" ||
    current === "task-type-selection" ||
    current === "submit-failure"
  );
}

export function canTransitionFlowStage(
  current: SubmissionFlowStage,
  next: SubmissionFlowStage,
): boolean {
  return ALLOWED_TRANSITIONS[current].includes(next);
}

export function transitionFlowStage(
  current: SubmissionFlowStage,
  next: SubmissionFlowStage,
): SubmissionFlowStage {
  return canTransitionFlowStage(current, next) ? next : current;
}

export function isPreviewVisible(stage: SubmissionFlowStage): boolean {
  return (
    stage === "preview" ||
    stage === "submitting" ||
    stage === "submit-success" ||
    stage === "submit-failure"
  );
}
