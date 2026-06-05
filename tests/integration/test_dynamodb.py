"""DynamoDBConnector integration tests (testcontainers + LocalStack).

Proves the real boto3 round-trip — the part the driver-free unit smoke
can't: ``batch_writer`` puts, paginated ``scan`` reads, and the
float→Decimal→float type round-trip DynamoDB forces.
"""

from __future__ import annotations

from collections.abc import Iterator

import boto3
import pytest
from testcontainers.localstack import LocalStackContainer

from etl_plugins.connectors.nosql.dynamodb import DynamoDBConnector
from etl_plugins.core.record import Record

pytestmark = pytest.mark.it

_TABLE = "events"


@pytest.fixture(scope="module")
def localstack() -> Iterator[LocalStackContainer]:
    container = LocalStackContainer(image="localstack/localstack:3.8")
    container.with_services("dynamodb")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture
def dynamo_table(localstack: LocalStackContainer) -> Iterator[str]:
    client = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        endpoint_url=localstack.get_url(),
        aws_access_key_id="test",
        aws_secret_access_key="test",  # pragma: allowlist secret
    )
    client.create_table(
        TableName=_TABLE,
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "N"}],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=_TABLE)
    try:
        yield _TABLE
    finally:
        client.delete_table(TableName=_TABLE)


@pytest.fixture
def connector(localstack: LocalStackContainer) -> Iterator[DynamoDBConnector]:
    c = DynamoDBConnector(
        region="us-east-1",
        table=_TABLE,
        endpoint_url=localstack.get_url(),
        aws_access_key_id="test",
        aws_secret_access_key="test",  # pragma: allowlist secret
    )
    c.connect()
    try:
        yield c
    finally:
        c.close()


def test_health_check(connector: DynamoDBConnector, dynamo_table: str) -> None:
    assert connector.health_check() is True


def test_write_then_scan_round_trip(connector: DynamoDBConnector, dynamo_table: str) -> None:
    records = [
        Record(data={"id": 1, "name": "alice", "price": 0.1}),
        Record(data={"id": 2, "name": "bob", "price": 9.99}),
        Record(data={"id": 3, "name": "carol", "price": 5}),
    ]
    written = connector.write(records, table=dynamo_table, mode="append")
    assert written == 3

    out = {r.data["id"]: r.data for r in connector.read(query=dynamo_table)}
    assert set(out) == {1, 2, 3}
    assert out[1]["name"] == "alice"
    # float → Decimal (write) → float (read) round-trips.
    assert out[2]["price"] == pytest.approx(9.99)
    assert isinstance(out[2]["price"], float)
    # integral value comes back as int, not float/Decimal.
    assert out[3]["price"] == 5
    assert isinstance(out[3]["price"], int)


def test_put_replaces_by_primary_key(connector: DynamoDBConnector, dynamo_table: str) -> None:
    connector.write([Record(data={"id": 7, "name": "v1"})], table=dynamo_table)
    # Same key, new value — DynamoDB put_item replaces (upsert by key).
    connector.write([Record(data={"id": 7, "name": "v2"})], table=dynamo_table, mode="upsert")
    rows = [r.data for r in connector.read(query=dynamo_table) if r.data["id"] == 7]
    assert len(rows) == 1
    assert rows[0]["name"] == "v2"


def test_read_unknown_attrs_survive(connector: DynamoDBConnector, dynamo_table: str) -> None:
    """Sparse / heterogeneous items (schemaless) read back as-is."""
    connector.write([Record(data={"id": 100, "extra": {"nested": [1, 2.5]}})], table=dynamo_table)
    rows = [r.data for r in connector.read(query=dynamo_table) if r.data["id"] == 100]
    assert rows[0]["extra"]["nested"] == [1, pytest.approx(2.5)]
