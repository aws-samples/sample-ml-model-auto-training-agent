# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json

import pytest

core = pytest.importorskip("aws_cdk")
assertions = pytest.importorskip("aws_cdk.assertions")

from infra.infra_stack import AutogluonServerlessStack


def _template():
    app = core.App()
    stack = AutogluonServerlessStack(app, "test-stack")
    return assertions.Template.from_stack(stack)


def _china_template():
    app = core.App()
    stack = AutogluonServerlessStack(
        app,
        "test-stack-cn",
        env=core.Environment(account="123456789012", region="cn-northwest-1"),
    )
    return assertions.Template.from_stack(stack)


def test_s3_buckets_present_and_fully_private():
    template = _template()
    # data bucket, frontend bucket, image-build source bucket, access-logs bucket
    template.resource_count_is("AWS::S3::Bucket", 4)
    # All buckets must block all public access (S3.1/S3.2/S3.3).
    buckets = template.find_resources("AWS::S3::Bucket")
    for resource in buckets.values():
        public_access_block = resource["Properties"]["PublicAccessBlockConfiguration"]
        assert public_access_block == {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }


def test_s3_access_logging_and_lifecycle():
    template = _template()
    buckets = template.find_resources("AWS::S3::Bucket")
    logging_count = 0
    lifecycle_count = 0
    for resource in buckets.values():
        props = resource["Properties"]
        if "LoggingConfiguration" in props:
            logging_count += 1
        if "LifecycleConfiguration" in props:
            lifecycle_count += 1
            rules = props["LifecycleConfiguration"]["Rules"]
            assert all(r.get("Status") == "Enabled" for r in rules)
    # data + frontend + imgbuild log to the access-logs bucket (which does not
    # log to itself).
    assert logging_count == 3
    # every bucket carries at least the abort-multipart lifecycle rule
    assert lifecycle_count == 4


def test_dynamodb_security_options_default_off():
    template = _template()
    template.resource_count_is("AWS::CloudTrail::Trail", 0)
    template.resource_count_is("AWS::Config::ConfigRule", 0)
    template.resource_count_is("AWS::Config::ConfigurationRecorder", 0)
    for resource in template.find_resources("AWS::DynamoDB::Table").values():
        assert "PointInTimeRecoverySpecification" not in resource["Properties"]


def test_dynamodb_security_options_enabled():
    app = core.App(
        context={
            "dynamodbSecurity": {
                "pointInTimeRecovery": True,
                "cloudtrailDataEvents": True,
                "configRules": True,
                "createConfigRecorder": True,
            }
        }
    )
    stack = AutogluonServerlessStack(app, "test-stack-ddbsec")
    template = assertions.Template.from_stack(stack)
    tables = template.find_resources("AWS::DynamoDB::Table")
    assert len(tables) == 6
    for resource in tables.values():
        assert resource["Properties"]["PointInTimeRecoverySpecification"] == {
            "PointInTimeRecoveryEnabled": True
        }
    template.resource_count_is("AWS::CloudTrail::Trail", 1)
    trail = list(template.find_resources("AWS::CloudTrail::Trail").values())[0]
    selectors = trail["Properties"]["AdvancedEventSelectors"][0]["FieldSelectors"]
    arn_selector = [s for s in selectors if s["Field"] == "resources.ARN"][0]
    # data-event logging is scoped to exactly the six project tables
    assert len(arn_selector["Equals"]) == 6
    template.resource_count_is("AWS::Config::ConfigRule", 2)
    template.resource_count_is("AWS::Config::ConfigurationRecorder", 1)
    template.resource_count_is("AWS::Config::DeliveryChannel", 1)


def test_codebuild_image_build_project_present():
    template = _template()
    template.resource_count_is("AWS::CodeBuild::Project", 1)
    template.has_resource_properties(
        "AWS::CodeBuild::Project",
        {
            "Environment": assertions.Match.object_like(
                {
                    # privileged is required to run docker build inside CodeBuild
                    "PrivilegedMode": True,
                }
            )
        },
    )


