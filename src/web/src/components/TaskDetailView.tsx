// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useState } from "react";

import { TaskLogPanel } from "./TaskLogPanel";
import { EvaluationReportPanel } from "./EvaluationReportPanel";
import type {
  EvaluationReportViewModel,
  TaskExecution,
  TaskLogViewModel,
  TaskSummaryViewModel,
} from "../lib/types";

type DetailTab = "info" | "executions" | "logs" | "evaluation";

const CANCELLABLE_STATUSES = new Set([
  "SUBMITTED",
  "PENDING",
  "QUEUED",
  "RUNNING",
]);

// A periodic task can be stopped from any non-cancelled state (to halt future
// runs) even when idle between executions.
const NON_CANCELLABLE_STATUSES = new Set(["CANCELLED"]);

interface TaskDetailViewProps {
  task: TaskSummaryViewModel;
  logs: TaskLogViewModel | null;
  logsLoading: boolean;
  logError: string;
  onBack: () => void;
  onLoadLogs: (executionId?: string) => void;
  onCancel: () => void;
  cancelling: boolean;
  onDeploy: () => void;
  deploying: boolean;
  evaluation: EvaluationReportViewModel | null;
  evaluationLoading: boolean;
  evaluationError: string;
  onLoadEvaluation: (executionId?: string) => void;
}

const DEPLOYMENT_MODE_LABELS: Record<string, string> = {
  auto: "自动部署",
  manual: "手动部署",
};

