from types import SimpleNamespace

import authorizer.handler as authorizer_handler


def test_authorizer_allows_matching_token(monkeypatch):
    monkeypatch.setenv("TOKEN_SECRET_ARN", "secret-arn")
    monkeypatch.setattr(authorizer_handler, "get_expected_token", lambda arn: "secret-token")

    response = authorizer_handler.handler(
        {
            "authorizationToken": "Bearer secret-token",
            "methodArn": "arn:aws:execute-api:cn/test",
        },
        SimpleNamespace(aws_request_id="req-1"),
    )

    assert response["policyDocument"]["Statement"][0]["Effect"] == "Allow"


def test_authorizer_denies_non_matching_token(monkeypatch):
    monkeypatch.setenv("TOKEN_SECRET_ARN", "secret-arn")
    monkeypatch.setattr(authorizer_handler, "get_expected_token", lambda arn: "secret-token")

    response = authorizer_handler.handler(
        {
            "authorizationToken": "Bearer wrong-token",
            "methodArn": "arn:aws:execute-api:cn/test",
        },
        SimpleNamespace(aws_request_id="req-1"),
    )

    assert response["policyDocument"]["Statement"][0]["Effect"] == "Deny"
