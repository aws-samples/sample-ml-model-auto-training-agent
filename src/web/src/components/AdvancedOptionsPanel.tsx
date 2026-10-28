// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { DraftValidationResult, SubmissionDraft } from "../lib/types";

interface AdvancedOptionsPanelProps {
  draft: SubmissionDraft;
  validation: DraftValidationResult;
  onChange: (field: keyof SubmissionDraft, value: string) => void;
}

export function AdvancedOptionsPanel({
  draft,
  validation,
  onChange,
}: AdvancedOptionsPanelProps) {
  // Show the image tag input directly — no collapsible "advanced options" panel.
  return (
    <section className="section" data-testid="advanced-options-panel">
      <label className="field section">
        <span>镜像标签</span>
        <input
          value={draft.imageTag}
          onChange={(event) => onChange("imageTag", event.target.value)}
          data-testid="submission-image-tag-input"
        />
        <span className="helper-text">留空时默认使用 stable。</span>
        {validation.fieldErrors.imageTag ? (
          <span className="error-text">{validation.fieldErrors.imageTag}</span>
        ) : null}
      </label>
    </section>
  );
}
