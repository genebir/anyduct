"""Driver-free tests for the RabbitMQ connector (Phase AGT, ADR-0086).

Fake aio-pika channel/queue pin the pure logic — registry, protocol
surface, the consume→yield→ack(commit) flow, publish shape, and the
"driver missing" error. The real AMQP round-trip is left to a live
broker (aio-pika is not a dev dep).
"""

from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from etl_plugins.connectors.stream.rabbitmq import RabbitMQConnector
from etl_plugins.core.connector import StreamSink, StreamSource
from etl_plugins.core.exceptions import ConnectError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


def test_rabbitmq_resolves_through_registry() -> None:
    assert ConnectorRegistry.get("rabbitmq") is RabbitMQConnector


def test_rabbitmq_implements_stream_protocols() -> None:
    c = RabbitMQConnector(host="h")
    assert isinstance(c, StreamSource)
    assert isinstance(c, StreamSink)


def test_connect_is_flag_only_and_health_check() -> None:
    c = RabbitMQConnector()
    assert c.health_check() is False
    c.connect()
    assert c.health_check() is True
    c.close()
    assert c.health_check() is False


async def test_driver_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "aio_pika", None)
    c = RabbitMQConnector()
    c.connect()
    with pytest.raises(ConnectError, match="aio-pika not installed"):
        await c._ensure_channel()


async def test_ensure_channel_requires_connect() -> None:
    c = RabbitMQConnector()
    with pytest.raises(ConnectError, match="not connected"):
        await c._ensure_channel()


async def test_flush_is_noop() -> None:
    assert await RabbitMQConnector().flush() is None


async def test_commit_without_pending_is_safe() -> None:
    assert await RabbitMQConnector().commit() is None


# ---------- fake aio-pika channel ---------------------------------------


class _FakeMessage:
    def __init__(self, data: dict[str, Any]) -> None:
        self.body = json.dumps(data).encode("utf-8")
        self.delivery_tag = 1
        self.acked = False

    async def ack(self) -> None:
        self.acked = True


class _FakeQueueIterator:
    def __init__(self, msgs: list[_FakeMessage]) -> None:
        self._msgs = msgs

    async def __aenter__(self) -> _FakeQueueIterator:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def __aiter__(self) -> Any:
        for m in self._msgs:
            yield m


class _FakeQueue:
    def __init__(self, msgs: list[_FakeMessage]) -> None:
        self._msgs = msgs

    def iterator(self) -> _FakeQueueIterator:
        return _FakeQueueIterator(self._msgs)


class _FakeExchange:
    def __init__(self) -> None:
        self.published: list[tuple[Any, str]] = []

    async def publish(self, message: Any, routing_key: str) -> None:
        self.published.append((message, routing_key))


class _FakeChannel:
    def __init__(self, msgs: list[_FakeMessage] | None = None) -> None:
        self._msgs = msgs or []
        self.declared: list[str] = []
        self.default_exchange = _FakeExchange()

    async def declare_queue(self, name: str, durable: bool = False) -> _FakeQueue:
        self.declared.append(name)
        return _FakeQueue(self._msgs)


def _bind(channel: _FakeChannel) -> RabbitMQConnector:
    c = RabbitMQConnector(host="h")
    c.connect()
    c._channel = channel
    return c


async def test_subscribe_yields_then_commit_acks() -> None:
    msgs = [_FakeMessage({"id": 1}), _FakeMessage({"id": 2})]
    channel = _FakeChannel(msgs)
    c = _bind(channel)
    out: list[Record] = []
    async for rec in c.subscribe("jobs"):
        out.append(rec)
        if len(out) == 2:
            break
    assert [r.data["id"] for r in out] == [1, 2]
    assert out[0].metadata["source"] == "rabbitmq"
    assert "jobs" in channel.declared
    await c.commit()
    assert all(m.acked for m in msgs)


async def test_publish_declares_and_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    # publish imports aio_pika for Message + DeliveryMode — fake it.
    fake_aio = types.ModuleType("aio_pika")
    fake_aio.Message = lambda body, delivery_mode=None: types.SimpleNamespace(  # type: ignore[attr-defined]
        body=body, delivery_mode=delivery_mode
    )
    fake_aio.DeliveryMode = types.SimpleNamespace(PERSISTENT="persistent")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aio_pika", fake_aio)

    channel = _FakeChannel()
    c = _bind(channel)
    await c.publish("jobs", Record(data={"id": 7, "name": "a"}))
    assert "jobs" in channel.declared
    message, routing_key = channel.default_exchange.published[0]
    assert routing_key == "jobs"
    assert json.loads(message.body.decode("utf-8")) == {"id": 7, "name": "a"}
