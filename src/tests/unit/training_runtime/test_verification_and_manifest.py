import json
from pathlib import Path

from training_runtime.config import load_runtime_config
from training_runtime.contracts import load_runtime_contract
from training_runtime.manifest import AttemptSummary, build_runtime_manifest
from training_runtime.outcomes import determine_terminal_outcome
from training_runtime.providers import resolve_provider_selection
from training_runtime.skills import EffectiveSkill, EffectiveSkillBundle
from training_runtime.verification import verify_attempt_output
from training_runtime.workspace import prepare_workspace


class FakeS3Client:
    def __init__(self, objects: dict[str, str]):
        self._objects = objects

    def download_file(self, bucket: str, key: str, filename: str):
        target = Path(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._objects[key], encoding="utf-8")


def _build_contract():
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


def _compare_script() -> str:
    return """
import argparse
import csv
import json

parser = argparse.ArgumentParser()
parser.add_argument("--pred")
parser.add_argument("--truth")
parser.add_argument("--json-out")
parser.add_argument("--no-plot", action="store_true")
args = parser.parse_args()

with open(args.pred, "r", encoding="utf-8") as handle:
    pred_rows = list(csv.DictReader(handle))
with open(args.truth, "r", encoding="utf-8") as handle:
    truth_rows = list(csv.DictReader(handle))

matched_rows = min(len(pred_rows), len(truth_rows))
with open(args.json_out, "w", encoding="utf-8") as handle:
    json.dump({"matched_rows": matched_rows, "overall": {"MAPE": 0.132}}, handle)
"""


def test_verify_attempt_output_requires_prediction_and_report(tmp_path):
    config = load_runtime_config({"REQUIRED_PREDICTION_COUNT": "1"})
    output_dir = tmp_path / "attempt"
    output_dir.mkdir()

    result = verify_attempt_output(output_dir=output_dir, config=config)

    assert not result.achieved
    assert "prediction csv" in result.missing_artifacts


def test_verify_attempt_output_marks_success_when_metric_meets_threshold(tmp_path):
    contract = _build_contract()
    config = load_runtime_config(
        {
            "WORKSPACE_ROOT": str(tmp_path / "workspace"),
            "TARGET_METRIC_THRESHOLD": "20.0",
            "REQUIRED_PREDICTION_COUNT": "1",
            "GROUND_TRUTH_KEY": "verification/ground_truth_hourly.csv",
            "COMPARE_SCRIPT_KEY": "verification/compare_timeseries.py",
            "MIN_MATCHED_ROWS_PER_DEVICE": "1",
        }
    )
    workspace = prepare_workspace(str(tmp_path / "workspace"), contract)
    output_dir = workspace.attempt_dir(1)
    (output_dir / "prediction_device.csv").write_text(
        "timestamp,prediction\n2026-01-01T00:00:00Z,12.0\n",
        encoding="utf-8",
    )
    s3 = FakeS3Client(
        {
            "verification/ground_truth_hourly.csv": (
                "item_id,timestamp,target_true\n"
                "device,2026-01-01T00:00:00Z,10.0\n"
            ),
            "verification/compare_timeseries.py": _compare_script(),
        }
    )

    result = verify_attempt_output(
        output_dir=output_dir,
        config=config,
        contract=contract,
        workspace=workspace,
        s3_client=s3,
        explicit_metric_value=1.0,
    )

    assert result.achieved
    assert result.metric_value == 13.2
    report = json.loads((output_dir / "runner_verification.json").read_text(encoding="utf-8"))
    assert report["source"] == "independent"
    assert report["claimed_metric_value"] == 1.0


def test_verify_rejects_predictor_with_no_fit_models(tmp_path):
    """A predictor whose log shows no trained model must fail verification even
    when good-looking prediction CSVs exist (it cannot serve once deployed)."""
    contract = _build_contract()
    config = load_runtime_config(
        {
            "WORKSPACE_ROOT": str(tmp_path / "workspace"),
            "TARGET_METRIC_THRESHOLD": "20.0",
            "REQUIRED_PREDICTION_COUNT": "1",
            "GROUND_TRUTH_KEY": "verification/ground_truth_hourly.csv",
            "COMPARE_SCRIPT_KEY": "verification/compare_timeseries.py",
            "MIN_MATCHED_ROWS_PER_DEVICE": "1",
        }
    )
    workspace = prepare_workspace(str(tmp_path / "workspace"), contract)
    output_dir = workspace.attempt_dir(1)
    (output_dir / "prediction_device.csv").write_text(
        "timestamp,prediction\n2026-01-01T00:00:00Z,12.0\n",
        encoding="utf-8",
    )
    # AutoGluon persisted an empty predictor (Chronos download failed).
    log_dir = output_dir / "models" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "predictor_log.txt").write_text(
        "Warning: Exception caused Chronos to fail during training... Skipping.\n"
        "Not fitting ensemble as no models were successfully trained.\n"
        "Trainer has no fit models that can predict.\n",
        encoding="utf-8",
    )
    s3 = FakeS3Client(
        {
            "verification/ground_truth_hourly.csv": (
                "item_id,timestamp,target_true\n"
                "device,2026-01-01T00:00:00Z,10.0\n"
            ),
            "verification/compare_timeseries.py": _compare_script(),
        }
    )

    result = verify_attempt_output(
        output_dir=output_dir,
        config=config,
        contract=contract,
        workspace=workspace,
        s3_client=s3,
        explicit_metric_value=1.0,
    )

    assert not result.achieved
    assert "usable trained model" in result.missing_artifacts


