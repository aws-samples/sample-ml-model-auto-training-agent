from botocore.exceptions import ClientError
import pytest

from deployment.sagemaker_adapter import SageMakerAdapter, SageMakerDeploymentError


class FakeSageMakerClient:
    def __init__(self):
        self.create_model_calls = 0
        self.create_endpoint_config_calls = 0
        self.create_endpoint_calls = 0
        self.describe_model_response = None
        self.describe_endpoint_config_response = None
        self.describe_endpoint_response = {
            "EndpointStatus": "Creating",
            "EndpointArn": "arn:endpoint",
        }
        self.create_model_error = None
        self.create_endpoint_error = None

    def describe_model(self, ModelName):
        if self.describe_model_response is not None:
            return self.describe_model_response
        raise ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "Could not find model",
                }
            },
            "DescribeModel",
        )

    def create_model(self, **kwargs):
        self.create_model_calls += 1
        self.last_create_model_kwargs = kwargs
        if self.create_model_error is not None:
            raise self.create_model_error
        return {}

    def describe_endpoint_config(self, EndpointConfigName):
        if self.describe_endpoint_config_response is not None:
            return self.describe_endpoint_config_response
        raise ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "Could not find endpoint config",
                }
            },
            "DescribeEndpointConfig",
        )

    def create_endpoint_config(self, **kwargs):
        self.create_endpoint_config_calls += 1
        return {}

    def describe_endpoint(self, EndpointName):
        # Once the endpoint has been created, report it so deploy_model can read
        # back the final status; before that, behave as not-found.
        if self.create_endpoint_calls > 0:
            return {"EndpointStatus": "Creating", "EndpointArn": "arn:endpoint"}
        raise ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "Could not find endpoint",
                }
            },
            "DescribeEndpoint",
        )

    def create_endpoint(self, **kwargs):
        self.create_endpoint_calls += 1
        if self.create_endpoint_error is not None:
            raise self.create_endpoint_error
        return {}


class FakeEcrClient:
    """ECR stub for the serve-tag probe: knows a configurable set of tags."""

    def __init__(self, existing_tags=()):
        self.existing_tags = set(existing_tags)
        self.describe_calls = []

    def describe_images(self, repositoryName, imageIds):
        tag = imageIds[0]["imageTag"]
        self.describe_calls.append((repositoryName, tag))
        if tag in self.existing_tags:
            return {"imageDetails": [{"imageTags": [tag]}]}
        raise ClientError(
            {
                "Error": {
                    "Code": "ImageNotFoundException",
                    "Message": f"no image with tag {tag}",
                }
            },
            "DescribeImages",
        )


class _FakePaginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return self._pages


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    """Minimal S3 stub: one model object under the models/ dir; supports the
    list/get/put used when packaging the model.tar.gz."""

    def __init__(self):
        self.put_calls = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _FakePaginator(
            [{"Contents": [{"Key": "output/task-1/output/models/predictor.pkl"}]}]
        )

    def get_object(self, Bucket, Key):
        return {"Body": _FakeBody(b"model-bytes")}

    def put_object(self, Bucket, Key, Body):
        self.put_calls.append(Key)
        return {}


def test_sagemaker_adapter_sets_offline_hf_cache_env_on_model():
    """The inference container must load the zero-shot Chronos weights OFFLINE
    from the pre-baked in-image HF cache (/opt/hf-cache), never from a runtime
    HuggingFace download (which is unreliable on real-time endpoints)."""
    client = FakeSageMakerClient()
    adapter = SageMakerAdapter(
        client=client, s3_client=FakeS3Client(), ecr_client=FakeEcrClient()
    )

    result = adapter.deploy_model(
        task_record={
            "task_id": "task-1",
            "target_endpoint": "demo-endpoint-01",
            "resolved_image_tag": "stable",
        },
        artifact_reference={
            "bucket": "bucket-name",
            "key": "output/task-1/output/models/predictor.pkl",
            "artifact_type": "predictor",
        },
        image_repo_uri="123456789012.dkr.ecr.us-west-2.amazonaws.com/test",
        execution_role_arn="arn:aws:iam::123456789012:role/SageMakerExecutionRole",
        instance_type="ml.m5.large",
        initial_instance_count=1,
    )
    assert result["operation"] == "create"
    env = client.last_create_model_kwargs["PrimaryContainer"]["Environment"]
    # cache points at the baked in-image location and offline mode is forced
    assert env["HF_HOME"] == "/opt/hf-cache"
    assert env["HF_HUB_CACHE"].startswith("/opt/hf-cache")
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"


