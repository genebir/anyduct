"""KinesisConnector integration tests (testcontainers + LocalStack).

Proves the real boto3/Kinesis round-trip the driver-free unit smoke
can't: ``put_record`` publish + shard-iterator ``get_records`` subscribe.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import boto3
import pytest
from testcontainers.localstack import LocalStackContainer

from etl_plugins.connectors.stream.kinesis import KinesisConnector
from etl_plugins.core.record import Record

pytestmark = pytest.mark.it

_STREAM = "events"


@pytest.fixture(scope="module")
def localstack() -> Iterator[LocalStackContainer]:
    container = LocalStackContainer(image="localstack/localstack:3.8")
    container.with_services("kinesis")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="module")
def kinesis_stream(localstack: LocalStackContainer) -> Iterator[str]:
    client = boto3.client(
        "kinesis",
        region_name="us-east-1",
        endpoint_url=localstack.get_url(),
        aws_access_key_id="test",
        aws_secret_access_key="test",  # pragma: allowlist secret
    )
    client.create_stream(StreamName=_STREAM, ShardCount=1)
    client.get_waiter("stream_exists").wait(StreamName=_STREAM)
    try:
        yield _STREAM
    finally:
        client.delete_stream(StreamName=_STREAM, EnforceConsumerDeletion=True)


@pytest.fixture
def connector(localstack: LocalStackContainer) -> Iterator[KinesisConnector]:
    c = KinesisConnector(
        region="us-east-1",
        endpoint_url=localstack.get_url(),
        aws_access_key_id="test",
        aws_secret_access_key="test",  # pragma: allowlist secret
    )
    c.connect()
    try:
        yield c
    finally:
        c.close()


def test_health_check(connector: KinesisConnector, kinesis_stream: str) -> None:
    assert connector.health_check() is True


async def test_publish_then_subscribe_round_trip(
    connector: KinesisConnector, kinesis_stream: str
) -> None:
    for i in range(1, 4):
        await connector.publish(
            kinesis_stream, Record(data={"id": i, "name": f"n{i}"}), key=str(i).encode()
        )

    collected: list[Record] = []

    async def drain() -> None:
        async for rec in connector.subscribe(kinesis_stream, poll_interval=0.2):
            collected.append(rec)
            if len(collected) == 3:
                break

    await asyncio.wait_for(drain(), timeout=30)

    by_id = {r.data["id"]: r.data for r in collected}
    assert set(by_id) == {1, 2, 3}
    assert by_id[2]["name"] == "n2"
    assert collected[0].metadata["source"] == "kinesis"
    assert collected[0].metadata["sequence_number"] is not None
