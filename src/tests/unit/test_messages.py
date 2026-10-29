# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
from typing import Any

from shared.notifications import record_status_change


class FakeMessageRepo:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def add_message(self, **kwargs: Any) -> dict[str, Any]:
        record = dict(kwargs)
        record.setdefault("read", False)
        self.messages.append(record)
        return record

    def list_messages(
        self, recipient: str, limit: int = 50, unread_only: bool = False
    ) -> list[dict[str, Any]]:
        items = [m for m in self.messages if m.get("recipient") == recipient]
        if unread_only:
            items = [m for m in items if not m.get("read", False)]
        return items

    def mark_read(self, message_id: str) -> None:
        for m in self.messages:
            if m.get("message_id") == message_id:
                m["read"] = True


def _task() -> dict[str, Any]:
    return {
        "task_id": "t-1",
        "task_name": "扬尘训练",
        "submitted_by": "user1",
        "status": "RUNNING",
    }


def test_record_status_change_adds_message_on_change():
    repo = FakeMessageRepo()
    record_status_change(_task(), "RUNNING", "SUCCEEDED", repository=repo)
    assert len(repo.messages) == 1
    msg = repo.messages[0]
    assert msg["recipient"] == "user1"
    assert msg["submitted_by"] == "user1"
    assert msg["task_id"] == "t-1"
    # content mentions the human-readable transition
    assert "训练成功" in msg["content"]
    assert "扬尘训练" in msg["content"]


def test_record_status_change_noop_when_status_unchanged():
    repo = FakeMessageRepo()
    record_status_change(_task(), "RUNNING", "RUNNING", repository=repo)
    assert repo.messages == []


def test_record_status_change_noop_without_submitter():
    repo = FakeMessageRepo()
    task = _task()
    task["submitted_by"] = ""
    record_status_change(task, "RUNNING", "FAILED", repository=repo)
    assert repo.messages == []


def test_record_status_change_swallows_repo_errors():
    class BoomRepo:
        def add_message(self, **kwargs):
            raise RuntimeError("ddb down")

    # Must not raise — notification recording is best-effort.
    record_status_change(_task(), "RUNNING", "FAILED", repository=BoomRepo())


def test_message_repository_add_and_expiry(monkeypatch):
    from shared import message_repository as mr

    class FakeTable:
        def __init__(self):
            self.items = []

        def put_item(self, Item):
            self.items.append(Item)

        def query(self, **kwargs):
            return {"Items": list(reversed(self.items))}

    class FakeResource:
        def __init__(self, table):
            self._table = table

        def Table(self, name):
            return self._table

    table = FakeTable()
    repo = mr.MessageRepository(
        table_name="msgs", dynamodb_resource=FakeResource(table)
    )
    record = repo.add_message(
        recipient="user1", content="hello", task_id="t1", task_name="n"
    )
    assert record["recipient"] == "user1"
    # expires_at is a future Unix epoch (7-day TTL)
    assert isinstance(record["expires_at"], int)
    assert record["expires_at"] > 0
    assert table.items and table.items[0]["message_id"] == record["message_id"]


def test_api_handle_get_messages_returns_list(monkeypatch):
    from api import handler as api_handler

    repo = FakeMessageRepo()
    repo.add_message(recipient="user1", content="c1", task_id="t1", task_name="n1")
    monkeypatch.setattr(api_handler, "build_message_repository", lambda: repo)

    event = {
        "httpMethod": "GET",
        "resource": "/messages",
        "queryStringParameters": {"recipient": "user1"},
    }
    response = api_handler.handle_get_messages(event)
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert len(body["messages"]) == 1
    assert body["messages"][0]["content"] == "c1"


def test_api_handle_get_messages_requires_recipient():
    from api import handler as api_handler
    from api.validators import TaskValidationError

    event = {"httpMethod": "GET", "resource": "/messages", "queryStringParameters": {}}
    try:
        api_handler.handle_get_messages(event)
        assert False, "expected TaskValidationError"
    except TaskValidationError:
        pass


def test_api_get_messages_excludes_read(monkeypatch):
    from api import handler as api_handler

    repo = FakeMessageRepo()
    repo.add_message(
        message_id="m1", recipient="user1", content="unread", task_id="t1", task_name="n"
    )
    read_msg = repo.add_message(
        message_id="m2", recipient="user1", content="read", task_id="t2", task_name="n"
    )
    read_msg["read"] = True
    monkeypatch.setattr(api_handler, "build_message_repository", lambda: repo)

    event = {
        "httpMethod": "GET",
        "resource": "/messages",
        "queryStringParameters": {"recipient": "user1"},
    }
    body = json.loads(api_handler.handle_get_messages(event)["body"])
    contents = [m["content"] for m in body["messages"]]
    assert contents == ["unread"]  # read message excluded


def test_api_mark_message_read(monkeypatch):
    from api import handler as api_handler

    repo = FakeMessageRepo()
    repo.add_message(
        message_id="m1", recipient="user1", content="c", task_id="t", task_name="n"
    )
    monkeypatch.setattr(api_handler, "build_message_repository", lambda: repo)

    event = {
        "httpMethod": "POST",
        "resource": "/messages",
        "body": json.dumps({"message_id": "m1"}),
    }
    response = api_handler.handle_mark_message_read(event)
    assert response["statusCode"] == 200
    assert repo.messages[0]["read"] is True


def test_api_mark_message_read_requires_id():
    from api import handler as api_handler
    from api.validators import TaskValidationError

    event = {"httpMethod": "POST", "resource": "/messages", "body": json.dumps({})}
    try:
        api_handler.handle_mark_message_read(event)
        assert False, "expected TaskValidationError"
    except TaskValidationError:
        pass


def test_message_repository_mark_read_and_unread_filter():
    from shared import message_repository as mr

    class FakeTable:
        def __init__(self):
            self.items = [
                {"message_id": "a", "recipient": "u", "read": False, "created_at": "1"},
                {"message_id": "b", "recipient": "u", "read": True, "created_at": "2"},
            ]

        def query(self, **kwargs):
            return {"Items": list(self.items)}

        def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues):
            for it in self.items:
                if it["message_id"] == Key["message_id"]:
                    it["read"] = True

    class FakeResource:
        def __init__(self, table):
            self._t = table

        def Table(self, name):
            return self._t

    table = FakeTable()
    repo = mr.MessageRepository(table_name="m", dynamodb_resource=FakeResource(table))
    unread = repo.list_messages("u", unread_only=True)
    assert [m["message_id"] for m in unread] == ["a"]
    repo.mark_read("a")
    assert table.items[0]["read"] is True
