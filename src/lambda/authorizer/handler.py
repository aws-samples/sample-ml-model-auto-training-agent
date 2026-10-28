# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import hmac
from functools import lru_cache
import os
from typing import Any

import boto3

from shared.logging_utils import log_event


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    token = extract_token(event.get("authorizationToken", ""))
    method_arn = event.get("methodArn", "*")
    expected_token = get_expected_token(os.environ["TOKEN_SECRET_ARN"])
    is_allowed = hmac.compare_digest(token, expected_token)

    log_event(
        "authorizer-handler",
        "authorization-evaluated",
        decision="Allow" if is_allowed else "Deny",
    )
    return build_policy(
        principal_id="api-token-user",
        effect="Allow" if is_allowed else "Deny",
        resource=method_arn,
    )


def extract_token(raw_header: str) -> str:
    value = raw_header.strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


@lru_cache(maxsize=8)
def get_expected_token(secret_arn: str) -> str:
    # The PoC keeps a small in-process cache to avoid repeated secret lookups on warm starts.
    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=secret_arn)
    return response["SecretString"]


def build_policy(principal_id: str, effect: str, resource: str) -> dict[str, Any]:
    return {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": resource,
                }
            ],
        },
    }
