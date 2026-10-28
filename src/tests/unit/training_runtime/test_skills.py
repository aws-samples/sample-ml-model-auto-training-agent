from pathlib import Path

from training_runtime.config import load_runtime_config
from training_runtime.contracts import load_runtime_contract
from training_runtime.skills import load_effective_skill_bundle
from training_runtime.workspace import prepare_workspace


class FakeS3Client:
    def __init__(self, objects: dict[str, str]):
        self._objects = objects

    def list_objects_v2(self, *, Bucket: str, Prefix: str):
        return {
            "Contents": [
                {"Key": key}
                for key in sorted(self._objects)
                if key.startswith(Prefix)
            ]
        }

    def download_file(self, bucket: str, key: str, filename: str):
        Path(filename).write_text(self._objects[key], encoding="utf-8")


def test_skill_bundle_uses_s3_override_when_present(tmp_path):
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "general-agent-spec.md").write_text("base-general", encoding="utf-8")
    (baked_dir / "timeseries-forecast-train.md").write_text("base-domain", encoding="utf-8")

    config = load_runtime_config(
        {
            "BAKED_SKILLS_DIR": str(baked_dir),
            "WORKSPACE_ROOT": str(tmp_path / "workspace"),
        }
    )
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
    workspace = prepare_workspace(str(tmp_path / "workspace"), contract)

    bundle = load_effective_skill_bundle(
        contract=contract,
        config=config,
        workspace=workspace,
        s3_client=FakeS3Client({"skills/timeseries-forecast-train.md": "override-domain"}),
    )

    assert bundle.ordered_skills[0].content == "base-general"
    assert bundle.ordered_skills[1].content == "override-domain"
    assert bundle.ordered_skills[1].source == "s3"
