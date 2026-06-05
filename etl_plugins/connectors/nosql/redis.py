"""Redis Streams connector — StreamSource + StreamSink (Phase AGN, ADR-0085).

Uses Redis Streams (the durable append-only log with consumer groups) via
``redis-py``. Optional dependency::

    pip install 'etl-plugins[redis]'

Redis Streams map cleanly onto the StreamSource/StreamSink contract:

* **subscribe** — ``XREADGROUP`` over a consumer group (auto-created with
  ``MKSTREAM``); each entry's JSON payload is decoded into a Record.
  Message ids are held pending until ``commit``.
* **publish** — ``XADD`` (payload stored in a single ``data`` field).
* **commit** — ``XACK`` the ids read since the last commit. Like SQS's
  delete, this is the at-least-once acknowledgement.
* **flush** — no-op.

redis-py is synchronous, so the async methods wrap calls in
``asyncio.to_thread``. The ``topic`` argument is the stream key. Driver
imported lazily.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from etl_plugins.core.connector import StreamSink, StreamSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


@ConnectorRegistry.register("redis")
class RedisConnector(StreamSource, StreamSink):
    """Redis Streams source + sink (redis-py backed)."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        *,
        password: str | None = None,
        **extra: Any,
    ) -> None:
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self._extra: dict[str, Any] = extra
        self._client: Any = None
        # (stream, group) -> [message_id, ...] read since the last commit.
        self._pending: dict[tuple[str, str], list[Any]] = {}

    # ---------- lifecycle ---------------------------------------------------

    def connect(self) -> None:
        if self._client is not None:
            return
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - import side effect
            raise ConnectError(
                "redis not installed. Install with: pip install 'etl-plugins[redis]'"
            ) from exc
        try:
            self._client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                password=self.password,
                **self._extra,
            )
        except Exception as exc:  # redis.exceptions.* is broad
            raise ConnectError(f"redis connect failed: {exc}") from exc

    def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
        self._client = None
        self._pending.clear()

    def health_check(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    async def aclose(self) -> None:
        self.close()

    @property
    def client(self) -> Any:
        if self._client is None:
            raise ConnectError("RedisConnector is not connected")
        return self._client

    # ---------- StreamSource ----------------------------------------------

    async def subscribe(
        self,
        topic: str,
        *,
        group_id: str | None = None,
        **options: Any,
    ) -> AsyncIterator[Record]:
        if self._client is None:
            raise ConnectError("RedisConnector is not connected — call connect() first")
        group = group_id or "etl"
        consumer = str(options.get("consumer", "etl-1"))
        count = int(options.get("count", 100))
        block_ms = int(options.get("block_ms", 1000))

        # Create the group (and the stream, via MKSTREAM) if absent. A
        # BusyGroupError just means it already exists — ignore it.
        def _ensure_group() -> None:
            try:
                self._client.xgroup_create(topic, group, id="0", mkstream=True)
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    raise

        try:
            await asyncio.to_thread(_ensure_group)
        except Exception as exc:
            raise ConnectError(f"redis subscribe (group create) failed: {exc}") from exc

        while True:
            try:
                resp = await asyncio.to_thread(
                    self._client.xreadgroup,
                    group,
                    consumer,
                    {topic: ">"},
                    count,
                    block_ms,
                )
            except Exception as exc:
                raise ReadError(f"redis xreadgroup failed: {exc}") from exc
            if not resp:
                continue
            for _stream, entries in resp:
                for msg_id, fields in entries:
                    raw = fields.get(b"data") if isinstance(fields, dict) else None
                    if raw is None and isinstance(fields, dict):
                        raw = fields.get("data")
                    try:
                        data = json.loads(raw) if raw else {}
                    except (json.JSONDecodeError, TypeError) as exc:
                        raise ReadError(
                            f"redis stream entry {msg_id!r} is not valid JSON: {exc}"
                        ) from exc
                    self._pending.setdefault((topic, group), []).append(msg_id)
                    yield Record(
                        data=data,
                        metadata={
                            "source": "redis",
                            "stream": topic,
                            "group": group,
                            "message_id": msg_id.decode() if isinstance(msg_id, bytes) else msg_id,
                        },
                    )

    async def commit(self, offsets: Any = None) -> None:
        """``XACK`` the entries read since the last commit (at-least-once)."""
        if self._client is None or not self._pending:
            return
        for (stream, group), ids in list(self._pending.items()):
            if not ids:
                continue
            try:
                await asyncio.to_thread(self._client.xack, stream, group, *ids)
            except Exception as exc:
                raise ConnectError(f"redis commit (xack) failed: {exc}") from exc
        self._pending.clear()

    # ---------- StreamSink ------------------------------------------------

    async def publish(
        self,
        topic: str,
        record: Record,
        key: bytes | None = None,
    ) -> None:
        if self._client is None:
            raise ConnectError("RedisConnector is not connected — call connect() first")
        value = json.dumps(record.data, ensure_ascii=False, default=str)
        try:
            await asyncio.to_thread(self._client.xadd, topic, {"data": value})
        except Exception as exc:
            raise WriteError(f"redis publish (xadd) failed: {exc}") from exc

    async def flush(self) -> None:
        """No-op: ``XADD`` is synchronous per call."""
        return None
