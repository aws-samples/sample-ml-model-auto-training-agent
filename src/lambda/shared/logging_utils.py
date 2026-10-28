# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
import os
from datetime import datetime, timezone
from typing import Any

# Minimum level actually emitted, from the LOG_LEVEL env var (default INFO).
# Set LOG_LEVEL=WARN in production to silence INFO/DEBUG messages.
_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARN": 30, "WARNING": 30, "ERROR": 40}

# Field names whose values are always masked in the log output.
_SENSITIVE_MARKERS = ("token", "secret", "password", "credential", "api_key")


def _min_level() -> int:
    configured = os.environ.get("LOG_LEVEL", "INFO").upper()
    return _LEVEL_ORDER.get(configured, 20)


def log_event(component: str, message: str, level: str = "INFO", **fields: Any) -> None:
    if _LEVEL_ORDER.get(level.upper(), 20) < _min_level():
        return
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "component": component,
        "level": level,
        "message": message,
    }
    for key, value in fields.items():
        if value is None:
            continue
        if any(marker in key.lower() for marker in _SENSITIVE_MARKERS):
            payload[key] = "***"
        else:
            payload[key] = value
    print(json.dumps(payload, default=str, sort_keys=True))
