"""Driver-free tests for the NATS JetStream connector (Phase AGU, ADR-0087).

A fake JetStream context pins the pure logic — registry, protocol
surface, servers parsing, the pull-fetch→yield→ack(commit) flow, publish
shape, and the "driver missing" error. The real JetStream round-trip is
left to a live server (nats-py is not a dev dep).
"""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from etl_plugins.connectors.stream.nats import NatsConnector
from etl_plugins.core.connector import StreamSink, StreamSource
from etl_plugins.core.exceptions import ConnectError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


def test_nats_resolves_through_registry() -> None:
    assert ConnectorRegistry.get("nats") is NatsConnector


def test_nats_implements_stream_protocols() -> None:
    c = NatsConnector()
    assert isinstance(c, StreamSource)
    assert isinstance(c, StreamSink)


def test_servers_string_parsed_to_list() -> None:
    c = NatsConnector(servers="nats://a:4222, nats://b:4222")
    assert c.servers == ["nats://a:4222", "nats://b:4222"]


def test_connect_flag_only() -> None:
    c = NatsConnector()
    assert c.health_check() is False
    c.connect()
    assert c.health_check() is True


async def test_driver_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "nats", None)
    c = NatsConnector()
    c.connect()
    with pytest.raises(ConnectError, match="nats-py not installed"):
        await c._ensure_js()


async def test_ensure_js_requires_connect() -> None:
    c = NatsConnector()
    with pytest.raises(ConnectError, match="not connected"):
        await c._ensure_js()


async def test_flush_and_empty_commit_are_safe() -> None:
    c = NatsConnector()
    assert await c.flush() is None
    assert await c.commit() is None


# ---------- fake JetStream ---------------------------------------------


class _FakeMsg:
    def __init__(self, data: dict[str, Any], subject: str = "events") -> None:
        self.data = json.dumps(data).encode("utf-8")
        self.subject = subject
        self.acked = False

    async def ack(self) -> None:
        self.acked = True


class _NatsTimeoutError(Exception):
    """Stand-in whose class name contains 'timeout' (the connector treats
    a fetch timeout as 'no messages, poll again')."""


class _FakePullSub:
    def __init__(self, msgs: list[_FakeMsg]) -> None:
        self._msgs = msgs
        self._served = False

    async def fetch(self, batch: int, timeout: float | None = None) -> list[_FakeMsg]:
        if self._served:
            raise _NatsTimeoutError("nats: fetch timeout")
        self._served = True
        return self._msgs


class _FakeJS:
    def __init__(self, msgs: list[_FakeMsg] | None = None) -> None:
        self._msgs = msgs or []
        self.published: list[tuple[str, bytes]] = []
        self.durables: list[str] = []

    async def pull_subscribe(self, subject: str, durable: str | None = None) -> _FakePullSub:
        self.durables.append(durable or "")
        return _FakePullSub(self._msgs)

    async def publish(self, subject: str, body: bytes) -> None:
        self.published.append((subject, body))


def _bind(js: _FakeJS) -> NatsConnector:
    c = NatsConnector()
    c.connect()
    c._js = js
    return c


async def test_subscribe_yields_then_commit_acks() -> None:
    msgs = [_FakeMsg({"id": 1}), _FakeMsg({"id": 2})]
    js = _FakeJS(msgs)
    c = _bind(js)
    out: list[Record] = []
    async for rec in c.subscribe("events", group_id="g"):
        out.append(rec)
        if len(out) == 2:
            break
    assert [r.data["id"] for r in out] == [1, 2]
    assert out[0].metadata["source"] == "nats"
    assert "g" in js.durables
    await c.commit()
    assert all(m.acked for m in msgs)


async def test_publish_sends_json() -> None:
    js = _FakeJS()
    c = _bind(js)
    await c.publish("events", Record(data={"id": 7, "name": "a"}))
    subject, body = js.published[0]
    assert subject == "events"
    assert json.loads(body.decode("utf-8")) == {"id": 7, "name": "a"}
