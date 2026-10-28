// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import { AdvancedOptionsPanel } from "./AdvancedOptionsPanel";
import { BasicFieldsSection } from "./BasicFieldsSection";
import type { ApiClient } from "../lib/api";
import type { DraftValidationResult, SubmissionDraft } from "../lib/types";

interface TaskDraftFormProps {
  draft: SubmissionDraft;
  validation: DraftValidationResult;
  onChange: (field: keyof SubmissionDraft, value: string | boolean) => void;
  apiClient: ApiClient;
}

export function TaskDraftForm({
  draft,
  validation,
  onChange,
  apiClient,
}: TaskDraftFormProps) {
  return (
    <section className="section" data-testid="task-draft-form">
      <BasicFieldsSection
        draft={draft}
        validation={validation}
        onChange={onChange}
        apiClient={apiClient}
      />
      <AdvancedOptionsPanel
        draft={draft}
        validation={validation}
        onChange={onChange}
      />
    </section>
  );
}
