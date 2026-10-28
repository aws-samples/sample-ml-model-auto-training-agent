# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

from dataclasses import dataclass

from training_runtime.verification import VerificationResult


@dataclass(frozen=True)
class TerminalOutcome:
    status: str
    achieved: bool
    reason: str
    final_metric_value: float | None


def determine_terminal_outcome(
    *,
    verification_result: VerificationResult | None,
    execution_error: str | None = None,
) -> TerminalOutcome:
    if execution_error:
        return TerminalOutcome(
            status="FAILED",
            achieved=False,
            reason=execution_error,
            final_metric_value=verification_result.metric_value if verification_result else None,
        )
    if verification_result is None:
        return TerminalOutcome(
            status="FAILED",
            achieved=False,
            reason="Verification result unavailable",
            final_metric_value=None,
        )
    if verification_result.achieved:
        return TerminalOutcome(
            status="SUCCEEDED",
            achieved=True,
            reason=verification_result.reason,
            final_metric_value=verification_result.metric_value,
        )
    return TerminalOutcome(
        status="FAILED",
        achieved=False,
        reason=verification_result.reason,
        final_metric_value=verification_result.metric_value,
    )