def _deploy(adapter):
    return adapter.deploy_model(
        task_record={
            "task_id": "task-1",
            "target_endpoint": "demo-endpoint-01",
            "resolved_image_tag": "stable",
        },
        artifact_reference={
            "bucket": "bucket-name",
            "key": "output/task-1/output/models/predictor.pkl",
            "artifact_type": "predictor",
        },
        image_repo_uri="123456789012.dkr.ecr.us-west-2.amazonaws.com/test",
        execution_role_arn="arn:aws:iam::123456789012:role/SageMakerExecutionRole",
        instance_type="ml.m5.large",
        initial_instance_count=1,
    )


def test_sagemaker_adapter_wraps_create_endpoint_resource_limit_error():
    # ResourceLimitExceeded (g5 endpoint quota exhausted) from create_endpoint
    # must surface as SageMakerDeploymentError so the handler marks the task
    # FAILED instead of letting it bubble and stick in DEPLOYING.
    client = FakeSageMakerClient()
    client.create_endpoint_error = ClientError(
        {
            "Error": {
                "Code": "ResourceLimitExceeded",
                "Message": "ml.g5.4xlarge for endpoint usage is 1 Instance",
            }
        },
        "CreateEndpoint",
    )
    adapter = SageMakerAdapter(
        client=client, s3_client=FakeS3Client(), ecr_client=FakeEcrClient()
    )
    with pytest.raises(SageMakerDeploymentError):
        _deploy(adapter)


def test_sagemaker_adapter_prefers_non_root_serve_image_variant():
    """When the companion non-root "<tag>-serve" image exists in ECR, the
    endpoint model must use it (deployed containers should not run as root)."""
    client = FakeSageMakerClient()
    ecr = FakeEcrClient(existing_tags={"stable-serve"})
    adapter = SageMakerAdapter(
        client=client, s3_client=FakeS3Client(), ecr_client=ecr
    )
    result = _deploy(adapter)
    image = client.last_create_model_kwargs["PrimaryContainer"]["Image"]
    assert image.endswith(":stable-serve")
    assert result["provider_status_detail"]["image_uri"].endswith(":stable-serve")
    # probe used the repository name parsed from the repo URI
    assert ecr.describe_calls == [("test", "stable-serve")]


def test_sagemaker_adapter_falls_back_to_base_tag_without_serve_variant():
    """Images built before the serve stage existed have no "-serve" tag; the
    deployment must fall back to the base tag instead of failing."""
    client = FakeSageMakerClient()
    ecr = FakeEcrClient(existing_tags=set())
    adapter = SageMakerAdapter(
        client=client, s3_client=FakeS3Client(), ecr_client=ecr
    )
    _deploy(adapter)
    image = client.last_create_model_kwargs["PrimaryContainer"]["Image"]
    assert image.endswith(":stable")


def test_sagemaker_adapter_surfaces_parameter_validation_errors():
    client = FakeSageMakerClient()
    client.create_model_error = ClientError(
        {
            "Error": {
                "Code": "ValidationException",
                "Message": "Invalid image uri",
            }
        },
        "CreateModel",
    )
    adapter = SageMakerAdapter(
        client=client, s3_client=FakeS3Client(), ecr_client=FakeEcrClient()
    )

    with pytest.raises(SageMakerDeploymentError):
        adapter.deploy_model(
            task_record={
                "task_id": "task-1",
                "target_endpoint": "demo-endpoint-01",
                "resolved_image_tag": "stable",
            },
            artifact_reference={
                "bucket": "bucket-name",
                "key": "output/task-1/model.tar.gz",
                "artifact_type": "tarball",
            },
            image_repo_uri="123456789012.dkr.ecr.cn/test",
            execution_role_arn="arn:aws-cn:iam::123456789012:role/SageMakerExecutionRole",
            instance_type="ml.m5.large",
            initial_instance_count=1,
        )


def test_sagemaker_adapter_skips_create_calls_when_resources_already_exist():
    client = FakeSageMakerClient()
    client.describe_model_response = {"ModelName": "existing-model"}
    client.describe_endpoint_config_response = {"EndpointConfigName": "existing-config"}
    adapter = SageMakerAdapter(
        client=client, s3_client=FakeS3Client(), ecr_client=FakeEcrClient()
    )

    adapter.deploy_model(
        task_record={
            "task_id": "task-1",
            "target_endpoint": "demo-endpoint-01",
            "resolved_image_tag": "stable",
        },
        artifact_reference={
            "bucket": "bucket-name",
            "key": "output/task-1/model.tar.gz",
            "artifact_type": "tarball",
        },
        image_repo_uri="123456789012.dkr.ecr.cn/test",
        execution_role_arn="arn:aws-cn:iam::123456789012:role/SageMakerExecutionRole",
        instance_type="ml.m5.large",
        initial_instance_count=1,
    )

    assert client.create_model_calls == 0
    assert client.create_endpoint_config_calls == 0
