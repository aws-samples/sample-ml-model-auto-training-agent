// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useEffect, useMemo, useState } from "react";

import { S3PathPicker } from "./S3PathPicker";
import { TaskTypeSelector } from "./TaskTypeSelector";
import type { ApiClient } from "../lib/api";
import type {
  ConfirmedInferSubmission,
  FrontendRuntimeConfiguration,
  InferSubmissionDraft,
  ModelSummaryViewModel,
  SubmissionResultViewModel,
  TaskTypeOption,
} from "../lib/types";

// Fixed S3 subfolder prefixes (see backend shared/constants.py).
const INFERENCE_DATA_PREFIX = "inference_data/";
const INFERENCE_OUTPUT_PREFIX = "inference_output/";

const INITIAL_DRAFT: InferSubmissionDraft = {
  taskName: "",
  taskType: "timeseries_forecast",
  dataLocation: "inference_data/predict.csv",
  outputLocation: "inference_output/timeseries-infer",
  modelName: "",
  targetEndpoint: "",
  targetColumn: "target",
  description: "",
};

// Mirror of the backend validators: names must match NAME_PATTERN, locations
// must start with an allowed prefix (or s3://) and contain no "..".
const NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{2,62}$/;

function isValidLocation(value: string, prefixes: string[]): boolean {
  if (!value || value.includes("..")) {
    return false;
  }
  if (value.startsWith("s3://")) {
    return /^s3:\/\/[A-Za-z0-9._/\-]+$/.test(value);
  }
  if (!prefixes.some((p) => value.startsWith(p))) {
    return false;
  }
  return /^[A-Za-z0-9._/\-]+$/.test(value);
}

interface InferTaskWizardProps {
  config: FrontendRuntimeConfiguration;
  apiClient: ApiClient;
  onSubmit: (
    payload: ConfirmedInferSubmission,
  ) => Promise<SubmissionResultViewModel>;
  onFinished: () => void;
}

