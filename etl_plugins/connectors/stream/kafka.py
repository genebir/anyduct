"""Kafka connector — StreamSource + StreamSink. SPEC.md §4.1 / §6.

Built on aiokafka (native asyncio) so ``subscribe()`` is a real async generator.

Optional dependency::

    pip install 'etl-plugins[kafka]'

Lifecycle notes
---------------
The sync ``connect()`` / ``close()`` are flag-only — actual Kafka clients
(``AIOKafkaConsumer`` / ``AIOKafkaProducer``) are started lazily on first
``subscribe()`` / ``publish()`` and stopped via:

* ``subscribe()`` is an async generator — its ``finally`` clause stops the
  consumer when the iteration ends or is cancelled.
* ``publish()`` keeps a single producer alive for the connector's lifetime.
  Call ``await connector.aclose()`` to stop it cleanly.

For sync-only callers there's an ``aclose()`` shim that schedules an
``asyncio.run`` on the running loop if needed.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.errors import KafkaError

from etl_plugins.core.connector import StreamSink, StreamSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


@ConnectorRegistry.register("kafka")
class KafkaConnector(StreamSource, StreamSink):
    """Kafka stream source + sink (aiokafka backed)."""

    def __init__(
        self,
        bootstrap_servers: str | list[str] = "localhost:9092",
        *,
        client_id: str = "etl-plugins",
        # SASL/SSL (placeholders — Step 5에서 확장)
        security_protocol: str = "PLAINTEXT",
        sasl_mechanism: str | None = None,
        sasl_username: str | None = None,
        sasl_password: str | None = None,
        # Producer/consumer defaults
        compression_type: str | None = None,  # None | "gzip" | "snappy" | "lz4" | "zstd"
        acks: str | int = "all",
        **extra: Any,
    ) -> None:
        if isinstance(bootstrap_servers, list):
            bootstrap_servers = ",".join(bootstrap_servers)
        self.bootstrap_servers = bootstrap_servers
        self.client_id = client_id
        self.security_protocol = security_protocol
        self.sasl_mechanism = sasl_mechanism
        self.sasl_username = sasl_username
        self.sasl_password = sasl_password
        self.compression_type = compression_type
        self.acks = acks
        self._extra: dict[str, Any] = extra
        self._producer: AIOKafkaProducer | None = None
        self._connected = False

    # ---------- sync lifecycle (flag-only) ---------------------------------

    def connect(self) -> None:
        """Mark the connector as logically connected. Real clients are lazy."""
        self._connected = True

    def close(self) -> None:
        """Mark as closed. For proper async cleanup call ``aclose()`` instead."""
        self._connected = False

    def health_check(self) -> bool:
        return self._connected

    # ---------- async lifecycle -------------------------------------------

    async def aclose(self) -> None:
        """Stop the cached producer (and any cached consumer). Idempotent."""
        if self._producer is not None:
            try:
                await self._producer.stop()
            finally:
                self._producer = None
        self._connected = False

    # ---------- StreamSource ----------------------------------------------

    async def subscribe(
        self,
        topic: str,
        *,
        group_id: str | None = None,
        **options: Any,
    ) -> AsyncIterator[Record]:
        """Subscribe to ``topic`` and yield Records.

        Each consumed message:
          * value is JSON-decoded into ``Record.data``
          * key / partition / offset / topic land in ``Record.metadata``

        ``auto.offset.reset`` defaults to ``"earliest"`` for predictable replay
        in tests; override via ``options['auto_offset_reset']``.

        Auto-commit is disabled — at-least-once delivery requires the caller
        (or the Pipeline runtime, Step 3) to commit after sink flush.
        """
        if not self._connected:
            raise ConnectError("KafkaConnector is not connected — call connect() first")

        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=self.bootstrap_servers,
            client_id=self.client_id,
            group_id=group_id,
            security_protocol=self.security_protocol,
            sasl_mechanism=self.sasl_mechanism,
            sasl_plain_username=self.sasl_username,
            sasl_plain_password=self.sasl_password,
            auto_offset_reset=str(options.get("auto_offset_reset", "earliest")),
            enable_auto_commit=False,
        )
        try:
            await consumer.start()
        except KafkaError as exc:
            raise ConnectError(f"kafka subscribe (consumer start) failed: {exc}") from exc

        try:
            async for msg in consumer:
                try:
                    data = json.loads(msg.value.decode("utf-8")) if msg.value else {}
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ReadError(
                        f"kafka message at {msg.topic}@{msg.partition}:{msg.offset} "
                        f"is not valid JSON: {exc}"
                    ) from exc
                yield Record(
                    data=data,
                    metadata={
                        "source": "kafka",
                        "topic": msg.topic,
                        "partition": msg.partition,
                        "offset": msg.offset,
                        "key": msg.key.decode("utf-8") if msg.key else None,
                        "timestamp": msg.timestamp,
                    },
                )
        finally:
            await consumer.stop()

    def commit(self, offsets: Any) -> None:
        """Not implemented in Step 2.3 — see Step 3 retry+observability integration."""
        raise NotImplementedError(
            "Explicit offset commit arrives with the Pipeline runtime in Step 3."
        )

    # ---------- StreamSink ------------------------------------------------

    async def _ensure_producer(self) -> AIOKafkaProducer:
        if self._producer is not None:
            return self._producer
        producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            client_id=self.client_id,
            security_protocol=self.security_protocol,
            sasl_mechanism=self.sasl_mechanism,
            sasl_plain_username=self.sasl_username,
            sasl_plain_password=self.sasl_password,
            compression_type=self.compression_type,
            acks=self.acks,
        )
        try:
            await producer.start()
        except KafkaError as exc:
            raise ConnectError(f"kafka producer start failed: {exc}") from exc
        self._producer = producer
        return producer

    async def publish(
        self,
        topic: str,
        record: Record,
        key: bytes | None = None,
    ) -> None:
        if not self._connected:
            raise ConnectError("KafkaConnector is not connected — call connect() first")
        producer = await self._ensure_producer()
        try:
            value = json.dumps(record.data, ensure_ascii=False, default=str).encode("utf-8")
            await producer.send_and_wait(topic, value=value, key=key)
        except KafkaError as exc:
            raise WriteError(f"kafka publish failed: {exc}") from exc

    async def flush(self) -> None:
        if self._producer is not None:
            await self._producer.flush()
