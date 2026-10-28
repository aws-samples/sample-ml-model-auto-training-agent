# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import base64
import json
import os
from typing import Any

from auth_users.passwords import hash_password, verify_password
from auth_users.repository import ROLE_ADMIN, ROLE_USER, UserRepository
from shared.logging_utils import log_event
from shared.responses import error_response, json_response
from shared.time_utils import utc_timestamp


# There are deliberately no built-in accounts. A built-in default would give
# every deployment the same publicly known admin password, so when SEED_ACCOUNTS
# is absent or unreadable nothing is seeded and login stays closed until the
# deployer supplies accounts (cdk.json context "seedAccounts" -> SEED_ACCOUNTS
# env var); see _load_seed_accounts.
_NO_ACCOUNTS: tuple[tuple[str, str, str], ...] = ()

# Roles accepted from configuration; anything else falls back to ROLE_USER.
_VALID_ROLES = {ROLE_ADMIN, ROLE_USER}


def _load_seed_accounts() -> tuple[tuple[str, str, str], ...]:
    """Read the seed accounts from the SEED_ACCOUNTS env var (JSON array of
    ``{"username", "password", "role"}`` objects).

    Fails closed: a missing, malformed or empty list seeds nothing, so a
    misconfiguration locks login rather than opening it with a known password.
    Each case is logged so the reason is visible in CloudWatch."""
    raw = os.environ.get("SEED_ACCOUNTS", "").strip()
    if not raw:
        log_event("auth-handler", "seed-accounts-not-configured", level="WARN")
        return _NO_ACCOUNTS
    try:
        entries = json.loads(raw)
        if not isinstance(entries, list):
            raise ValueError("SEED_ACCOUNTS must be a JSON array")
        accounts: list[tuple[str, str, str]] = []
        for entry in entries:
            username = (entry.get("username") or "").strip()
            password = entry.get("password") or ""
            role = entry.get("role") or ROLE_USER
            if not username or not password:
                continue
            if role not in _VALID_ROLES:
                role = ROLE_USER
            accounts.append((username, password, role))
        return tuple(accounts)
    except (ValueError, AttributeError, TypeError):
        log_event(
            "auth-handler",
            "seed-accounts-parse-failed",
            level="WARN",
        )
        return _NO_ACCOUNTS


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """POST /login: validate username+password against the users table and
    return the user's role. Configured accounts are seeded on first invocation."""
    request_id = getattr(context, "aws_request_id", None)
    repository = UserRepository(table_name=os.environ["USER_TABLE_NAME"])
    _seed_demo_accounts(repository)

    try:
        payload = _parse_body(event)
    except ValueError:
        return error_response(400, "Request body must be valid JSON", "VALIDATION_ERROR")

    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    if not username or not password:
        return error_response(400, "缺少用户名或密码", "VALIDATION_ERROR")

    user = repository.get_user(username)
    if user is None or not verify_password(password, user.get("password_hash", "")):
        # Do not reveal which of the two was wrong.
        log_event(
            "auth-handler",
            "login-failed",
            level="WARN",
            request_id=request_id,
            username=username,
        )
        return error_response(401, "用户名或密码错误", "INVALID_CREDENTIALS")

    log_event(
        "auth-handler",
        "login-succeeded",
        request_id=request_id,
        username=username,
        role=user.get("role"),
    )
    return json_response(
        200,
        {
            "username": username,
            "role": user.get("role", ROLE_USER),
            "display_name": user.get("display_name", username),
        },
    )


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode("utf-8")
    payload = json.loads(raw_body)
    if not isinstance(payload, dict):
        raise ValueError("body must be an object")
    return payload


def _seed_demo_accounts(repository: UserRepository) -> None:
    """Idempotently create the configured accounts. Existing rows are left untouched
    so a re-seed never overwrites a changed password."""
    now = utc_timestamp()
    for username, password, role in _load_seed_accounts():
        repository.create_user_if_absent(
            {
                "username": username,
                "password_hash": hash_password(password),
                "role": role,
                "display_name": username,
                "created_at": now,
                "updated_at": now,
            }
        )
