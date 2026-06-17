"""Task-to-task value passing — XCom (ADR-0097, 자유도 3단계).

A downstream task pulls an upstream task's published summary via
``{{ xcom.<task>.<key> }}``, resolved per task at execution time (after the
upstream has run). Slice 1 = auto-xcom (records_read / records_written /
success / new_cursor); explicit push is a follow-up.
"""

from __future__ import annotations

import pytest

from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.pipeline import Pipeline, Task
from etl_plugins.core.record import Record
from tests.fixtures.connectors import InMemoryBatchSink, InMemoryBatchSource


def _connect(*conns: object) -> None:
    for c in conns:
        c.connect()  # type: ignore[attr-defined]


def test_downstream_pulls_upstream_records_written_into_query() -> None:
    """B.query references {{ xcom.A.records_written }} — A writes 3, so B's
    source sees the rendered '3'."""
    sa = InMemoryBatchSource([Record(data={"i": i}) for i in range(3)])
    ka = InMemoryBatchSink()
    sb = InMemoryBatchSource([Record(data={"x": 1})])
    kb = InMemoryBatchSink()

    a = Task(name="A", source="sa", sink="ka")
    b = Task(
        name="B",
        source="sb",
        query="SELECT * FROM t WHERE n = {{ xcom.A.records_written }}",
        sink="kb",
        depends_on=["A"],
    )
    p = Pipeline("p").add(a).add(b)
    _connect(sa, ka, sb, kb)
    result = p.run(connectors={"sa": sa, "ka": ka, "sb": sb, "kb": kb})

    assert result.success is True
    assert sb.last_query == "SELECT * FROM t WHERE n = 3"
    # Store carries the published summary too.
    assert p._xcom["A"]["records_written"] == 3
    assert p._xcom["A"]["records_read"] == 3
    assert p._xcom["A"]["success"] is True


def test_xcom_into_sink_table_name() -> None:
    """XCom can parametrize a downstream sink table."""
    sa = InMemoryBatchSource([Record(data={"i": i}) for i in range(2)])
    ka = InMemoryBatchSink()
    sb = InMemoryBatchSource([Record(data={"x": 1})])
    kb = InMemoryBatchSink()

    a = Task(name="A", source="sa", sink="ka")
    b = Task(
        name="B",
        source="sb",
        sink="kb",
        sink_table="out_{{ xcom.A.records_read }}",
        depends_on=["A"],
    )
    p = Pipeline("p").add(a).add(b)
    _connect(sa, ka, sb, kb)
    p.run(connectors={"sa": sa, "ka": ka, "sb": sb, "kb": kb})

    assert kb.last_table == "out_2"


def test_xcom_whole_string_preserves_type() -> None:
    """A whole-string xcom ref keeps the value's native type (int), so a
    numeric source_option stays an int rather than becoming '3'."""
    sa = InMemoryBatchSource([Record(data={"i": i}) for i in range(3)])
    ka = InMemoryBatchSink()
    sb = InMemoryBatchSource([Record(data={"x": 1})])
    kb = InMemoryBatchSink()

    a = Task(name="A", source="sa", sink="ka")
    b = Task(
        name="B",
        source="sb",
        sink="kb",
        source_options={"chunk_size": "{{ xcom.A.records_written }}"},
        depends_on=["A"],
    )
    p = Pipeline("p").add(a).add(b)
    _connect(sa, ka, sb, kb)
    p.run(connectors={"sa": sa, "ka": ka, "sb": sb, "kb": kb})

    assert sb.last_chunk_size == 3
    assert isinstance(sb.last_chunk_size, int)


def test_undefined_xcom_reference_raises() -> None:
    """A reference to a key the upstream never published fails the run with a
    clear ConfigError (typo / bad ordering surfaces immediately)."""
    sa = InMemoryBatchSource([Record(data={"i": 1})])
    ka = InMemoryBatchSink()
    sb = InMemoryBatchSource([Record(data={"x": 1})])
    kb = InMemoryBatchSink()

    a = Task(name="A", source="sa", sink="ka")
    b = Task(
        name="B",
        source="sb",
        query="SELECT {{ xcom.A.nope }}",
        sink="kb",
        depends_on=["A"],
    )
    p = Pipeline("p").add(a).add(b)
    _connect(sa, ka, sb, kb)
    with pytest.raises(ConfigError, match=r"xcom\.A\.nope"):
        p.run(connectors={"sa": sa, "ka": ka, "sb": sb, "kb": kb})


def test_no_xcom_pipeline_is_untouched() -> None:
    """A pipeline with no xcom reference runs exactly as before (query passes
    through verbatim, store stays scoped to the run)."""
    sa = InMemoryBatchSource([Record(data={"i": 1})])
    ka = InMemoryBatchSink()
    a = Task(name="A", source="sa", query="SELECT 1", sink="ka")
    p = Pipeline("p").add(a)
    _connect(sa, ka)
    result = p.run(connectors={"sa": sa, "ka": ka})
    assert result.success is True
    assert sa.last_query == "SELECT 1"
