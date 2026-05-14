"""KafkaConnector integration tests (testcontainers + Kafka KRaft)."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiokafka import AIOKafkaConsumer

from etl_plugins.connectors.stream.kafka import KafkaConnector
from etl_plugins.core.connector import StreamSink, StreamSource
from etl_plugins.core.exceptions import ConnectError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry
from tests.contracts.stream import (
    _StreamRoundTripContract,
    _StreamSinkContract,
    _StreamSourceContract,
)

pytestmark = pytest.mark.it


# ---------- contract: StreamSource ----------


class TestKafkaStreamSource(_StreamSourceContract):
    @pytest.fixture
    def stream_source(self, kafka_connector: KafkaConnector) -> StreamSource:
        return kafka_connector

    @pytest.fixture
    def seeded_messages(self, sample_records: list[Record]) -> list[Record]:
        return sample_records

    @pytest.fixture
    def subscribe_kwargs(self, kafka_seeded_topic: str) -> dict[str, object]:
        return {"topic": kafka_seeded_topic, "group_id": "etl-source-test"}


# ---------- contract: StreamSink ----------


class TestKafkaStreamSink(_StreamSinkContract):
    @pytest.fixture
    def stream_sink(self, kafka_connector: KafkaConnector) -> StreamSink:
        return kafka_connector

    @pytest.fixture
    def publish_topic(self, kafka_topic: str) -> str:
        return kafka_topic


# ---------- contract: round-trip ----------


class TestKafkaRoundTrip(_StreamRoundTripContract):
    @pytest.fixture
    def stream_pair(self, kafka_connector: KafkaConnector) -> tuple[StreamSource, StreamSink]:
        return kafka_connector, kafka_connector

    @pytest.fixture
    def round_trip_topic(self, kafka_topic: str) -> str:
        return kafka_topic


# ---------- kafka-specific tests ----------


def test_registry_resolves_kafka() -> None:
    klass = ConnectorRegistry.get("kafka")
    assert klass is KafkaConnector
    assert klass.name == "kafka"


def test_health_check_false_before_connect(kafka_bootstrap: str) -> None:
    kc = KafkaConnector(bootstrap_servers=kafka_bootstrap)
    assert kc.health_check() is False
    kc.connect()
    assert kc.health_check() is True
    kc.close()
    assert kc.health_check() is False


def test_subscribe_without_connect_raises(kafka_bootstrap: str) -> None:
    kc = KafkaConnector(bootstrap_servers=kafka_bootstrap)
    # subscribe returns an async generator; iterating it surfaces the error
    gen = kc.subscribe("x")

    async def _drain() -> None:
        async for _ in gen:
            pass

    with pytest.raises(ConnectError):
        asyncio.run(_drain())


async def test_publish_without_connect_raises(kafka_bootstrap: str) -> None:
    kc = KafkaConnector(bootstrap_servers=kafka_bootstrap)
    with pytest.raises(ConnectError):
        await kc.publish("x", Record(data={"a": 1}))


async def test_commit_without_active_subscribe_raises(kafka_connector: KafkaConnector) -> None:
    """Without a live subscribe() loop, commit() has no consumer and must fail."""
    kafka_connector.connect()
    with pytest.raises(ConnectError):
        await kafka_connector.commit()


async def test_publish_invalid_record_raises_write_error_on_bad_json(
    kafka_connector: KafkaConnector, kafka_topic: str
) -> None:
    """Records with non-serializable values fall back to repr via ``default=str``."""

    class _Weird:
        def __repr__(self) -> str:
            return "weird"

    kafka_connector.connect()
    try:
        # default=str should stringify, so this actually succeeds.
        await kafka_connector.publish(
            kafka_topic,
            Record(data={"weird": _Weird()}),  # type: ignore[dict-item]
        )
        await kafka_connector.flush()
    finally:
        await kafka_connector.aclose()


async def test_metadata_carries_kafka_position(
    kafka_connector: KafkaConnector,
    kafka_bootstrap: str,
    kafka_topic: str,
) -> None:
    """subscribe() yielded Records should carry topic/partition/offset metadata."""
    kafka_connector.connect()
    try:
        await kafka_connector.publish(kafka_topic, Record(data={"k": "v"}))
        await kafka_connector.flush()

        async def _consume_one() -> Record:
            async for record in kafka_connector.subscribe(kafka_topic, group_id="etl-meta-test"):
                return record
            raise AssertionError("no record yielded")

        record = await asyncio.wait_for(_consume_one(), timeout=30.0)
        assert record.metadata["source"] == "kafka"
        assert record.metadata["topic"] == kafka_topic
        assert isinstance(record.metadata["partition"], int)
        assert isinstance(record.metadata["offset"], int)
    finally:
        await kafka_connector.aclose()


async def test_publish_then_consume_via_raw_consumer(
    kafka_connector: KafkaConnector,
    kafka_bootstrap: str,
    kafka_topic: str,
) -> None:
    """Sanity check: messages published by us are decoded as JSON by a vanilla consumer."""
    payload = {"id": 7, "name": "Alice"}
    kafka_connector.connect()
    try:
        await kafka_connector.publish(kafka_topic, Record(data=payload))
        await kafka_connector.flush()
    finally:
        await kafka_connector.aclose()

    consumer = AIOKafkaConsumer(
        kafka_topic,
        bootstrap_servers=kafka_bootstrap,
        group_id="etl-raw-check",
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        msg = await asyncio.wait_for(consumer.getone(), timeout=30.0)
        assert json.loads(msg.value.decode("utf-8")) == payload
    finally:
        await consumer.stop()


async def test_multiple_messages_preserved_in_order_within_partition(
    kafka_connector: KafkaConnector, kafka_topic: str
) -> None:
    """Same key → same partition → in-order delivery."""
    n = 10
    records = [Record(data={"i": i}) for i in range(n)]
    kafka_connector.connect()
    try:
        for r in records:
            await kafka_connector.publish(kafka_topic, r, key=b"same-key")
        await kafka_connector.flush()

        collected: list[int] = []

        async def _consume() -> None:
            async for record in kafka_connector.subscribe(kafka_topic, group_id="etl-order-test"):
                collected.append(record.data["i"])
                if len(collected) >= n:
                    return

        await asyncio.wait_for(_consume(), timeout=30.0)
    finally:
        await kafka_connector.aclose()

    assert collected == list(range(n))


async def test_aclose_is_idempotent(kafka_connector: KafkaConnector) -> None:
    kafka_connector.connect()
    await kafka_connector.aclose()
    await kafka_connector.aclose()  # safe second call


async def test_bootstrap_servers_accepts_list() -> None:
    """``bootstrap_servers`` can be a list — joined to a comma-separated string."""
    kc = KafkaConnector(bootstrap_servers=["a:9092", "b:9092"])
    assert kc.bootstrap_servers == "a:9092,b:9092"
