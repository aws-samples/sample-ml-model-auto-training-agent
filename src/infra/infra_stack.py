# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
import json
from pathlib import Path

from aws_cdk import (
    Annotations,
    ArnFormat,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Size,
    Stack,
    aws_apigateway as apigw,
    aws_batch as batch,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as cloudfront_origins,
    aws_cloudtrail as cloudtrail,
    aws_codebuild as codebuild,
    aws_config as config,
    aws_dynamodb as dynamodb,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_destinations as lambda_destinations,
    aws_logs as aws_logs,
    aws_s3 as s3,
    aws_s3_deployment as s3_deployment,
    aws_scheduler as scheduler,
    aws_secretsmanager as secretsmanager,
    aws_sqs as sqs,
    aws_wafv2 as wafv2,
)
from constructs import Construct


class AutogluonServerlessStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Region branch flag, defined early so bucket definitions can use it.
        is_china = self.region.startswith("cn-")
        # China orgs often enforce an SCP that DENIES s3:PutBucketPublicAccessBlock
        # , which makes CloudFormation fail to create a bucket
        # that sets a bucket-level Public Access Block. So in China we omit the
        # bucket-level block and rely on the account-level Public Access Block
        # (managed org-wide) to keep buckets private. Commercial regions keep the
        # explicit bucket-level BLOCK_ALL.
        bucket_public_access_block = (
            None if is_china else s3.BlockPublicAccess.BLOCK_ALL
        )

        # AWS X-Ray tracing toggle. Enabled by default (security baseline: enable
        # tracing for services that support it). Set cdk.json context
        # "enableXray": false to turn it off (e.g. to avoid X-Ray cost/permissions
        # in regions or accounts where it is not desired). Applies to all Lambda
        # functions and the API Gateway stage.
        enable_xray = self.node.try_get_context("enableXray")
        enable_xray = True if enable_xray is None else bool(enable_xray)
        self._lambda_tracing = (
            lambda_.Tracing.ACTIVE if enable_xray else lambda_.Tracing.DISABLED
        )
        self._enable_xray = enable_xray

        lambda_source = Path(__file__).resolve().parent.parent / "lambda"
        training_runtime_source = (
            Path(__file__).resolve().parent.parent / "training_runtime"
        )

        # S3 lifecycle settings (security baseline: buckets should have lifecycle
        # policies). Defaults below; override any of them via the cdk.json context
        # "s3Lifecycle" object, e.g. {"logsExpireDays": 30}. Set a value to 0 to
        # disable that specific rule.
        lc = self.node.try_get_context("s3Lifecycle") or {}
        lc_logs_days = int(lc.get("logsExpireDays", 90))
        lc_build_src_days = int(lc.get("buildSourceExpireDays", 30))
        lc_access_logs_days = int(lc.get("accessLogsExpireDays", 90))
        lc_abort_mpu_days = int(lc.get("abortMultipartDays", 7))

        def _rule(**kw):
            return s3.LifecycleRule(**kw)

        def _common_rules():
            # Applied to every bucket: clean up stale incomplete multipart uploads.
            rules = []
            if lc_abort_mpu_days > 0:
                rules.append(
                    _rule(
                        id="abort-incomplete-multipart",
                        abort_incomplete_multipart_upload_after=Duration.days(
                            lc_abort_mpu_days
                        ),
                    )
                )
            return rules

        # DynamoDB security options (all default OFF; enable via the cdk.json
        # context object "dynamodbSecurity"):
        #  - pointInTimeRecovery: continuous backups (PITR) on every table.
        #  - cloudtrailDataEvents: a CloudTrail trail logging DynamoDB data-plane
        #    events (GetItem/PutItem/...) for the project tables only.
        #  - configRules: AWS Config managed rules monitoring the tables
        #    (requires a configuration recorder to already exist in the account).
        #  - createConfigRecorder: also create the recorder + delivery channel.
        #    Only ONE recorder is allowed per region — leave this false if the
        #    account (e.g. an org-managed Landing Zone) already records Config.
        dsec = self.node.try_get_context("dynamodbSecurity") or {}
        ddb_pitr = bool(dsec.get("pointInTimeRecovery", False))
        ddb_trail_data_events = bool(dsec.get("cloudtrailDataEvents", False))
        ddb_config_rules = bool(dsec.get("configRules", False))
        ddb_config_recorder = bool(dsec.get("createConfigRecorder", False))
        pitr_spec = (
            dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            )
            if ddb_pitr
            else None
        )

        # Server access logging (security baseline: enable access logging on all
        # in-scope buckets). One dedicated log bucket receives access logs from
        # every other bucket, separated by prefix. The log bucket itself expires
        # its objects and does NOT log to itself (avoids recursion).
        self.access_logs_bucket = s3.Bucket(
            self,
            "AccessLogsBucket",
            bucket_name=f"auto-training-agent-accesslogs-{self.account}-{self.region}",
            block_public_access=bucket_public_access_block,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            # S3 log delivery requires ACLs enabled on the target bucket.
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
            lifecycle_rules=_common_rules()
            + (
                [
                    _rule(
                        id="expire-access-logs",
                        expiration=Duration.days(lc_access_logs_days),
                    )
                ]
                if lc_access_logs_days > 0
                else []
            ),
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        self.bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=f"auto-training-agent-data-{self.account}-{self.region}",
            # China: None(靠账号级 block,规避 SCP deny);商业区:BLOCK_ALL。见上方注释。
            block_public_access=bucket_public_access_block,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            server_access_logs_bucket=self.access_logs_bucket,
            server_access_logs_prefix="data-bucket/",
            # Lifecycle: training logs under logs/ expire (they are mirrored,
            # short-lived diagnostics); stale multipart uploads are aborted.
            # Training data (data/) and artifacts (output/) are NOT expired —
            # they are the user's durable content.
            lifecycle_rules=_common_rules()
            + (
                [
                    _rule(
                        id="expire-training-logs",
                        prefix="logs/",
                        expiration=Duration.days(lc_logs_days),
                    )
                ]
                if lc_logs_days > 0
                else []
            ),
            # Allow the browser to PUT directly to presigned upload URLs.
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.PUT],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                    max_age=3000,
                )
            ],
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        # Auth/hosting architecture is region-dependent (see the API-Gateway
        # section below for the full auth rationale):
        #  - Commercial regions (e.g. us-west-2): CDK hosts the static frontend
        #    on a private S3 bucket served over HTTPS via CloudFront (OAC), and
        #    the API uses a Lambda TokenAuthorizer.
        #  - China regions (cn-*): the frontend is self-hosted on an internal
        #    server (NOT managed by CDK), and the browser calls execute-api
        #    cross-origin, signing each request with SigV4 (IAM auth). So CDK
        #    creates no frontend bucket / CloudFront here; it only provisions the
        #    backend plus a least-privilege IAM signer user (added far below).
        # (is_china defined at the top of __init__.)
        if not is_china:
            self.frontend_bucket = s3.Bucket(
                self,
                "FrontendHostingBucket",
                bucket_name=f"auto-training-agent-frontend-{self.account}-{self.region}",
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                encryption=s3.BucketEncryption.S3_MANAGED,
                server_access_logs_bucket=self.access_logs_bucket,
                server_access_logs_prefix="frontend-bucket/",
                lifecycle_rules=_common_rules(),
                # enforce_ssl is intentionally NOT set: CloudFront reaches the S3
                # origin via OAC and the bucket stays private (BLOCK_ALL);
                # external access is HTTPS-only via REDIRECT_TO_HTTPS. The
                # DataBucket keeps enforce_ssl.
                removal_policy=RemovalPolicy.DESTROY,
                auto_delete_objects=True,
            )
            # Serve the static frontend over HTTPS via CloudFront, keeping the
            # hosting bucket fully private (S3.1/2/3) via the modern OAC.
            frontend_origin = cloudfront_origins.S3BucketOrigin.with_origin_access_control(
                self.frontend_bucket
            )
            self.frontend_distribution = cloudfront.Distribution(
                self,
                "FrontendDistribution",
                default_root_object="index.html",
                enable_ipv6=True,
                default_behavior=cloudfront.BehaviorOptions(
                    origin=frontend_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                ),
                # NOTE: no custom error_responses. A 403/404 SPA fallback would
                # be distribution-wide and rewrite unrelated errors into a 200
                # index.html. The app uses no client-side routing, so it is
                # unnecessary anyway.
            )
            self.frontend_url = (
                f"https://{self.frontend_distribution.distribution_domain_name}"
            )
        else:
            # China: frontend is self-hosted; CDK manages no frontend resources.
            self.frontend_bucket = None
            self.frontend_distribution = None
            self.frontend_url = None

        self.table = dynamodb.Table(
            self,
            "TaskTable",
            table_name="auto-training-agent-tasks",
            partition_key=dynamodb.Attribute(
                name="task_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=pitr_spec,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.table.add_global_secondary_index(
            index_name="batch-job-id-index",
            partition_key=dynamodb.Attribute(
                name="batch_job_id",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        self.model_registry_table = dynamodb.Table(
            self,
            "ModelRegistryTable",
            table_name="auto-training-agent-models",
            partition_key=dynamodb.Attribute(
                name="model_record_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=pitr_spec,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.model_registry_table.add_global_secondary_index(
            index_name="target-endpoint-index",
            partition_key=dynamodb.Attribute(
                name="target_endpoint",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="created_at",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )
        # Inference tasks: each row is an async SageMaker endpoint invocation.
        self.infer_table = dynamodb.Table(
            self,
            "InferTaskTable",
            table_name="auto-training-agent-infer-tasks",
            partition_key=dynamodb.Attribute(
                name="task_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=pitr_spec,
            removal_policy=RemovalPolicy.DESTROY,
        )
        # Application users + roles (admin / user). The login handler seeds the
        # two demo accounts on first use and validates credentials against this
        # table; the frontend uses the returned role to gate admin-only UI.
        self.user_table = dynamodb.Table(
            self,
            "UserTable",
            table_name="auto-training-agent-users",
            partition_key=dynamodb.Attribute(
                name="username",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=pitr_spec,
            removal_policy=RemovalPolicy.DESTROY,
        )
        # Training-agent images: the source of truth for the image list. A row is
        # created (BUILDING) at build start and reconciled to SUCCEEDED/FAILED by
        # the CodeBuild completion handler. The GSI resolves the row from the
        # CodeBuild run id carried by the completion event.
        self.image_table = dynamodb.Table(
            self,
            "ImageTable",
            table_name="auto-training-agent-images",
            partition_key=dynamodb.Attribute(
                name="tag",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=pitr_spec,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.image_table.add_global_secondary_index(
            index_name="build-id-index",
            partition_key=dynamodb.Attribute(
                name="build_id",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # User notification messages (e.g. training task status changes). Rows
        # self-expire after 7 days via the DynamoDB TTL on ``expires_at`` (a Unix
        # epoch second count). A recipient-index GSI lists a user's messages
        # newest-first by created_at.
        self.message_table = dynamodb.Table(
            self,
            "MessageTable",
            table_name="auto-training-agent-messages",
            partition_key=dynamodb.Attribute(
                name="message_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=pitr_spec,
            time_to_live_attribute="expires_at",
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.message_table.add_global_secondary_index(
            index_name="recipient-index",
            partition_key=dynamodb.Attribute(
                name="recipient",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="created_at",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        self._all_dynamodb_tables = [
            self.table,
            self.model_registry_table,
            self.infer_table,
            self.user_table,
            self.image_table,
            self.message_table,
        ]

        # Optional: CloudTrail DynamoDB data-plane events (context
        # "dynamodbSecurity.cloudtrailDataEvents"). Records item-level API calls
        # (GetItem/PutItem/Query/...) on the project tables only — data events
        # are billed per event, so the selector is scoped to these table ARNs
        # rather than all DynamoDB resources. Logs land in the shared access-logs
        # bucket under cloudtrail/ and follow its expiration lifecycle.
        if ddb_trail_data_events:
            # L1 CfnTrail: the L2 Trail cannot express "data events only via
            # AdvancedEventSelectors, no management events" (its validation
            # rejects management_events=NONE without an L2 selector). CloudTrail
            # needs the standard bucket-policy grants on the target bucket;
            # appended to the existing policy (enforce_ssl already created one).
            self.access_logs_bucket.add_to_resource_policy(
                iam.PolicyStatement(
                    sid="AWSCloudTrailAclCheck",
                    principals=[iam.ServicePrincipal("cloudtrail.amazonaws.com")],
                    actions=["s3:GetBucketAcl"],
                    resources=[self.access_logs_bucket.bucket_arn],
                )
            )
            self.access_logs_bucket.add_to_resource_policy(
                iam.PolicyStatement(
                    sid="AWSCloudTrailWrite",
                    principals=[iam.ServicePrincipal("cloudtrail.amazonaws.com")],
                    actions=["s3:PutObject"],
                    resources=[
                        f"{self.access_logs_bucket.bucket_arn}/cloudtrail/AWSLogs/{self.account}/*"
                    ],
                    conditions={
                        "StringEquals": {
                            "s3:x-amz-acl": "bucket-owner-full-control"
                        }
                    },
                )
            )
            ddb_trail = cloudtrail.CfnTrail(
                self,
                "DynamoDbDataEventsTrail",
                trail_name="auto-training-agent-ddb-data-events",
                s3_bucket_name=self.access_logs_bucket.bucket_name,
                s3_key_prefix="cloudtrail",
                is_logging=True,
                is_multi_region_trail=False,
                enable_log_file_validation=True,
                # Management events are usually covered by an account/org-level
                # trail already; this trail exists solely for data events.
                advanced_event_selectors=[
                    cloudtrail.CfnTrail.AdvancedEventSelectorProperty(
                        name="auto-training-agent-dynamodb-data-events",
                        field_selectors=[
                            cloudtrail.CfnTrail.AdvancedFieldSelectorProperty(
                                field="eventCategory", equal_to=["Data"]
                            ),
                            cloudtrail.CfnTrail.AdvancedFieldSelectorProperty(
                                field="resources.type",
                                equal_to=["AWS::DynamoDB::Table"],
                            ),
                            cloudtrail.CfnTrail.AdvancedFieldSelectorProperty(
                                field="resources.ARN",
                                equal_to=[
                                    t.table_arn for t in self._all_dynamodb_tables
                                ],
                            ),
                        ],
                    )
                ],
            )
            bucket_policy = self.access_logs_bucket.policy
            if bucket_policy is not None:
                ddb_trail.node.add_dependency(bucket_policy)

        # Optional: AWS Config continuous monitoring (context
        # "dynamodbSecurity.configRules"). Managed rules evaluate the DynamoDB
        # tables on every configuration change. Config rules REQUIRE a
        # configuration recorder in the region; org-managed accounts usually
        # already have one — set "createConfigRecorder": true only when the
        # account has none (one recorder max per region, duplicate creation
        # fails the deploy).
        if ddb_config_rules:
            if ddb_config_recorder:
                config_role = iam.Role(
                    self,
                    "ConfigRecorderRole",
                    assumed_by=iam.ServicePrincipal("config.amazonaws.com"),
                    managed_policies=[
                        iam.ManagedPolicy.from_aws_managed_policy_name(
                            "service-role/AWS_ConfigRole"
                        )
                    ],
                )
                self.access_logs_bucket.grant_write(
                    config_role, "config/*"
                )
                self.access_logs_bucket.grant_read(config_role)
                recorder = config.CfnConfigurationRecorder(
                    self,
                    "ConfigRecorder",
                    role_arn=config_role.role_arn,
                    recording_group=config.CfnConfigurationRecorder.RecordingGroupProperty(
                        all_supported=False,
                        resource_types=["AWS::DynamoDB::Table"],
                    ),
                )
                delivery_channel = config.CfnDeliveryChannel(
                    self,
                    "ConfigDeliveryChannel",
                    s3_bucket_name=self.access_logs_bucket.bucket_name,
                    s3_key_prefix="config",
                )
                delivery_channel.node.add_dependency(recorder)

            ddb_table_scope = config.RuleScope.from_resources(
                [config.ResourceType.DYNAMODB_TABLE]
            )
            pitr_rule = config.ManagedRule(
                self,
                "DdbPitrEnabledRule",
                identifier=config.ManagedRuleIdentifiers.DYNAMODB_PITR_ENABLED,
                rule_scope=ddb_table_scope,
                description="Checks that point-in-time recovery is enabled on DynamoDB tables.",
            )
            encryption_rule = config.ManagedRule(
                self,
                "DdbEncryptionEnabledRule",
                identifier=config.ManagedRuleIdentifiers.DYNAMODB_TABLE_ENCRYPTION_ENABLED,
                rule_scope=ddb_table_scope,
                description="Checks that server-side encryption is enabled on DynamoDB tables.",
            )
            if ddb_config_recorder:
                pitr_rule.node.add_dependency(recorder)
                encryption_rule.node.add_dependency(recorder)

        # Optional: immutable image tags (context "ecrImmutableTags", default
        # off). Off by default because the console rebuilds the rolling "stable"
        # tag in place. When on, a pushed tag can never be overwritten, so every
        # build must use a new tag (e.g. a date or version) and tasks select it
        # explicitly; "stable" itself can be built only once.
        ecr_immutable_tags = str(
            self.node.try_get_context("ecrImmutableTags") or False
        ).lower() in ("true", "1")
        self.repo = ecr.Repository(
            self,
            "AgentImageRepo",
            repository_name="autogluon-agent",
            image_scan_on_push=True,
            image_tag_mutability=(
                ecr.TagMutability.IMMUTABLE
                if ecr_immutable_tags
                else ecr.TagMutability.MUTABLE
            ),
            removal_policy=RemovalPolicy.DESTROY,
            empty_on_delete=True,
        )

        # CodeBuild project that builds the training image natively on x86 in the
        # cloud and pushes it to ECR. This replaces slow cross-architecture local
        # docker builds. The build source is uploaded to S3 (the src/ tree zipped)
        # and the project is started on demand via `aws codebuild start-build`.
        self.image_build_source_bucket = s3.Bucket(
            self,
            "ImageBuildSourceBucket",
            bucket_name=f"auto-training-agent-imgbuild-{self.account}-{self.region}",
            # China: None(靠账号级 block,规避 SCP deny);商业区:BLOCK_ALL。
            block_public_access=bucket_public_access_block,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            server_access_logs_bucket=self.access_logs_bucket,
            server_access_logs_prefix="imgbuild-bucket/",
            # Lifecycle: repackaged build-source zips under agent-uploads/ are
            # transient (only needed until CodeBuild consumes them); the
            # agent-baseline/ tree is redeployed by CDK and is NOT expired.
            lifecycle_rules=_common_rules()
            + (
                [
                    _rule(
                        id="expire-build-sources",
                        prefix="agent-uploads/",
                        expiration=Duration.days(lc_build_src_days),
                    )
                ]
                if lc_build_src_days > 0
                else []
            ),
            # Allow the browser to PUT agent zips to presigned upload URLs.
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.PUT],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                    max_age=3000,
                )
            ],
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        self.image_build_project = codebuild.Project(
            self,
            "AgentImageBuildProject",
            project_name="autogluon-agent-image-build",
            # Source is the zipped src/ tree uploaded under build-source/source.zip.
            source=codebuild.Source.s3(
                bucket=self.image_build_source_bucket,
                path="build-source/source.zip",
            ),
            environment=codebuild.BuildEnvironment(
                # x86_64 Linux image with Docker available; privileged is required
                # to run docker build inside the build container.
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                compute_type=codebuild.ComputeType.LARGE,
                privileged=True,
            ),
            environment_variables={
                "ECR_REPO_URI": codebuild.BuildEnvironmentVariable(
                    value=self.repo.repository_uri
                ),
                "AWS_DEFAULT_REGION": codebuild.BuildEnvironmentVariable(
                    value=self.region
                ),
                "AWS_ACCOUNT_ID": codebuild.BuildEnvironmentVariable(
                    value=self.account
                ),
                # Override at start-build time for A/B image tags; defaults to stable.
                "IMAGE_TAG": codebuild.BuildEnvironmentVariable(value="stable"),
                # Which already-pushed tag to read the BuildKit inline cache from.
                # Defaults to "stable"; override at start-build time to warm from
                # a specific previous tag (e.g. the last successful vNNN) when
                # "stable" is older than the layers you want to reuse.
                "CACHE_FROM_TAG": codebuild.BuildEnvironmentVariable(value="stable"),
                # Region-specific docker build args. China needs the Aliyun PyPI
                # mirror, the Aliyun PyTorch-wheels mirror (the CUDA 11.8
                # torch/nvidia-cu11 layer, which is not on the normal PyPI index
                # and times out from download.pytorch.org in China), AND the
                # hf-mirror.com HuggingFace endpoint (used to pre-bake the Chronos
                # weights into the image at build time); commercial regions use
                # the official PyPI + PyTorch index + huggingface.co (Dockerfile
                # defaults), so pass nothing there.
                # APT_MIRROR is the same story for Debian packages: the image
                # installs libgomp1 (LightGBM needs it at runtime), and
                # deb.debian.org is unreachable from China's CodeBuild egress —
                # a build failed with "Failed to fetch ... libgomp1_14.2.0 ...
                # Unable to connect to deb.debian.org" after a 457s stall.
                "PIP_BUILD_ARGS": codebuild.BuildEnvironmentVariable(
                    value=(
                        "--build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ "
                        "--build-arg PIP_TRUSTED_HOST=mirrors.aliyun.com "
                        "--build-arg TORCH_FIND_LINKS=https://mirrors.aliyun.com/pytorch-wheels/cu118/ "
                        "--build-arg HF_ENDPOINT=https://hf-mirror.com "
                        "--build-arg APT_MIRROR=http://mirrors.aliyun.com"
                        if is_china
                        else ""
                    )
                ),
            },
            # AutoGluon pulls in torch/CUDA/etc. (multi-GB) and the build has
            # no layer cache, so wall time is set by whatever the Aliyun mirrors
            # are doing that day: observed 37 / 48 / 48 / 67 / 72 / 96 minutes,
            # and one build was still resolving AutoGluon deps when it hit the
            # 120-minute cap. 3h covers the slow tail.
            #
            # A buildx + ECR-registry-cache setup was tried and reverted: the
            # docker-container driver pulls moby/buildkit from
            # registry-1.docker.io, which CodeBuild cannot reach in the China
            # regions (the build image itself comes from public.ecr.aws, so
            # nothing else in this project depends on Docker Hub).
            timeout=Duration.hours(3),
            build_spec=codebuild.BuildSpec.from_object(
                {
                    "version": "0.2",
                    "phases": {
                        "pre_build": {
                            "commands": [
                                "echo Logging in to Amazon ECR...",
                                # Derive the ECR registry host from ECR_REPO_URI
                                # (everything before the first "/"). This is
                                # region-correct in every partition — China ECR
                                # uses the .amazonaws.com.cn suffix, so hardcoding
                                # ".amazonaws.com" here breaks docker login in
                                # cn-north-1 / cn-northwest-1.
                                "ECR_REGISTRY=${ECR_REPO_URI%%/*}",
                                "aws ecr get-login-password --region $AWS_DEFAULT_REGION "
                                "| docker login --username AWS --password-stdin "
                                "$ECR_REGISTRY",
                            ],
                        },
                        "build": {
                            "commands": [
                                "echo Building training image $ECR_REPO_URI:$IMAGE_TAG",
                                # Locate the Dockerfile: prefer training_runtime/,
                                # fall back to repo root, so both the platform src
                                # tree and a user-uploaded agent zip work.
                                "if [ -f training_runtime/Dockerfile ]; then "
                                "DOCKERFILE=training_runtime/Dockerfile; CONTEXT=.; "
                                "elif [ -f Dockerfile ]; then "
                                "DOCKERFILE=Dockerfile; CONTEXT=.; "
                                "else "
                                "DOCKERFILE=$(find . -name Dockerfile | head -n1); "
                                "CONTEXT=$(dirname $DOCKERFILE); fi",
                                "echo Using Dockerfile=$DOCKERFILE context=$CONTEXT",
                                # PIP_BUILD_ARGS is region-specific (empty in
                                # commercial regions => official PyPI; the Aliyun
                                # mirror args in China). Unquoted so an empty
                                # value expands to nothing.
                                # Layer cache via BuildKit *inline* cache: the
                                # cache metadata is embedded in the image we push,
                                # so --cache-from just points at a previously
                                # pushed tag. No extra registry tag, no new IAM.
                                #
                                # Why inline and not `buildx --cache-to registry`:
                                # registry cache needs the docker-container
                                # driver, which pulls moby/buildkit from
                                # registry-1.docker.io — unreachable from
                                # CodeBuild in the China regions (a build died
                                # there in 15s). BuildKit is built into the
                                # daemon, so DOCKER_BUILDKIT=1 with the default
                                # driver needs nothing from Docker Hub.
                                #
                                # Payoff: the expensive layers (torch/CUDA
                                # download, the ~43-minute AutoGluon pip resolve,
                                # the Chronos weight bake) all sit BEFORE
                                # `COPY training_runtime` in the Dockerfile, so a
                                # skill-only edit invalidates just the last few
                                # tiny layers. Uncached builds ran 37~120 min
                                # purely on mirror luck, and one hit the timeout.
                                #
                                # CACHE_TAG: prefer the rolling "stable" tag; a
                                # first-ever build simply gets no cache hit and
                                # proceeds cold (--cache-from on a missing ref is
                                # not an error).
                                "export DOCKER_BUILDKIT=1",
                                "CACHE_TAG=${CACHE_FROM_TAG:-stable}",
                                "echo Using cache source $ECR_REPO_URI:$CACHE_TAG",
                                # Without --target, docker builds the LAST
                                # stage — "serve" (non-root) — so training would
                                # silently run as the unprivileged user. Pin the
                                # training stage when the Dockerfile has one.
                                "TRAIN_TARGET=; if grep -qi 'AS training' $DOCKERFILE; then TRAIN_TARGET='--target training'; fi",
                                "docker build $PIP_BUILD_ARGS $TRAIN_TARGET -f $DOCKERFILE "
                                "--build-arg BUILDKIT_INLINE_CACHE=1 "
                                "--cache-from $ECR_REPO_URI:$CACHE_TAG "
                                "-t $ECR_REPO_URI:$IMAGE_TAG $CONTEXT",
                                # Companion non-root variant for SageMaker
                                # endpoints (security baseline: deployed
                                # containers must not run as root). Only built
                                # when the Dockerfile defines the serve stage —
                                # user-uploaded agent Dockerfiles may not.
                                # Reuses the training build's layer cache, so
                                # this adds seconds, not a second full build.
                                "if grep -qi 'AS serve' $DOCKERFILE; then "
                                "docker build $PIP_BUILD_ARGS --target serve "
                                "--build-arg BUILDKIT_INLINE_CACHE=1 "
                                "--cache-from $ECR_REPO_URI:$CACHE_TAG "
                                "-f $DOCKERFILE -t $ECR_REPO_URI:$IMAGE_TAG-serve $CONTEXT; "
                                "else echo 'No serve stage in Dockerfile, skipping serve variant'; fi",
                            ],
                        },
                        "post_build": {
                            "commands": [
                                "echo Pushing image to ECR...",
                                "docker push $ECR_REPO_URI:$IMAGE_TAG",
                                "if docker image inspect $ECR_REPO_URI:$IMAGE_TAG-serve >/dev/null 2>&1; then "
                                "docker push $ECR_REPO_URI:$IMAGE_TAG-serve; fi",
                                "echo Done: $ECR_REPO_URI:$IMAGE_TAG",
                            ],
                        },
                    },
                }
            ),
        )
        # Allow the build project to pull/push images and read the source bucket.
        self.repo.grant_pull_push(self.image_build_project)
        self.image_build_source_bucket.grant_read(self.image_build_project)

        # Deploy the agent code baseline (the training_runtime tree) into the
        # build-source bucket. Image orchestration downloads this baseline,
        # overlays the two customer-edited skills, repackages it, and starts the
        # build — so the customer never uploads a zip and the agent code stays
        # program-managed (defined here in infrastructure, not hand-uploaded).
        s3_deployment.BucketDeployment(
            self,
            "AgentBaselineDeployment",
            sources=[
                s3_deployment.Source.asset(
                    str(training_runtime_source),
                    # Exclude compiled bytecode: stale .pyc files would be
                    # repackaged into the build source and could shadow the
                    # freshly-overlaid .py sources at runtime.
                    exclude=["__pycache__", "**/__pycache__/**", "*.pyc"],
                )
            ],
            destination_bucket=self.image_build_source_bucket,
            destination_key_prefix="agent-baseline/training_runtime",
            prune=False,
            retain_on_delete=False,
        )

        self.token_secret = secretsmanager.Secret(
            self,
            "ApiToken",
            secret_name="auto-training-agent-api-token",
            description="Pre-shared token for REST API auth",
            removal_policy=RemovalPolicy.DESTROY,
            # The token is embedded in the frontend runtime-config.js JSON and
            # sent verbatim in the Authorization header, so it must avoid
            # characters that break JSON/JS string literals or HTTP headers
            # (quotes, backslashes, backticks, semicolons, etc.). Restrict the
            # generated value to URL/JSON-safe alphanumerics.
            generate_secret_string=secretsmanager.SecretStringGenerator(
                password_length=40,
                exclude_punctuation=True,
                exclude_characters="\"'`\\;:/@ ",
                require_each_included_type=False,
            ),
        )
        self.provider_api_secret = secretsmanager.Secret(
            self,
            "RuntimeProviderApiKey",
            secret_name="auto-training-agent-provider-api-key",
            description="Optional OpenAI-compatible provider token for training runtime",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Networking has two modes, selected by the cdk.json context object "vpc":
        #
        #  1. MANAGED (default, no context): CDK creates the VPC with a public
        #     subnet + NAT gateway, as before. Needs ec2:CreateInternetGateway
        #     and ec2:AllocateAddress.
        #
        #  2. IMPORTED (context "vpc.vpcId" set): the customer pre-creates the
        #     VPC and subnets by hand and CDK only looks them up. Required where
        #     an SCP explicitly DENIES ec2:CreateInternetGateway /
        #     ec2:AllocateAddress, so the stack cannot create an IGW, EIP or NAT
        #     gateway at all. In that case the workloads run in ISOLATED subnets
        #     with no route to the internet, and every AWS API they call must be
        #     reached over a VPC endpoint (see the interface endpoints below).
        #
        # In imported mode the subnets carry no CDK subnet-type metadata, so the
        # subnet ids are passed explicitly and referenced via a SubnetSelection
        # built from them (self._workload_subnets) rather than by subnet type.
        vpc_ctx = self.node.try_get_context("vpc") or {}
        imported_vpc_id = str(vpc_ctx.get("vpcId") or "").strip()
        self._vpc_is_imported = bool(imported_vpc_id)

        if self._vpc_is_imported:
            subnet_ids = [
                str(s).strip()
                for s in (vpc_ctx.get("privateSubnetIds") or [])
                if str(s).strip()
            ]
            if not subnet_ids:
                raise ValueError(
                    'cdk.json context "vpc.privateSubnetIds" must list at least '
                    'one subnet id when "vpc.vpcId" is set'
                )
            availability_zones = [
                str(z).strip()
                for z in (vpc_ctx.get("availabilityZones") or [])
                if str(z).strip()
            ]
            if len(availability_zones) != len(subnet_ids):
                raise ValueError(
                    'cdk.json context "vpc.availabilityZones" must have one '
                    'availability zone per entry in "vpc.privateSubnetIds"'
                )
            # from_vpc_attributes avoids a live lookup, so the stack still
            # synthesizes without credentials for the target account.
            # The endpoint security group needs the CIDR, which is not derivable
            # from an imported VPC, so it is supplied explicitly.
            imported_vpc_cidr = str(vpc_ctx.get("cidrBlock") or "").strip()
            if not imported_vpc_cidr:
                raise ValueError(
                    'cdk.json context "vpc.cidrBlock" must be set when '
                    '"vpc.vpcId" is set (e.g. "10.0.0.0/16"); it is required to '
                    "scope the VPC endpoint security group"
                )
            self.vpc = ec2.Vpc.from_vpc_attributes(
                self,
                "Vpc",
                vpc_id=imported_vpc_id,
                vpc_cidr_block=imported_vpc_cidr,
                availability_zones=availability_zones,
                private_subnet_ids=subnet_ids,
                private_subnet_route_table_ids=(
                    [
                        str(r).strip()
                        for r in (vpc_ctx.get("privateSubnetRouteTableIds") or [])
                        if str(r).strip()
                    ]
                    or None
                ),
            )
            # Route table ids are attached per subnet as well, so that a gateway
            # endpoint (which writes routes) can resolve them.
            route_table_ids = [
                str(r).strip()
                for r in (vpc_ctx.get("privateSubnetRouteTableIds") or [])
                if str(r).strip()
            ]
            self._workload_subnets = ec2.SubnetSelection(
                subnets=[
                    ec2.Subnet.from_subnet_attributes(
                        self,
                        f"WorkloadSubnet{index}",
                        subnet_id=subnet_id,
                        availability_zone=availability_zones[index],
                        route_table_id=(
                            route_table_ids[index]
                            if index < len(route_table_ids)
                            else None
                        ),
                    )
                    for index, subnet_id in enumerate(subnet_ids)
                ]
            )
        else:
            self.vpc = ec2.Vpc(
                self,
                "Vpc",
                max_azs=2,
                nat_gateways=1,
                subnet_configuration=[
                    ec2.SubnetConfiguration(
                        name="Public",
                        subnet_type=ec2.SubnetType.PUBLIC,
                        cidr_mask=24,
                    ),
                    ec2.SubnetConfiguration(
                        name="Private",
                        subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                        cidr_mask=24,
                    ),
                ],
            )
            self._workload_subnets = ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            )

        # Gateway endpoints (free) keep S3/DynamoDB traffic off the NAT gateway.
        # They write route-table entries, so in imported mode they can only be
        # created once the customer supplies the route table ids. In managed mode
        # `subnets` is deliberately NOT passed, so CDK keeps attaching them to
        # every route table in the VPC (changing it would rewrite the existing
        # deployment's endpoints).
        #
        # In imported mode, if the customer's route tables ALREADY have S3/
        # DynamoDB gateway endpoints, creating a second one fails the deploy with
        # "route table rtb-... already has a route with destination-prefix-list-
        # id pl-..." (AlreadyExists). In that case simply omit
        # "vpc.privateSubnetRouteTableIds" so CDK does not create them — the
        # pre-existing endpoints keep serving the traffic, so nothing is lost.
        create_gateway_endpoints = not self._vpc_is_imported or bool(
            vpc_ctx.get("privateSubnetRouteTableIds")
        )
        if create_gateway_endpoints:
            gateway_endpoint_kwargs = (
                {"subnets": [self._workload_subnets]} if self._vpc_is_imported else {}
            )
            self.vpc.add_gateway_endpoint(
                "S3Endpoint",
                service=ec2.GatewayVpcEndpointAwsService.S3,
                **gateway_endpoint_kwargs,
            )
            self.vpc.add_gateway_endpoint(
                "DynamoDbEndpoint",
                service=ec2.GatewayVpcEndpointAwsService.DYNAMODB,
                **gateway_endpoint_kwargs,
            )

        # Interface endpoints (PrivateLink). With no NAT gateway the workloads
        # have no internet route, so every AWS API they use must be reachable
        # privately. Enabled automatically in imported mode; can be forced on or
        # off with context "vpc.createInterfaceEndpoints".
        #
        # COST NOTE: each interface endpoint is billed per hour PER AZ plus data
        # processing, so this set is materially more expensive than a NAT
        # gateway. It is the only option when an SCP forbids IGW/EIP.
        create_interface_endpoints = vpc_ctx.get("createInterfaceEndpoints")
        if create_interface_endpoints is None:
            create_interface_endpoints = self._vpc_is_imported
        if create_interface_endpoints:
            self.vpc_endpoint_security_group = ec2.SecurityGroup(
                self,
                "VpcEndpointSecurityGroup",
                vpc=self.vpc,
                description="Allows workloads in the VPC to reach AWS interface endpoints over HTTPS",
                allow_all_outbound=False,
            )
            self.vpc_endpoint_security_group.add_ingress_rule(
                peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
                connection=ec2.Port.tcp(443),
                description="HTTPS from inside the VPC",
            )
            # ECR_DOCKER+ECR pull the training image; ECS_* register the Batch
            # container instances; the rest are called by the Lambdas and the
            # training container itself.
            interface_services = {
                "EcrDocker": ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
                "EcrApi": ec2.InterfaceVpcEndpointAwsService.ECR,
                "SecretsManager": ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
                "CloudWatchLogs": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
                "Sts": ec2.InterfaceVpcEndpointAwsService.STS,
                "Ecs": ec2.InterfaceVpcEndpointAwsService.ECS,
                "EcsAgent": ec2.InterfaceVpcEndpointAwsService.ECS_AGENT,
                "EcsTelemetry": ec2.InterfaceVpcEndpointAwsService.ECS_TELEMETRY,
                "Batch": ec2.InterfaceVpcEndpointAwsService.BATCH,
                "SageMakerApi": ec2.InterfaceVpcEndpointAwsService.SAGEMAKER_API,
                "SageMakerRuntime": ec2.InterfaceVpcEndpointAwsService.SAGEMAKER_RUNTIME,
                "EventBridge": ec2.InterfaceVpcEndpointAwsService.EVENTBRIDGE,
                "Lambda": ec2.InterfaceVpcEndpointAwsService.LAMBDA_,
            }
            for endpoint_id, service in interface_services.items():
                self.vpc.add_interface_endpoint(
                    f"{endpoint_id}Endpoint",
                    service=service,
                    subnets=self._workload_subnets,
                    security_groups=[self.vpc_endpoint_security_group],
                    private_dns_enabled=True,
                )

        # Optional: VPC flow logs (context "enableVpcFlowLogs", default off).
        # Captures ALL traffic (accepted + rejected) for the whole VPC and
        # delivers to the shared access-logs bucket under vpcflowlogs/, which
        # already has an expiration lifecycle rule — so log retention and
        # automatic deletion come for free (s3Lifecycle.accessLogsExpireDays).
        enable_vpc_flow_logs = bool(
            self.node.try_get_context("enableVpcFlowLogs") or False
        )
        if enable_vpc_flow_logs:
            ec2.FlowLog(
                self,
                "VpcFlowLog",
                resource_type=ec2.FlowLogResourceType.from_vpc(self.vpc),
                destination=ec2.FlowLogDestination.to_s3(
                    self.access_logs_bucket, "vpcflowlogs/"
                ),
                traffic_type=ec2.FlowLogTrafficType.ALL,
            )

        # Minimum log level for all Lambdas (context "lambdaLogLevel", default
        # INFO). Set to WARN in production so INFO/DEBUG events are suppressed;
        # WARN/ERROR diagnostics keep flowing. Enforced in shared/logging_utils.
        lambda_log_level = str(
            self.node.try_get_context("lambdaLogLevel") or "INFO"
        ).upper()
        lambda_defaults = {
            "runtime": lambda_.Runtime.PYTHON_3_14,
            "code": lambda_.Code.from_asset(str(lambda_source)),
            "vpc": self.vpc,
            # Managed mode: private-with-egress subnets. Imported mode: the
            # customer's isolated subnets (no internet route).
            "vpc_subnets": self._workload_subnets,
            # X-Ray tracing (toggle via cdk.json context "enableXray").
            "tracing": self._lambda_tracing,
            "environment": {"LOG_LEVEL": lambda_log_level},
        }

        # Optional: on-failure destination for asynchronous invocations
        # (context "enableAsyncFailureQueue", default off). Three functions are
        # invoked asynchronously — oncomplete (EventBridge: Batch state change),
        # image_oncomplete (EventBridge: CodeBuild completion) and deployment
        # (lambda invoke with InvocationType=Event). After Lambda's built-in
        # async retries are exhausted the event is otherwise DROPPED silently,
        # leaving task/image/model state stuck. With this on, the full failed
        # event lands in an encrypted SQS queue (14-day retention) for alerting
        # and manual redrive.
        enable_async_failure_queue = bool(
            self.node.try_get_context("enableAsyncFailureQueue") or False
        )
        async_failure_kwargs: dict[str, object] = {}
        if enable_async_failure_queue:
            self.async_failure_queue = sqs.Queue(
                self,
                "AsyncFailureQueue",
                queue_name="auto-training-agent-async-failures",
                encryption=sqs.QueueEncryption.SQS_MANAGED,
                enforce_ssl=True,
                # Keep failed events the maximum time so on-call has a redrive
                # window even across long weekends.
                retention_period=Duration.days(14),
            )
            async_failure_kwargs = {
                "on_failure": lambda_destinations.SqsDestination(
                    self.async_failure_queue
                ),
                # Default async retries (2) stay in place; the destination only
                # receives events that exhausted them.
            }
            CfnOutput(
                self,
                "AsyncFailureQueueUrl",
                value=self.async_failure_queue.queue_url,
            )
        else:
            self.async_failure_queue = None

        self.authorizer_fn = lambda_.Function(
            self,
            "AuthorizerFn",
            function_name="auto-training-agent-authorizer",
            handler="authorizer.handler.handler",
            timeout=Duration.seconds(10),
            **lambda_defaults,
        )
        self.api_fn = lambda_.Function(
            self,
            "ApiFn",
            function_name="auto-training-agent-api",
            handler="api.handler.handler",
            timeout=Duration.seconds(30),
            **lambda_defaults,
        )
        self.oncomplete_fn = lambda_.Function(
            self,
            "OnCompleteFn",
            function_name="auto-training-agent-oncomplete",
            handler="oncomplete.handler.handler",
            timeout=Duration.seconds(30),
            **lambda_defaults,
            **async_failure_kwargs,
        )
        self.deployment_fn = lambda_.Function(
            self,
            "DeploymentFn",
            function_name="auto-training-agent-deployment",
            handler="deployment.handler.handler",
            # 60s was not enough and failed in a way that could not self-heal:
            # the handler creates the SageMaker model + endpoint config + endpoint
            # from inside the VPC, and every observed invocation was SIGKILLed at
            # exactly 60000 ms before it even created the model. A timeout kill
            # does not run the except block at deployment/handler.py:128, so the
            # record stayed DEPLOYING with execution_claimed_at set — after which
            # claim_deployment_execution() drops every re-invoke as "already
            # claimed" and POST /tasks/deploy rejects the task for not being
            # SUCCEEDED. Recovery required deleting the model registry row by hand.
            timeout=Duration.seconds(60),
            **lambda_defaults,
            **async_failure_kwargs,
        )
        self.data_list_fn = lambda_.Function(
            self,
            "DataListFn",
            function_name="auto-training-agent-data-list",
            handler="data_list.handler.handler",
            timeout=Duration.seconds(30),
            **lambda_defaults,
        )
        self.data_upload_fn = lambda_.Function(
            self,
            "DataUploadFn",
            function_name="auto-training-agent-data-upload",
            handler="data_upload.handler.handler",
            timeout=Duration.seconds(30),
            **lambda_defaults,
        )
        self.image_list_fn = lambda_.Function(
            self,
            "ImageListFn",
            function_name="auto-training-agent-image-list",
            handler="image_list.handler.handler",
            timeout=Duration.seconds(30),
            **lambda_defaults,
        )
        self.image_build_fn = lambda_.Function(
            self,
            "ImageBuildFn",
            function_name="auto-training-agent-image-build-fn",
            handler="image_build.handler.handler",
            # Orchestration downloads the agent baseline and repackages it into a
            # build-source zip in-process, so give it more than the default 30s.
            timeout=Duration.seconds(60),
            memory_size=512,
            **lambda_defaults,
        )
        self.infer_fn = lambda_.Function(
            self,
            "InferFn",
            function_name="auto-training-agent-infer",
            handler="infer.handler.handler",
            timeout=Duration.seconds(30),
            **lambda_defaults,
        )
        self.auth_fn = lambda_.Function(
            self,
            "AuthFn",
            function_name="auto-training-agent-auth",
            handler="auth_users.handler.handler",
            timeout=Duration.seconds(15),
            **lambda_defaults,
        )
        self.image_oncomplete_fn = lambda_.Function(
            self,
            "ImageOnCompleteFn",
            function_name="auto-training-agent-image-oncomplete",
            handler="image_oncomplete.handler.handler",
            timeout=Duration.seconds(30),
            **lambda_defaults,
            **async_failure_kwargs,
        )

        # Managed mode places the GPU instances in the PUBLIC subnet: pulling the
        # multi-GB training image straight over the internet gateway avoids NAT
        # data-processing charges, and the instances are protected by a security
        # group with no inbound rules.
        # Imported mode has no public subnet (the SCP forbids IGW/EIP), so the
        # instances run in the customer's isolated subnets and pull the image
        # through the ECR/S3 VPC endpoints instead.
        self.compute_env = batch.ManagedEc2EcsComputeEnvironment(
            self,
            "GpuComputeEnv",
            compute_environment_name="auto-training-agent-gpu-ce",
            vpc=self.vpc,
            instance_types=[ec2.InstanceType("g5.xlarge")],
            maxv_cpus=16,
            minv_cpus=0,
            use_optimal_instance_classes=False,
            vpc_subnets=(
                self._workload_subnets
                if self._vpc_is_imported
                else ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC)
            ),
        )

        self.job_queue = batch.JobQueue(
            self,
            "JobQueue",
            job_queue_name="auto-training-agent-job-queue",
            compute_environments=[
                batch.OrderedComputeEnvironment(
                    compute_environment=self.compute_env,
                    order=1,
                )
            ],
        )

        self.job_role = iam.Role(
            self,
            "JobRole",
            role_name="auto-training-agent-job-role",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        self.bucket.grant_read_write(self.job_role)
        self.job_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=[
                    # Bedrock foundation-model ARNs carry an empty account segment.
                    # region "*": cross-region inference profiles (us./global.)
                    # route to foundation models outside the stack's region.
                    self.format_arn(
                        service="bedrock",
                        region="*",
                        account="",
                        resource="foundation-model",
                        resource_name="*",
                        arn_format=ArnFormat.SLASH_RESOURCE_NAME,
                    ),
                    self.format_arn(
                        service="bedrock",
                        region=self.region,
                        resource="inference-profile",
                        resource_name="*",
                        arn_format=ArnFormat.SLASH_RESOURCE_NAME,
                    ),
                ],
            )
        )
        self.provider_api_secret.grant_read(self.job_role)
        self.sagemaker_execution_role = iam.Role(
            self,
            "SageMakerExecutionRole",
            role_name="auto-training-agent-sagemaker-role",
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
        )
        self.bucket.grant_read(self.sagemaker_execution_role)
        self.repo.grant_pull(self.sagemaker_execution_role)

        # LLM endpoint for the training agent (cdk.json context "llm"). Defaults
        # to a public endpoint for quick trials; an
        # isolated-subnet deployment MUST override baseUrl with the customer's
        # in-VPC address reachable without an internet route.
        # Task-type registry (cdk.json context "taskTypes"): the single source
        # of truth for every supported task type — id (task_type value), label
        # (frontend dropdown), skill (domain skill baked into the image), and
        # the ground-truth / compare-script file names used for independent
        # verification. Serialized to JSON and injected as TASK_TYPES_JSON into
        # the api + infer Lambdas (see shared/task_types.py). Adding a task type
        # is a cdk.json edit plus a skill file and eval package — no code edits.
        # When unset the Lambdas fall back to the built-in timeseries-forecast type.
        task_types_ctx = self.node.try_get_context("taskTypes")
        task_types_json = (
            json.dumps(task_types_ctx, ensure_ascii=False) if task_types_ctx else ""
        )
        # Default domain skill baked into the container: the first configured
        # task type's skill, or the default timeseries skill when unconfigured.
        default_domain_skill = "timeseries-forecast-train.md"
        if isinstance(task_types_ctx, list) and task_types_ctx:
            first_skill = str(
                (task_types_ctx[0] or {}).get("skill") or ""
            ).strip()
            if first_skill:
                default_domain_skill = first_skill

        # Which LLM the training agent talks to, from cdk.json context "llm".
        #
        # The agent speaks the OpenAI Chat Completions API, so baseUrl can point at
        # any OpenAI-compatible endpoint: a managed service, a self-hosted model
        # server, or a translating proxy you run yourself. Set MODEL_PROVIDER to
        # "bedrock" instead if you want the runtime to call Bedrock directly.
        #
        # An isolated-subnet deployment MUST override baseUrl with an address that
        # is reachable without an internet route (the check below enforces this).
        llm_ctx = self.node.try_get_context("llm") or {}
        if isinstance(llm_ctx, str):
            # `cdk deploy -c llm='{...}'` passes the value as a JSON string.
            llm_ctx = json.loads(llm_ctx) if llm_ctx.strip() else {}
        llm_base_url = str(
            llm_ctx.get("baseUrl") or "https://api.openai.com/v1"
        ).strip()
        llm_model_id = str(llm_ctx.get("modelId") or "gpt-4o-mini").strip()
        # "openai" (default): OpenAI-compatible endpoint, API key read from the
        # provider secret. "bedrock": call Amazon Bedrock directly with the job
        # role (modelId is then a Bedrock model / inference-profile ID).
        llm_provider = str(llm_ctx.get("provider") or "openai").strip().lower()
        if llm_provider not in ("openai", "bedrock"):
            raise ValueError('cdk.json context "llm.provider" must be "openai" or "bedrock"')
        if (
            self._vpc_is_imported
            and llm_provider == "openai"
            and not llm_ctx.get("baseUrl")
        ):
            # Fail fast at synth time: an imported (isolated-subnet) VPC has no
            # internet route, so the public default endpoint is unreachable. The
            # deployment would build fine and then every training run would hang
            # on the first model call.
            raise ValueError(
                "An imported (isolated-subnet) VPC has no internet route, so the "
                "default public LLM endpoint is unreachable. Set cdk.json context "
                '"llm": {"baseUrl": "http://<reachable-endpoint>/v1", "modelId": "..."} '
                "to an endpoint reachable from the VPC, or set MODEL_PROVIDER=bedrock "
                "and add a bedrock-runtime VPC endpoint."
            )

        self.container_def = batch.EcsEc2ContainerDefinition(
            self,
            "AgentContainer",
            image=ecs.ContainerImage.from_ecr_repository(self.repo, "stable"),
            # Must fit inside the compute environment's instance type or Batch
            # parks the job in RUNNABLE forever (no error, just silent queuing).
            # g5.xlarge is 4 vCPU / 16 GiB, so ask for 4 vCPU and leave ~2 GiB
            # to the ECS agent / OS rather than requesting the full 16.
            # Measured headroom for the tabular-regression sample: the whole three-step pipeline
            # peaks at 417 MB (build_pipeline reading 9878 JSONs), and it never
            # touches the GPU — the model is XGBoost + ExtraTrees + GBM_huber.
            cpu=4,
            memory=Size.gibibytes(14),
            gpu=1,
            job_role=self.job_role,
            environment={
                "WORKSPACE_ROOT": "/work/runtime",
                "MODEL_PROVIDER": llm_provider,
                "BEDROCK_REGION": self.region,
                # OpenAI-compatible LLM endpoint. Override both of these via the
                # cdk.json context object "llm" to point at the customer's
                # a self-hosted OpenAI-compatible endpoint, e.g.
                #   "llm": {
                #     "baseUrl": "http://llm.intra.example.com:8000/v1",
                #     "modelId": "local-model"
                #   }
                # This is REQUIRED in an isolated-subnet deployment: there is no
                # internet route, so a public provider URL is unreachable and the
                # agent cannot call the model at all.
                "OPENAI_BASE_URL": llm_base_url,
                "OPENAI_API_KEY_SECRET_ARN": self.provider_api_secret.secret_arn,
                "GENERAL_SKILL_NAME": "general-agent-spec.md",
                "DOMAIN_SKILL_NAME": default_domain_skill,
                "LLM_MODEL_ID": llm_model_id,
                # Output budget + sliding window are tuned against the model's
                # 163840-token context limit (input + output must fit under it).
                # 16384 output was too aggressive: during self-repair the agent
                # pastes the failing script + data + history into the prompt,
                # which pushed input to ~147k tokens; 147457 + 16384 = 163841
                # exceeded the limit by one token, so EVERY model call 400'd
                # ("maximum context length is 163840 tokens") and the agent
                # could neither fix its script nor advance to the next attempt.
                # 8192 was still not enough for the tabular-regression sample: three consecutive
                # runs died with "prompt contains at least 155649 input tokens"
                # (155649 + 8192 = 163841, again over by one) and produced no
                # artifacts at all. Dropping the output budget to 4096 frees that
                # much input headroom and was the single change that turned a
                # 0/3 failure streak into a successful run — overflow events still
                # occurred but the history-compaction retry then converged.
                # 4096 remains enough to emit the training script tail (the
                # to_csv/assert that writes the prediction CSVs); raise it only
                # together with a measured check that inputs stay under ~155k.
                "MODEL_MAX_TOKENS": "4096",
                "CONVERSATION_WINDOW_MESSAGES": "12",
                # HuggingFace endpoint for downloading the Chronos weights, set
                # per region: China cannot reach huggingface.co directly and must
                # use the hf-mirror.com mirror, while commercial regions reach
                # huggingface.co directly (the mirror is slow/unreachable from
                # there and previously caused Chronos to fail to download,
                # leaving an empty, unusable predictor). Setting this at the
                # container level makes it authoritative rather than relying on
                # the agent to export it from the skill instructions.
                "HF_ENDPOINT": (
                    "https://hf-mirror.com" if is_china else "https://huggingface.co"
                ),
                # Isolated subnets cannot reach any HuggingFace mirror. The
                # Chronos weights are pre-baked into the image at build time, so
                # force offline resolution from the in-image cache instead of
                # letting the loader stall on an unreachable endpoint.
                **(
                    {
                        "HF_HUB_OFFLINE": "1",
                        "TRANSFORMERS_OFFLINE": "1",
                    }
                    if self._vpc_is_imported
                    else {}
                ),
            },
        )
        self.job_def = batch.EcsJobDefinition(
            self,
            "TrainingJobDef",
            job_definition_name="auto-training-agent-job-def",
            container=self.container_def,
        )

        # Auth mode is region-dependent:
        #  - China (is_china): all methods use AWS_IAM authorization and the
        #    frontend signs each request with SigV4. The self-hosted frontend
        #    calls execute-api CROSS-ORIGIN, so CORS preflight is required; the
        #    OPTIONS methods CDK generates are MOCK integrations with
        #    authorization NONE, so they pass anonymously while the business
        #    methods still require SigV4. (The browser uses query-string SigV4,
        #    so real requests carry no x-amz-* headers.)
        #  - Commercial regions (e.g. us-west-2): a Lambda TokenAuthorizer with a
        #    pre-shared token, plus the same CORS preflight so the browser can
        #    call the API cross-origin with a plain Authorization header.
        # Both regions share one CORS config; only the per-method authorization
        # differs.
        cors_preflight = apigw.CorsOptions(
            allow_origins=apigw.Cors.ALL_ORIGINS,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )
        method_auth_kwargs: dict[str, object] = {}
        if is_china:
            method_auth_kwargs = {"authorization_type": apigw.AuthorizationType.IAM}
        else:
            authorizer = apigw.TokenAuthorizer(
                self,
                "ApiAuthorizer",
                handler=self.authorizer_fn,
                identity_source=apigw.IdentitySource.header("Authorization"),
                results_cache_ttl=Duration.seconds(0),
            )
            method_auth_kwargs = {"authorizer": authorizer}

        # API access logs: $context metadata ONLY (no Authorization header, no
        # request/response bodies), so no tokens or payload data can leak into
        # the log. The log group has an explicit retention (auto-deletion) —
        # CloudWatch's default is never-expire, which fails data-retention
        # expectations. Days configurable via context "apiLogRetentionDays".
        api_log_retention_days = int(
            self.node.try_get_context("apiLogRetentionDays") or 90
        )
        # CloudWatch only accepts specific retention values; snap the configured
        # days UP to the nearest supported tier (so retention is never shorter
        # than asked for).
        _retention_tiers = {
            1: aws_logs.RetentionDays.ONE_DAY,
            3: aws_logs.RetentionDays.THREE_DAYS,
            5: aws_logs.RetentionDays.FIVE_DAYS,
            7: aws_logs.RetentionDays.ONE_WEEK,
            14: aws_logs.RetentionDays.TWO_WEEKS,
            30: aws_logs.RetentionDays.ONE_MONTH,
            60: aws_logs.RetentionDays.TWO_MONTHS,
            90: aws_logs.RetentionDays.THREE_MONTHS,
            120: aws_logs.RetentionDays.FOUR_MONTHS,
            180: aws_logs.RetentionDays.SIX_MONTHS,
            365: aws_logs.RetentionDays.ONE_YEAR,
            731: aws_logs.RetentionDays.TWO_YEARS,
            1827: aws_logs.RetentionDays.FIVE_YEARS,
            3653: aws_logs.RetentionDays.TEN_YEARS,
        }
        api_log_retention = _retention_tiers[
            min(
                (d for d in _retention_tiers if d >= api_log_retention_days),
                default=3653,
            )
        ]
        self.api_access_log_group = aws_logs.LogGroup(
            self,
            "ApiAccessLogGroup",
            log_group_name="/aws/apigateway/auto-training-agent-access",
            retention=api_log_retention,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.api = apigw.RestApi(
            self,
            "RestApi",
            rest_api_name="auto-training-agent",
            # China regions (and several others) do not support EDGE-optimized
            # API Gateway endpoints; REGIONAL is required there and works in all
            # commercial regions too.
            endpoint_configuration=apigw.EndpointConfiguration(
                types=[apigw.EndpointType.REGIONAL],
            ),
            # Execution logs (CloudWatch) at ERROR level; data_trace stays OFF so
            # request bodies (which may carry tokens) are never written to logs.
            cloud_watch_role=True,
            deploy_options=apigw.StageOptions(
                stage_name="poc",
                # X-Ray tracing for the API stage (toggle via context "enableXray").
                tracing_enabled=self._enable_xray,
                access_log_destination=apigw.LogGroupLogDestination(
                    self.api_access_log_group
                ),
                access_log_format=apigw.AccessLogFormat.json_with_standard_fields(
                    caller=True,
                    http_method=True,
                    ip=True,
                    protocol=True,
                    request_time=True,
                    resource_path=True,
                    response_length=True,
                    status=True,
                    user=True,
                ),
                logging_level=apigw.MethodLoggingLevel.ERROR,
                data_trace_enabled=False,
            ),
            default_cors_preflight_options=cors_preflight,
        )
        # First-pass request validation at the API Gateway layer: every method
        # validates declared request parameters; POST bodies are additionally
        # required to be non-empty via a minimal model on the JSON content type.
        # NOTE: the China frontend intentionally posts JSON as text/plain to
        # avoid CORS preflight on business calls, and API Gateway only applies
        # body models when the request Content-Type matches — so body-model
        # validation is best-effort by design and the Lambdas keep doing full
        # application-layer validation (lambda/api/validators.py etc.).
        request_validator = self.api.add_request_validator(
            "RequestValidator",
            request_validator_name="validate-body-and-parameters",
            validate_request_body=True,
            validate_request_parameters=True,
        )
        json_object_model = self.api.add_model(
            "JsonObjectModel",
            model_name="JsonObject",
            content_type="application/json",
            schema=apigw.JsonSchema(
                schema=apigw.JsonSchemaVersion.DRAFT4,
                title="json-object",
                type=apigw.JsonSchemaType.OBJECT,
            ),
        )
        body_validation_kwargs = {
            "request_validator": request_validator,
            "request_models": {"application/json": json_object_model},
        }
        api_integration = apigw.LambdaIntegration(self.api_fn)
        tasks = self.api.root.add_resource("tasks")
        tasks.add_method(
            "POST", api_integration, **method_auth_kwargs, **body_validation_kwargs
        )
        tasks.add_method("GET", api_integration, **method_auth_kwargs)
        tasks_cancel = tasks.add_resource("cancel")
        tasks_cancel.add_method(
            "POST", api_integration, **method_auth_kwargs, **body_validation_kwargs
        )
        tasks_deploy = tasks.add_resource("deploy")
        tasks_deploy.add_method(
            "POST", api_integration, **method_auth_kwargs, **body_validation_kwargs
        )
        tasks_evaluation = tasks.add_resource("evaluation")
        tasks_evaluation.add_method("GET", api_integration, **method_auth_kwargs)
        logs = self.api.root.add_resource("logs")
        logs.add_method("GET", api_integration, **method_auth_kwargs)
        messages = self.api.root.add_resource("messages")
        messages.add_method("GET", api_integration, **method_auth_kwargs)
        messages.add_method(
            "POST", api_integration, **method_auth_kwargs, **body_validation_kwargs
        )
        models = self.api.root.add_resource("models")
        models.add_method("GET", api_integration, **method_auth_kwargs)
        task_types_resource = self.api.root.add_resource("task-types")
        task_types_resource.add_method("GET", api_integration, **method_auth_kwargs)
        data_files = self.api.root.add_resource("data-files")
        data_files.add_method(
            "GET",
            apigw.LambdaIntegration(self.data_list_fn),
            **method_auth_kwargs,
        )
        data_files.add_method(
            "POST",
            apigw.LambdaIntegration(self.data_upload_fn),
            **method_auth_kwargs,
            **body_validation_kwargs,
        )
        data_files.add_method(
            "DELETE",
            apigw.LambdaIntegration(self.data_upload_fn),
            **method_auth_kwargs,
        )
        images = self.api.root.add_resource("images")
        images.add_method(
            "GET",
            apigw.LambdaIntegration(self.image_list_fn),
            **method_auth_kwargs,
        )
        images.add_method(
            "POST",
            apigw.LambdaIntegration(self.image_build_fn),
            **method_auth_kwargs,
            **body_validation_kwargs,
        )
        infer_tasks = self.api.root.add_resource("infer-tasks")
        infer_tasks.add_method(
            "GET",
            apigw.LambdaIntegration(self.infer_fn),
            **method_auth_kwargs,
        )
        infer_tasks.add_method(
            "POST",
            apigw.LambdaIntegration(self.infer_fn),
            **method_auth_kwargs,
            **body_validation_kwargs,
        )
        login = self.api.root.add_resource("login")
        login.add_method(
            "POST",
            apigw.LambdaIntegration(self.auth_fn),
            **method_auth_kwargs,
            **body_validation_kwargs,
        )

        # Optional: AWS WAF on the public API endpoint (context "enableApiWaf",
        # default off — a WebACL carries a fixed monthly fee). Managed rule
        # groups (common threats + known bad inputs) plus a per-IP rate limit
        # (context "apiWafRateLimit", default 2000 requests / 5 minutes).
        enable_api_waf = bool(self.node.try_get_context("enableApiWaf") or False)
        if enable_api_waf:
            waf_rate_limit = int(
                self.node.try_get_context("apiWafRateLimit") or 2000
            )
            api_web_acl = wafv2.CfnWebACL(
                self,
                "ApiWebAcl",
                name="auto-training-agent-api",
                scope="REGIONAL",
                default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
                visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                    cloud_watch_metrics_enabled=True,
                    metric_name="auto-training-agent-api",
                    sampled_requests_enabled=True,
                ),
                rules=[
                    wafv2.CfnWebACL.RuleProperty(
                        name="AWSManagedRulesCommonRuleSet",
                        priority=0,
                        override_action=wafv2.CfnWebACL.OverrideActionProperty(
                            none={}
                        ),
                        statement=wafv2.CfnWebACL.StatementProperty(
                            managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                                vendor_name="AWS",
                                name="AWSManagedRulesCommonRuleSet",
                                # The API legitimately accepts JSON posted as
                                # text/plain (China CORS workaround) with large
                                # skill texts; SizeRestrictions_BODY (8KB) would
                                # false-positive on image-build payloads.
                                rule_action_overrides=[
                                    wafv2.CfnWebACL.RuleActionOverrideProperty(
                                        name="SizeRestrictions_BODY",
                                        action_to_use=wafv2.CfnWebACL.RuleActionProperty(
                                            count={}
                                        ),
                                    )
                                ],
                            )
                        ),
                        visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                            cloud_watch_metrics_enabled=True,
                            metric_name="common-rule-set",
                            sampled_requests_enabled=True,
                        ),
                    ),
                    wafv2.CfnWebACL.RuleProperty(
                        name="AWSManagedRulesKnownBadInputsRuleSet",
                        priority=1,
                        override_action=wafv2.CfnWebACL.OverrideActionProperty(
                            none={}
                        ),
                        statement=wafv2.CfnWebACL.StatementProperty(
                            managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                                vendor_name="AWS",
                                name="AWSManagedRulesKnownBadInputsRuleSet",
                            )
                        ),
                        visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                            cloud_watch_metrics_enabled=True,
                            metric_name="known-bad-inputs",
                            sampled_requests_enabled=True,
                        ),
                    ),
                    wafv2.CfnWebACL.RuleProperty(
                        name="RateLimitPerIp",
                        priority=2,
                        action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                        statement=wafv2.CfnWebACL.StatementProperty(
                            rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                                limit=waf_rate_limit,
                                aggregate_key_type="IP",
                            )
                        ),
                        visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                            cloud_watch_metrics_enabled=True,
                            metric_name="rate-limit-per-ip",
                            sampled_requests_enabled=True,
                        ),
                    ),
                ],
            )
            wafv2.CfnWebACLAssociation(
                self,
                "ApiWebAclAssociation",
                resource_arn=self.api.deployment_stage.stage_arn,
                web_acl_arn=api_web_acl.attr_arn,
            )

        # NOTE: China no longer routes the API through CloudFront. The frontend
        # is self-hosted on an internal server and calls execute-api
        # cross-origin, signing each request with SigV4 (query-string presign);
        # the CORS preflight configured above handles the browser's OPTIONS.

        self.complete_rule = events.Rule(
            self,
            "BatchCompleteRule",
            event_pattern=events.EventPattern(
                source=["aws.batch"],
                detail_type=["Batch Job State Change"],
                detail={
                    "status": [
                        "RUNNABLE",
                        "STARTING",
                        "RUNNING",
                        "SUCCEEDED",
                        "FAILED",
                    ]
                },
            ),
        )
        self.complete_rule.add_target(targets.LambdaFunction(self.oncomplete_fn))

        self.authorizer_fn.add_environment(
            "TOKEN_SECRET_ARN",
            self.token_secret.secret_arn,
        )
        self.token_secret.grant_read(self.authorizer_fn)

        self.api_fn.add_environment("TABLE_NAME", self.table.table_name)
        self.api_fn.add_environment("BUCKET_NAME", self.bucket.bucket_name)
        if task_types_json:
            self.api_fn.add_environment("TASK_TYPES_JSON", task_types_json)
        self.api_fn.add_environment("JOB_QUEUE_ARN", self.job_queue.job_queue_arn)
        self.api_fn.add_environment("JOB_DEF_ARN", self.job_def.job_definition_arn)
        self.api_fn.add_environment("IMAGE_REPO_URI", self.repo.repository_uri)
        self.api_fn.add_environment("MAX_CONCURRENT", "5")
        self.api_fn.add_environment("TASK_BATCH_JOB_INDEX", "batch-job-id-index")
        self.api_fn.add_environment(
            "MODEL_REGISTRY_TABLE_NAME",
            self.model_registry_table.table_name,
        )
        self.api_fn.add_environment(
            "MODEL_REGISTRY_TARGET_ENDPOINT_INDEX",
            "target-endpoint-index",
        )
        self.api_fn.add_environment(
            "MESSAGE_TABLE_NAME", self.message_table.table_name
        )
        # Manual deployment (POST /tasks/deploy) invokes the deployment lambda,
        # reusing the same dispatch path as the automatic on-success flow.
        self.api_fn.add_environment(
            "DEPLOYMENT_FUNCTION_NAME",
            self.deployment_fn.function_name,
        )

        self.oncomplete_fn.add_environment("TABLE_NAME", self.table.table_name)
        self.oncomplete_fn.add_environment(
            "MESSAGE_TABLE_NAME", self.message_table.table_name
        )
        # oncomplete runs the auto-deploy eligibility check, which reads the
        # task type's enforceVerification as a fallback signal (see
        # runtime_outputs.evaluate_deployment_eligibility).
        if task_types_json:
            self.oncomplete_fn.add_environment("TASK_TYPES_JSON", task_types_json)
            # deployment_fn needs it too: _resolve_instance_type() looks up the
            # task type's "instanceType" to pick the endpoint instance. Without
            # this the lookup silently returns None and every deployment falls
            # back to SAGEMAKER_INSTANCE_TYPE (a GPU instance) — which made a
            # byo_deploy task fail with ResourceLimitExceeded on the
            # 'ml.g5.4xlarge for endpoint usage' quota (limit 1) even though
            # cdk.json had configured it as the CPU-only ml.m5.xlarge.
            self.deployment_fn.add_environment("TASK_TYPES_JSON", task_types_json)
        self.oncomplete_fn.add_environment(
            "TASK_BATCH_JOB_INDEX",
            "batch-job-id-index",
        )
        self.oncomplete_fn.add_environment("BUCKET_NAME", self.bucket.bucket_name)
        self.oncomplete_fn.add_environment(
            "MODEL_REGISTRY_TABLE_NAME",
            self.model_registry_table.table_name,
        )
        self.oncomplete_fn.add_environment(
            "MODEL_REGISTRY_TARGET_ENDPOINT_INDEX",
            "target-endpoint-index",
        )
        self.oncomplete_fn.add_environment(
            "DEPLOYMENT_FUNCTION_NAME",
            self.deployment_fn.function_name,
        )

        self.deployment_fn.add_environment("TABLE_NAME", self.table.table_name)
        self.deployment_fn.add_environment(
            "TASK_BATCH_JOB_INDEX",
            "batch-job-id-index",
        )
        self.deployment_fn.add_environment(
            "MODEL_REGISTRY_TABLE_NAME",
            self.model_registry_table.table_name,
        )
        self.deployment_fn.add_environment(
            "MODEL_REGISTRY_TARGET_ENDPOINT_INDEX",
            "target-endpoint-index",
        )
        self.deployment_fn.add_environment("BUCKET_NAME", self.bucket.bucket_name)
        self.deployment_fn.add_environment(
            "MESSAGE_TABLE_NAME", self.message_table.table_name
        )
        self.deployment_fn.add_environment("IMAGE_REPO_URI", self.repo.repository_uri)
        # The adapter probes ECR for the non-root "<tag>-serve" image variant
        # before creating the SageMaker model (falls back to the base tag).
        self.repo.grant(self.deployment_fn, "ecr:DescribeImages")
        self.deployment_fn.add_environment(
            "SAGEMAKER_EXECUTION_ROLE_ARN",
            self.sagemaker_execution_role.role_arn,
        )
        # The Chronos t5-base model requires a GPU at inference time (AutoGluon
        # hard-codes num_gpus=1 for the chronos-t5 family), so the real-time
        # endpoint must run on a GPU instance, matching the g5 training instance.
        self.deployment_fn.add_environment("SAGEMAKER_INSTANCE_TYPE", "ml.g5.xlarge")
        self.deployment_fn.add_environment("SAGEMAKER_INITIAL_INSTANCE_COUNT", "1")

        self.table.grant_read_write_data(self.api_fn)
        self.table.grant_read_write_data(self.oncomplete_fn)
        self.table.grant_read_write_data(self.deployment_fn)
        # Message table: api reads (list) + writes (cancel notifications);
        # oncomplete/deployment write status-change notifications.
        self.message_table.grant_read_write_data(self.api_fn)
        self.message_table.grant_read_write_data(self.oncomplete_fn)
        self.message_table.grant_read_write_data(self.deployment_fn)
        # api_fn writes the model registry when manually accepting a deployment
        # attempt (POST /tasks/deploy), same as the auto path.
        self.model_registry_table.grant_read_write_data(self.api_fn)
        self.model_registry_table.grant_read_write_data(self.oncomplete_fn)
        self.model_registry_table.grant_read_write_data(self.deployment_fn)
        self.bucket.grant_read(self.api_fn)
        self.bucket.grant_read(self.oncomplete_fn)
        # deployment needs write to upload the packaged model.tar.gz for SageMaker.
        self.bucket.grant_read_write(self.deployment_fn)
        self.deployment_fn.grant_invoke(self.oncomplete_fn)
        self.deployment_fn.grant_invoke(self.api_fn)

        # Training-data browser/uploader: list needs read, upload needs to issue
        # presigned PUT URLs (grant_put covers the s3:PutObject the URL signs).
        self.data_list_fn.add_environment("BUCKET_NAME", self.bucket.bucket_name)
        self.data_upload_fn.add_environment("BUCKET_NAME", self.bucket.bucket_name)
        self.bucket.grant_read(self.data_list_fn)
        self.bucket.grant_put(self.data_upload_fn)
        # DELETE /data-files removes a single user-uploaded object; the backend
        # confines deletion to the data/ and inference_data/ input areas.
        self.bucket.grant_delete(self.data_upload_fn)

        # Base-image browser/builder: list reads ECR; build issues a presigned
        # PUT URL for the agent zip and starts the CodeBuild project.
        # Image list now reads the image DynamoDB table (source of truth), not ECR.
        self.image_list_fn.add_environment("IMAGE_TABLE_NAME", self.image_table.table_name)
        self.image_table.grant_read_data(self.image_list_fn)
        self.image_build_fn.add_environment(
            "IMAGE_BUILD_SOURCE_BUCKET",
            self.image_build_source_bucket.bucket_name,
        )
        self.image_build_fn.add_environment(
            "IMAGE_BUILD_PROJECT_NAME",
            self.image_build_project.project_name,
        )
        self.image_build_fn.add_environment("IMAGE_TABLE_NAME", self.image_table.table_name)
        self.image_table.grant_read_write_data(self.image_build_fn)
        # Orchestration reads the agent baseline (get/list) AND writes the
        # repackaged build-source zip, so read+write on the bucket is required.
        self.image_build_source_bucket.grant_read_write(self.image_build_fn)
        self.image_build_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["codebuild:StartBuild"],
                resources=[self.image_build_project.project_arn],
            )
        )
        # CodeBuild completion handler: reconciles image status via build-id-index
        # and enriches size from ECR.
        self.image_oncomplete_fn.add_environment(
            "IMAGE_TABLE_NAME", self.image_table.table_name
        )
        self.image_oncomplete_fn.add_environment(
            "IMAGE_BUILD_ID_INDEX", "build-id-index"
        )
        self.image_oncomplete_fn.add_environment(
            "IMAGE_REPO_NAME", self.repo.repository_name
        )
        self.image_table.grant_read_write_data(self.image_oncomplete_fn)
        self.repo.grant(self.image_oncomplete_fn, "ecr:DescribeImages")
        # EventBridge: CodeBuild state changes for the agent image build project
        # drive image status updates.
        self.image_build_rule = events.Rule(
            self,
            "ImageBuildCompleteRule",
            event_pattern=events.EventPattern(
                source=["aws.codebuild"],
                detail_type=["CodeBuild Build State Change"],
                detail={
                    "project-name": [self.image_build_project.project_name],
                    "build-status": ["SUCCEEDED", "FAILED", "FAULT", "TIMED_OUT", "STOPPED"],
                },
            ),
        )
        self.image_build_rule.add_target(
            targets.LambdaFunction(self.image_oncomplete_fn)
        )

        # Auth/login function: seeds accounts and validates credentials against
        # the users table. The account list (admin + regular users) is
        # configuration, read from the `seedAccounts` context entry and injected
        # as a JSON env var. There is no built-in fallback account: with nothing
        # configured, no account is seeded and login stays closed (fail closed),
        # so a deployment never ships with a publicly known default password.
        #
        # Supply accounts at deploy time rather than committing passwords, e.g.
        #   cdk deploy -c 'seedAccounts=[{"username":"admin","password":"...","role":"admin"}]'
        # Values given with -c arrive as a string, so JSON text is parsed here.
        self.auth_fn.add_environment("USER_TABLE_NAME", self.user_table.table_name)
        seed_accounts = self.node.try_get_context("seedAccounts")
        if isinstance(seed_accounts, str):
            seed_accounts = json.loads(seed_accounts) if seed_accounts.strip() else []
        if seed_accounts:
            self.auth_fn.add_environment(
                "SEED_ACCOUNTS", json.dumps(seed_accounts, ensure_ascii=False)
            )
        else:
            Annotations.of(self).add_warning_v2(
                "auto-training-agent:no-seed-accounts",
                'No "seedAccounts" configured: no login account will be created '
                "and nobody can sign in to the web console. Pass accounts with "
                "-c seedAccounts='[...]' at deploy time.",
            )
        self.user_table.grant_read_write_data(self.auth_fn)

        # Inference function: reads/writes the data bucket (input payload +
        # async output/failure objects), reads/writes its own DynamoDB table,
        # and invokes SageMaker endpoints asynchronously.
        self.infer_fn.add_environment("INFER_TABLE_NAME", self.infer_table.table_name)
        self.infer_fn.add_environment("BUCKET_NAME", self.bucket.bucket_name)
        if task_types_json:
            self.infer_fn.add_environment("TASK_TYPES_JSON", task_types_json)
        self.infer_table.grant_read_write_data(self.infer_fn)
        self.bucket.grant_read_write(self.infer_fn)
        self.infer_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["sagemaker:InvokeEndpointAsync", "sagemaker:InvokeEndpoint"],
                resources=[
                    self.format_arn(
                        service="sagemaker",
                        resource="endpoint",
                        resource_name="*",
                    ),
                ],
            )
        )

        for fn in (self.api_fn, self.oncomplete_fn):
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=[
                        "batch:DescribeJobs",
                        "batch:ListJobs",
                        "batch:DescribeJobDefinitions",
                    ],
                    resources=["*"],
                )
            )
        # The API function can cancel (terminate) a Batch job for a task.
        self.api_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["batch:TerminateJob"],
                resources=["*"],
            )
        )
        self.deployment_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["sagemaker:CreateModel", "sagemaker:DescribeModel"],
                resources=[
                    self.format_arn(service="sagemaker", resource="model", resource_name="*"),
                ],
            )
        )
        self.deployment_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "sagemaker:CreateEndpointConfig",
                    "sagemaker:DescribeEndpointConfig",
                ],
                resources=[
                    self.format_arn(
                        service="sagemaker",
                        resource="endpoint-config",
                        resource_name="*",
                    ),
                ],
            )
        )
        self.deployment_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "sagemaker:CreateEndpoint",
                    "sagemaker:UpdateEndpoint",
                    "sagemaker:DescribeEndpoint",
                ],
                # CreateEndpoint/UpdateEndpoint authorize against BOTH the
                # endpoint and the endpoint-config it references.
                resources=[
                    self.format_arn(service="sagemaker", resource="endpoint", resource_name="*"),
                    self.format_arn(
                        service="sagemaker",
                        resource="endpoint-config",
                        resource_name="*",
                    ),
                ],
            )
        )
        self.deployment_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[self.sagemaker_execution_role.role_arn],
            )
        )

        # China only: frontend SigV4 signer identity. The browser embeds these
        # static credentials (in runtime-config.js) to sign execute-api requests
        # with SigV4 — this is what lets calls pass the China ICP edge rule that
        # blocks Lambda-authorizer / unauthenticated public access. Least-priv:
        # can ONLY invoke this API. Commercial regions use a token authorizer
        # instead, so this signer is not created there.
        if is_china:
            self.frontend_api_user = iam.User(
                self,
                "FrontendApiInvokeUser",
                user_name="auto-training-agent-frontend-invoke",
            )
            self.frontend_api_user.add_to_policy(
                iam.PolicyStatement(
                    actions=["execute-api:Invoke"],
                    resources=[
                        self.format_arn(
                            service="execute-api",
                            resource=self.api.rest_api_id,
                            resource_name="poc/*",
                        ),
                    ],
                )
            )
            self.frontend_api_access_key = iam.AccessKey(
                self,
                "FrontendApiInvokeAccessKey",
                user=self.frontend_api_user,
            )
            CfnOutput(
                self,
                "FrontendApiAccessKeyId",
                value=self.frontend_api_access_key.access_key_id,
            )
            CfnOutput(
                self,
                "FrontendApiSecretAccessKey",
                value=self.frontend_api_access_key.secret_access_key.unsafe_unwrap(),
            )
            # The self-hosted frontend signs SigV4 against the execute-api origin
            # host (bare host, no scheme/path) and region. Expose both so the
            # operator can populate runtime-config.js (signHost / signRegion).
            CfnOutput(
                self,
                "FrontendSignHost",
                value=(
                    f"{self.api.rest_api_id}.execute-api.{self.region}.amazonaws.com.cn"
                ),
            )
            CfnOutput(self, "FrontendSignRegion", value=self.region)
        else:
            # Commercial regions: expose the pre-shared token so the operator can
            # drop it into the frontend runtime-config.js.
            CfnOutput(
                self,
                "ApiTokenSecretName",
                value=self.token_secret.secret_name,
            )

        # Frontend hosting outputs only exist in commercial regions, where CDK
        # hosts the frontend on S3 + CloudFront. China self-hosts the frontend.
        if not is_china:
            CfnOutput(self, "FrontendBucketName", value=self.frontend_bucket.bucket_name)
            CfnOutput(self, "FrontendWebsiteUrl", value=self.frontend_url)
            CfnOutput(
                self,
                "FrontendDistributionId",
                value=self.frontend_distribution.distribution_id,
            )
            CfnOutput(
                self,
                "FrontendRuntimeConfigPath",
                value=f"s3://{self.frontend_bucket.bucket_name}/runtime-config.js",
            )
        CfnOutput(self, "ApiBaseUrl", value=self.api.url)
        CfnOutput(self, "RuntimeProviderSecretArn", value=self.provider_api_secret.secret_arn)
        self.api_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["batch:SubmitJob"],
                resources=[
                    self.job_queue.job_queue_arn,
                    # Covers the base job definition and any per-image-tag variants
                    # the adapter registers at submission time for A/B comparisons.
                    self.format_arn(
                        service="batch",
                        resource="job-definition",
                        resource_name="*",
                    ),
                ],
            )
        )
        self.api_fn.add_to_role_policy(
            iam.PolicyStatement(
                # RegisterJobDefinition does not support resource-level scoping,
                # so it is constrained by action rather than resource.
                actions=["batch:RegisterJobDefinition"],
                resources=["*"],
            )
        )
        # When a non-stable image tag is submitted, the API adapter registers a
        # per-tag Batch job definition cloned from the base one. RegisterJobDefinition
        # requires iam:PassRole for BOTH roles the cloned containerProperties carry:
        # the job role AND the auto-created ECS task execution role. Passing only
        # the job role made non-stable submissions fail with AccessDeniedException
        # (iam:PassRole on AgentContainerExecutionRole). Stable submissions reuse
        # the base definition and don't register, so they were unaffected.
        pass_role_arns = [self.job_role.role_arn]
        if self.container_def.execution_role is not None:
            pass_role_arns.append(self.container_def.execution_role.role_arn)
        self.api_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=pass_role_arns,
            )
        )

        # Recurring/scheduled training tasks. A dedicated EventBridge Scheduler
        # group holds the per-task schedules; a role lets Scheduler invoke the
        # API Lambda; the API Lambda can create/delete schedules and pass that
        # role. Each schedule fires api_fn with a {scheduled_trigger:true}
        # payload at its start time and then every rate() interval.
        self.schedule_group = scheduler.CfnScheduleGroup(
            self,
            "TrainingScheduleGroup",
            name="auto-training-agent-schedules",
        )
        # Build the API function ARN from its LITERAL name to avoid a dependency
        # cycle. Using self.api_fn.function_name (a token that Refs the function)
        # or self.api_fn.function_arn inside the function's own env var makes
        # api_fn depend on itself. The name is a fixed string, so hardcode it.
        api_fn_name = "auto-training-agent-api"
        api_fn_arn = self.format_arn(
            service="lambda",
            resource="function",
            resource_name=api_fn_name,
            arn_format=ArnFormat.COLON_RESOURCE_NAME,
        )
        self.scheduler_invoke_role = iam.Role(
            self,
            "SchedulerInvokeRole",
            role_name="auto-training-agent-scheduler-role",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        # Grant the scheduler role invoke via an inline policy scoped by ARN
        # (string-built) rather than grant_invoke, which would add a dependency
        # edge from the role to api_fn and back.
        self.scheduler_invoke_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[api_fn_arn],
            )
        )
        self.api_fn.add_environment("SCHEDULER_ROLE_ARN", self.scheduler_invoke_role.role_arn)
        self.api_fn.add_environment("API_FUNCTION_ARN", api_fn_arn)
        self.api_fn.add_environment("SCHEDULE_GROUP_NAME", self.schedule_group.name)
        # Manage schedules only within our group.
        self.api_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "scheduler:CreateSchedule",
                    "scheduler:DeleteSchedule",
                    "scheduler:GetSchedule",
                    "scheduler:UpdateSchedule",
                ],
                resources=[
                    self.format_arn(
                        service="scheduler",
                        resource="schedule",
                        resource_name=f"{self.schedule_group.name}/*",
                    ),
                ],
            )
        )
        # Scheduler requires the caller to be able to pass the invoke role.
        self.api_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[self.scheduler_invoke_role.role_arn],
            )
        )

        CfnOutput(self, "ApiUrl", value=self.api.url)
        CfnOutput(self, "EcrRepoUri", value=self.repo.repository_uri)
        CfnOutput(self, "BucketName", value=self.bucket.bucket_name)
        CfnOutput(self, "TokenSecretArn", value=self.token_secret.secret_arn)
        CfnOutput(self, "ModelRegistryTableName", value=self.model_registry_table.table_name)
        CfnOutput(self, "InferTaskTableName", value=self.infer_table.table_name)
        CfnOutput(self, "UserTableName", value=self.user_table.table_name)
        CfnOutput(self, "ImageTableName", value=self.image_table.table_name)
        CfnOutput(self, "ImageBuildProjectName", value=self.image_build_project.project_name)
        CfnOutput(
            self,
            "ImageBuildSourceBucketName",
            value=self.image_build_source_bucket.bucket_name,
        )
