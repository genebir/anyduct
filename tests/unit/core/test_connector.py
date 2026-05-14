"""Connector 추상 클래스 테스트 (인메모리 구현 통한 동작 검증)."""

from __future__ import annotations

import pytest

from etl_plugins.core.connector import (
    BatchSink,
    BatchSource,
    Connector,
    StreamSink,
    StreamSource,
)

from .conftest import InMemoryBatchSink, InMemoryBatchSource


def test_abstract_bases_cannot_be_instantiated() -> None:
    for cls in [Connector, BatchSource, BatchSink, StreamSource, StreamSink]:
        with pytest.raises(TypeError):
            cls()  # type: ignore[abstract]


def test_in_memory_source_is_a_batchsource() -> None:
    s = InMemoryBatchSource()
    assert isinstance(s, BatchSource)
    assert isinstance(s, Connector)


def test_context_manager_calls_connect_and_close() -> None:
    src = InMemoryBatchSource()
    assert src.health_check() is False
    with src as opened:
        assert opened is src
        assert src.health_check() is True
    assert src.health_check() is False


def test_context_manager_closes_on_exception() -> None:
    src = InMemoryBatchSource()
    with pytest.raises(RuntimeError), src:
        assert src.connected
        raise RuntimeError("boom")
    assert src.connected is False


def test_batch_source_read_passes_query_and_chunk_size() -> None:
    src = InMemoryBatchSource()
    list(src.read("SELECT 1", chunk_size=50))
    assert src.last_query == "SELECT 1"
    assert src.last_chunk_size == 50


def test_batch_sink_captures_mode_and_key_columns(sample_records) -> None:  # type: ignore[no-untyped-def]
    sink = InMemoryBatchSink()
    n = sink.write(iter(sample_records), mode="upsert", key_columns=["id"])
    assert n == 3
    assert sink.last_mode == "upsert"
    assert sink.last_key_columns == ["id"]
    assert [r.data["id"] for r in sink.records] == [1, 2, 3]