def test_frontend_served_through_cloudfront():
    template = _template()
    template.resource_count_is("AWS::CloudFront::Distribution", 1)
    template.has_resource_properties(
        "AWS::CloudFront::Distribution",
        {
            "DistributionConfig": assertions.Match.object_like(
                {
                    "DefaultRootObject": "index.html",
                    "DefaultCacheBehavior": assertions.Match.object_like(
                        {"ViewerProtocolPolicy": "redirect-to-https"}
                    ),
                }
            )
        },
    )


def test_data_bucket_enforces_ssl():
    template = _template()
    # enforce_ssl adds a bucket policy denying non-secure transport.
    template.has_resource_properties(
        "AWS::S3::BucketPolicy",
        {
            "PolicyDocument": assertions.Match.object_like(
                {
                    "Statement": assertions.Match.array_with(
                        [
                            assertions.Match.object_like(
                                {
                                    "Effect": "Deny",
                                    "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                                }
                            )
                        ]
                    )
                }
            )
        },
    )


def test_ecr_image_scan_on_push_enabled():
    template = _template()
    template.has_resource_properties(
        "AWS::ECR::Repository",
        {"ImageScanningConfiguration": {"ScanOnPush": True}},
    )


def test_dynamodb_table_and_gsi_present():
    template = _template()
    # tasks, model-registry, infer-tasks, users, images, messages
    template.resource_count_is("AWS::DynamoDB::Table", 6)
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "KeySchema": [{"AttributeName": "task_id", "KeyType": "HASH"}],
            "GlobalSecondaryIndexes": assertions.Match.array_with(
                [
                    assertions.Match.object_like(
                        {
                            "IndexName": "batch-job-id-index",
                        }
                    )
                ]
            ),
        },
    )
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "KeySchema": [{"AttributeName": "model_record_id", "KeyType": "HASH"}],
            "GlobalSecondaryIndexes": assertions.Match.array_with(
                [
                    assertions.Match.object_like(
                        {
                            "IndexName": "target-endpoint-index",
                        }
                    )
                ]
            ),
        },
    )


def test_business_lambdas_present():
    template = _template()
    functions = template.find_resources("AWS::Lambda::Function")
    python_functions = [
        resource
        for resource in functions.values()
        if resource.get("Properties", {}).get("Runtime") == "python3.14"
    ]
    # authorizer, api, oncomplete, deployment, data-list, data-upload,
    # image-list, image-build, infer, image-oncomplete, auth
    assert len(python_functions) == 11


def test_data_files_routes_present():
    template = _template()
    # /data-files resource exposes GET (list/download), POST (upload url) and
    # DELETE (remove a user-uploaded object).
    template.has_resource_properties(
        "AWS::ApiGateway::Resource",
        {"PathPart": "data-files"},
    )
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {"HttpMethod": "DELETE"},
    )


def test_images_routes_present():
    template = _template()
    template.has_resource_properties(
        "AWS::ApiGateway::Resource",
        {"PathPart": "images"},
    )


def test_vpc_nat_and_endpoints_present():
    template = _template()
    template.resource_count_is("AWS::EC2::VPC", 1)
    template.resource_count_is("AWS::EC2::NatGateway", 1)
    template.resource_count_is("AWS::EC2::VPCEndpoint", 2)


# Imported-VPC (isolated subnet) mode: used where an org SCP explicitly denies
# ec2:CreateInternetGateway / ec2:AllocateAddress, so the stack must not create
# an IGW, EIP or NAT gateway at all.
_IMPORTED_VPC_CONTEXT = {
    "vpc": {
        "vpcId": "vpc-0123456789abcdef0",
        "cidrBlock": "10.20.0.0/16",
        "privateSubnetIds": ["subnet-aaa111", "subnet-bbb222"],
        "availabilityZones": ["cn-north-1a", "cn-north-1b"],
    },
    "llm": {
        "baseUrl": "http://llm.intra.example.com:8000/v1",
        "modelId": "local-model",
    },
}


def _imported_vpc_template(extra_vpc=None, context=None):
    ctx = {
        "vpc": {**_IMPORTED_VPC_CONTEXT["vpc"], **(extra_vpc or {})},
        "llm": _IMPORTED_VPC_CONTEXT["llm"],
        **(context or {}),
    }
    app = core.App(context=ctx)
    stack = AutogluonServerlessStack(
        app,
        "test-stack-imported-vpc",
        env=core.Environment(account="210987654321", region="cn-north-1"),
    )
    return assertions.Template.from_stack(stack)


