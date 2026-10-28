// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { S3PathPicker } from "./S3PathPicker";
import type { ApiClient } from "../lib/api";
import type { DraftValidationResult, SubmissionDraft } from "../lib/types";

// Fixed S3 subfolder prefixes (see backend shared/constants.py). Users only
// edit the subfolder/file part after these.
const DATA_PREFIX = "data/";
const TRAINING_OUTPUT_PREFIX = "output/";

interface BasicFieldsSectionProps {
  draft: SubmissionDraft;
  validation: DraftValidationResult;
  onChange: (field: keyof SubmissionDraft, value: string | boolean) => void;
  apiClient: ApiClient;
}

export function BasicFieldsSection({
  draft,
  validation,
  onChange,
  apiClient,
}: BasicFieldsSectionProps) {
  return (
    <div className="grid-two" data-testid="basic-fields-section">
      <label className="field field-span-full">
        <span>任务名称</span>
        <input
          value={draft.taskName}
          onChange={(event) => onChange("taskName", event.target.value)}
          placeholder="可选，留空将使用模型名称"
          data-testid="submission-task-name-input"
        />
        {validation.fieldErrors.taskName ? (
          <span className="error-text">{validation.fieldErrors.taskName}</span>
        ) : null}
      </label>
      <label className="field">
        <span>训练数据位置</span>
        <S3PathPicker
          apiClient={apiClient}
          prefix={DATA_PREFIX}
          mode="file-or-dir"
          value={draft.dataLocation}
          onChange={(full) => onChange("dataLocation", full)}
          placeholder="train.csv、子目录/train.csv 或 子目录/"
          testId="submission-data-location-input"
        />
        {validation.fieldErrors.dataLocation ? (
          <span className="error-text">{validation.fieldErrors.dataLocation}</span>
        ) : null}
      </label>
      <label className="field">
        <span>训练产出物位置</span>
        <S3PathPicker
          apiClient={apiClient}
          prefix={TRAINING_OUTPUT_PREFIX}
          mode="dir"
          value={draft.outputLocation}
          onChange={(full) => onChange("outputLocation", full)}
          placeholder="timeseries-demo（可填写新目录）"
          testId="submission-output-location-input"
        />
        {validation.fieldErrors.outputLocation ? (
          <span className="error-text">{validation.fieldErrors.outputLocation}</span>
        ) : null}
      </label>
      <label className="field">
        <span>模型名称</span>
        <input
          value={draft.modelName}
          onChange={(event) => onChange("modelName", event.target.value)}
          data-testid="submission-model-name-input"
        />
        {validation.fieldErrors.modelName ? (
          <span className="error-text">{validation.fieldErrors.modelName}</span>
        ) : null}
      </label>
      <label className="field">
        <span>目标端点</span>
        <input
          value={draft.targetEndpoint}
          onChange={(event) => onChange("targetEndpoint", event.target.value)}
          data-testid="submission-target-endpoint-input"
        />
        {validation.fieldErrors.targetEndpoint ? (
          <span className="error-text">{validation.fieldErrors.targetEndpoint}</span>
        ) : null}
      </label>
      <label className="field field-span-full">
        <span>训练说明（可选）</span>
        <textarea
          value={draft.comment}
          onChange={(event) => onChange("comment", event.target.value)}
          placeholder="可选，仅用于备注该任务，不影响训练过程"
          data-testid="submission-comment-textarea"
        />
        {validation.fieldErrors.comment ? (
          <span className="error-text">{validation.fieldErrors.comment}</span>
        ) : null}
      </label>

      <label className="field">
        <span>部署方式</span>
        <select
          value={draft.deploymentMode}
          onChange={(event) => onChange("deploymentMode", event.target.value)}
          data-testid="submission-deployment-mode-select"
        >
          <option value="auto">自动部署（训练成功后自动上线）</option>
          <option value="manual">手动部署（训练成功后手动触发）</option>
        </select>
      </label>

      <div className="checkbox-field field-span-full">
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={draft.isRecurring}
            onChange={(event) => onChange("isRecurring", event.target.checked)}
            data-testid="submission-recurring-checkbox"
          />
          <span>周期性任务（定时重复执行）</span>
        </label>
      </div>

      {draft.isRecurring ? (
        <>
          <label className="field">
            <span>开始时间</span>
            <input
              type="datetime-local"
              value={draft.scheduleStart}
              onChange={(event) => onChange("scheduleStart", event.target.value)}
              data-testid="submission-schedule-start-input"
            />
            {validation.fieldErrors.scheduleStart ? (
              <span className="error-text">
                {validation.fieldErrors.scheduleStart}
              </span>
            ) : null}
          </label>
          <label className="field">
            <span>执行周期</span>
            <div className="inline-row">
              <span>每</span>
              <input
                type="number"
                min={1}
                value={draft.intervalValue}
                onChange={(event) => onChange("intervalValue", event.target.value)}
                data-testid="submission-interval-value-input"
                style={{ width: "6rem" }}
              />
              <select
                value={draft.intervalUnit}
                onChange={(event) => onChange("intervalUnit", event.target.value)}
                data-testid="submission-interval-unit-select"
              >
                <option value="days">天</option>
                <option value="hours">小时</option>
                <option value="minutes">分钟</option>
              </select>
            </div>
            {validation.fieldErrors.intervalValue ? (
              <span className="error-text">
                {validation.fieldErrors.intervalValue}
              </span>
            ) : null}
          </label>
        </>
      ) : null}
    </div>
  );
}
