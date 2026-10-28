// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { DraftValidationResult } from "../lib/types";

interface ValidationSummaryProps {
  validation: DraftValidationResult;
}

export function ValidationSummary({ validation }: ValidationSummaryProps) {
  return (
    <section className="section">
      <h3 className="panel-title">校验摘要</h3>
      <div className="summary-list" data-testid="validation-summary">
        {validation.summaryErrors.length === 0 ? (
          <div className="summary-item">输入已满足预览条件。</div>
        ) : (
          validation.summaryErrors.map((error) => (
            <div key={error} className="summary-item error-text">
              {error}
            </div>
          ))
        )}
      </div>
    </section>
  );
}
