"""Driver-free tests for the Kinesis connector (Phase AGL, ADR-0083).

The real boto3/Kinesis round-trip is covered by a LocalStack integration
test; here a fake boto3 client pins the pure logic — registry, protocol
surface, JSON encode/decode, partition-key handling, the shard-poll loop,
and the "driver missing" error.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from etl_plugins.connectors.stream.kinesis import KinesisConnector
from etl_plugins.core.connector import StreamSink, StreamSource
from etl_plugins.core.exceptions import ConnectError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


def test_kinesis_resolves_through_registry() -> None:
    assert ConnectorRegistry.get("kinesis") is KinesisConnector


def test_kinesis_implements_stream_protocols() -> None:
    c = KinesisConnector(region="us-east-1")
    assert isinstance(c, StreamSource)
    assert isinstance(c, StreamSink)


def test_kinesis_connect_error_when_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "boto3", None)
    c = KinesisConnector()
    with pytest.raises(ConnectError) as excinfo:
        c.connect()
    msg = str(excinfo.value)
    assert "boto3 not installed" in msg
    assert "pip install" in msg


async def test_publish_without_connect_raises() -> None:
    c = KinesisConnector()
    with pytest.raises(ConnectError, match="not connected"):
        await c.publish("s", Record(data={"id": 1}))


async def test_commit_and_flush_are_noops() -> None:
    c = KinesisConnector()
    assert await c.commit() is None
    assert await c.flush() is None


# ---------- fake-client logic ------------------------------------------


class _FakeKinesis:
    def __init__(self, batches: list[list[dict[str, Any]]] | None = None) -> None:
        self.put_calls: list[dict[str, Any]] = []
        # Successive get_records responses (each a list of record dicts).
        self._batches = batches or []
        self._batch_idx = 0

    def put_record(self, **kwargs: Any) -> dict[str, Any]:
        self.put_calls.append(kwargs)
        return {"SequenceNumber": "1", "ShardId": "shardId-0"}

    def describe_stream(self, **kwargs: Any) -> dict[str, Any]:
        return {"StreamDescription": {"Shards": [{"ShardId": "shardId-0"}]}}

    def get_shard_iterator(self, **kwargs: Any) -> dict[str, Any]:
        return {"ShardIterator": "it-0"}

    def get_records(self, **kwargs: Any) -> dict[str, Any]:
        if self._batch_idx < len(self._batches):
            recs = self._batches[self._batch_idx]
            self._batch_idx += 1
        else:
            recs = []
        return {"Records": recs, "NextShardIterator": "it-next"}


def _bind(fake: _FakeKinesis) -> KinesisConnector:
    c = KinesisConnector(region="us-east-1")
    c._client = fake
    return c


async def test_publish_encodes_json_and_partition_key() -> None:
    fake = _FakeKinesis()
    c = _bind(fake)
    await c.publish("events", Record(data={"id": 1, "name": "a"}), key=b"pk1")
    call = fake.put_calls[0]
    assert call["StreamName"] == "events"
    assert call["PartitionKey"] == "pk1"
    assert json.loads(call["Data"].decode("utf-8")) == {"id": 1, "name": "a"}


async def test_publish_defaults_partition_key() -> None:
    fake = _FakeKinesis()
    c = _bind(fake)
    await c.publish("events", Record(data={"id": 1}))
    assert fake.put_calls[0]["PartitionKey"] == "0"


async def test_subscribe_yields_decoded_records() -> None:
    batch = [
        {"Data": json.dumps({"id": 1}).encode(), "SequenceNumber": "100", "PartitionKey": "a"},
        {"Data": json.dumps({"id": 2}).encode(), "SequenceNumber": "101", "PartitionKey": "b"},
    ]
    fake = _FakeKinesis(batches=[batch])
    c = _bind(fake)
    out: list[Record] = []
    async for rec in c.subscribe("events", poll_interval=0.0):
        out.append(rec)
        if len(out) == 2:
            break
    assert [r.data["id"] for r in out] == [1, 2]
    assert out[0].metadata["sequence_number"] == "100"
    assert out[0].metadata["shard_id"] == "shardId-0"
