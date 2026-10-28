import json
from typing import Any

from auth_users import handler as auth_handler
from auth_users.passwords import hash_password, verify_password


class FakeUserRepo:
    def __init__(self, seeded: dict[str, dict[str, Any]] | None = None) -> None:
        self.items: dict[str, dict[str, Any]] = seeded or {}

    def get_user(self, username): return self.items.get(username)
    def put_user(self, record): self.items[record["username"]] = dict(record)
    def create_user_if_absent(self, record):
        if record["username"] in self.items:
            return False
        self.items[record["username"]] = dict(record)
        return True


def _event(body: dict) -> dict:
    return {"body": json.dumps(body)}


_CONFIGURED = [
    {"username": "admin", "password": "example-admin-pw", "role": "admin"},
    {"username": "user1", "password": "example-user-pw", "role": "user"},
]


def _setup(monkeypatch, repo, accounts=_CONFIGURED):
    monkeypatch.setattr(auth_handler, "UserRepository", lambda table_name: repo)
    monkeypatch.setenv("USER_TABLE_NAME", "users")
    if accounts is None:
        monkeypatch.delenv("SEED_ACCOUNTS", raising=False)
    else:
        monkeypatch.setenv("SEED_ACCOUNTS", json.dumps(accounts))


def test_password_hash_roundtrip():
    encoded = hash_password("example-pw")
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password("example-pw", encoded)
    assert not verify_password("wrong", encoded)


def test_password_verify_rejects_malformed():
    assert not verify_password("x", "")
    assert not verify_password("x", "garbage")


def test_login_seeds_and_authenticates_admin(monkeypatch):
    repo = FakeUserRepo()
    _setup(monkeypatch, repo)

    # First login seeds the configured accounts, then validates admin.
    resp = auth_handler.handler(_event({"username": "admin", "password": "example-admin-pw"}), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["role"] == "admin"
    # both configured accounts were seeded
    assert "admin" in repo.items and "user1" in repo.items


def test_login_regular_user_role(monkeypatch):
    repo = FakeUserRepo()
    _setup(monkeypatch, repo)
    resp = auth_handler.handler(_event({"username": "user1", "password": "example-user-pw"}), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["role"] == "user"


def test_login_wrong_password_rejected(monkeypatch):
    repo = FakeUserRepo()
    _setup(monkeypatch, repo)
    resp = auth_handler.handler(_event({"username": "admin", "password": "nope"}), None)
    assert resp["statusCode"] == 401
    assert json.loads(resp["body"])["error_code"] == "INVALID_CREDENTIALS"


def test_login_unknown_user_rejected(monkeypatch):
    repo = FakeUserRepo()
    _setup(monkeypatch, repo)
    resp = auth_handler.handler(_event({"username": "ghost", "password": "x"}), None)
    assert resp["statusCode"] == 401


def test_login_missing_fields(monkeypatch):
    repo = FakeUserRepo()
    _setup(monkeypatch, repo)
    resp = auth_handler.handler(_event({"username": "admin"}), None)
    assert resp["statusCode"] == 400


def test_seed_accounts_from_config_env(monkeypatch):
    # Accounts come from the SEED_ACCOUNTS env var (configuration), not the
    # hardcoded defaults.
    repo = FakeUserRepo()
    monkeypatch.setattr(auth_handler, "UserRepository", lambda table_name: repo)
    monkeypatch.setenv("USER_TABLE_NAME", "users")
    monkeypatch.setenv(
        "SEED_ACCOUNTS",
        json.dumps(
            [
                {"username": "boss", "password": "s3cret", "role": "admin"},
                {"username": "worker", "password": "pw123", "role": "user"},
            ]
        ),
    )
    resp = auth_handler.handler(_event({"username": "boss", "password": "s3cret"}), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["role"] == "admin"
    # The old hardcoded defaults must NOT be seeded when config is present.
    assert "admin" not in repo.items
    assert "boss" in repo.items and "worker" in repo.items


def test_seed_accounts_invalid_role_falls_back_to_user(monkeypatch):
    repo = FakeUserRepo()
    monkeypatch.setattr(auth_handler, "UserRepository", lambda table_name: repo)
    monkeypatch.setenv("USER_TABLE_NAME", "users")
    monkeypatch.setenv(
        "SEED_ACCOUNTS",
        json.dumps([{"username": "x", "password": "p", "role": "superuser"}]),
    )
    resp = auth_handler.handler(_event({"username": "x", "password": "p"}), None)
    assert json.loads(resp["body"])["role"] == "user"


def test_seed_accounts_malformed_env_seeds_nothing(monkeypatch):
    # Fail closed: a malformed list must not fall back to any built-in account.
    repo = FakeUserRepo()
    monkeypatch.setattr(auth_handler, "UserRepository", lambda table_name: repo)
    monkeypatch.setenv("USER_TABLE_NAME", "users")
    monkeypatch.setenv("SEED_ACCOUNTS", "not-json")
    resp = auth_handler.handler(_event({"username": "admin", "password": "guessed-default-pw"}), None)
    assert resp["statusCode"] == 401
    assert repo.items == {}


def test_no_seed_accounts_configured_seeds_nothing(monkeypatch):
    # With no configuration there is no default admin: nobody can log in, and
    # a guessed default password in particular must not work.
    repo = FakeUserRepo()
    _setup(monkeypatch, repo, accounts=None)
    for username, password in (("admin", "guessed-default-pw"), ("user1", "guessed-default-pw")):
        resp = auth_handler.handler(_event({"username": username, "password": password}), None)
        assert resp["statusCode"] == 401
    assert repo.items == {}


def test_empty_seed_accounts_list_seeds_nothing(monkeypatch):
    repo = FakeUserRepo()
    _setup(monkeypatch, repo, accounts=[])
    resp = auth_handler.handler(_event({"username": "admin", "password": "guessed-default-pw"}), None)
    assert resp["statusCode"] == 401
    assert repo.items == {}


def test_reseed_does_not_overwrite_changed_password(monkeypatch):
    # A pre-existing admin with a custom password must survive re-seeding.
    repo = FakeUserRepo(
        {
            "admin": {
                "username": "admin",
                "password_hash": hash_password("custom-pw"),
                "role": "admin",
            }
        }
    )
    _setup(monkeypatch, repo)
    # the configured seed password should NOT work; the custom one should
    assert auth_handler.handler(_event({"username": "admin", "password": "example-admin-pw"}), None)["statusCode"] == 401
    assert auth_handler.handler(_event({"username": "admin", "password": "custom-pw"}), None)["statusCode"] == 200
