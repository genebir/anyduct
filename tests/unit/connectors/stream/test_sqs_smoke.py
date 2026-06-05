"""Driver-free tests for the SQS connector (Phase AGM, ADR-0084).

A fake boto3 client pins the pure logic — registry, protocol surface,
queue-URL resolution/caching, JSON encode/decode, the receive→yield→
commit(delete) flow, and the "driver missing" error. The real round-trip
is covered by a LocalStack integration test.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from etl_plugins.connectors.stream.sqs import SQSConnector
from etl_plugins.core.connector import StreamSink, StreamSource
from etl_plugins.core.exceptions import ConnectError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


def test_sqs_resolves_through_registry() -> None:
    assert ConnectorRegistry.get("sqs") is SQSConnector


def test_sqs_implements_stream_protocols() -> None:
    c = SQSConnector(region="us-east-1")
    assert isinstance(c, StreamSource)
    assert isinstance(c, StreamSink)


def test_sqs_connect_error_when_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "boto3", None)
    c = SQSConnector()
    with pytest.raises(ConnectError) as excinfo:
        c.connect()
    assert "boto3 not installed" in str(excinfo.value)


async def test_publish_without_connect_raises() -> None:
    c = SQSConnector()
    with pytest.raises(ConnectError, match="not connected"):
        await c.publish("q", Record(data={"id": 1}))


async def test_flush_is_noop() -> None:
    assert await SQSConnector().flush() is None


# ---------- fake-client logic ------------------------------------------


class _FakeSQS:
    def __init__(self, messages: list[dict[str, Any]] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.deleted: list[dict[str, Any]] = []
        self.get_url_calls = 0
        self._messages = messages or []
        self._served = False

    def get_queue_url(self, QueueName: str) -> dict[str, Any]:
        self.get_url_calls += 1
        return {"QueueUrl": f"http://sqs/{QueueName}"}

    def send_message(self, **kwargs: Any) -> dict[str, Any]:
        self.sent.append(kwargs)
        return {"MessageId": "m1"}

    def receive_message(self, **kwargs: Any) -> dict[str, Any]:
        if not self._served:
            self._served = True
            return {"Messages": self._messages}
        return {"Messages": []}

    def delete_message_batch(self, **kwargs: Any) -> dict[str, Any]:
        self.deleted.append(kwargs)
        return {"Successful": kwargs.get("Entries", [])}


def _bind(fake: _FakeSQS) -> SQSConnector:
    c = SQSConnector(region="us-east-1")
    c._client = fake
    return c


async def test_publish_resolves_url_and_encodes_json() -> None:
    fake = _FakeSQS()
    c = _bind(fake)
    await c.publish("orders", Record(data={"id": 1, "name": "a"}))
    call = fake.sent[0]
    assert call["QueueUrl"] == "http://sqs/orders"
    assert json.loads(call["MessageBody"]) == {"id": 1, "name": "a"}


async def test_url_passthrough_when_already_url() -> None:
    fake = _FakeSQS()
    c = _bind(fake)
    await c.publish("http://sqs/direct", Record(data={"id": 1}))
    assert fake.sent[0]["QueueUrl"] == "http://sqs/direct"
    assert fake.get_url_calls == 0  # no resolution needed


async def test_subscribe_yields_then_commit_deletes() -> None:
    msgs = [
        {"Body": json.dumps({"id": 1}), "ReceiptHandle": "r1", "MessageId": "m1"},
        {"Body": json.dumps({"id": 2}), "ReceiptHandle": "r2", "MessageId": "m2"},
    ]
    fake = _FakeSQS(messages=msgs)
    c = _bind(fake)
    out: list[Record] = []
    async for rec in c.subscribe("orders", wait_seconds=0):
        out.append(rec)
        if len(out) == 2:
            break
    assert [r.data["id"] for r in out] == [1, 2]
    assert out[0].metadata["source"] == "sqs"
    # commit deletes the two pending receipt handles.
    await c.commit()
    handles = [e["ReceiptHandle"] for call in fake.deleted for e in call["Entries"]]
    assert handles == ["r1", "r2"]


async def test_commit_without_pending_is_safe() -> None:
    fake = _FakeSQS()
    c = _bind(fake)
    await c.commit()
    assert fake.deleted == []