def test_imported_vpc_creates_no_igw_eip_or_nat():
    template = _imported_vpc_template()
    # None of these may be created: the SCP denies them.
    for resource_type in (
        "AWS::EC2::VPC",
        "AWS::EC2::InternetGateway",
        "AWS::EC2::VPCGatewayAttachment",
        "AWS::EC2::EIP",
        "AWS::EC2::NatGateway",
        "AWS::EC2::Subnet",
        "AWS::EC2::RouteTable",
    ):
        template.resource_count_is(resource_type, 0)


def test_imported_vpc_adds_interface_endpoints_and_uses_given_subnets():
    template = _imported_vpc_template()
    endpoints = template.find_resources("AWS::EC2::VPCEndpoint")
    interface = [
        r
        for r in endpoints.values()
        if r["Properties"].get("VpcEndpointType") == "Interface"
    ]
    # With no NAT, every AWS API the workloads call needs a PrivateLink endpoint.
    assert len(interface) == 13
    services = {
        str(r["Properties"]["ServiceName"]).rsplit(".", 1)[-1] for r in interface
    }
    for required in ("dkr", "secretsmanager", "logs", "sts", "ecs-agent"):
        assert required in services
    # Workloads land in the imported subnets.
    compute = list(
        template.find_resources("AWS::Batch::ComputeEnvironment").values()
    )[0]
    assert compute["Properties"]["ComputeResources"]["Subnets"] == [
        "subnet-aaa111",
        "subnet-bbb222",
    ]
    lambda_fn = [
        r
        for r in template.find_resources("AWS::Lambda::Function").values()
        if r["Properties"].get("Runtime") == "python3.14"
    ][0]
    assert lambda_fn["Properties"]["VpcConfig"]["SubnetIds"] == [
        "subnet-aaa111",
        "subnet-bbb222",
    ]


def test_imported_vpc_gateway_endpoints_only_with_route_tables():
    # Gateway endpoints write routes, so they need the route table ids.
    without = _imported_vpc_template()
    assert not [
        r
        for r in without.find_resources("AWS::EC2::VPCEndpoint").values()
        if r["Properties"].get("VpcEndpointType") == "Gateway"
    ]
    with_rtb = _imported_vpc_template(
        extra_vpc={"privateSubnetRouteTableIds": ["rtb-111", "rtb-222"]}
    )
    gateway = [
        r
        for r in with_rtb.find_resources("AWS::EC2::VPCEndpoint").values()
        if r["Properties"].get("VpcEndpointType") == "Gateway"
    ]
    assert len(gateway) == 2  # S3 + DynamoDB


def test_imported_vpc_wires_internal_llm_and_offline_hf():
    template = _imported_vpc_template()
    job_def = list(template.find_resources("AWS::Batch::JobDefinition").values())[0]
    env = {
        e["Name"]: e["Value"]
        for e in job_def["Properties"]["ContainerProperties"]["Environment"]
    }
    assert env["OPENAI_BASE_URL"] == "http://llm.intra.example.com:8000/v1"
    assert env["LLM_MODEL_ID"] == "local-model"
    # No internet route: Chronos must load from the in-image cache.
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"


def test_imported_vpc_rejects_public_llm_endpoint():
    # Fail fast rather than deploying something whose every model call hangs.
    app = core.App(context={"vpc": _IMPORTED_VPC_CONTEXT["vpc"]})
    with pytest.raises(ValueError, match="no internet route"):
        stack = AutogluonServerlessStack(
            app,
            "test-stack-bad-llm",
            env=core.Environment(account="210987654321", region="cn-north-1"),
        )
        assertions.Template.from_stack(stack)


