// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useState } from "react";

import type { ModelSummaryViewModel } from "../lib/types";

type ModelDetailTab = "info";

interface ModelDetailViewProps {
  model: ModelSummaryViewModel;
  onBack: () => void;
}

const INFO_ROWS: ReadonlyArray<{ key: keyof ModelSummaryViewModel; label: string }> = [
  { key: "model_record_id", label: "模型记录 ID" },
  { key: "model_name", label: "模型名称" },
  { key: "status", label: "部署状态" },
  { key: "provider_status", label: "SageMaker 状态" },
  { key: "target_endpoint", label: "目标端点" },
  { key: "resolved_image_tag", label: "镜像标签" },
  { key: "task_id", label: "来源任务 ID" },
  { key: "endpoint_arn", label: "Endpoint ARN" },
  { key: "endpoint_config_name", label: "Endpoint 配置名" },
  { key: "sagemaker_model_name", label: "SageMaker 模型名" },
  { key: "failure_reason", label: "失败原因" },
  { key: "created_at", label: "创建时间" },
  { key: "updated_at", label: "更新时间" },
];

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

export function ModelDetailView({ model, onBack }: ModelDetailViewProps) {
  const [tab] = useState<ModelDetailTab>("info");

  return (
    <section className="detail-view" data-testid="model-detail-view">
      <div className="detail-header">
        <button
          type="button"
          className="secondary-button"
          onClick={onBack}
          data-testid="model-detail-back-button"
        >
          ← 返回模型列表
        </button>
        <div>
          <div className="detail-title">{model.model_name}</div>
          <div className="muted-text">{model.model_record_id}</div>
        </div>
        <span className="status-badge">{model.status}</span>
      </div>

      <div className="detail-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "info"}
          className="detail-tab is-active"
          data-testid="model-detail-tab-info"
        >
          模型信息
        </button>
      </div>

      <dl className="detail-info-grid" data-testid="model-detail-info">
        {INFO_ROWS.map((row) => (
          <div key={row.key} className="detail-info-row">
            <dt>{row.label}</dt>
            <dd>{formatValue(model[row.key])}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