def test_manifest_contains_terminal_outcome_and_skill_sources(tmp_path):
    contract = _build_contract()
    provider = resolve_provider_selection(load_runtime_config({}))
    config = load_runtime_config(
        {
            "WORKSPACE_ROOT": str(tmp_path / "workspace"),
            "GROUND_TRUTH_KEY": "verification/ground_truth_hourly.csv",
            "COMPARE_SCRIPT_KEY": "verification/compare_timeseries.py",
            "MIN_MATCHED_ROWS_PER_DEVICE": "1",
        }
    )
    workspace = prepare_workspace(str(tmp_path / "workspace"), contract)
    output_dir = workspace.attempt_dir(1)
    (output_dir / "prediction_device.csv").write_text(
        "timestamp,prediction\n2026-01-01T00:00:00Z,12.0\n",
        encoding="utf-8",
    )
    verification = verify_attempt_output(
        output_dir=output_dir,
        config=config,
        contract=contract,
        workspace=workspace,
        s3_client=FakeS3Client(
            {
                "verification/ground_truth_hourly.csv": (
                    "item_id,timestamp,target_true\n"
                    "device,2026-01-01T00:00:00Z,10.0\n"
                ),
                "verification/compare_timeseries.py": _compare_script(),
            }
        ),
        explicit_metric_value=15.0,
    )
    outcome = determine_terminal_outcome(verification_result=verification)
    bundle = EffectiveSkillBundle(
        ordered_skills=(
            EffectiveSkill("general-agent-spec.md", "baked-in", output_dir, "general"),
            EffectiveSkill("timeseries-forecast-train.md", "s3", output_dir, "domain"),
        )
    )
    manifest = build_runtime_manifest(
        contract=contract,
        provider_selection=provider,
        skill_bundle=bundle,
        attempts=[
            AttemptSummary(
                attempt_number=1,
                output_dir="/tmp/attempt-1",
                claimed_metric_value=15.0,
                verification_result=verification,
            )
        ],
        terminal_outcome=outcome,
        log_reference={"bucket": "bucket-name", "key": "logs/task-1/events.jsonl"},
        published_artifacts=[{"bucket": "bucket-name", "key": "output/task-1/runtime_manifest.json"}],
    )

    assert manifest["task_id"] == "task-1"
    assert manifest["terminal_outcome"]["status"] in {"SUCCEEDED", "FAILED"}
    assert len(manifest["skills"]) == 2
    assert manifest["attempts"][0]["verification"]["metric_value"] == 13.2
    assert manifest["attempts"][0]["verification"]["source"] == "independent"
    assert (
        manifest["attempts"][0]["verification"]["threshold_direction"] == "lower_is_better"
    )


def test_required_prediction_count_zero_is_not_floored_to_one():
    """File-transfer task types (byo_deploy) set 0. It must survive config
    loading — max(1, ...) used to silently turn it back into 1."""
    config = load_runtime_config({"REQUIRED_PREDICTION_COUNT": "0"})

    assert config.required_prediction_count == 0


def test_fallback_verification_accepts_transfer_only_run_without_predictions(tmp_path):
    """byo_deploy transfers a model and deliberately runs NO inference, so the
    output has a models/ tree and a report but no prediction*.csv. With
    REQUIRED_PREDICTION_COUNT=0 that must PASS; every attempt used to fail with
    "Missing required artifacts: prediction csv"."""
    config = load_runtime_config(
        {"ENFORCE_VERIFICATION": "0", "REQUIRED_PREDICTION_COUNT": "0"}
    )
    output_dir = tmp_path / "attempt"
    (output_dir / "models" / "model").mkdir(parents=True)
    (output_dir / "models" / "inference.py").write_text(
        "def model_fn(model_dir):\n    return object()\n", encoding="utf-8"
    )
    (output_dir / "models" / "model" / "lgbm_seed0.txt").write_text(
        "tree\n", encoding="utf-8"
    )
    (output_dir / "evaluation_report.json").write_text(
        json.dumps({"task": "byo_model_deploy", "trained": False, "blocking_issues": []}),
        encoding="utf-8",
    )

    result = verify_attempt_output(output_dir=output_dir, config=config)

    assert result.achieved, result.reason
    assert "prediction csv" not in (result.missing_artifacts or ())


def test_fallback_verification_still_requires_predictions_by_default(tmp_path):
    """Guard the change: a TRAINING task type (no requiredPredictionCount set,
    so the default 1 applies) must still be rejected without predictions."""
    config = load_runtime_config({"ENFORCE_VERIFICATION": "0"})
    output_dir = tmp_path / "attempt"
    output_dir.mkdir()

    result = verify_attempt_output(output_dir=output_dir, config=config)

    assert not result.achieved
    assert "prediction csv" in result.missing_artifacts
