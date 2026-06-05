"""NATS JetStream connector — StreamSource + StreamSink (Phase AGU, ADR-0087).

Built on nats-py (native asyncio). Uses **JetStream** (NATS's persistence
+ acknowledgement layer) so the source has at-least-once semantics — core
NATS pub/sub is fire-and-forget and can't satisfy the commit contract.
Optional dependency::

    pip install 'etl-plugins[nats]'

* **subscribe** — a durable **pull** consumer (`pull_subscribe` + `fetch`)
  over the subject; each message's JSON payload is decoded. Messages are
  held pending until ``commit``.
* **publish** — ``js.publish`` (the subject must map to a JetStream
  stream configured on the server).
* **commit** — ``msg.ack()`` for everything fetched since the last
  commit (JetStream acknowledgement).
* **flush** — no-op.

Lifecycle mirrors the Kafka/RabbitMQ connectors: sync ``connect()`` is
flag-only; the NATS connection + JetStream context are opened lazily.
``topic`` is the subject; ``group_id`` is the durable consumer name.
Imported lazily.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from etl_plugins.core.connector import StreamSink, StreamSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


@ConnectorRegistry.register("nats")
class NatsConnector(StreamSource, StreamSink):
    """NATS JetStream stream source + sink (nats-py backed)."""

    def __init__(
        self,
        servers: str | list[str] = "nats://localhost:4222",
        *,
        token: str | None = None,
        user: str | None = None,
        password: str | None = None,
        **extra: Any,
    ) -> None:
        if isinstance(servers, str):
            self.servers = [s.strip() for s in servers.split(",") if s.strip()]
        else:
            self.servers = list(servers)
        self.token = token
        self.user = user
        self.password = password
        self._extra: dict[str, Any] = extra
        self._nc: Any = None
        self._js: Any = None
        self._connected = False
        self._pending: list[Any] = []

    # ---------- sync lifecycle (flag-only) ---------------------------------

    def connect(self) -> None:
        self._connected = True

    def close(self) -> None:
        self._connected = False

    def health_check(self) -> bool:
        return self._connected

    async def aclose(self) -> None:
        if self._nc is not None:
            try:
                await self._nc.close()
            finally:
                self._nc = None
                self._js = None
        self._connected = False

    # ---------- async connection (lazy) ------------------------------------

    async def _ensure_js(self) -> Any:
        if self._js is not None:
            return self._js
        if not self._connected:
            raise ConnectError("NatsConnector is not connected — call connect() first")
        try:
            import nats
        except ImportError as exc:  # pragma: no cover - import side effect
            raise ConnectError(
                "nats-py not installed. Install with: pip install 'etl-plugins[nats]'"
            ) from exc
        opts: dict[str, Any] = {"servers": self.servers, **self._extra}
        if self.token:
            opts["token"] = self.token
        if self.user:
            opts["user"] = self.user
        if self.password:
            opts["password"] = self.password
        try:
            self._nc = await nats.connect(**opts)
            self._js = self._nc.jetstream()
        except Exception as exc:  # nats.errors.* is broad
            raise ConnectError(f"nats connect failed: {exc}") from exc
        return self._js

    # ---------- StreamSource ----------------------------------------------

    async def subscribe(
        self,
        topic: str,
        *,
        group_id: str | None = None,
        **options: Any,
    ) -> AsyncIterator[Record]:
        js = await self._ensure_js()
        durable = group_id or "etl"
        batch = int(options.get("batch", 100))
        timeout = float(options.get("fetch_timeout", 5.0))
        try:
            psub = await js.pull_subscribe(topic, durable=durable)
        except Exception as exc:
            raise ConnectError(f"nats subscribe (pull_subscribe) failed: {exc}") from exc

        while True:
            try:
                msgs = await psub.fetch(batch, timeout=timeout)
            except Exception as exc:
                # fetch raises a TimeoutError when no messages arrived in the
                # window — that's normal for a stream, just poll again.
                if "timeout" in type(exc).__name__.lower():
                    continue
                raise ReadError(f"nats fetch failed: {exc}") from exc
            for msg in msgs:
                raw = msg.data or b""
                try:
                    data = json.loads(raw.decode("utf-8")) if raw else {}
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ReadError(f"nats message is not valid JSON: {exc}") from exc
                self._pending.append(msg)
                yield Record(
                    data=data,
                    metadata={
                        "source": "nats",
                        "subject": getattr(msg, "subject", topic),
                    },
                )

    async def commit(self, offsets: Any = None) -> None:
        """Ack everything fetched since the last commit (at-least-once)."""
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        for msg in pending:
            try:
                await msg.ack()
            except Exception as exc:
                raise ConnectError(f"nats commit (ack) failed: {exc}") from exc

    # ---------- StreamSink ------------------------------------------------

    async def publish(
        self,
        topic: str,
        record: Record,
        key: bytes | None = None,
    ) -> None:
        js = await self._ensure_js()
        body = json.dumps(record.data, ensure_ascii=False, default=str).encode("utf-8")
        try:
            await js.publish(topic, body)
        except Exception as exc:
            raise WriteError(f"nats publish failed: {exc}") from exc

    async def flush(self) -> None:
        """No-op: ``js.publish`` is awaited individually."""
        return None
