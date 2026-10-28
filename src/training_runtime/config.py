# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

from dataclasses import dataclass
import os


class ConfigError(ValueError):
    """Raised when runtime configuration is invalid."""


@dataclass(frozen=True)
class RuntimeConfig:
    workspace_root: str
    verify_dir: str
    baked_skills_dir: str
    general_skill_name: str
    domain_skill_name: str
    data_package_prefix: str
    ground_truth_key: str
    compare_script_key: str
    model_provider: str
    llm_model_id: str
    bedrock_region: str
    openai_base_url: str
    openai_api_key: str
    openai_api_key_secret_arn: str
    model_temperature: float
    model_max_tokens: int
    max_attempts: int
    target_metric_name: str
    target_metric_threshold: float
    target_metric_direction: str
    log_sync_seconds: int
    required_prediction_count: int
    min_matched_rows_per_device: int
    enforce_verification: bool
    conversation_window_messages: int


def _coerce_int(env: dict[str, str], name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"{name} must be an integer") from exc


def _coerce_float(env: dict[str, str], name: str, default: float) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"{name} must be a number") from exc


def load_runtime_config(env: dict[str, str] | None = None) -> RuntimeConfig:
    env_map = env or dict(os.environ)
    model_provider = (env_map.get("MODEL_PROVIDER") or "bedrock").strip().lower()
    if model_provider not in {"bedrock", "openai"}:
        raise ConfigError("MODEL_PROVIDER must be either 'bedrock' or 'openai'")
    target_metric_direction = (
        env_map.get("TARGET_METRIC_DIRECTION") or "lower_is_better"
    ).strip()
    if target_metric_direction not in {"lower_is_better", "higher_is_better"}:
        raise ConfigError(
            "TARGET_METRIC_DIRECTION must be either 'lower_is_better' or 'higher_is_better'"
        )
    data_package_prefix = (
        env_map.get("DATA_PACKAGE_PREFIX") or "sample_eval_package"
    ).strip()

    config = RuntimeConfig(
        workspace_root=(env_map.get("WORKSPACE_ROOT") or "/tmp/auto-training-agent-runtime").strip(),
        verify_dir=(env_map.get("VERIFY_DIR") or os.path.join("/tmp", "runner_verify")).strip(),
        baked_skills_dir=(env_map.get("BAKED_SKILLS_DIR") or os.path.join(os.path.dirname(__file__), "skills")).strip(),
        general_skill_name=(env_map.get("GENERAL_SKILL_NAME") or "general-agent-spec.md").strip(),
        domain_skill_name=(env_map.get("DOMAIN_SKILL_NAME") or "timeseries-forecast-train.md").strip(),
        data_package_prefix=data_package_prefix,
        ground_truth_key=(
            env_map.get("GROUND_TRUTH_KEY")
            or f"{data_package_prefix}/data/ground_truth_hourly.csv"
        ).strip(),
        compare_script_key=(
            env_map.get("COMPARE_SCRIPT_KEY")
            or f"{data_package_prefix}/eval/compare_timeseries.py"
        ).strip(),
        model_provider=model_provider,
        llm_model_id=(env_map.get("LLM_MODEL_ID") or "gpt-4o-mini").strip(),
        bedrock_region=(env_map.get("BEDROCK_REGION") or env_map.get("AWS_REGION") or "cn-north-1").strip(),
        openai_base_url=(env_map.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").strip(),
        openai_api_key=(env_map.get("OPENAI_API_KEY") or env_map.get("LLM_API_KEY") or "").strip(),
        openai_api_key_secret_arn=(env_map.get("OPENAI_API_KEY_SECRET_ARN") or "").strip(),
        model_temperature=_coerce_float(env_map, "MODEL_TEMPERATURE", 0.0),
        model_max_tokens=_coerce_int(env_map, "MODEL_MAX_TOKENS", 8192),
        max_attempts=max(1, _coerce_int(env_map, "MAX_ATTEMPTS", 3)),
        target_metric_name=(env_map.get("TARGET_METRIC_NAME") or "MAPE").strip(),
        target_metric_threshold=_coerce_float(env_map, "TARGET_METRIC_THRESHOLD", 20.0),
        target_metric_direction=target_metric_direction,
        log_sync_seconds=max(1, _coerce_int(env_map, "LOG_SYNC_SECONDS", 10)),
        # 0 is meaningful and must NOT be floored to 1: file-transfer task types
        # (byo_deploy) deliberately produce no predictions, so requiring one made
        # every attempt fail with "Missing required artifacts: prediction csv".
        # Negative values still clamp to 0.
        required_prediction_count=max(
            0, _coerce_int(env_map, "REQUIRED_PREDICTION_COUNT", 1)
        ),
        min_matched_rows_per_device=max(
            1,
            _coerce_int(env_map, "MIN_MATCHED_ROWS_PER_DEVICE", 100),
        ),
        enforce_verification=(env_map.get("ENFORCE_VERIFICATION") or "1").strip() == "1",
        conversation_window_messages=max(
            4,
            _coerce_int(env_map, "CONVERSATION_WINDOW_MESSAGES", 24),
        ),
    )
    if config.model_provider == "openai" and not (
        config.openai_api_key or config.openai_api_key_secret_arn
    ):
        raise ConfigError(
            "OpenAI-compatible provider mode requires OPENAI_API_KEY or OPENAI_API_KEY_SECRET_ARN"
        )
    return config
