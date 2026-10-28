# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import boto3

from training_runtime.config import RuntimeConfig


class ProviderConfigurationError(RuntimeError):
    """Raised when provider configuration is invalid or incomplete."""


@dataclass(frozen=True)
class ProviderSelection:
    provider_mode: str
    model_id: str
    region_name: str
    base_url: str
    api_key: str
    temperature: float
    max_tokens: int


def resolve_provider_selection(
    config: RuntimeConfig,
    *,
    secrets_client: Any | None = None,
) -> ProviderSelection:
    if config.model_provider == "bedrock":
        return ProviderSelection(
            provider_mode="bedrock",
            model_id=config.llm_model_id,
            region_name=config.bedrock_region,
            base_url="",
            api_key="",
            temperature=config.model_temperature,
            max_tokens=config.model_max_tokens,
        )

    api_key = config.openai_api_key
    if not api_key and config.openai_api_key_secret_arn:
        client = secrets_client or boto3.client("secretsmanager", region_name=config.bedrock_region)
        response = client.get_secret_value(SecretId=config.openai_api_key_secret_arn)
        secret_text = response.get("SecretString", "")
        if secret_text.startswith("{"):
            payload = json.loads(secret_text)
            api_key = str(payload.get("api_key") or payload.get("token") or "").strip()
        else:
            api_key = secret_text.strip()
    if not api_key:
        raise ProviderConfigurationError("OpenAI-compatible provider mode requires an API key")
    return ProviderSelection(
        provider_mode="openai",
        model_id=config.llm_model_id,
        region_name="",
        base_url=config.openai_base_url,
        api_key=api_key,
        temperature=config.model_temperature,
        max_tokens=config.model_max_tokens,
    )


def build_model(selection: ProviderSelection) -> Any:
    if selection.provider_mode == "openai":
        from strands.models.openai import OpenAIModel

        return OpenAIModel(
            client_args={
                "api_key": selection.api_key,
                "base_url": selection.base_url,
            },
            model_id=selection.model_id,
            params={
                "temperature": selection.temperature,
                "max_tokens": selection.max_tokens,
            },
        )

    from botocore.config import Config as BotocoreConfig
    from strands.models import BedrockModel

    # A single agent turn can involve a long model response; without an explicit
    # read timeout botocore defaults to 60s and a slow Bedrock call aborts the
    # whole attempt (observed: "Read timed out" killing an entire retry). Give
    # generous timeouts plus adaptive retries so transient throttling/timeouts
    # recover in-call instead of burning one of the 3 training attempts.
    boto_client_config = BotocoreConfig(
        connect_timeout=10,
        read_timeout=120,
        retries={"max_attempts": 3, "mode": "adaptive"},
    )
    return BedrockModel(
        model_id=selection.model_id,
        region_name=selection.region_name,
        temperature=selection.temperature,
        max_tokens=selection.max_tokens,
        boto_client_config=boto_client_config,
    )
