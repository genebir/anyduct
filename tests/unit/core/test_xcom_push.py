"""Explicit XCom push — ``push_xcom`` (ADR-0097 follow-up, 자유도 3단계).

Auto-XCom only publishes a task's summary (records_read / records_written /
success / new_cursor) — none of which is a fan-out *list*. ``push_xcom``
projects a column of the rows a task processes into a list under
``xcom.<task>.<key>``, so a downstream task can ``expand`` over it. This is
the keystone that makes the documented "upstream XCom list" expand source
(ADR-0098) actually work end-to-end.
"""

from __future__ import annotations

import pytest

from etl_plugins.config.models import PipelineConfig
from etl_plugins.core.exceptions import TaskError
from etl_plugins.core.pipeline import Pipeline, Task
from etl_plugins.core.record import Record
from etl_plugins.runtime.builder import build_pipeline
from tests.fixtures.connectors import InMemoryBatchSink, InMemoryBatchSource


class _CaptureSource(InMemoryBatchSource):
    """Records every query it's asked to read (one per mapped instance)."""

    def __init__(self, records: list[Record] | None = None) -> None:
        super().__init__(records)
        self.queries: list[str | None] = []

    def read(self, query=None, *, chunk_size=10_000, **opts):  # type: ignore[no-untyped-def, override]
        self.queries.append(query)
        yield from self._records


def _connect(*conns: object) -> None:
    for c in conns:
        c.connect()  # type: ignore[attr-defined]


def test_push_xcom_publishes_column_list() -> None:
    src = InMemoryBatchSource([Record(data={"region": "kr"}), Record(data={"region": "us"})])
    sink = InMemoryBatchSink()
    task = Task(
        name="discover",
        source="s",
        sink="k",
        push_xcom={"regions": {"column": "region"}},
    )
    p = Pipeline("p").add(task)
    _connect(src, sink)
    result = p.run(connectors={"s": src, "k": sink})

    assert result.success is True
    assert p._xcom["discover"]["regions"] == ["kr", "us"]
    # Auto summary still present alongside the explicit push.
    assert p._xcom["discover"]["records_written"] == 2
    # The rows still flowed to the sink (push is additive, not a diversion).
    assert len(sink.records) == 2


def test_push_xcom_distinct_dedupes_preserving_order() -> None:
    src = InMemoryBatchSource([Record(data={"r": v}) for v in ["kr", "us", "kr", "jp", "us"]])
    sink = InMemoryBatchSink()
    task = Task(
        name="d",
        source="s",
        sink="k",
        push_xcom={"rs": {"column": "r", "distinct": True}},
    )
    p = Pipeline("p").add(task)
    _connect(src, sink)
    p.run(connectors={"s": src, "k": sink})

    assert p._xcom["d"]["rs"] == ["kr", "us", "jp"]


def test_push_xcom_missing_column_fails_task() -> None:
    src = InMemoryBatchSource([Record(data={"region": "kr"})])
    sink = InMemoryBatchSink()
    task = Task(
        name="d",
        source="s",
        sink="k",
        push_xcom={"x": {"column": "nope"}},
    )
    p = Pipeline("p").add(task)
    _connect(src, sink)
    with pytest.raises(TaskError, match="nope"):
        p.run(connectors={"s": src, "k": sink})


def test_push_xcom_empty_source_pushes_empty_list() -> None:
    src = InMemoryBatchSource([])
    sink = InMemoryBatchSink()
    task = Task(name="d", source="s", sink="k", push_xcom={"rs": {"column": "r"}})
    p = Pipeline("p").add(task)
    _connect(src, sink)
    result = p.run(connectors={"s": src, "k": sink})

    assert result.success is True
    assert p._xcom["d"]["rs"] == []


def test_push_then_expand_end_to_end() -> None:
    """The keystone: a discover task pushes a list, a downstream task fans
    out over it via ``{{ xcom.discover.regions }}``."""
    discover_src = InMemoryBatchSource(
        [Record(data={"region": "kr"}), Record(data={"region": "jp"})]
    )
    discover_sink = InMemoryBatchSink()
    load_src = _CaptureSource([Record(data={"i": 1})])
    load_sink = InMemoryBatchSink()

    discover = Task(
        name="discover",
        source="ds",
        sink="dk",
        push_xcom={"regions": {"column": "region"}},
    )
    load = Task(
        name="load",
        source="ls",
        query="SELECT * FROM orders WHERE region = '{{ map.region }}'",
        sink="lk",
        depends_on=["discover"],
        expand={"region": "{{ xcom.discover.regions }}"},
    )
    p = Pipeline("p").add(discover).add(load)
    _connect(discover_src, discover_sink, load_src, load_sink)
    result = p.run(
        connectors={
            "ds": discover_src,
            "dk": discover_sink,
            "ls": load_src,
            "lk": load_sink,
        }
    )

    assert result.success is True
    # The load task fanned out into one instance per discovered region.
    assert load_src.queries == [
        "SELECT * FROM orders WHERE region = 'kr'",
        "SELECT * FROM orders WHERE region = 'jp'",
    ]


def test_push_xcom_via_builder_uses_records_path() -> None:
    """Built from a config (exercises the builder forward) — a push_xcom task
    runs the records data path and collects the list."""
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                {
                    "name": "d",
                    "source": {"connection": "s", "query": "SELECT region FROM t"},
                    "sink": {"connection": "k", "table": "out", "mode": "append"},
                    "push_xcom": {"regions": {"column": "region"}},
                }
            ],
        }
    )
    src = InMemoryBatchSource([Record(data={"region": "kr"}), Record(data={"region": "us"})])
    sink = InMemoryBatchSink()
    pipeline, _ = build_pipeline(cfg, {"s": src, "k": sink})
    assert pipeline.tasks[0].push_xcom == {"regions": {"column": "region"}}
    _connect(src, sink)
    result = pipeline.run(connectors={"s": src, "k": sink})

    assert result.success is True
    assert result.data_paths.get("d") == "records"
    assert pipeline._xcom["d"]["regions"] == ["kr", "us"]


def test_push_xcom_invalid_spec_rejected_at_config() -> None:
    with pytest.raises(ValueError, match="push_xcom"):
        PipelineConfig.model_validate(
            {
                "name": "p",
                "tasks": [
                    {
                        "name": "d",
                        "source": {"connection": "wh"},
                        "sink": {"connection": "wh", "table": "out"},
                        "push_xcom": {"x": {"distinct": True}},  # no 'column'
                    }
                ],
            }
        )
