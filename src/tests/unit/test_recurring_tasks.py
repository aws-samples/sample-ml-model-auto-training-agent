import json
from typing import Any

import pytest

from api import handler as api_handler
from api.scheduler_adapter import ScheduleError, build_rate_expression
from api.validators import TaskValidationError, parse_submission
from shared.constants import (
    TASK_STATUS_CANCELLED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SCHEDULED,
    TASK_STATUS_SUBMITTED,
)


class FakeRepo:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    def create_task(self, r): self.items[r["task_id"]] = dict(r)

    def update_task(self, r):
        # Mirror TaskRepository.update_task: sync the active execution entry so
        # periodic execution history stays consistent in tests.
        from shared.executions import sync_active_execution

        sync_active_execution(r, "2026-07-06T00:00:00+00:00")
        self.items[r["task_id"]] = dict(r)
    def get_task(self, tid): return self.items.get(tid)
    def count_active_tasks(self): return 0
    def list_children(self, parent_id):
        return [i for i in self.items.values() if i.get("parent_task_id") == parent_id]


class FakeScheduler:
    def __init__(self): self.created = []; self.deleted = []
    def create_schedule(self, **kwargs):
        self.created.append(kwargs); return "arn:schedule"
    def delete_schedule(self, **kwargs): self.deleted.append(kwargs)


def _api_event(body: dict) -> dict:
    return {"httpMethod": "POST", "resource": "/tasks", "body": json.dumps(body)}


RECURRING_BODY = {
    "task_type": "timeseries_forecast",
    "task_name": "定时训练",
    "data_location": "data/train.csv",
    "output_location": "output/run",
    "model_name": "timeseries_model",
    "target_endpoint": "timeseries-endpoint",
    "submitted_by": "demo-operator",
    "confirmation_flag": True,
    "is_recurring": True,
    "schedule_start": "2026-07-02T09:00",
    "interval_value": 2,
    "interval_unit": "hours",
}


def test_build_rate_expression():
    assert build_rate_expression(1, "days") == "rate(1 day)"
    assert build_rate_expression(2, "hours") == "rate(2 hours)"
    assert build_rate_expression(30, "minutes") == "rate(30 minutes)"
    with pytest.raises(ScheduleError):
        build_rate_expression(1, "weeks")


def test_validate_recurring_requires_fields():
    body = dict(RECURRING_BODY)
    body["interval_value"] = 0
    with pytest.raises(TaskValidationError):
        parse_submission(_api_event(body))
    body2 = dict(RECURRING_BODY)
    body2["schedule_start"] = "not-a-date"
    with pytest.raises(TaskValidationError):
        parse_submission(_api_event(body2))


def _wire(monkeypatch, repo, scheduler):
    monkeypatch.setattr(api_handler, "build_repository", lambda: repo)
    monkeypatch.setattr(api_handler, "SchedulerAdapter", lambda: scheduler)
    monkeypatch.setenv("BUCKET_NAME", "b")
    monkeypatch.setenv("JOB_QUEUE_ARN", "q")
    monkeypatch.setenv("JOB_DEF_ARN", "d")
    monkeypatch.setenv("IMAGE_REPO_URI", "r")
    monkeypatch.setenv("SCHEDULE_GROUP_NAME", "grp")
    monkeypatch.setenv("SCHEDULER_ROLE_ARN", "role")
    monkeypatch.setenv("API_FUNCTION_ARN", "fnarn")


def test_recurring_submit_creates_scheduled_parent_no_batch(monkeypatch):
    repo, scheduler = FakeRepo(), FakeScheduler()
    _wire(monkeypatch, repo, scheduler)
    # Batch must NOT be called for a recurring parent.
    monkeypatch.setattr(
        api_handler, "BatchAdapter",
        lambda: (_ for _ in ()).throw(AssertionError("Batch should not run")),
    )

    resp = api_handler.handler(_api_event(RECURRING_BODY), None)
    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["status"] == TASK_STATUS_SCHEDULED
    parent = repo.items[body["task_id"]]
    assert parent["is_recurring"] is True
    assert parent["schedule_expression"] == "rate(2 hours)"
    # a schedule was created targeting the API fn
    assert len(scheduler.created) == 1
    assert scheduler.created[0]["rate_expression"] == "rate(2 hours)"
    assert scheduler.created[0]["input_payload"]["scheduled_trigger"] is True


