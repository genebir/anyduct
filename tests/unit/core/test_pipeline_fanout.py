"""Fan-out (one source → many sinks) tests — ADR-0026."""

from __future__ import annotations

import pytest

from etl_plugins.core.exceptions import TaskError
from etl_plugins.core.pipeline import Pipeline, SinkSpec, Task
from etl_plugins.core.record import Record

from .conftest import InMemoryBatchSink, InMemoryBatchSource


def test_effective_sinks_single_legacy() -> None:
    t = Task.extract("src", "q").load("snk", table="T")
    specs = t.effective_sinks()
    assert [s.name for s in specs] == ["snk"]
    assert specs[0].table == "T"


def test_effective_sinks_prefers_sinks_list() -> None:
    t = Task(
        source="src",
        sink="ignored",
        sinks=[SinkSpec(name="a"), SinkSpec(name="b")],
    )
    assert [s.name for s in t.effective_sinks()] == ["a", "b"]


def test_fanout_writes_all_records_to_every_sink(
    sample_records: list[Record],
) -> None:
    src = InMemoryBatchSource(sample_records)
    s1 = InMemoryBatchSink()
    s2 = InMemoryBatchSink()
    task = Task(
        source="src",
        query="q",
        sinks=[SinkSpec(name="s1", table="T1"), SinkSpec(name="s2", table="T2")],
    )
    pipeline = Pipeline("fan").add(task)
    result = pipeline.run(connectors={"src": src, "s1": s1, "s2": s2})

    assert result.success is True
    # records_read counted once despite two read passes.
    assert result.records_read == len(sample_records)
    # records_written sums across sinks.
    assert result.records_written == 2 * len(sample_records)
    assert s1.records == sample_records
    assert s2.records == sample_records


def test_fanout_passes_per_sink_options(sample_records: list[Record]) -> None:
    src = InMemoryBatchSource(sample_records)
    s1 = InMemoryBatchSink()
    s2 = InMemoryBatchSink()
    task = Task(
        source="src",
        sinks=[
            SinkSpec(name="s1", mode="overwrite"),
            SinkSpec(name="s2", mode="upsert", key_columns=["id"]),
        ],
    )
    Pipeline("fan").add(task).run(connectors={"src": src, "s1": s1, "s2": s2})

    assert s1.last_mode == "overwrite"
    assert s2.last_mode == "upsert"
    assert s2.last_key_columns == ["id"]


def test_fanout_applies_transforms_per_sink(sample_records: list[Record]) -> None:
    def tag(r: Record) -> Record:
        return Record(data={**r.data, "tagged": True}, metadata=r.metadata)

    src = InMemoryBatchSource(sample_records)
    s1 = InMemoryBatchSink()
    s2 = InMemoryBatchSink()
    task = Task(source="src", sinks=[SinkSpec(name="s1"), SinkSpec(name="s2")])
    task.transform(tag)
    Pipeline("fan").add(task).run(connectors={"src": src, "s1": s1, "s2": s2})

    assert all(r.data["tagged"] for r in s1.records)
    assert all(r.data["tagged"] for r in s2.records)


def test_fanout_missing_sink_connector_raises(sample_records: list[Record]) -> None:
    src = InMemoryBatchSource(sample_records)
    s1 = InMemoryBatchSink()
    task = Task(source="src", sinks=[SinkSpec(name="s1"), SinkSpec(name="missing")])
    with pytest.raises(TaskError, match="missing"):
        Pipeline("fan").add(task).run(connectors={"src": src, "s1": s1})


def test_no_sink_raises(sample_records: list[Record]) -> None:
    src = InMemoryBatchSource(sample_records)
    task = Task(source="src")
    with pytest.raises(TaskError, match="missing sink"):
        Pipeline("fan").add(task).run(connectors={"src": src})


# ---------- conditional routing (ADR-0027) ----------


def _recs(*types: str) -> list[Record]:
    return [Record(data={"type": t, "n": i}) for i, t in enumerate(types)]


def test_routing_first_match_switch() -> None:
    src = InMemoryBatchSource(_recs("a", "b", "a", "c"))
    sa = InMemoryBatchSink()
    sb = InMemoryBatchSink()
    task = Task(
        source="src",
        sinks=[
            SinkSpec(name="sa", when="data['type'] == 'a'"),
            SinkSpec(name="sb", when="data['type'] == 'b'"),
        ],
    )
    result = Pipeline("r").add(task).run(connectors={"src": src, "sa": sa, "sb": sb})

    assert result.records_read == 4
    assert [r.data["type"] for r in sa.records] == ["a", "a"]
    assert [r.data["type"] for r in sb.records] == ["b"]
    # 'c' matched no branch and there's no default sink → dropped.
    assert result.records_written == 3


def test_routing_default_sink_catches_unmatched() -> None:
    src = InMemoryBatchSource(_recs("a", "b", "c"))
    sa = InMemoryBatchSink()
    rest = InMemoryBatchSink()
    task = Task(
        source="src",
        sinks=[
            SinkSpec(name="sa", when="data['type'] == 'a'"),
            SinkSpec(name="rest"),  # default — no `when`
        ],
    )
    Pipeline("r").add(task).run(connectors={"src": src, "sa": sa, "rest": rest})

    assert [r.data["type"] for r in sa.records] == ["a"]
    # default catches everything that didn't match a conditional branch.
    assert [r.data["type"] for r in rest.records] == ["b", "c"]


def test_routing_first_match_is_exclusive() -> None:
    """A record matching two predicates goes only to the first one."""
    src = InMemoryBatchSource(_recs("a"))
    first = InMemoryBatchSink()
    second = InMemoryBatchSink()
    task = Task(
        source="src",
        sinks=[
            SinkSpec(name="first", when="data['type'] == 'a'"),
            SinkSpec(name="second", when="'type' in data"),  # also true for 'a'
        ],
    )
    Pipeline("r").add(task).run(connectors={"src": src, "first": first, "second": second})

    assert len(first.records) == 1
    assert len(second.records) == 0


def test_routing_no_when_is_pure_fanout(sample_records: list[Record]) -> None:
    """Without any `when`, every sink still receives every record (ADR-0026)."""
    src = InMemoryBatchSource(sample_records)
    s1 = InMemoryBatchSink()
    s2 = InMemoryBatchSink()
    task = Task(source="src", sinks=[SinkSpec(name="s1"), SinkSpec(name="s2")])
    Pipeline("r").add(task).run(connectors={"src": src, "s1": s1, "s2": s2})

    assert s1.records == sample_records
    assert s2.records == sample_records


def test_routing_bad_when_syntax_raises() -> None:
    src = InMemoryBatchSource(_recs("a"))
    task = Task(source="src", sinks=[SinkSpec(name="s", when="data[")])
    with pytest.raises(TaskError, match="cannot compile routing"):
        Pipeline("r").add(task).run(connectors={"src": src, "s": InMemoryBatchSink()})


def test_routing_when_runtime_error_raises() -> None:
    from etl_plugins.core.exceptions import TransformError

    src = InMemoryBatchSource(_recs("a"))
    task = Task(source="src", sinks=[SinkSpec(name="s", when="data['missing'] > 1")])
    with pytest.raises(TransformError, match="routing 'when' failed"):
        Pipeline("r").add(task).run(connectors={"src": src, "s": InMemoryBatchSink()})