@pytest.mark.parametrize(
    "bad_vpc,expected",
    [
        ({"cidrBlock": None}, "cidrBlock"),
        ({"privateSubnetIds": []}, "privateSubnetIds"),
        ({"availabilityZones": ["cn-north-1a"]}, "availabilityZones"),
    ],
)
def test_imported_vpc_requires_complete_configuration(bad_vpc, expected):
    vpc_ctx = {**_IMPORTED_VPC_CONTEXT["vpc"]}
    for key, value in bad_vpc.items():
        if value is None:
            vpc_ctx.pop(key, None)
        else:
            vpc_ctx[key] = value
    app = core.App(context={"vpc": vpc_ctx, "llm": _IMPORTED_VPC_CONTEXT["llm"]})
    with pytest.raises(ValueError, match=expected):
        stack = AutogluonServerlessStack(
            app,
            "test-stack-bad-vpc",
            env=core.Environment(account="210987654321", region="cn-north-1"),
        )
        assertions.Template.from_stack(stack)


def test_vpc_flow_logs_default_off():
    template = _template()
    template.resource_count_is("AWS::EC2::FlowLog", 0)


def test_vpc_flow_logs_enabled():
    app = core.App(context={"enableVpcFlowLogs": True})
    stack = AutogluonServerlessStack(app, "test-stack-flowlog")
    template = assertions.Template.from_stack(stack)
    template.resource_count_is("AWS::EC2::FlowLog", 1)
    flow_log = list(template.find_resources("AWS::EC2::FlowLog").values())[0]
    props = flow_log["Properties"]
    assert props["ResourceType"] == "VPC"
    assert props["TrafficType"] == "ALL"
    assert props["LogDestinationType"] == "s3"
    # delivered under the access-logs bucket's vpcflowlogs/ prefix so the
    # bucket's expiration lifecycle applies to the flow logs too
    dest = json.dumps(props["LogDestination"])
    assert "vpcflowlogs" in dest
    assert "AccessLogsBucket" in dest


def test_lambdas_are_vpc_attached():
    template = _template()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "VpcConfig": assertions.Match.any_value(),
        },
    )


def test_batch_resources_present():
    template = _template()
    template.resource_count_is("AWS::Batch::ComputeEnvironment", 1)
    template.resource_count_is("AWS::Batch::JobQueue", 1)
    template.resource_count_is("AWS::Batch::JobDefinition", 1)


def test_commercial_region_uses_token_authorizer():
    # Default (non-China) region: original design — Lambda TokenAuthorizer + CORS
    # preflight. No ICP constraint.
    template = _template()
    template.resource_count_is("AWS::ApiGateway::RestApi", 1)
    template.resource_count_is("AWS::ApiGateway::Authorizer", 1)
    # CORS preflight adds OPTIONS mock methods.
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {"HttpMethod": "OPTIONS"},
    )
    rendered = json.dumps(template.to_json(), ensure_ascii=False)
    assert "/models" in rendered


def test_china_region_uses_iam_auth():
    # China: methods use AWS_IAM auth (SigV4), not a Lambda authorizer, so no
    # Authorizer resource should exist. The self-hosted frontend calls
    # execute-api cross-origin, so CORS-preflight OPTIONS methods DO exist now
    # (MOCK integration, authorization NONE) alongside the AWS_IAM business
    # methods.
    template = _china_template()
    template.resource_count_is("AWS::ApiGateway::RestApi", 1)
    template.resource_count_is("AWS::ApiGateway::Authorizer", 0)
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {"HttpMethod": "POST", "AuthorizationType": "AWS_IAM"},
    )
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {"HttpMethod": "GET", "AuthorizationType": "AWS_IAM"},
    )
    # Cross-origin now requires CORS preflight: OPTIONS methods are anonymous
    # (AuthorizationType NONE) so the browser preflight passes.
    template.has_resource_properties(
        "AWS::ApiGateway::Method",
        {"HttpMethod": "OPTIONS", "AuthorizationType": "NONE"},
    )


