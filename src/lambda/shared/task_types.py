# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Task-type registry.

The supported task types are defined ONCE in cdk.json (context "taskTypes")
and injected into the Lambdas as the ``TASK_TYPES_JSON`` environment variable.
Each entry describes one task type end to end:

    {
      "id": "timeseries_forecast",          # the task_type value on the API
      "label": "时序预测（示例）",             # human-friendly name (frontend dropdown)
      "skill": "timeseries-forecast-train.md",     # domain skill baked into the image
      "groundTruthFile": "ground_truth_hourly.csv",  # under <pkg>/data/
      "compareScript": "compare_timeseries.py"                  # under <pkg>/eval/
    }

Adding a task type is therefore a cdk.json change plus a domain skill file and
an evaluation package — no code edits. This module is the single place that
parses that config; all Lambdas import from here.
"""
from __future__ import annotations

import json
import os
from typing import Any

from shared.constants import TASK_TYPE_TIMESERIES_FORECAST

# Fallback used when TASK_TYPES_JSON is absent/empty (e.g. older deployments or
# unit tests): the original single timeseries-forecast type, with the file names
# that were previously hard-coded in batch_adapter.py.
_DEFAULT_TASK_TYPES: tuple[dict[str, Any], ...] = (
    {
        "id": TASK_TYPE_TIMESERIES_FORECAST,
        "label": "时序预测（示例）",
        "skill": "timeseries-forecast-train.md",
        "groundTruthFile": "ground_truth_hourly.csv",
        "compareScript": "compare_timeseries.py",
        # Independent anti-cheat verification: the Runner downloads the ground
        # truth + compare script and recomputes the metric itself. Defaults ON.
        "enforceVerification": True,
    },
)


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes")


def _coerce_optional_int(value: Any) -> int | None:
    """None when absent/unparseable, so callers can tell "not configured" apart
    from a configured 0 (which is meaningful — see requiredPredictionCount)."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    task_id = str(raw.get("id") or "").strip()
    if not task_id:
        return None
    return {
        "id": task_id,
        "label": str(raw.get("label") or task_id).strip(),
        "skill": str(raw.get("skill") or "").strip(),
        "groundTruthFile": str(raw.get("groundTruthFile") or "").strip(),
        "compareScript": str(raw.get("compareScript") or "").strip(),
        # How many prediction*.csv files a run must produce to pass the lenient
        # (enforceVerification=false) check. None means "use the runtime default
        # of 1"; file-transfer types (byo_deploy) set 0 because they run no
        # inference at all. Read by api/batch_adapter to build the container env.
        "requiredPredictionCount": _coerce_optional_int(
            raw.get("requiredPredictionCount")
        ),
        # Optional per-type SageMaker endpoint instance. Empty means "use the
        # stack-wide SAGEMAKER_INSTANCE_TYPE default" (a GPU instance, needed by
        # the AutoGluon models). CPU-only types — byo_deploy, which just
        # transfers an algorithm team's scikit-learn/XGBoost model — set it to a
        # CPU instance so they neither pay for a GPU nor consume the very small
        # GPU endpoint quota. Read by deployment/handler._resolve_instance_type.
        "instanceType": str(raw.get("instanceType") or "").strip(),
        # Default ON so existing configs (which omit the field) keep verifying.
        "enforceVerification": _coerce_bool(
            raw.get("enforceVerification"), default=True
        ),
    }


def load_task_types(raw_json: str | None = None) -> list[dict[str, Any]]:
    """Return the configured task types (falls back to the default task type)."""
    if raw_json is None:
        raw_json = os.environ.get("TASK_TYPES_JSON", "")
    raw_json = (raw_json or "").strip()
    if not raw_json:
        return [dict(entry) for entry in _DEFAULT_TASK_TYPES]
    try:
        parsed = json.loads(raw_json)
    except (ValueError, TypeError):
        return [dict(entry) for entry in _DEFAULT_TASK_TYPES]
    entries = [
        coerced
        for coerced in (_coerce_entry(item) for item in parsed or [])
        if coerced is not None
    ]
    return entries or [dict(entry) for entry in _DEFAULT_TASK_TYPES]


def task_type_ids(raw_json: str | None = None) -> set[str]:
    return {entry["id"] for entry in load_task_types(raw_json)}


def get_task_type(task_type: str, raw_json: str | None = None) -> dict[str, Any] | None:
    for entry in load_task_types(raw_json):
        if entry["id"] == task_type:
            return entry
    return None