function formatTime(iso?: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

const INFO_ROWS: ReadonlyArray<{ key: keyof TaskSummaryViewModel; label: string }> = [
  { key: "task_name", label: "任务名称" },
  { key: "task_id", label: "任务 ID" },
  { key: "status", label: "状态" },
  { key: "task_type", label: "任务类型" },
  { key: "model_name", label: "模型名称" },
  { key: "target_endpoint", label: "目标端点" },
  { key: "resolved_image_tag", label: "镜像标签" },
  { key: "deployment_mode", label: "部署方式" },
  { key: "submitted_by", label: "提交人" },
  { key: "created_at", label: "创建时间" },
  { key: "updated_at", label: "更新时间" },
];

export function TaskDetailView({
  task,
  logs,
  logsLoading,
  logError,
  onBack,
  onLoadLogs,
  onCancel,
  cancelling,
  onDeploy,
  deploying,
  evaluation,
  evaluationLoading,
  evaluationError,
  onLoadEvaluation,
}: TaskDetailViewProps) {
  const [tab, setTab] = useState<DetailTab>("info");
  const [selectedExecutionId, setSelectedExecutionId] = useState<string>("");
  const isPeriodical = !!task.is_periodical;
  const executions: TaskExecution[] = task.executions ?? [];
  // Newest execution first.
  const orderedExecutions = [...executions].reverse();
  // Periodic tasks can be cancelled from any non-cancelled state; one-off tasks
  // only from the active statuses.
  const canCancel = isPeriodical
    ? !NON_CANCELLABLE_STATUSES.has(task.status)
    : CANCELLABLE_STATUSES.has(task.status);
  // Manual deploy button: only for manual-mode tasks that have succeeded (and
  // are not already deploying/deployed).
  const canDeploy =
    task.deployment_mode === "manual" && task.status === "SUCCEEDED";

  return (
    <section className="detail-view" data-testid="task-detail-view">
      <div className="detail-header">
        <button
          type="button"
          className="secondary-button"
          onClick={onBack}
          data-testid="task-detail-back-button"
        >
          ← 返回任务列表
        </button>
        <div className="detail-header-main">
          <div className="detail-title">{task.task_name}</div>
          <div className="muted-text">{task.task_id}</div>
        </div>
        <span className="status-badge">{task.status}</span>
        {canCancel ? (
          <button
            type="button"
            className="danger-button"
            onClick={() => {
              if (window.confirm("确定取消该任务？将在 AWS Batch 终止其执行。")) {
                onCancel();
              }
            }}
            disabled={cancelling}
            data-testid="task-cancel-button"
          >
            {cancelling ? "取消中..." : "取消任务"}
          </button>
        ) : null}
        {canDeploy ? (
          <button
            type="button"
            className="primary-button"
            onClick={() => {
              if (window.confirm("确定部署该模型到 SageMaker Endpoint？")) {
                onDeploy();
              }
            }}
            disabled={deploying}
            data-testid="task-deploy-button"
          >
            {deploying ? "部署中..." : "部署到 SageMaker Endpoint"}
          </button>
        ) : null}
      </div>

      <div className="detail-tabs" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "info"}
          className={`detail-tab${tab === "info" ? " is-active" : ""}`}
          onClick={() => setTab("info")}
          data-testid="task-detail-tab-info"
        >
          任务信息
        </button>
        {isPeriodical ? (
          <button
            type="button"
            role="tab"
            aria-selected={tab === "executions"}
            className={`detail-tab${tab === "executions" ? " is-active" : ""}`}
            onClick={() => setTab("executions")}
            data-testid="task-detail-tab-executions"
          >
            执行详情
          </button>
        ) : null}
        <button
          type="button"
          role="tab"
          aria-selected={tab === "logs"}
          className={`detail-tab${tab === "logs" ? " is-active" : ""}`}
          onClick={() => {
            setTab("logs");
            // For periodic tasks, default to the newest execution's logs.
            if (isPeriodical) {
              const execId =
                selectedExecutionId || orderedExecutions[0]?.execution_id || "";
              if (execId && execId !== selectedExecutionId) {
                setSelectedExecutionId(execId);
              }
              onLoadLogs(execId || undefined);
            } else if (!logs && !logsLoading) {
              onLoadLogs();
            }
          }}
          data-testid="task-detail-tab-logs"
        >
          任务日志
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "evaluation"}
          className={`detail-tab${tab === "evaluation" ? " is-active" : ""}`}
          onClick={() => {
            setTab("evaluation");
            // For periodic tasks, default to the newest execution's report.
            if (isPeriodical) {
              const execId =
                selectedExecutionId || orderedExecutions[0]?.execution_id || "";
              if (execId && execId !== selectedExecutionId) {
                setSelectedExecutionId(execId);
              }
              onLoadEvaluation(execId || undefined);
            } else if (!evaluation && !evaluationLoading) {
              onLoadEvaluation();
            }
          }}
          data-testid="task-detail-tab-evaluation"
        >
          训练结果
        </button>
      </div>

      {tab === "info" ? (
        <dl className="detail-info-grid" data-testid="task-detail-info">
          {INFO_ROWS.map((row) => (
            <div key={row.key} className="detail-info-row">
              <dt>{row.label}</dt>
              <dd>
                {row.key === "deployment_mode"
                  ? DEPLOYMENT_MODE_LABELS[String(task.deployment_mode ?? "auto")] ??
                    String(task.deployment_mode ?? "—")
                  : String(task[row.key] ?? "—")}
              </dd>
            </div>
          ))}
        </dl>
      ) : tab === "executions" ? (
        <div className="execution-panel" data-testid="task-detail-executions">
          {orderedExecutions.length === 0 ? (
            <div className="empty-state">尚无执行记录，等待首次定时触发。</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>执行时间</th>
                  <th>状态</th>
                  <th>结束时间</th>
                  <th>日志</th>
                </tr>
              </thead>
              <tbody>
                {orderedExecutions.map((exe) => (
                  <tr key={exe.execution_id} data-testid="execution-row">
                    <td>{formatTime(exe.started_at)}</td>
                    <td>
                      <span className="status-badge">{exe.status}</span>
                    </td>
                    <td className="muted-text">{formatTime(exe.finished_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={() => {
                          setSelectedExecutionId(exe.execution_id);
                          setTab("logs");
                          onLoadLogs(exe.execution_id);
                        }}
                        data-testid="execution-view-logs"
                      >
                        查看日志
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : tab === "logs" ? (
        <>
          {isPeriodical && orderedExecutions.length > 0 ? (
            <div className="execution-log-selector">
              <label>
                选择执行：
                <select
                  value={selectedExecutionId}
                  onChange={(event) => {
                    setSelectedExecutionId(event.target.value);
                    onLoadLogs(event.target.value || undefined);
                  }}
                  data-testid="execution-log-select"
                >
                  {orderedExecutions.map((exe) => (
                    <option key={exe.execution_id} value={exe.execution_id}>
                      {formatTime(exe.started_at)}（{exe.status}）
                    </option>
                  ))}
                </select>
              </label>
            </div>
          ) : null}
          <TaskLogPanel logs={logs} isLoading={logsLoading} errorMessage={logError} />
        </>
      ) : (
        <>
          {isPeriodical && orderedExecutions.length > 0 ? (
            <div className="execution-log-selector">
              <label>
                选择执行：
                <select
                  value={selectedExecutionId}
                  onChange={(event) => {
                    setSelectedExecutionId(event.target.value);
                    onLoadEvaluation(event.target.value || undefined);
                  }}
                  data-testid="execution-evaluation-select"
                >
                  {orderedExecutions.map((exe) => (
                    <option key={exe.execution_id} value={exe.execution_id}>
                      {formatTime(exe.started_at)}（{exe.status}）
                    </option>
                  ))}
                </select>
              </label>
            </div>
          ) : null}
          <EvaluationReportPanel
            report={evaluation?.report ?? null}
            isLoading={evaluationLoading}
            errorMessage={evaluationError}
          />
        </>
      )}
    </section>
  );
}
