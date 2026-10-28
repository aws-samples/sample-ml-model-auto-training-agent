from training_runtime.contracts import ContractError, join_bucket_path, load_runtime_contract


def test_load_runtime_contract_requires_all_canonical_fields():
    try:
        load_runtime_contract({})
    except ContractError as exc:
        assert "TASK_ID" in str(exc)
    else:  # pragma: no cover - defensive
        assert False, "Expected ContractError"


def test_load_runtime_contract_normalizes_relative_paths():
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

    assert contract.data_uri == "s3://bucket-name/data/train.csv"
    assert contract.skills_uri == "s3://bucket-name/skills/"


def test_join_bucket_path_preserves_absolute_uri():
    assert (
        join_bucket_path("ignored-bucket", "s3://real-bucket/path/file.csv")
        == "s3://real-bucket/path/file.csv"
    )
