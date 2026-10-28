// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useState } from "react";

import type { ApiClient } from "../lib/api";
import { downloadBase64 } from "../lib/download";
import type { InferTaskSummaryViewModel } from "../lib/types";

// Terminal success status for an inference task (mirrors backend
// TASK_STATUS_SUCCEEDED).
const STATUS_SUCCEEDED = "SUCCEEDED";

interface InferTaskDetailViewProps {
  task: InferTaskSummaryViewModel;
  apiClient: ApiClient;
  onBack: () => void;
}

const INFO_ROWS: ReadonlyArray<{
  key: keyof InferTaskSummaryViewModel;
  label: string;
}> = [
  { key: "task_name", label: "任务名称" },
  { key: "task_id", label: "任务 ID" },
  { key: "status", label: "状态" },
  { key: "task_type", label: "任务类型" },
  { key: "model_name", label: "模型名称" },
  { key: "target_endpoint", label: "推理端点" },
  { key: "data_location", label: "预测数据位置" },
  { key: "target_column", label: "目标列名" },
  { key: "output_location", label: "输出位置" },
  { key: "result_location", label: "结果位置" },
  { key: "prediction_count", label: "预测条数" },
  { key: "description", label: "任务说明" },
  { key: "submitted_by", label: "提交人" },
  { key: "created_at", label: "创建时间" },
  { key: "updated_at", label: "更新时间" },
];

export function InferTaskDetailView({
  task,
  apiClient,
  onBack,
}: InferTaskDetailViewProps) {
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");

  const canDownload =
    task.status === STATUS_SUCCEEDED && !!(task.result_location || "").trim();

  async function handleDownload() {
    if (downloading) return;
    try {
      setDownloading(true);
      setDownloadError("");
      const { content_base64, file_name, content_type } =
        await apiClient.getInferResultDownload(task.task_id);
      // China blocks browser access to presigned S3 URLs, so the result comes
      // back inline as base64 and we reconstruct the file locally.
      downloadBase64(content_base64, file_name, content_type);
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : "下载失败");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <section className="detail-view" data-testid="infer-task-detail-view">
      <div className="detail-header">
        <button
          type="button"
          className="secondary-button"
          onClick={onBack}
          data-testid="infer-task-detail-back-button"
        >
          ← 返回推理任务列表
        </button>
        <div className="detail-header-main">
          <div className="detail-title">{task.task_name}</div>
          <div className="muted-text">{task.task_id}</div>
        </div>
        {canDownload ? (
          <button
            type="button"
            className="primary-button"
            onClick={() => void handleDownload()}
            disabled={downloading}
            data-testid="infer-result-download-button"
          >
            {downloading ? "获取下载地址..." : "下载预测结果"}
          </button>
        ) : null}
        <span className="status-badge">{task.status}</span>
      </div>

      {downloadError ? (
        <div className="error-text" data-testid="infer-download-error">
          {downloadError}
        </div>
      ) : null}

      <dl className="detail-info-grid" data-testid="infer-task-detail-info">
        {INFO_ROWS.map((row) => (
          <div key={row.key} className="detail-info-row">
            <dt>{row.label}</dt>
            <dd>{String(task[row.key] ?? "—")}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