def test_china_region_has_no_cdk_managed_frontend():
    # China self-hosts the frontend on an internal server, so CDK creates no
    # CloudFront distribution / OAI and no frontend hosting bucket. Only the
    # data bucket and the image-build source bucket remain (2 buckets).
    template = _china_template()
    template.resource_count_is("AWS::CloudFront::Distribution", 0)
    template.resource_count_is(
        "AWS::CloudFront::CloudFrontOriginAccessIdentity", 0
    )
    # data + imgbuild + access-logs (no frontend bucket in China)
    template.resource_count_is("AWS::S3::Bucket", 3)
    # The SigV4 signer user + access key are retained for the frontend.
    template.resource_count_is("AWS::IAM::User", 1)
    template.resource_count_is("AWS::IAM::AccessKey", 1)
    # Frontend hosting outputs are gone; the SigV4 signing outputs are present.
    outputs = template.to_json().get("Outputs", {})
    assert "FrontendBucketName" not in outputs
    assert "FrontendWebsiteUrl" not in outputs
    assert "FrontendDistributionId" not in outputs
    assert "FrontendRuntimeConfigPath" not in outputs
    assert "FrontendApiAccessKeyId" in outputs
    assert "FrontendSignHost" in outputs
    assert "FrontendSignRegion" in outputs


def test_lambda_log_level_wired_and_configurable():
    # Default: every Lambda gets LOG_LEVEL=INFO.
    template = _template()
    python_fns = [
        r
        for r in template.find_resources("AWS::Lambda::Function").values()
        if r["Properties"].get("Runtime") == "python3.14"
    ]
    assert len(python_fns) == 11
    for fn in python_fns:
        variables = fn["Properties"]["Environment"]["Variables"]
        assert variables.get("LOG_LEVEL") == "INFO"
    # Production override via context: LOG_LEVEL=WARN suppresses INFO events.
    app = core.App(context={"lambdaLogLevel": "warn"})
    stack = AutogluonServerlessStack(app, "test-stack-loglevel")
    template2 = assertions.Template.from_stack(stack)
    for fn in template2.find_resources("AWS::Lambda::Function").values():
        if fn["Properties"].get("Runtime") == "python3.14":
            assert fn["Properties"]["Environment"]["Variables"]["LOG_LEVEL"] == "WARN"


def test_async_failure_queue_default_off():
    template = _template()
    template.resource_count_is("AWS::SQS::Queue", 0)
    template.resource_count_is("AWS::Lambda::EventInvokeConfig", 0)


def test_async_failure_queue_enabled():
    app = core.App(context={"enableAsyncFailureQueue": True})
    stack = AutogluonServerlessStack(app, "test-stack-dlq")
    template = assertions.Template.from_stack(stack)
    template.resource_count_is("AWS::SQS::Queue", 1)
    queue = list(template.find_resources("AWS::SQS::Queue").values())[0]
    props = queue["Properties"]
    assert props["SqsManagedSseEnabled"] is True
    assert props["MessageRetentionPeriod"] == 14 * 24 * 3600
    # The three asynchronously-invoked functions each route exhausted-retry
    # events to the queue.
    configs = template.find_resources("AWS::Lambda::EventInvokeConfig")
    assert len(configs) == 3
    for cfg in configs.values():
        dest = cfg["Properties"]["DestinationConfig"]["OnFailure"]["Destination"]
        assert "AsyncFailureQueue" in json.dumps(dest)
    template = _template()
    # Access-log group exists with an explicit retention (auto-deletion) —
    # CloudWatch's default is never-expire.
    log_groups = [
        r
        for r in template.find_resources("AWS::Logs::LogGroup").values()
        if "apigateway" in str(r["Properties"].get("LogGroupName", ""))
    ]
    assert len(log_groups) == 1
    assert log_groups[0]["Properties"]["RetentionInDays"] == 90
    stage = list(template.find_resources("AWS::ApiGateway::Stage").values())[0]
    props = stage["Properties"]
    assert "AccessLogSetting" in props
    # Metadata-only format: no Authorization header / request body fields.
    fmt = props["AccessLogSetting"]["Format"].lower()
    assert "authorization" not in fmt
    # Execution logs at ERROR, data trace stays off (bodies may carry tokens).
    for setting in props["MethodSettings"]:
        assert setting.get("LoggingLevel") == "ERROR"
        assert not setting.get("DataTraceEnabled")


