"""Unit tests for the Arrow interchange plane (ADR-0093 Phase 2, P2a)."""

from __future__ import annotations

import pytest

from etl_plugins.core.arrow import (
    ArrowReadable,
    ArrowWritable,
    Partition,
    batches_to_records,
    records_to_batches,
)
from etl_plugins.core.record import Record


class TestAdapters:
    def test_round_trip_multi_chunk(self) -> None:
        recs = [Record(data={"id": i, "name": f"n{i}"}) for i in range(10)]
        batches = list(records_to_batches(iter(recs), batch_rows=3))
        assert len(batches) == 4  # 3+3+3+1
        assert [b.num_rows for b in batches] == [3, 3, 3, 1]
        out = list(batches_to_records(batches))
        assert [r.data for r in out] == [r.data for r in recs]

    def test_missing_keys_become_nulls_within_chunk(self) -> None:
        recs = [Record(data={"a": 1, "b": "x"}), Record(data={"a": 2})]
        out = list(batches_to_records(records_to_batches(iter(recs))))
        assert out[1].data == {"a": 2, "b": None}

    def test_empty_stream_yields_no_batches(self) -> None:
        assert list(records_to_batches(iter([]))) == []

    def test_metadata_does_not_survive(self) -> None:
        recs = [Record(data={"a": 1}, metadata={"offset": 7})]
        out = list(batches_to_records(records_to_batches(iter(recs))))
        assert out[0].metadata == {}

    def test_batch_rows_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            list(records_to_batches(iter([]), batch_rows=0))


class TestPartition:
    def test_partition_is_frozen_value_object(self) -> None:
        p = Partition("id", 100, 200)
        assert (p.column, p.lower, p.upper) == ("id", 100, 200)
        with pytest.raises(AttributeError):
            p.lower = 0  # type: ignore[misc]

    def test_unbounded_partition_defaults(self) -> None:
        p = Partition("id")
        assert p.lower is None and p.upper is None


class TestProtocols:
    def test_runtime_checkable_capability_detection(self) -> None:
        class _Reader:
            def read_arrow(self, *, query=None, partition=None, **options):  # type: ignore[no-untyped-def]
                yield from ()

        class _Writer:
            def write_arrow(
                self, batches, *, table=None, mode="append", key_columns=None, **options
            ):  # type: ignore[no-untyped-def]
                return 0

        assert isinstance(_Reader(), ArrowReadable)
        assert not isinstance(_Reader(), ArrowWritable)
        assert isinstance(_Writer(), ArrowWritable)
        assert not isinstance(_Writer(), ArrowReadable)
