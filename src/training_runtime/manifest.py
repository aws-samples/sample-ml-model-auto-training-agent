# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from training_runtime.contracts import RuntimeContract
from training_runtime.outcomes import TerminalOutcome
from training_runtime.providers import ProviderSelection
from training_runtime.skills import EffectiveSkillBundle
from training_runtime.verification import VerificationResult


@dataclass(frozen=True)
class AttemptSummary:
    attempt_number: int
    output_dir: str
    claimed_metric_value: float | None
    verification_result: VerificationResult


def build_runtime_manifest(
    *,
    contract: RuntimeContract,
    provider_selection: ProviderSelection,
    skill_bundle: EffectiveSkillBundle,
    attempts: list[AttemptSummary],
    terminal_outcome: TerminalOutcome,
    log_reference: dict[str, str] | None,
    published_artifacts: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "task_id": contract.task_id,
        "provider_mode": provider_selection.provider_mode,
        "model_id": provider_selection.model_id,
        "skills": skill_bundle.sources,
        "attempts": [
            {
                "attempt_number": attempt.attempt_number,
                "output_dir": attempt.output_dir,
                "claimed_metric_value": attempt.claimed_metric_value,
                "verification": {
                    "achieved": attempt.verification_result.achieved,
                    "metric_name": attempt.verification_result.metric_name,
                    "metric_value": attempt.verification_result.metric_value,
                    "threshold": attempt.verification_result.threshold,
                    "threshold_direction": attempt.verification_result.threshold_direction,
                    "reason": attempt.verification_result.reason,
                    "missing_artifacts": list(attempt.verification_result.missing_artifacts),
                    "report_path": attempt.verification_result.report_path,
                    "source": attempt.verification_result.source,
                },
            }
            for attempt in attempts
        ],
        "terminal_outcome": {
            "status": terminal_outcome.status,
            "achieved": terminal_outcome.achieved,
            "reason": terminal_outcome.reason,
            "final_metric_value": terminal_outcome.final_metric_value,
        },
        "logs": log_reference or {},
        "published_artifacts": published_artifacts,
        "output_prefix": contract.output_prefix,
    }


def write_manifest(output_dir: Path, manifest: dict[str, Any]) -> Path:
    manifest_path = output_dir / "runtime_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path
