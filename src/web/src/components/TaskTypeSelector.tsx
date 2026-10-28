// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { TaskType, TaskTypeOption } from "../lib/types";

interface TaskTypeSelectorProps {
  selectedTaskType: TaskType;
  onSelect: (taskType: TaskType) => void;
  options?: TaskTypeOption[];
}

// Fallback shown before the backend list loads (or if it fails); matches the
// built-in default the server falls back to.
const FALLBACK_OPTIONS: TaskTypeOption[] = [
  { id: "timeseries_forecast", label: "时序预测（示例）" },
];

export function TaskTypeSelector({
  selectedTaskType,
  onSelect,
  options,
}: TaskTypeSelectorProps) {
  const items = options && options.length > 0 ? options : FALLBACK_OPTIONS;
  return (
    <section className="section">
      <label className="field">
        <span>任务类型</span>
        <select
          value={selectedTaskType}
          onChange={(event) => onSelect(event.target.value as TaskType)}
          data-testid="task-type-selector"
        >
          {items.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
    </section>
  );
}