export function InferTaskWizard({
  config,
  apiClient,
  onSubmit,
  onFinished,
}: InferTaskWizardProps) {
  const [draft, setDraft] = useState<InferSubmissionDraft>(INITIAL_DRAFT);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [result, setResult] = useState<SubmissionResultViewModel | null>(null);
  const [models, setModels] = useState<ModelSummaryViewModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelsError, setModelsError] = useState("");
  const [taskTypes, setTaskTypes] = useState<TaskTypeOption[]>([]);

  // Task types come from the backend (cdk.json "taskTypes"), so the dropdown
  // matches what the server accepts. Default the draft to the first configured
  // type once loaded. Mirrors TaskWizard's behaviour.
  useEffect(() => {
    let cancelled = false;
    apiClient
      .getTaskTypes()
      .then((options) => {
        if (cancelled || options.length === 0) return;
        setTaskTypes(options);
        setDraft((current) =>
          options.some((option) => option.id === current.taskType)
            ? current
            : { ...current, taskType: options[0].id },
        );
      })
      .catch(() => {
        // Keep the built-in default (timeseries_forecast) if the lookup fails.
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient]);

  // Load the model registry so the user picks a model from a dropdown instead of
  // typing its name; selecting a model auto-fills the inference endpoint from the
  // model's deployment target. Deployed models are offered first.
  useEffect(() => {
    let cancelled = false;
    setModelsLoading(true);
    setModelsError("");
    apiClient
      .getModels()
      .then((items) => {
        if (cancelled) return;
        setModels(items);
      })
      .catch((err) => {
        if (cancelled) return;
        setModelsError(err instanceof Error ? err.message : "模型列表加载失败");
      })
      .finally(() => {
        if (!cancelled) setModelsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [apiClient]);

  // Deduplicate by model_name (a model may have several registry rows across
  // redeploys); prefer a DEPLOYED row so the offered endpoint is live.
  const selectableModels = useMemo(() => {
    const byName = new Map<string, ModelSummaryViewModel>();
    for (const model of models) {
      if (!model.model_name) continue;
      const existing = byName.get(model.model_name);
      if (!existing || (model.status === "DEPLOYED" && existing.status !== "DEPLOYED")) {
        byName.set(model.model_name, model);
      }
    }
    return [...byName.values()].sort((a, b) => {
      // DEPLOYED first, then by name.
      if ((a.status === "DEPLOYED") !== (b.status === "DEPLOYED")) {
        return a.status === "DEPLOYED" ? -1 : 1;
      }
      return a.model_name.localeCompare(b.model_name);
    });
  }, [models]);

  function selectModel(modelName: string) {
    setSubmitError("");
    const model = selectableModels.find((m) => m.model_name === modelName);
    setDraft((current) => ({
      ...current,
      modelName,
      // Auto-fill the inference endpoint from the chosen model's deployment
      // target; the user no longer types it.
      targetEndpoint: model?.target_endpoint || current.targetEndpoint,
    }));
  }

  const fieldErrors = useMemo(() => {
    const errors: Partial<Record<keyof InferSubmissionDraft, string>> = {};
    if (draft.taskName && draft.taskName.length > 100) {
      errors.taskName = "任务名称不超过 100 字";
    }
    if (!isValidLocation(draft.dataLocation, ["inference_data/"])) {
      errors.dataLocation = "需以 inference_data/ 或 s3:// 开头，且不含 ..";
    }
    if (!isValidLocation(draft.outputLocation, ["inference_output/"])) {
      errors.outputLocation = "需以 inference_output/ 或 s3:// 开头，且不含 ..";
    }
    if (!NAME_PATTERN.test(draft.modelName)) {
      errors.modelName = "3-63 位字母数字及 . _ -，首位为字母数字";
    }
    if (!NAME_PATTERN.test(draft.targetEndpoint)) {
      errors.targetEndpoint = "3-63 位字母数字及 . _ -，首位为字母数字";
    }
    if (draft.description && draft.description.length > 1000) {
      errors.description = "任务说明不超过 1000 字";
    }
    // target_column is only used by time-series tasks (it names the historical
    // target column in the input CSV). Tabular-regression tasks predict from
    // feature rows and ignore it, so it is optional — only bound the length.
    if (draft.targetColumn.length > 128) {
      errors.targetColumn = "目标列名过长";
    }
    return errors;
  }, [draft]);

  const canSubmit = Object.keys(fieldErrors).length === 0;

  function updateDraft(field: keyof InferSubmissionDraft, value: string) {
    setSubmitError("");
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function handleSubmit() {
    if (!canSubmit || submitting) {
      return;
    }
    const payload: ConfirmedInferSubmission = {
      task_name: draft.taskName.trim() || draft.modelName,
      task_type: draft.taskType,
      data_location: draft.dataLocation.trim(),
      output_location: draft.outputLocation.trim(),
      model_name: draft.modelName.trim(),
      target_endpoint: draft.targetEndpoint.trim(),
      submitted_by: config.submittedBy,
      description: draft.description.trim(),
      target_column: draft.targetColumn.trim(),
      confirmation_flag: true,
    };
    try {
      setSubmitting(true);
      setSubmitError("");
      setResult(await onSubmit(payload));
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "推理任务提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  if (result) {
    return (
      <div className="wizard" data-testid="infer-task-wizard">
        <div className="banner banner-success" data-testid="infer-submit-success">
          <div>推理任务已提交</div>
          <div className="muted-text">任务 ID：{result.taskId}</div>
          <div className="muted-text">状态：{result.status}</div>
        </div>
        <div className="actions section">
          <button
            type="button"
            className="primary-button"
            onClick={onFinished}
            data-testid="infer-wizard-finish-button"
          >
            返回推理任务列表
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="wizard" data-testid="infer-task-wizard">
      <TaskTypeSelector
        options={taskTypes}
        selectedTaskType={draft.taskType}
        onSelect={(taskType) => updateDraft("taskType", taskType)}
      />

      <label className="field section">
        <span>任务名称</span>
        <input
          type="text"
          value={draft.taskName}
          placeholder="可选，默认取模型名称"
          onChange={(event) => updateDraft("taskName", event.target.value)}
          data-testid="infer-task-name-input"
        />
        {fieldErrors.taskName ? (
          <span className="error-text">{fieldErrors.taskName}</span>
        ) : null}
      </label>

      <label className="field section">
        <span>预测数据位置</span>
        <S3PathPicker
          apiClient={apiClient}
          prefix={INFERENCE_DATA_PREFIX}
          mode="file-or-dir"
          value={draft.dataLocation}
          onChange={(full) => updateDraft("dataLocation", full)}
          placeholder="predict.csv 或 子目录/"
          testId="infer-data-location-input"
        />
        {fieldErrors.dataLocation ? (
          <span className="error-text">{fieldErrors.dataLocation}</span>
        ) : null}
      </label>

      <label className="field section">
        <span>预测结果位置</span>
        <S3PathPicker
          apiClient={apiClient}
          prefix={INFERENCE_OUTPUT_PREFIX}
          mode="dir"
          value={draft.outputLocation}
          onChange={(full) => updateDraft("outputLocation", full)}
          placeholder="timeseries-infer（可填写新目录）"
          testId="infer-output-location-input"
        />
        {fieldErrors.outputLocation ? (
          <span className="error-text">{fieldErrors.outputLocation}</span>
        ) : null}
      </label>

      <label className="field section">
        <span>模型名称</span>
        <select
          value={draft.modelName}
          onChange={(event) => selectModel(event.target.value)}
          disabled={modelsLoading}
          data-testid="infer-model-name-select"
        >
          <option value="">
            {modelsLoading ? "模型加载中..." : "请选择模型"}
          </option>
          {selectableModels.map((model) => (
            <option key={model.model_name} value={model.model_name}>
              {model.model_name}
              {model.status === "DEPLOYED" ? "（已部署）" : `（${model.status}）`}
            </option>
          ))}
        </select>
        {modelsError ? (
          <span className="error-text">{modelsError}</span>
        ) : null}
        {!modelsError && !modelsLoading && selectableModels.length === 0 ? (
          <span className="helper-text">
            暂无可用模型，请先训练并部署模型后再创建推理任务。
          </span>
        ) : null}
        {fieldErrors.modelName ? (
          <span className="error-text">{fieldErrors.modelName}</span>
        ) : null}
      </label>

      <label className="field section">
        <span>推理端点</span>
        <input
          type="text"
          value={draft.targetEndpoint}
          readOnly
          placeholder="选择模型后自动填充"
          data-testid="infer-target-endpoint-input"
        />
        <span className="helper-text">
          自动使用所选模型的部署端点，无需手动填写。
        </span>
        {fieldErrors.targetEndpoint ? (
          <span className="error-text">{fieldErrors.targetEndpoint}</span>
        ) : null}
      </label>

      <label className="field section">
        <span>目标列名（可选）</span>
        <input
          type="text"
          value={draft.targetColumn}
          onChange={(event) => updateDraft("targetColumn", event.target.value)}
          placeholder="仅时序任务需要，如 sensor_value；表格回归任务留空即可"
          data-testid="infer-target-column-input"
        />
        <span className="helper-text">
          仅时序预测任务需要：指定输入 CSV 中历史目标值所在的列，推理时映射为模型的 target
          列。表格回归任务（如表格回归（示例））从特征行预测，无需填写。
        </span>
        {fieldErrors.targetColumn ? (
          <span className="error-text">{fieldErrors.targetColumn}</span>
        ) : null}
      </label>

      <label className="field section">
        <span>任务说明</span>
        <textarea
          value={draft.description}
          placeholder="可选"
          onChange={(event) => updateDraft("description", event.target.value)}
          data-testid="infer-description-textarea"
        />
        {fieldErrors.description ? (
          <span className="error-text">{fieldErrors.description}</span>
        ) : null}
      </label>

      {submitError ? (
        <div className="error-text section" data-testid="infer-submit-error">
          {submitError}
        </div>
      ) : null}

      <div className="actions section">
        <button
          type="button"
          className="primary-button"
          disabled={!canSubmit || submitting}
          onClick={() => void handleSubmit()}
          data-testid="infer-submit-button"
        >
          {submitting ? "提交中..." : "提交推理任务"}
        </button>
      </div>
    </div>
  );
}