def test_scheduled_trigger_runs_execution_on_same_record(monkeypatch):
    repo, scheduler = FakeRepo(), FakeScheduler()
    _wire(monkeypatch, repo, scheduler)
    # seed a scheduled periodic task (single-record model)
    repo.create_task(
        {
            "task_id": "task-1",
            "task_name": "定时训练",
            "task_type": "timeseries_forecast",
            "comment": "",
            "data_location": "data/train.csv",
            "output_location": "output/run",
            "model_name": "timeseries_model",
            "target_endpoint": "timeseries-endpoint",
            "submitted_by": "op",
            "resolved_image_tag": "stable",
            "status": TASK_STATUS_SCHEDULED,
            "is_periodical": True,
            "is_recurring": True,
            "executions": [],
        }
    )

    submitted = {}

    class FakeBatch:
        def submit_task(self, **kwargs):
            submitted["task_id"] = kwargs["task_record"]["task_id"]
            submitted["execution_id"] = kwargs.get("execution_id")
            return {"job_id": "job-xyz"}

    monkeypatch.setattr(api_handler, "BatchAdapter", lambda: FakeBatch())

    event = {"scheduled_trigger": True, "parent_task_id": "task-1"}
    resp = api_handler.handler(event, None)
    assert resp["statusCode"] == 200
    # the SAME record ran; no child was created
    assert submitted["task_id"] == "task-1"
    assert len(repo.items) == 1
    task = repo.items["task-1"]
    assert task["status"] == TASK_STATUS_SUBMITTED
    assert task["batch_job_id"] == "job-xyz"
    # an execution entry was appended and scoped by execution id
    assert len(task["executions"]) == 1
    exe = task["executions"][0]
    assert exe["execution_id"] == submitted["execution_id"]
    assert exe["batch_job_id"] == "job-xyz"


def test_scheduled_trigger_skips_when_previous_execution_active(monkeypatch):
    repo, scheduler = FakeRepo(), FakeScheduler()
    _wire(monkeypatch, repo, scheduler)
    repo.create_task(
        {
            "task_id": "task-1",
            "task_name": "定时训练",
            "task_type": "timeseries_forecast",
            "model_name": "m",
            "target_endpoint": "e",
            "status": TASK_STATUS_RUNNING,  # a run is still active
            "is_periodical": True,
            "executions": [{"execution_id": "e1", "status": "RUNNING"}],
        }
    )

    class FakeBatch:
        def submit_task(self, **kwargs):
            raise AssertionError("must not submit while active")

    monkeypatch.setattr(api_handler, "BatchAdapter", lambda: FakeBatch())
    resp = api_handler.handler(
        {"scheduled_trigger": True, "parent_task_id": "task-1"}, None
    )
    assert resp["statusCode"] == 200
    # no new execution appended
    assert len(repo.items["task-1"]["executions"]) == 1


def test_cancel_periodical_deletes_schedule_and_terminates_run(monkeypatch):
    repo, scheduler = FakeRepo(), FakeScheduler()
    _wire(monkeypatch, repo, scheduler)
    # a periodic task idle between runs (SUCCEEDED) with a stale batch job id
    repo.create_task(
        {
            "task_id": "task-1",
            "task_name": "定时",
            "task_type": "timeseries_forecast",
            "model_name": "m",
            "target_endpoint": "e",
            "status": "SUCCEEDED",
            "is_periodical": True,
            "is_recurring": True,
            "schedule_name": "demo-training-task-1",
            "batch_job_id": "job-abc",
            "executions": [{"execution_id": "e1", "status": "SUCCEEDED"}],
        }
    )

    terminated = []

    class FakeBatch:
        def terminate_task(self, job_id, reason=""):
            terminated.append(job_id)

    monkeypatch.setattr(api_handler, "BatchAdapter", lambda: FakeBatch())

    event = {
        "httpMethod": "POST",
        "resource": "/tasks/cancel",
        "body": json.dumps({"task_id": "task-1"}),
    }
    resp = api_handler.handler(event, None)
    assert resp["statusCode"] == 200
    # schedule deleted so no future runs fire
    assert scheduler.deleted and scheduler.deleted[0]["name"] == "demo-training-task-1"
    # the record's own batch job terminated + record cancelled
    assert "job-abc" in terminated
    assert repo.items["task-1"]["status"] == TASK_STATUS_CANCELLED
