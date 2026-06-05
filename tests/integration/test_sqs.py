"""SQSConnector integration tests (testcontainers + LocalStack).

Proves the real boto3/SQS round-trip: ``send_message`` publish +
``receive_message`` subscribe + ``delete_message_batch`` commit (the ack
that stops redelivery).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import boto3
import pytest
from testcontainers.localstack import LocalStackContainer

from etl_plugins.connectors.stream.sqs import SQSConnector
from etl_plugins.core.record import Record

pytestmark = pytest.mark.it

_QUEUE = "etl-jobs"


@pytest.fixture(scope="module")
def localstack() -> Iterator[LocalStackContainer]:
    container = LocalStackContainer(image="localstack/localstack:3.8")
    container.with_services("sqs")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="module")
def sqs_queue(localstack: LocalStackContainer) -> Iterator[str]:
    client = boto3.client(
        "sqs",
        region_name="us-east-1",
        endpoint_url=localstack.get_url(),
        aws_access_key_id="test",
        aws_secret_access_key="test",  # pragma: allowlist secret
    )
    client.create_queue(QueueName=_QUEUE)
    yield _QUEUE


@pytest.fixture
def connector(localstack: LocalStackContainer) -> Iterator[SQSConnector]:
    c = SQSConnector(
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


def test_health_check(connector: SQSConnector, sqs_queue: str) -> None:
    assert connector.health_check() is True


async def test_publish_subscribe_commit_round_trip(connector: SQSConnector, sqs_queue: str) -> None:
    for i in range(1, 4):
        await connector.publish(sqs_queue, Record(data={"id": i}))

    collected: list[Record] = []

    async def drain() -> None:
        async for rec in connector.subscribe(sqs_queue, wait_seconds=1, max_messages=10):
            collected.append(rec)
            if len(collected) == 3:
                break

    await asyncio.wait_for(drain(), timeout=30)
    assert {r.data["id"] for r in collected} == {1, 2, 3}
    assert collected[0].metadata["source"] == "sqs"

    # commit deletes the 3 messages → a fresh receive returns nothing.
    await connector.commit()
    client = connector.client
    url = client.get_queue_url(QueueName=sqs_queue)["QueueUrl"]
    resp = client.receive_message(QueueUrl=url, WaitTimeSeconds=1, MaxNumberOfMessages=10)
    assert resp.get("Messages", []) == []
