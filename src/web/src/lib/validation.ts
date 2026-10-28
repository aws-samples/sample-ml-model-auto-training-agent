// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { DraftValidationResult, SubmissionDraft } from "./types";

const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{2,62}$/;
const IMAGE_TAG_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

function isValidLocation(value: string, prefixes: string[]): boolean {
  if (!value || value.includes("..")) {
    return false;
  }
  return prefixes.some((prefix) => value.startsWith(prefix));
}

export function validateDraft(
  draft: SubmissionDraft,
): DraftValidationResult {
  const fieldErrors: DraftValidationResult["fieldErrors"] = {};

  if (!isValidLocation(draft.dataLocation.trim(), ["data/", "s3://"])) {
    fieldErrors.dataLocation = "训练数据位置格式不正确";
  }
  if (!isValidLocation(draft.outputLocation.trim(), ["output/", "s3://"])) {
    fieldErrors.outputLocation = "输出位置格式不正确";
  }
  if (!NAME_PATTERN.test(draft.modelName.trim())) {
    fieldErrors.modelName = "模型名称格式不正确";
  }
  if (!NAME_PATTERN.test(draft.targetEndpoint.trim())) {
    fieldErrors.targetEndpoint = "目标端点格式不正确";
  }
  if (draft.taskName.trim().length > 100) {
    fieldErrors.taskName = "任务名称不能超过 100 个字符";
  }
  // comment is optional; only bound its length.
  if (draft.comment.trim().length > 1000) {
    fieldErrors.comment = "任务说明不能超过 1000 个字符";
  }
  if (draft.imageTag.trim() && !IMAGE_TAG_PATTERN.test(draft.imageTag.trim())) {
    fieldErrors.imageTag = "镜像标签格式不正确";
  }
  if (draft.isRecurring) {
    if (!draft.scheduleStart.trim()) {
      fieldErrors.scheduleStart = "请选择开始时间";
    }
    const interval = Number(draft.intervalValue);
    if (
      !Number.isInteger(interval) ||
      interval < 1 ||
      interval > 1000
    ) {
      fieldErrors.intervalValue = "周期需为 1–1000 的整数";
    }
  }

  const summaryErrors = Object.values(fieldErrors);
  return {
    canPreview: summaryErrors.length === 0,
    fieldErrors,
    summaryErrors,
  };
}
