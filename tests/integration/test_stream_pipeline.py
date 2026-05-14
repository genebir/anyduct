"""End-to-end stream pipeline tests against a real Kafka container [Step 3.2]."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from aiokafka import AIOKafkaConsumer

from etl_plugins.connectors.stream.kafka import KafkaConnector
from etl_plugins.core.pipeline import Pipeline, Task
from etl_plugins.core.record import Record

pytestmark = pytest.mark.it


async def _seed_topic(bootstrap: str, topic: str, records: list[Record]) -> None:
    from aiokafka import AIOKafkaProducer

    producer = AIOKafkaProducer(bootstrap_servers=bootstrap)
    await producer.start()
    try:
        for r in records:
            await producer.send_and_wait(topic, value=json.dumps(r.data).encode("utf-8"))
    finally:
        await producer.stop()


async def _drain_topic(
    bootstrap: str, topic: str, *, expected: int, timeout: float = 30.0
) -> list[dict]:
    """Read at least ``expected`` messages from ``topic`` then stop."""
    consumer = AIOKafkaConsumer(
        topic,
        bootstrap_servers=bootstrap,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        group_id=f"drain-{uuid4().hex[:8]}",
    )
    await consumer.start()
    out: list[dict] = []
    try:

        async def _collect() -> None:
            async for msg in consumer:
                out.append(json.loads(msg.value.decode("utf-8")))
                if len(out) >= expected:
                    return

        await asyncio.wait_for(_collect(), timeout=timeout)
    finally:
        await consumer.stop()
    return out


async def test_stream_pipeline_kafka_to_kafka_with_transform(
    kafka_bootstrap: str, sample_records: list[Record]
) -> None:
    """Seed topic_in → Pipeline.arun_stream renames a field → topic_out.

    Verifies the full Step 3.2 stream runtime over a real Kafka instance:
    subscribe → transform → publish → flush → commit.
    """
    topic_in = f"in-{uuid4().hex[:8]}"
    topic_out = f"out-{uuid4().hex[:8]}"

    await _seed_topic(kafka_bootstrap, topic_in, sample_records)

    def _rename_name_to_full_name(record: Record) -> Record:
        data = dict(record.data)
        if "name" in data:
            data["full_name"] = data.pop("name")
        return Record(data=data, metadata=record.metadata)

    src = KafkaConnector(bootstrap_servers=kafka_bootstrap)
    snk = KafkaConnector(bootstrap_servers=kafka_bootstrap)

    task = Task(
        name="s2s",
        source="src",
        source_options={"topic": topic_in, "group_id": f"g-{uuid4().hex[:8]}"},
        sink="snk",
        sink_options={"topic": topic_out, "buffer": {"max_records": 1}},
        transforms=[_rename_name_to_full_name],
    )
    p = Pipeline("kafka-stream-test", mode="stream").add(task)

    src.connect()
    snk.connect()
    try:
        result = await p.arun_stream(
            connectors={"src": src, "snk": snk},
            stop_after_records=len(sample_records),
        )
    finally:
        await src.aclose()
        await snk.aclose()

    assert result.success is True
    assert result.records_read == len(sample_records)
    assert result.records_written == len(sample_records)

    drained = await _drain_topic(kafka_bootstrap, topic_out, expected=len(sample_records))
    assert len(drained) == len(sample_records)
    for d in drained:
        assert "full_name" in d
        assert "name" not in d


async def test_stream_pipeline_commits_offsets(
    kafka_bootstrap: str, sample_records: list[Record]
) -> None:
    """After arun_stream completes with default commit_strategy, a fresh
    consumer in the same group must NOT see the already-committed messages."""
    topic_in = f"in-{uuid4().hex[:8]}"
    topic_out = f"out-{uuid4().hex[:8]}"
    group_id = f"g-{uuid4().hex[:8]}"

    await _seed_topic(kafka_bootstrap, topic_in, sample_records)

    src = KafkaConnector(bootstrap_servers=kafka_bootstrap)
    snk = KafkaConnector(bootstrap_servers=kafka_bootstrap)
    task = Task(
        name="commit-check",
        source="src",
        source_options={"topic": topic_in, "group_id": group_id},
        sink="snk",
        sink_options={"topic": topic_out, "buffer": {"max_records": 1}},
    )
    p = Pipeline("kafka-stream-commit", mode="stream").add(task)

    src.connect()
    snk.connect()
    try:
        await p.arun_stream(
            connectors={"src": src, "snk": snk},
            stop_after_records=len(sample_records),
        )
    finally:
        await src.aclose()
        await snk.aclose()

    # Fresh consumer in the same group, no earliest reset — should see nothing
    # because the previous run committed.
    follow_up = AIOKafkaConsumer(
        topic_in,
        bootstrap_servers=kafka_bootstrap,
        group_id=group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
    )
    await follow_up.start()
    try:
        try:
            msg = await asyncio.wait_for(follow_up.getone(), timeout=3.0)
        except TimeoutError:
            msg = None
        assert msg is None, f"expected no uncommitted messages, got {msg!r}"
    finally:
        await follow_up.stop()
