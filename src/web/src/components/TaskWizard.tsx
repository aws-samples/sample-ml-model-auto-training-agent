// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { useEffect, useMemo, useState } from "react";

import { SubmissionResultBanner } from "./SubmissionResultBanner";
import { TaskDraftForm } from "./TaskDraftForm";
import { TaskTypeSelector } from "./TaskTypeSelector";
import { TrainingPlanPreviewPanel } from "./TrainingPlanPreviewPanel";
import { ValidationSummary } from "./ValidationSummary";
import { buildPreview } from "../lib/preview";
import type { ApiClient } from "../lib/api";
import type {
  FrontendRuntimeConfiguration,
  SubmissionDraft,
  SubmissionResultViewModel,
  TaskTypeOption,
} from "../lib/types";
import { validateDraft } from "../lib/validation";

type WizardStep = 1 | 2 | 3;

const STEP_LABELS: Record<WizardStep, string> = {
  1: "1. 填写任务信息",
  2: "2. 确认训练计划",
  3: "3. 完成",
};

const INITIAL_DRAFT: SubmissionDraft = {
  taskName: "",
  taskType: "timeseries_forecast",
  dataLocation: "data/train.csv",
  outputLocation: "output/timeseries-demo",
  modelName: "timeseries_model",
  targetEndpoint: "timeseries-endpoint",
  comment: "",
  imageTag: "",
  deploymentMode: "auto",
  isRecurring: false,
  scheduleStart: "",
  intervalValue: "1",
  intervalUnit: "days",
};

interface TaskWizardProps {
  config: FrontendRuntimeConfiguration;
  apiClient: ApiClient;
  onSubmit: (
    payload: ReturnType<typeof buildPreview>["payload"],
  ) => Promise<SubmissionResultViewModel>;
  onFinished: () => void;
}

export function TaskWizard({ config, apiClient, onSubmit, onFinished }: TaskWizardProps) {
  const [step, setStep] = useState<WizardStep>(1);
  const [draft, setDraft] = useState<SubmissionDraft>(INITIAL_DRAFT);
  const [confirmed, setConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [result, setResult] = useState<SubmissionResultViewModel | null>(null);
  const [taskTypes, setTaskTypes] = useState<TaskTypeOption[]>([]);

  // Task types come from the backend (cdk.json "taskTypes"), so the dropdown
  // always matches what the server will accept. Default the draft to the first
  // configured type once loaded.
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

  const validation = useMemo(() => validateDraft(draft), [draft]);
  const preview = useMemo(
    () => (validation.canPreview ? buildPreview(draft, config) : null),
    [draft, config, validation.canPreview],
  );

  function updateDraft(
    field: keyof SubmissionDraft,
    value: string | boolean,
  ) {
    setConfirmed(false);
    setSubmitError("");
    setDraft((current) => ({ ...current, [field]: value }));
  }

  async function handleConfirm() {
    if (!preview || !confirmed || submitting) {
      return;
    }
    try {
      setSubmitting(true);
      setSubmitError("");
      const submission = await onSubmit(preview.payload);
      setResult(submission);
      setStep(3);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "任务提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="wizard" data-testid="task-wizard">
      <ol className="wizard-steps" aria-label="提交步骤">
        {([1, 2, 3] as WizardStep[]).map((value) => (
          <li
            key={value}
            className={`wizard-step${step === value ? " is-active" : ""}${
              step > value ? " is-done" : ""
            }`}
            data-testid={`wizard-step-indicator-${value}`}
          >
            {STEP_LABELS[value]}
          </li>
        ))}
      </ol>

      {step === 1 ? (
        <div data-testid="wizard-step-1">
          <TaskTypeSelector
            options={taskTypes}
            selectedTaskType={draft.taskType}
            onSelect={(taskType) => updateDraft("taskType", taskType)}
          />
          <TaskDraftForm
            draft={draft}
            validation={validation}
            onChange={updateDraft}
            apiClient={apiClient}
          />
          <ValidationSummary validation={validation} />
          <div className="actions section">
            <button
              type="button"
              className="primary-button"
              disabled={!validation.canPreview}
              onClick={() => {
                setConfirmed(false);
                setStep(2);
              }}
              data-testid="wizard-next-button"
            >
              生成训练计划预览
            </button>
          </div>
        </div>
      ) : null}

      {step === 2 ? (
        <div data-testid="wizard-step-2">
          <TrainingPlanPreviewPanel
            preview={preview}
            confirmed={confirmed}
            onConfirmationChange={setConfirmed}
            onConfirm={() => void handleConfirm()}
            disabled={!preview || submitting}
          />
          <SubmissionResultBanner result={null} errorMessage={submitError} />
          <div className="actions section">
            <button
              type="button"
              className="secondary-button"
              onClick={() => setStep(1)}
              disabled={submitting}
              data-testid="wizard-back-button"
            >
              返回修改
            </button>
          </div>
        </div>
      ) : null}

      {step === 3 ? (
        <div data-testid="wizard-step-3">
          <SubmissionResultBanner result={result} errorMessage="" />
          <div className="actions section">
            <button
              type="button"
              className="primary-button"
              onClick={onFinished}
              data-testid="wizard-finish-button"
            >
              返回任务列表
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
