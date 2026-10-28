// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { TrainingPlanPreview } from "../lib/types";

interface TrainingPlanPreviewPanelProps {
  preview: TrainingPlanPreview | null;
  confirmed: boolean;
  onConfirmationChange: (confirmed: boolean) => void;
  onConfirm: () => void;
  disabled: boolean;
}

export function TrainingPlanPreviewPanel({
  preview,
  confirmed,
  onConfirmationChange,
  onConfirm,
  disabled,
}: TrainingPlanPreviewPanelProps) {
  return (
    <section className="section">
      <h3 className="panel-title">训练计划预览</h3>
      {preview ? (
        <div className="preview-list" data-testid="training-plan-preview-panel">
          <div className="preview-item">提交人: {preview.submittedBy}</div>
          <div className="preview-item">镜像标签: {preview.resolvedImageTag}</div>
          {preview.payload.comment ? (
            <div className="preview-item">
              <strong>任务说明</strong>
              <div className="muted-text">{preview.payload.comment}</div>
            </div>
          ) : null}
          <label className="preview-item" htmlFor="training-plan-confirm-checkbox">
            <input
              id="training-plan-confirm-checkbox"
              type="checkbox"
              checked={confirmed}
              onChange={(event) => onConfirmationChange(event.target.checked)}
              data-testid="training-plan-confirm-checkbox"
            />
            我已核对训练计划，并确认按该计划提交任务
          </label>
          <div className="actions">
            <button
              className="primary-button"
              type="button"
              onClick={onConfirm}
              disabled={disabled || !confirmed}
              data-testid="training-plan-confirm-button"
            >
              确认并提交
            </button>
          </div>
        </div>
      ) : (
        <div className="preview-item muted-text">
          先通过本地校验并生成预览。
        </div>
      )}
    </section>
  );
}
