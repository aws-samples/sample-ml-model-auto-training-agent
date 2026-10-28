// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { TaskSummaryViewModel } from "../lib/types";

interface TaskListPanelProps {
  tasks: TaskSummaryViewModel[];
  onSelectTask: (taskId: string) => void;
  onRefresh: () => void;
  onNewTask: () => void;
  isLoading: boolean;
  errorMessage: string;
}

export function TaskListPanel({
  tasks,
  onSelectTask,
  onRefresh,
  onNewTask,
  isLoading,
  errorMessage,
}: TaskListPanelProps) {
  return (
    <section className="content-panel" data-testid="task-list-view">
      <div className="content-header">
        <h2 className="content-title">任务列表</h2>
        <div className="content-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={onRefresh}
            data-testid="task-list-refresh-button"
          >
            刷新
          </button>
          <button
            className="primary-button"
            type="button"
            onClick={onNewTask}
            data-testid="new-task-button"
          >
            + 新建任务
          </button>
        </div>
      </div>

      {errorMessage ? <div className="error-text">{errorMessage}</div> : null}

      {isLoading ? (
        <div className="muted-text">任务列表加载中...</div>
      ) : tasks.length === 0 ? (
        <div className="empty-state">暂无任务，点击右上角「新建任务」开始。</div>
      ) : (
        <table className="data-table" data-testid="task-list-panel">
          <thead>
            <tr>
              <th>任务名称</th>
              <th>模型名称</th>
              <th>任务 ID</th>
              <th>状态</th>
              <th>镜像标签</th>
              <th>创建时间</th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr
                key={task.task_id}
                className="data-row"
                tabIndex={0}
                role="button"
                onClick={() => onSelectTask(task.task_id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelectTask(task.task_id);
                  }
                }}
                data-testid={`task-list-item-${task.task_id}`}
              >
                <td>
                  <strong>{task.task_name}</strong>
                </td>
                <td>{task.model_name}</td>
                <td className="muted-text">{task.task_id}</td>
                <td>
                  <span className="status-badge">{task.status}</span>
                </td>
                <td>{task.resolved_image_tag}</td>
                <td className="muted-text">{task.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
