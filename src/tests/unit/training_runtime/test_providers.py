# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
from training_runtime.config import load_runtime_config
from training_runtime.providers import resolve_provider_selection


class FakeSecretsClient:
    def get_secret_value(self, SecretId: str):
        assert SecretId == "arn:aws-cn:secretsmanager:secret:test"
        return {"SecretString": '{"api_key": "secret-token"}'}


def test_resolve_bedrock_provider_selection():
    config = load_runtime_config({"MODEL_PROVIDER": "bedrock", "AWS_REGION": "cn-north-1"})
    selection = resolve_provider_selection(config)

    assert selection.provider_mode == "bedrock"
    assert selection.region_name == "cn-north-1"


def test_resolve_openai_provider_from_secret():
    config = load_runtime_config(
        {
            "MODEL_PROVIDER": "openai",
            "OPENAI_BASE_URL": "https://example.test/v1",
            "OPENAI_API_KEY_SECRET_ARN": "arn:aws-cn:secretsmanager:secret:test",
        }
    )
    selection = resolve_provider_selection(config, secrets_client=FakeSecretsClient())

    assert selection.provider_mode == "openai"
    assert selection.api_key == "secret-token"
    assert selection.base_url == "https://example.test/v1"
