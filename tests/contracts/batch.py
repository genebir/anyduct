"""Contract test mixins for Batch connectors.

A connector test inherits from one or more of these and provides the
required fixtures::

    class TestMySource(_BatchSourceContract):
        @pytest.fixture
        def source(self) -> BatchSource:
            return MySource(...)

        @pytest.fixture
        def seeded_records(self) -> list[Record]:
            return sample_records()

Pytest will discover the ``Test...`` class and run every inherited
``test_...`` method against the subclass's fixtures.

The classes here start with ``_`` so pytest doesn't try to collect them
on their own.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from etl_plugins.core.connector import BatchSink, BatchSource, Connector
from etl_plugins.core.record import Record
from tests.contracts._helpers import normalize_payloads


class _BatchSourceContract:
    """Every BatchSource must satisfy these tests.

    Required fixtures (subclass):
      * ``source`` — an unconnected :class:`BatchSource` instance
      * ``seeded_records`` — the records ``source.read()`` will yield once connected
    """

    # ---------- abstract fixtures (subclass overrides) -----------------

    @pytest.fixture
    def source(self) -> BatchSource:
        raise NotImplementedError("subclass must provide a 'source' fixture")

    @pytest.fixture
    def seeded_records(self) -> list[Record]:
        raise NotImplementedError("subclass must provide a 'seeded_records' fixture")

    # ---------- contract -----------------------------------------------

    def test_is_a_batchsource(self, source: BatchSource) -> None:
        assert isinstance(source, BatchSource)
        assert isinstance(source, Connector)

    def test_health_check_lifecycle(self, source: BatchSource) -> None:
        assert source.health_check() is False
        source.connect()
        assert source.health_check() is True
        source.close()
        assert source.health_check() is False

    def test_context_manager_lifecycle(self, source: BatchSource) -> None:
        with source as opened:
            assert opened is source
            assert source.health_check() is True
        assert source.health_check() is False

    def test_read_returns_iterator(self, source: BatchSource, seeded_records: list[Record]) -> None:
        with source:
            result = source.read()
            assert isinstance(result, Iterator)

    def test_read_yields_records(self, source: BatchSource, seeded_records: list[Record]) -> None:
        with source:
            result = list(source.read())
        assert all(isinstance(r, Record) for r in result)

    def test_read_returns_seeded_data(
        self, source: BatchSource, seeded_records: list[Record]
    ) -> None:
        with source:
            result = list(source.read())
        assert normalize_payloads(result) == normalize_payloads(seeded_records)

    def test_read_is_consumable_multiple_times(
        self, source: BatchSource, seeded_records: list[Record]
    ) -> None:
        with source:
            first = list(source.read())
            second = list(source.read())
        assert normalize_payloads(first) == normalize_payloads(second)


class _BatchSinkContract:
    """Every BatchSink must satisfy these tests.

    Required fixtures (subclass):
      * ``sink`` — an unconnected :class:`BatchSink` instance (mutated by tests)
      * ``records`` — records to write (defaults to ``sample_records`` via global fixture)
    """

    @pytest.fixture
    def sink(self) -> BatchSink:
        raise NotImplementedError("subclass must provide a 'sink' fixture")

    def test_is_a_batchsink(self, sink: BatchSink) -> None:
        assert isinstance(sink, BatchSink)
        assert isinstance(sink, Connector)

    def test_health_check_lifecycle(self, sink: BatchSink) -> None:
        assert sink.health_check() is False
        sink.connect()
        assert sink.health_check() is True
        sink.close()
        assert sink.health_check() is False

    def test_write_returns_int_count(self, sink: BatchSink, sample_records: list[Record]) -> None:
        with sink:
            n = sink.write(iter(sample_records))
        assert isinstance(n, int)
        assert n == len(sample_records)

    def test_write_empty_returns_zero(self, sink: BatchSink) -> None:
        with sink:
            n = sink.write(iter([]))
        assert n == 0

    def test_write_iterates_generator(self, sink: BatchSink) -> None:
        # 일부 sink가 list 전체를 한 번에 받는다고 가정하지 않도록
        def gen() -> Iterator[Record]:
            for i in range(5):
                yield Record(data={"id": i})

        with sink:
            n = sink.write(gen())
        assert n == 5


class _BatchRoundTripContract:
    """A connector that is both BatchSource and BatchSink must preserve data
    on a write-then-read round trip.

    Required fixture (subclass):
      * ``round_trip_connector`` — instance implementing both BatchSource and BatchSink,
        backed by a single shared store
    """

    @pytest.fixture
    def round_trip_connector(self) -> BatchSource:
        raise NotImplementedError(
            "subclass must provide a 'round_trip_connector' fixture "
            "(instance implementing both BatchSource and BatchSink)"
        )

    def test_round_trip_is_both_source_and_sink(self, round_trip_connector: BatchSource) -> None:
        assert isinstance(round_trip_connector, BatchSource)
        assert isinstance(round_trip_connector, BatchSink)

    def test_round_trip_preserves_count(
        self, round_trip_connector: BatchSource, sample_records: list[Record]
    ) -> None:
        c = round_trip_connector
        assert isinstance(c, BatchSink)
        with c:
            n = c.write(iter(sample_records))
            read_back = list(c.read())
        assert n == len(sample_records)
        assert len(read_back) == len(sample_records)

    def test_round_trip_preserves_payloads(
        self, round_trip_connector: BatchSource, sample_records: list[Record]
    ) -> None:
        c = round_trip_connector
        assert isinstance(c, BatchSink)
        with c:
            c.write(iter(sample_records))
            read_back = list(c.read())
        assert normalize_payloads(read_back) == normalize_payloads(sample_records)
