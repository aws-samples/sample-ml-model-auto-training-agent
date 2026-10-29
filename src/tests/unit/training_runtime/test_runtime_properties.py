# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from pathlib import Path

from hypothesis import given, strategies as st

from tests.unit.strategies import identifier_strategy
from training_runtime.config import load_runtime_config
from training_runtime.contracts import load_runtime_contract
from training_runtime.manifest import AttemptSummary, build_runtime_manifest
from training_runtime.outcomes import determine_terminal_outcome
from training_runtime.providers import resolve_provider_selection
from training_runtime.skills import EffectiveSkill, EffectiveSkillBundle, merge_skill_maps
from training_runtime.verification import VerificationResult


def _base_contract():
    return load_runtime_contract(
        {
            "TASK_ID": "task-1",
            "BUCKET": "bucket-name",
            "DATA_LOCATION": "data/train.csv",
            "SKILLS_PREFIX": "skills/",
            "LOGS_PREFIX": "logs/task-1/",
            "OUTPUT_PREFIX": "output/task-1/",
            "TRAINING_PROMPT": "train",
            "AWS_REGION": "cn-north-1",
        }
    )


@given(identifier_strategy("task"))
def test_contract_loading_is_idempotent_for_same_input(task_id: str):
    env = {
        "TASK_ID": task_id,
        "BUCKET": "bucket-name",
        "DATA_LOCATION": "data/train.csv",
        "SKILLS_PREFIX": "skills/",
        "LOGS_PREFIX": f"logs/{task_id}/",
        "OUTPUT_PREFIX": f"output/{task_id}/",
        "TRAINING_PROMPT": "train",
        "AWS_REGION": "cn-north-1",
    }

    first = load_runtime_contract(env)
    second = load_runtime_contract(env)

    assert first == second


@given(st.booleans())
def test_skill_merge_is_deterministic(has_override: bool):
    base_path = Path(".")
    defaults = {
        "general-agent-spec.md": EffectiveSkill("general-agent-spec.md", "baked-in", base_path, "general"),
        "timeseries-forecast-train.md": EffectiveSkill("timeseries-forecast-train.md", "baked-in", base_path, "domain"),
    }
    overrides = (
        {"timeseries-forecast-train.md": EffectiveSkill("timeseries-forecast-train.md", "s3", base_path, "override")}
        if has_override
        else {}
    )

    first = merge_skill_maps(defaults, overrides, ("general-agent-spec.md", "timeseries-forecast-train.md"))
    second = merge_skill_maps(defaults, overrides, ("general-agent-spec.md", "timeseries-forecast-train.md"))

    assert first == second


@given(st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False))
def test_manifest_shape_invariant(metric_value: float):
    contract = _base_contract()
    config = load_runtime_config({})
    provider = resolve_provider_selection(config)
    verification = VerificationResult(
        achieved=metric_value <= config.target_metric_threshold,
        metric_name=config.target_metric_name,
        metric_value=metric_value,
        threshold=config.target_metric_threshold,
        threshold_direction=config.target_metric_direction,
        reason="ok",
        missing_artifacts=(),
    )
    attempt = AttemptSummary(
        attempt_number=1,
        output_dir="/tmp/attempt-1",
        claimed_metric_value=metric_value,
        verification_result=verification,
    )
    bundle = EffectiveSkillBundle(
        ordered_skills=(
            EffectiveSkill("general-agent-spec.md", "baked-in", Path("."), "general"),
            EffectiveSkill("timeseries-forecast-train.md", "baked-in", Path("."), "domain"),
        )
    )
    manifest = build_runtime_manifest(
        contract=contract,
        provider_selection=provider,
        skill_bundle=bundle,
        attempts=[attempt],
        terminal_outcome=determine_terminal_outcome(verification_result=verification),
        log_reference={"bucket": "bucket-name", "key": "logs/task-1/events.jsonl"},
        published_artifacts=[],
    )

    assert set(manifest) >= {
        "task_id",
        "provider_mode",
        "skills",
        "attempts",
        "terminal_outcome",
        "logs",
        "published_artifacts",
    }
    assert manifest["attempts"][0]["verification"]["source"] == "independent"
    assert (
        manifest["attempts"][0]["verification"]["threshold_direction"]
        == config.target_metric_direction
    )