def test_api_request_validation_on_post_methods():
    template = _template()
    template.resource_count_is("AWS::ApiGateway::RequestValidator", 1)
    validator = list(
        template.find_resources("AWS::ApiGateway::RequestValidator").values()
    )[0]["Properties"]
    assert validator["ValidateRequestBody"] is True
    assert validator["ValidateRequestParameters"] is True
    methods = template.find_resources("AWS::ApiGateway::Method")
    business_posts = [
        m["Properties"]
        for m in methods.values()
        if m["Properties"].get("HttpMethod") == "POST"
        and m["Properties"].get("AuthorizationType") != "NONE"
    ]
    # every authenticated POST method carries the validator
    assert len(business_posts) == 8
    for props in business_posts:
        assert "RequestValidatorId" in props


def test_api_waf_default_off():
    template = _template()
    template.resource_count_is("AWS::WAFv2::WebACL", 0)
    template.resource_count_is("AWS::WAFv2::WebACLAssociation", 0)


def test_api_waf_enabled():
    app = core.App(context={"enableApiWaf": True, "apiWafRateLimit": 500})
    stack = AutogluonServerlessStack(app, "test-stack-waf")
    template = assertions.Template.from_stack(stack)
    template.resource_count_is("AWS::WAFv2::WebACL", 1)
    template.resource_count_is("AWS::WAFv2::WebACLAssociation", 1)
    acl = list(template.find_resources("AWS::WAFv2::WebACL").values())[0]
    rules = {r["Name"]: r for r in acl["Properties"]["Rules"]}
    assert set(rules) == {
        "AWSManagedRulesCommonRuleSet",
        "AWSManagedRulesKnownBadInputsRuleSet",
        "RateLimitPerIp",
    }
    rate = rules["RateLimitPerIp"]["Statement"]["RateBasedStatement"]
    assert rate["Limit"] == 500


def test_messages_route_present():
    template = _template()
    template.has_resource_properties(
        "AWS::ApiGateway::Resource",
        {"PathPart": "messages"},
    )


def test_message_table_has_ttl_and_recipient_index():
    template = _template()
    template.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TimeToLiveSpecification": {
                "AttributeName": "expires_at",
                "Enabled": True,
            },
            "GlobalSecondaryIndexes": assertions.Match.array_with(
                [
                    assertions.Match.object_like(
                        {"IndexName": "recipient-index"}
                    )
                ]
            ),
        },
    )


def test_infer_tasks_route_present():
    template = _template()
    template.has_resource_properties(
        "AWS::ApiGateway::Resource",
        {"PathPart": "infer-tasks"},
    )


def test_eventbridge_rule_for_batch_completion_present():
    template = _template()
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "EventPattern": assertions.Match.object_like(
                {
                    "source": ["aws.batch"],
                    "detail-type": ["Batch Job State Change"],
                }
            )
        },
    )


def test_api_lambda_environment_wiring_present():
    template = _template()
    template.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "Environment": {
                "Variables": assertions.Match.object_like(
                    {
                        "TABLE_NAME": assertions.Match.any_value(),
                        "BUCKET_NAME": assertions.Match.any_value(),
                        "JOB_QUEUE_ARN": assertions.Match.any_value(),
                        "JOB_DEF_ARN": assertions.Match.any_value(),
                        "IMAGE_REPO_URI": assertions.Match.any_value(),
                        "MODEL_REGISTRY_TABLE_NAME": assertions.Match.any_value(),
                    }
                )
            }
        },
    )


def test_runtime_job_definition_and_secret_wiring_present():
    template_json = json.dumps(_template().to_json(), ensure_ascii=False)
    assert "OPENAI_API_KEY_SECRET_ARN" in template_json
    assert "MODEL_PROVIDER" in template_json
    assert "secretsmanager:GetSecretValue" in template_json


def test_frontend_outputs_present():
    outputs = _template().to_json()["Outputs"]
    assert "FrontendBucketName" in outputs
    assert "FrontendWebsiteUrl" in outputs
    assert "ApiBaseUrl" in outputs
    assert "ModelRegistryTableName" in outputs


def test_deployment_permissions_present():
    template_json = json.dumps(_template().to_json(), ensure_ascii=False)
    assert "sagemaker:CreateEndpoint" in template_json
    assert "lambda:InvokeFunction" in template_json
