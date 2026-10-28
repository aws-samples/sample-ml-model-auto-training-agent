import json
from pathlib import Path

from training_runtime.config import load_runtime_config
from training_runtime.contracts import load_runtime_contract
from training_runtime.runner import AttemptExecutionResult, run_training_runtime
from training_runtime.workspace import prepare_workspace


class FakeS3Client:
    def __init__(self, objects: dict[str, str]):
        self._objects = objects
        self.downloaded = []
        self.uploaded = []
        self.uploaded_payloads = {}

    def download_file(self, bucket: str, key: str, filename: str):
        self.downloaded.append((bucket, key, filename))
        target = Path(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self._objects.get(key, "payload"), encoding="utf-8")

    def list_objects_v2(self, *, Bucket: str, Prefix: str):
        return {
            "Contents": [
                {"Key": key}
                for key in sorted(self._objects)
                if key.startswith(Prefix)
            ]
        }

    def upload_file(self, filename: str, bucket: str, key: str):
        self.uploaded.append((filename, bucket, key))
        self.uploaded_payloads[(bucket, key)] = Path(filename).read_text(encoding="utf-8")


class FakeExecutor:
    def execute(
        self,
        *,
        attempt_number,
        retry_guidance,
        contract,
        config,
        provider_selection,
        skill_bundle,
        workspace,
        attempt_dir,
        runtime_log,
    ):
        (attempt_dir / "prediction_device.csv").write_text(
            "timestamp,prediction\n2026-01-01T00:00:00Z,12.0\n",
            encoding="utf-8",
        )
        return AttemptExecutionResult(
            attempt_number=attempt_number,
            output_dir=attempt_dir,
            claimed_metric_value=1.0,
            agent_summary="MAPE=1.0",
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
    json.dump({"matched_rows": matched_rows, "overall": {"MAPE": 0.123}}, handle)
"""


def test_run_training_runtime_publishes_manifest_and_logs(tmp_path):
    contract = load_runtime_contract(
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
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "general-agent-spec.md").write_text("general", encoding="utf-8")
    (baked_dir / "timeseries-forecast-train.md").write_text("domain", encoding="utf-8")
    config = load_runtime_config(
        {
            "WORKSPACE_ROOT": str(tmp_path / "workspace"),
            "BAKED_SKILLS_DIR": str(baked_dir),
            "MAX_ATTEMPTS": "2",
            "GROUND_TRUTH_KEY": "verification/ground_truth_hourly.csv",
            "COMPARE_SCRIPT_KEY": "verification/compare_timeseries.py",
            "MIN_MATCHED_ROWS_PER_DEVICE": "1",
        }
    )
    workspace = prepare_workspace(str(tmp_path / "workspace"), contract)
    s3 = FakeS3Client(
        {
            "data/train.csv": "feature,target\n1,2\n",
            "verification/ground_truth_hourly.csv": (
                "item_id,timestamp,target_true\n"
                "device,2026-01-01T00:00:00Z,10.0\n"
            ),
            "verification/compare_timeseries.py": _compare_script(),
        }
    )

    result = run_training_runtime(
        contract=contract,
        config=config,
        workspace=workspace,
        executor=FakeExecutor(),
        s3_client=s3,
    )

    assert result.terminal_outcome.achieved
    assert result.manifest["task_id"] == "task-1"
    assert result.manifest["attempts"][0]["claimed_metric_value"] == 1.0
    assert result.manifest["attempts"][0]["verification"]["metric_value"] == 12.3
    assert result.manifest["attempts"][0]["verification"]["source"] == "independent"
    assert any(key.endswith("events.jsonl") for _, _, key in s3.uploaded)
    manifest_key = "output/task-1/runtime_manifest.json"
    assert any(key == manifest_key for _, _, key in s3.uploaded)
    uploaded_manifest = json.loads(s3.uploaded_payloads[("bucket-name", manifest_key)])
    assert uploaded_manifest["published_artifacts"]
