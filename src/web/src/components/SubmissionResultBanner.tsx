// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0
import type { SubmissionResultViewModel } from "../lib/types";

interface SubmissionResultBannerProps {
  result: SubmissionResultViewModel | null;
  errorMessage: string;
}

export function SubmissionResultBanner({
  result,
  errorMessage,
}: SubmissionResultBannerProps) {
  if (result) {
    return (
      <div
        className="banner banner-success"
        data-testid="submission-result-success-banner"
      >
        提交成功: {result.taskId} / {result.status}
      </div>
    );
  }
  if (errorMessage) {
    return (
      <div
        className="banner banner-error"
        data-testid="submission-result-error-banner"
      >
        {errorMessage}
      </div>
    );
  }
  return null;
}
