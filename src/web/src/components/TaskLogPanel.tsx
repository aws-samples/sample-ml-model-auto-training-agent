// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { TaskLogEntry, TaskLogViewModel } from "../lib/types";

const RESERVED_LOG_KEYS = new Set([
  "timestamp",
  "level",
  "event_type",
  "attempt_number",
  "message",
  "details",
]);

function formatLogValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2);
}

function formatLogEntry(entry: TaskLogEntry): string {
  const segments: string[] = [];
  if (typeof entry.timestamp === "string" && entry.timestamp) {
    segments.push(`[${entry.timestamp}]`);
  }
  if (typeof entry.level === "string" && entry.level) {
    segments.push(`[${entry.level}]`);
  }
  if (typeof entry.event_type === "string" && entry.event_type) {
    segments.push(entry.event_type);
  }
  if (typeof entry.attempt_number === "number") {
    segments.push(`attempt=${entry.attempt_number}`);
  }
  if (typeof entry.message === "string" && entry.message) {
    segments.push(entry.message);
  }
  if (entry.details !== undefined) {
    segments.push(formatLogValue(entry.details));
  }

  const extraFields = Object.fromEntries(
    Object.entries(entry).filter(([key]) => !RESERVED_LOG_KEYS.has(key)),
  );
  if (Object.keys(extraFields).length > 0) {
    segments.push(formatLogValue(extraFields));
  }
  return segments.join(" ");
}

interface TaskLogPanelProps {
  logs: TaskLogViewModel | null;
  isLoading: boolean;
  errorMessage: string;
}

export function TaskLogPanel({
  logs,
  isLoading,
  errorMessage,
}: TaskLogPanelProps) {
  return (
    <section className="operations-panel">
      <h3 className="panel-title">运行日志</h3>
      {errorMessage ? <div className="error-text">{errorMessage}</div> : null}
      <div className="log-box" data-testid="task-log-panel">
        {isLoading ? (
          <div className="muted-text">日志加载中...</div>
        ) : !logs ? (
          <div className="muted-text">选择任务后查看日志。</div>
        ) : logs.logs.length === 0 ? (
          <div className="muted-text">当前还没有日志，任务可能刚启动。</div>
        ) : (
          <div className="log-content">
            {logs.logs.map((entry, index) => (
              <pre
                key={`${entry.timestamp ?? "log"}-${index}`}
                className="log-entry"
                data-testid={`task-log-entry-${index}`}
              >
                {formatLogEntry(entry)}
              </pre>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
