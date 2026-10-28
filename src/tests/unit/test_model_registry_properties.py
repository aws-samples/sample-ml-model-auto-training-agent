from __future__ import annotations

from hypothesis import given, strategies as st

from deployment.model_registry import (
    build_pending_model_record,
    project_model_list_item,
    sort_model_records_newest_first,
)
from tests.unit.strategies import (
    deployment_command_strategy,
    deployment_decision_strategy,
    model_record_strategy,
    task_record_strategy,
)

from tests.unit.test_model_registry_repository import _repository


@given(task_record=task_record_strategy(), decision=deployment_decision_strategy())
def test_pending_record_identity_is_stable(task_record, decision):
    now = "2026-01-01T00:00:00+00:00"
    manifest_reference = {
        "bucket": "bucket-name",
        "key": f"output/{task_record['task_id']}/runtime_manifest.json",
    }

    first = build_pending_model_record(
        task_record=task_record,
        eligibility_decision=decision,
        manifest_reference=manifest_reference,
        now=now,
    )
    second = build_pending_model_record(
        task_record=task_record,
        eligibility_decision=decision,
        manifest_reference=manifest_reference,
        now=now,
    )

    assert first["model_record_id"] == second["model_record_id"]
    assert first["artifact_reference"] == second["artifact_reference"]
    assert first["status"] == "DEPLOYING"


@given(
    st.lists(
        model_record_strategy(),
        min_size=1,
        max_size=8,
        unique_by=lambda record: record["model_record_id"],
    )
)
def test_model_list_projection_preserves_shape_and_newest_first_order(records):
    sorted_records = sort_model_records_newest_first(records)
    projected = [project_model_list_item(record) for record in sorted_records]

    assert {item["model_record_id"] for item in projected} == {
        record["model_record_id"] for record in records
    }
    assert all(
        projected[index]["created_at"] >= projected[index + 1]["created_at"]
        for index in range(len(projected) - 1)
    )
    assert all("status" in item and "provider_status" in item for item in projected)


@given(
    st.lists(
        deployment_command_strategy(),
        min_size=1,
        max_size=8,
        unique_by=lambda command: command["task_record"]["task_id"],
    )
)
def test_registry_sequence_matches_reference_current_endpoint_model(commands):
    repository = _repository()
    expected_current_by_endpoint: dict[str, str] = {}

    for command in commands:
        task_record = command["task_record"]
        decision = command["decision"]
        manifest_reference = {
            "bucket": "bucket-name",
            "key": f"output/{task_record['task_id']}/runtime_manifest.json",
        }
        record, _ = repository.accept_deployment_attempt(
            task_record=task_record,
            eligibility_decision=decision,
            manifest_reference=manifest_reference,
            now=command["now"],
        )
        if command["succeeds"]:
            repository.record_deployment_result(
                model_record_id=record["model_record_id"],
                status="DEPLOYED",
                provider_status="InService",
                provider_status_detail={"stage": "ready"},
                failure_reason="",
                endpoint_arn=f"arn:{record['target_endpoint']}",
                endpoint_config_name=f"{record['target_endpoint']}-config",
                sagemaker_model_name=f"{record['target_endpoint']}-model",
                now=command["now"],
            )
            expected_current_by_endpoint[record["target_endpoint"]] = record["model_record_id"]
        else:
            repository.record_deployment_result(
                model_record_id=record["model_record_id"],
                status="FAILED",
                provider_status="FAILED",
                provider_status_detail={"stage": "deploy"},
                failure_reason="deployment failed",
                now=command["now"],
            )

        records = repository.list_model_records()
        grouped: dict[str, list[dict[str, object]]] = {}
        for item in records:
            grouped.setdefault(item["target_endpoint"], []).append(item)

        for endpoint, endpoint_records in grouped.items():
            current_ids = [
                item["model_record_id"]
                for item in endpoint_records
                if item.get("is_current_endpoint_target")
            ]
            assert len(current_ids) <= 1
            expected_id = expected_current_by_endpoint.get(endpoint)
            if expected_id is None:
                assert current_ids == []
            else:
                assert current_ids == [expected_id]
