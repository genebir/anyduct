"""Contract test mixins for StreamSource / StreamSink connectors.

Async counterparts to ``tests/contracts/batch.py``. Same subclass-with-fixture
pattern; ``_``-prefixed class names keep pytest from collecting the bases.

The tests use ``asyncio.wait_for`` to bound their waits — stream consumers
inherently block forever, so every test must declare a timeout (defaults to
30 seconds, overridable via the ``consume_timeout`` fixture).
"""

from __future__ import annotations

import asyncio

import pytest

from etl_plugins.core.connector import StreamSink, StreamSource
from etl_plugins.core.record import Record
from tests.contracts._helpers import normalize_payloads


class _StreamSourceContract:
    """A StreamSource must yield previously-seeded messages as Records.

    Required fixtures (subclass):
      * ``stream_source`` — a ``StreamSource`` (call ``connect()`` yourself or rely on contract)
      * ``seeded_messages`` — list of Records already published to the source's topic
      * ``subscribe_kwargs`` — kwargs passed to ``subscribe()`` (must include ``topic``)

    Optional fixtures:
      * ``consume_timeout`` — float seconds (default 30.0)
    """

    @pytest.fixture
    def stream_source(self) -> StreamSource:
        raise NotImplementedError("subclass must provide a 'stream_source' fixture")

    @pytest.fixture
    def seeded_messages(self) -> list[Record]:
        raise NotImplementedError("subclass must provide a 'seeded_messages' fixture")

    @pytest.fixture
    def subscribe_kwargs(self) -> dict[str, object]:
        raise NotImplementedError("subclass must provide 'subscribe_kwargs' (at least 'topic')")

    @pytest.fixture
    def consume_timeout(self) -> float:
        return 30.0

    def test_is_a_streamsource(self, stream_source: StreamSource) -> None:
        assert isinstance(stream_source, StreamSource)

    async def test_subscribe_yields_seeded_messages(
        self,
        stream_source: StreamSource,
        seeded_messages: list[Record],
        subscribe_kwargs: dict[str, object],
        consume_timeout: float,
    ) -> None:
        stream_source.connect()
        collected: list[Record] = []

        async def _consume() -> None:
            async for record in stream_source.subscribe(**subscribe_kwargs):  # type: ignore[arg-type]
                collected.append(record)
                if len(collected) >= len(seeded_messages):
                    return

        try:
            await asyncio.wait_for(_consume(), timeout=consume_timeout)
        finally:
            if hasattr(stream_source, "aclose"):
                await stream_source.aclose()  # type: ignore[attr-defined]
            stream_source.close()

        assert all(isinstance(r, Record) for r in collected)
        assert len(collected) >= len(seeded_messages)
        assert normalize_payloads(collected[: len(seeded_messages)]) == normalize_payloads(
            seeded_messages
        )


class _StreamSinkContract:
    """A StreamSink must accept publish() then flush() without raising.

    Required fixtures (subclass):
      * ``stream_sink`` — a ``StreamSink`` instance
      * ``publish_topic`` — the topic to publish to
    """

    @pytest.fixture
    def stream_sink(self) -> StreamSink:
        raise NotImplementedError("subclass must provide a 'stream_sink' fixture")

    @pytest.fixture
    def publish_topic(self) -> str:
        raise NotImplementedError("subclass must provide a 'publish_topic' fixture")

    def test_is_a_streamsink(self, stream_sink: StreamSink) -> None:
        assert isinstance(stream_sink, StreamSink)

    async def test_publish_and_flush(
        self,
        stream_sink: StreamSink,
        sample_records: list[Record],
        publish_topic: str,
    ) -> None:
        stream_sink.connect()
        try:
            for r in sample_records:
                await stream_sink.publish(publish_topic, r)
            await stream_sink.flush()
        finally:
            if hasattr(stream_sink, "aclose"):
                await stream_sink.aclose()  # type: ignore[attr-defined]
            stream_sink.close()


class _StreamRoundTripContract:
    """Publish then subscribe round-trip on the same topic preserves payloads.

    Required fixtures (subclass):
      * ``stream_pair`` — tuple of (StreamSource, StreamSink); both can be the
        same connector if it implements both ABCs
      * ``round_trip_topic`` — topic to publish + subscribe on
      * ``round_trip_group_id`` — consumer group id (defaults to ``"etl-roundtrip"``)
    """

    @pytest.fixture
    def stream_pair(self) -> tuple[StreamSource, StreamSink]:
        raise NotImplementedError("subclass must provide a 'stream_pair' fixture")

    @pytest.fixture
    def round_trip_topic(self) -> str:
        raise NotImplementedError("subclass must provide a 'round_trip_topic' fixture")

    @pytest.fixture
    def round_trip_group_id(self) -> str:
        return "etl-roundtrip"

    @pytest.fixture
    def consume_timeout(self) -> float:
        return 30.0

    async def test_publish_then_subscribe_preserves_payloads(
        self,
        stream_pair: tuple[StreamSource, StreamSink],
        sample_records: list[Record],
        round_trip_topic: str,
        round_trip_group_id: str,
        consume_timeout: float,
    ) -> None:
        source, sink = stream_pair
        source.connect()
        sink.connect()
        collected: list[Record] = []

        try:
            # Publish first so the consumer reads existing offsets from the beginning.
            for r in sample_records:
                await sink.publish(round_trip_topic, r)
            await sink.flush()

            async def _consume() -> None:
                async for record in source.subscribe(
                    round_trip_topic, group_id=round_trip_group_id
                ):
                    collected.append(record)
                    if len(collected) >= len(sample_records):
                        return

            await asyncio.wait_for(_consume(), timeout=consume_timeout)
        finally:
            # Close both together at the very end. If source and sink alias the
            # same connector, aclose() is idempotent.
            if hasattr(sink, "aclose"):
                await sink.aclose()  # type: ignore[attr-defined]
            sink.close()
            if source is not sink:
                if hasattr(source, "aclose"):
                    await source.aclose()  # type: ignore[attr-defined]
                source.close()

        assert normalize_payloads(collected[: len(sample_records)]) == normalize_payloads(
            sample_records
        )
