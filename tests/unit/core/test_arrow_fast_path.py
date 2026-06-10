"""Unit tests for the Arrow bulk fast path in ``Pipeline._run_task``
(ADR-0093 P2b) — eligibility routing with fake Arrow-capable connectors."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import pyarrow as pa

from etl_plugins.config.models import TransformConfig
from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.pipeline import Pipeline, SinkSpec, Task
from etl_plugins.core.record import Record
from etl_plugins.runtime.transforms import build_transform

_ROWS = [{"id": i, "name": f"n{i}"} for i in range(7)]


class FakeArrowSource(BatchSource):
    """BatchSource that also speaks Arrow; records which path was used."""

    def __init__(self) -> None:
        self.read_arrow_called = False
        self.read_called = False

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def health_check(self) -> bool:
        return True

    def read(self, query: str | None = None, **options: Any) -> Iterator[Record]:
        self.read_called = True
        for r in _ROWS:
            yield Record(data=dict(r))

    def read_arrow(
        self, *, query: str | None = None, partition: Any = None, **options: Any
    ) -> Iterator[pa.RecordBatch]:
        self.read_arrow_called = True
        yield from pa.Table.from_pylist(_ROWS).to_batches(max_chunksize=3)


class FakeArrowSink(BatchSink):
    """BatchSink that also speaks Arrow; captures whatever arrives."""

    def __init__(self) -> None:
        self.write_arrow_called = False
        self.write_called = False
        self.rows: list[dict[str, Any]] = []

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def health_check(self) -> bool:
        return True

    def write(self, records: Iterable[Record], **options: Any) -> int:
        self.write_called = True
        rows = [r.data for r in records]
        self.rows.extend(rows)
        return len(rows)

    def write_arrow(
        self,
        batches: Iterable[pa.RecordBatch],
        *,
        table: str | None = None,
        mode: str = "append",
        key_columns: list[str] | None = None,
        **options: Any,
    ) -> int:
        self.write_arrow_called = True
        n = 0
        for b in batches:
            self.rows.extend(b.to_pylist())
            n += b.num_rows
        return n


class FakePlainSink(BatchSink):
    """Record-only sink (no Arrow capability)."""

    def __init__(self) -> None:
        self.write_called = False

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def health_check(self) -> bool:
        return True

    def write(self, records: Iterable[Record], **options: Any) -> int:
        self.write_called = True
        return len(list(records))


def _run(task: Task, source: BatchSource, sink: BatchSink) -> Any:
    return Pipeline(name="p", tasks=[task]).run(connectors={"src": source, "dst": sink})


def test_fast_path_engages_and_counts() -> None:
    src, dst = FakeArrowSource(), FakeArrowSink()
    task = Task(name="t", source="src", sink="dst", query="q", sink_table="out")
    result = _run(task, src, dst)
    assert src.read_arrow_called and dst.write_arrow_called
    assert not src.read_called and not dst.write_called
    assert result.records_read == 7
    assert result.records_written == 7
    assert dst.rows == _ROWS


def test_transforms_disable_fast_path() -> None:
    src, dst = FakeArrowSource(), FakeArrowSink()
    task = Task(
        name="t",
        source="src",
        sink="dst",
        query="q",
        sink_table="out",
        transforms=[build_transform(TransformConfig(type="rename", mapping={"name": "label"}))],
    )
    _run(task, src, dst)
    assert src.read_called and dst.write_called
    assert not src.read_arrow_called and not dst.write_arrow_called


def test_upsert_mode_disables_fast_path() -> None:
    src, dst = FakeArrowSource(), FakeArrowSink()
    task = Task(
        name="t",
        source="src",
        sink="dst",
        query="q",
        sink_table="out",
        sink_mode="upsert",
        sink_key_columns=["id"],
    )
    _run(task, src, dst)
    assert dst.write_called and not dst.write_arrow_called


def test_fan_out_disables_fast_path() -> None:
    src, a, b = FakeArrowSource(), FakeArrowSink(), FakeArrowSink()
    task = Task(
        name="t",
        source="src",
        query="q",
        sinks=[SinkSpec(name="a", table="out"), SinkSpec(name="b", table="out")],
    )
    Pipeline(name="p", tasks=[task]).run(connectors={"src": src, "a": a, "b": b})
    assert a.write_called and b.write_called
    assert not a.write_arrow_called and not b.write_arrow_called


def test_when_routing_disables_fast_path() -> None:
    src, dst = FakeArrowSource(), FakeArrowSink()
    task = Task(
        name="t",
        source="src",
        query="q",
        sinks=[SinkSpec(name="dst", table="out", when="data['id'] > 3")],
    )
    Pipeline(name="p", tasks=[task]).run(connectors={"src": src, "dst": dst})
    assert dst.write_called and not dst.write_arrow_called


def test_record_only_sink_uses_record_path() -> None:
    src, dst = FakeArrowSource(), FakePlainSink()
    task = Task(name="t", source="src", sink="dst", query="q", sink_table="out")
    _run(task, src, dst)
    assert src.read_called and dst.write_called
    assert not src.read_arrow_called
