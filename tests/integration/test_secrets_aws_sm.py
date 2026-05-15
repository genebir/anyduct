"""AwsSmSecretBackend integration test (Step 7.4).

Uses LocalStack via testcontainers to run AWS Secrets Manager locally. The
backend's only AWS-specific code path is the boto3 client; LocalStack
implements the same wire protocol so this gives high confidence without
hitting real AWS.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from testcontainers.localstack import LocalStackContainer

from etl_plugins.config.secrets import AwsSmSecretBackend
from etl_plugins.core.exceptions import SecretError

pytestmark = pytest.mark.it


@pytest.fixture(scope="module")
def localstack_container() -> Iterator[LocalStackContainer]:
    """LocalStack with Secrets Manager enabled."""
    container = LocalStackContainer(image="localstack/localstack:3.8")
    container.with_services("secretsmanager")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
def aws_sm_backend(localstack_container: LocalStackContainer) -> AwsSmSecretBackend:
    return AwsSmSecretBackend(
        region_name="us-east-1",
        endpoint_url=localstack_container.get_url(),
        # LocalStack accepts any credentials; pass throwaway values rather
        # than depending on ambient AWS env vars on the dev machine.
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def test_aws_sm_round_trip(aws_sm_backend: AwsSmSecretBackend) -> None:
    aws_sm_backend.set("app/db/password", "s3cret")
    assert aws_sm_backend.get("app/db/password") == "s3cret"


def test_aws_sm_overwrites_existing_secret(aws_sm_backend: AwsSmSecretBackend) -> None:
    aws_sm_backend.set("app/api/key", "v1")
    aws_sm_backend.set("app/api/key", "v2")  # would error in AWS if not handled
    assert aws_sm_backend.get("app/api/key") == "v2"


def test_aws_sm_get_missing_raises(aws_sm_backend: AwsSmSecretBackend) -> None:
    with pytest.raises(SecretError, match="not in AWS Secrets Manager"):
        aws_sm_backend.get("does/not/exist")


def test_aws_sm_delete(aws_sm_backend: AwsSmSecretBackend) -> None:
    aws_sm_backend.set("temp/key", "x")
    aws_sm_backend.delete("temp/key")
    with pytest.raises(SecretError):
        aws_sm_backend.get("temp/key")
