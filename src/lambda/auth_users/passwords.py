# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

import hashlib
import hmac
import os

# PBKDF2-HMAC-SHA256 password hashing. Stored form:
#   pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 200_000
_SALT_BYTES = 16


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Return an encoded PBKDF2 hash of the password. A random salt is used
    unless one is supplied (supplying a salt is only for deterministic tests)."""
    if salt is None:
        salt = os.urandom(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _ITERATIONS
    )
    return f"{_ALGORITHM}${_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verify a password against an encoded PBKDF2 hash."""
    try:
        algorithm, iterations_str, salt_hex, hash_hex = encoded.split("$")
    except (ValueError, AttributeError):
        return False
    if algorithm != _ALGORITHM:
        return False
    try:
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    )
    return hmac.compare_digest(derived, expected)
