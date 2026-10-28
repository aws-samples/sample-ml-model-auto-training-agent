// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { ModelSummaryViewModel } from "../lib/types";

interface ModelListPanelProps {
  models: ModelSummaryViewModel[];
  onSelectModel: (modelRecordId: string) => void;
  onRefresh: () => void;
  isLoading: boolean;
  errorMessage: string;
}

export function ModelListPanel({
  models,
  onSelectModel,
  onRefresh,
  isLoading,
  errorMessage,
}: ModelListPanelProps) {
  return (
    <section className="content-panel" data-testid="model-list-view">
      <div className="content-header">
        <h2 className="content-title">模型列表</h2>
        <div className="content-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={onRefresh}
            data-testid="model-list-refresh-button"
          >
            刷新
          </button>
        </div>
      </div>

      {errorMessage ? <div className="error-text">{errorMessage}</div> : null}

      {isLoading ? (
        <div className="muted-text">模型列表加载中...</div>
      ) : models.length === 0 ? (
        <div className="empty-state">暂无已部署模型记录。</div>
      ) : (
        <table className="data-table" data-testid="model-list-panel">
          <thead>
            <tr>
              <th>模型名称</th>
              <th>目标端点</th>
              <th>状态</th>
              <th>镜像标签</th>
              <th>更新时间</th>
            </tr>
          </thead>
          <tbody>
            {models.map((model) => (
              <tr
                key={model.model_record_id}
                className="data-row"
                tabIndex={0}
                role="button"
                onClick={() => onSelectModel(model.model_record_id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelectModel(model.model_record_id);
                  }
                }}
                data-testid={`model-list-item-${model.model_record_id}`}
              >
                <td>
                  <strong>{model.model_name}</strong>
                </td>
                <td>{model.target_endpoint}</td>
                <td>
                  <span className="status-badge">{model.status}</span>
                </td>
                <td>{model.resolved_image_tag}</td>
                <td className="muted-text">{model.updated_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
