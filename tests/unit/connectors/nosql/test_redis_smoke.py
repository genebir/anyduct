"""Driver-free tests for the Redis Streams connector (Phase AGN, ADR-0085).

A fake redis client pins the pure logic — registry, protocol surface,
JSON encode/decode, the XREADGROUP→yield→XACK(commit) flow, BUSYGROUP
tolerance, and the "driver missing" error.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from etl_plugins.connectors.nosql.redis import RedisConnector
from etl_plugins.core.connector import StreamSink, StreamSource
from etl_plugins.core.exceptions import ConnectError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


def test_redis_resolves_through_registry() -> None:
    assert ConnectorRegistry.get("redis") is RedisConnector


def test_redis_implements_stream_protocols() -> None:
    c = RedisConnector(host="h")
    assert isinstance(c, StreamSource)
    assert isinstance(c, StreamSink)


def test_redis_connect_error_when_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "redis", None)
    c = RedisConnector()
    with pytest.raises(ConnectError) as excinfo:
        c.connect()
    assert "redis not installed" in str(excinfo.value)


async def test_publish_without_connect_raises() -> None:
    c = RedisConnector()
    with pytest.raises(ConnectError, match="not connected"):
        await c.publish("s", Record(data={"id": 1}))


async def test_flush_is_noop() -> None:
    assert await RedisConnector().flush() is None


# ---------- fake-client logic ------------------------------------------


class _FakeRedis:
    def __init__(self, entries: list[tuple[bytes, dict]] | None = None) -> None:
        self.added: list[tuple[str, dict]] = []
        self.acked: list[tuple] = []
        self.groups_created: list[tuple[str, str]] = []
        self._entries = entries or []
        self._served = False

    def ping(self) -> bool:
        return True

    def xgroup_create(self, name: str, group: str, id: str = "0", mkstream: bool = False) -> None:
        self.groups_created.append((name, group))

    def xadd(self, name: str, fields: dict) -> bytes:
        self.added.append((name, fields))
        return b"1-0"

    def xreadgroup(
        self, group: str, consumer: str, streams: dict, count: Any = None, block: Any = None
    ) -> Any:
        if not self._served:
            self._served = True
            stream = next(iter(streams))
            return [(stream.encode(), self._entries)]
        return []

    def xack(self, name: str, group: str, *ids: Any) -> int:
        self.acked.append((name, group, *ids))
        return len(ids)

    def close(self) -> None:
        pass


def _bind(fake: _FakeRedis) -> RedisConnector:
    c = RedisConnector(host="h")
    c._client = fake
    return c


async def test_publish_xadds_json() -> None:
    fake = _FakeRedis()
    c = _bind(fake)
    await c.publish("events", Record(data={"id": 1, "name": "a"}))
    name, fields = fake.added[0]
    assert name == "events"
    assert json.loads(fields["data"]) == {"id": 1, "name": "a"}


async def test_subscribe_yields_then_commit_acks() -> None:
    entries = [
        (b"1-0", {b"data": json.dumps({"id": 1}).encode()}),
        (b"2-0", {b"data": json.dumps({"id": 2}).encode()}),
    ]
    fake = _FakeRedis(entries=entries)
    c = _bind(fake)
    out: list[Record] = []
    async for rec in c.subscribe("events", group_id="g", block_ms=0):
        out.append(rec)
        if len(out) == 2:
            break
    assert [r.data["id"] for r in out] == [1, 2]
    assert out[0].metadata["source"] == "redis"
    assert out[0].metadata["message_id"] == "1-0"
    assert ("events", "g") in fake.groups_created
    # commit XACKs the two read ids.
    await c.commit()
    assert fake.acked == [("events", "g", b"1-0", b"2-0")]


async def test_busygroup_is_tolerated() -> None:
    class _BusyRedis(_FakeRedis):
        def xgroup_create(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")

    fake = _BusyRedis(entries=[])
    c = _bind(fake)
    # Should not raise — BUSYGROUP means the group already exists.
    agen = c.subscribe("events", group_id="g", block_ms=0)
    # Pull one step: no entries → loops; just ensure group-create didn't bubble.
    task = agen.__anext__()
    import asyncio

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=0.2)
    await agen.aclose()
