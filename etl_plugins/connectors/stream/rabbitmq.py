"""RabbitMQ connector — StreamSource + StreamSink (Phase AGT, ADR-0086).

Built on aio-pika (native asyncio AMQP client) so the StreamSource/
StreamSink async methods need no thread offload. Optional dependency::

    pip install 'etl-plugins[rabbitmq]'

* **subscribe** — declares a durable queue and consumes via the async
  iterator, JSON-decoding each message body. Messages are held pending
  (un-acked) until ``commit``.
* **publish** — declares the queue (idempotent) and publishes a
  persistent message to the default exchange keyed by queue name.
* **commit** — ``message.ack()`` for everything read since the last
  commit (AMQP's at-least-once acknowledgement, like SQS's delete).
* **flush** — no-op.

Lifecycle mirrors the Kafka connector: sync ``connect()`` is flag-only;
the real AMQP connection/channel are opened lazily on first use and
closed via ``aclose()``. ``topic`` is the queue name. Imported lazily.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from etl_plugins.core.connector import StreamSink, StreamSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


@ConnectorRegistry.register("rabbitmq")
class RabbitMQConnector(StreamSource, StreamSink):
    """RabbitMQ stream source + sink (aio-pika backed)."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5672,
        *,
        username: str = "guest",
        password: str = "guest",
        virtual_host: str = "/",
        **extra: Any,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.virtual_host = virtual_host
        self._extra: dict[str, Any] = extra
        self._conn: Any = None
        self._channel: Any = None
        self._connected = False
        # Messages read since the last commit (held for ack).
        self._pending: list[Any] = []

    # ---------- sync lifecycle (flag-only) ---------------------------------

    def connect(self) -> None:
        self._connected = True

    def close(self) -> None:
        self._connected = False

    def health_check(self) -> bool:
        return self._connected

    async def aclose(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            finally:
                self._conn = None
                self._channel = None
        self._connected = False

    # ---------- async connection (lazy) ------------------------------------

    async def _ensure_channel(self) -> Any:
        if self._channel is not None:
            return self._channel
        if not self._connected:
            raise ConnectError("RabbitMQConnector is not connected — call connect() first")
        try:
            import aio_pika
        except ImportError as exc:  # pragma: no cover - import side effect
            raise ConnectError(
                "aio-pika not installed. Install with: pip install 'etl-plugins[rabbitmq]'"
            ) from exc
        try:
            self._conn = await aio_pika.connect_robust(
                host=self.host,
                port=self.port,
                login=self.username,
                password=self.password,
                virtualhost=self.virtual_host,
                **self._extra,
            )
            self._channel = await self._conn.channel()
        except Exception as exc:  # aio_pika / aiormq errors are broad
            raise ConnectError(f"rabbitmq connect failed: {exc}") from exc
        return self._channel

    # ---------- StreamSource ----------------------------------------------

    async def subscribe(
        self,
        topic: str,
        *,
        group_id: str | None = None,
        **options: Any,
    ) -> AsyncIterator[Record]:
        channel = await self._ensure_channel()
        try:
            queue = await channel.declare_queue(topic, durable=True)
        except Exception as exc:
            raise ConnectError(f"rabbitmq subscribe (declare queue) failed: {exc}") from exc
        async with queue.iterator() as it:
            async for message in it:
                body = message.body or b""
                try:
                    data = json.loads(body.decode("utf-8")) if body else {}
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ReadError(f"rabbitmq message is not valid JSON: {exc}") from exc
                self._pending.append(message)
                yield Record(
                    data=data,
                    metadata={
                        "source": "rabbitmq",
                        "queue": topic,
                        "delivery_tag": getattr(message, "delivery_tag", None),
                    },
                )

    async def commit(self, offsets: Any = None) -> None:
        """Ack everything read since the last commit (at-least-once)."""
        if not self._pending:
            return
        pending, self._pending = self._pending, []
        for message in pending:
            try:
                await message.ack()
            except Exception as exc:
                raise ConnectError(f"rabbitmq commit (ack) failed: {exc}") from exc

    # ---------- StreamSink ------------------------------------------------

    async def publish(
        self,
        topic: str,
        record: Record,
        key: bytes | None = None,
    ) -> None:
        channel = await self._ensure_channel()
        try:
            import aio_pika

            await channel.declare_queue(topic, durable=True)
            body = json.dumps(record.data, ensure_ascii=False, default=str).encode("utf-8")
            message = aio_pika.Message(body=body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT)
            await channel.default_exchange.publish(message, routing_key=topic)
        except Exception as exc:
            raise WriteError(f"rabbitmq publish failed: {exc}") from exc

    async def flush(self) -> None:
        """No-op: publishes are awaited individually."""
        return None
