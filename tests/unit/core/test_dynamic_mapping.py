"""Dynamic task mapping — Airflow ``.expand()`` (ADR-0098, 자유도 3단계).

A task with ``expand`` fans out into one instance per element of a list
(cross product over multiple keys). Each instance exposes ``{{ map.<key> }}``
in its templatable fields. The list can be literal, a per-run param list
(``{{ params.regions }}``, resolved at load), or an upstream XCom list
(``{{ xcom.t.k }}``, resolved at execution).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from etl_plugins.config.models import PipelineConfig
from etl_plugins.core.exceptions import TaskError
from etl_plugins.core.pipeline import Pipeline, Task
from etl_plugins.core.record import Record
from etl_plugins.runtime.builder import build_pipeline
from etl_plugins.runtime.templating import RuntimeContext, render_config_templates
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


def test_expand_over_literal_list_fans_out() -> None:
    sb = _CaptureSource([Record(data={"i": 1}), Record(data={"i": 2})])
    kb = InMemoryBatchSink()
    task = Task(
        name="load",
        source="sb",
        query="region={{ map.region }}",
        sink="kb",
        expand={"region": ["kr", "us", "jp"]},
    )
    p = Pipeline("p").add(task)
    _connect(sb, kb)
    result = p.run(connectors={"sb": sb, "kb": kb})

    assert result.success is True
    assert sb.queries == ["region=kr", "region=us", "region=jp"]
    # 3 instances x 2 records each
    assert result.records_written == 6
    # aggregate published to xcom
    assert p._xcom["load"]["records_written"] == 6
    assert p._xcom["load"]["success"] is True


def test_expand_into_sink_table() -> None:
    sb = _CaptureSource([Record(data={"i": 1})])
    kb = InMemoryBatchSink()
    task = Task(
        name="load",
        source="sb",
        sink="kb",
        sink_table="orders_{{ map.region }}",
        expand={"region": ["kr", "us"]},
    )
    p = Pipeline("p").add(task)
    _connect(sb, kb)
    p.run(connectors={"sb": sb, "kb": kb})
    # last instance's table (single connector reused)
    assert kb.last_table == "orders_us"


def test_expand_cross_product_of_two_keys() -> None:
    sb = _CaptureSource([Record(data={"i": 1})])
    kb = InMemoryBatchSink()
    task = Task(
        name="load",
        source="sb",
        query="{{ map.region }}/{{ map.kind }}",
        sink="kb",
        expand={"region": ["kr", "us"], "kind": ["a", "b"]},
    )
    p = Pipeline("p").add(task)
    _connect(sb, kb)
    p.run(connectors={"sb": sb, "kb": kb})
    assert sb.queries == ["kr/a", "kr/b", "us/a", "us/b"]


def test_empty_expansion_runs_zero_instances() -> None:
    sb = _CaptureSource([Record(data={"i": 1})])
    kb = InMemoryBatchSink()
    task = Task(name="load", source="sb", sink="kb", expand={"region": []})
    p = Pipeline("p").add(task)
    _connect(sb, kb)
    result = p.run(connectors={"sb": sb, "kb": kb})
    assert result.success is True
    assert sb.queries == []
    assert result.records_written == 0
    assert p._xcom["load"]["records_written"] == 0


def test_expand_value_must_be_list() -> None:
    sb = _CaptureSource([Record(data={"i": 1})])
    kb = InMemoryBatchSink()
    task = Task(name="load", source="sb", sink="kb", expand={"region": "not-a-list"})
    p = Pipeline("p").add(task)
    _connect(sb, kb)
    with pytest.raises(TaskError, match="must resolve to a list"):
        p.run(connectors={"sb": sb, "kb": kb})


def test_one_instance_fails_others_still_run() -> None:
    """A failing instance doesn't abort the rest; the task is reported failed."""

    class _FailOnUs(_CaptureSource):
        def read(self, query=None, *, chunk_size=10_000, **opts):  # type: ignore[no-untyped-def, override]
            self.queries.append(query)
            if query and "us" in query:
                raise RuntimeError("boom-us")
            yield from self._records

    sb = _FailOnUs([Record(data={"i": 1})])
    kb = InMemoryBatchSink()
    task = Task(
        name="load",
        source="sb",
        query="{{ map.region }}",
        sink="kb",
        expand={"region": ["kr", "us", "jp"]},
    )
    p = Pipeline("p").add(task)
    _connect(sb, kb)
    with pytest.raises(RuntimeError, match="boom-us"):
        p.run(connectors={"sb": sb, "kb": kb})
    # all three were attempted despite the middle failure
    assert sb.queries == ["kr", "us", "jp"]


def test_downstream_pulls_mapped_aggregate_via_xcom() -> None:
    """A downstream task reads the mapped task's aggregate records_written."""
    sa = _CaptureSource([Record(data={"i": 1}), Record(data={"i": 2})])
    ka = InMemoryBatchSink()
    sb = _CaptureSource([Record(data={"x": 1})])
    kb = InMemoryBatchSink()

    a = Task(
        name="load",
        source="sa",
        query="{{ map.region }}",
        sink="ka",
        expand={"region": ["kr", "us"]},
    )
    b = Task(
        name="report",
        source="sb",
        query="SELECT {{ xcom.load.records_written }}",
        sink="kb",
        depends_on=["load"],
    )
    p = Pipeline("p").add(a).add(b)
    _connect(sa, ka, sb, kb)
    p.run(connectors={"sa": sa, "ka": ka, "sb": sb, "kb": kb})
    # load: 2 instances x 2 records = 4
    assert sb.queries == ["SELECT 4"]


def test_builder_resolves_param_driven_expand() -> None:
    """expand: {region: '{{ params.regions }}'} with a per-run list param
    becomes a concrete list in the built task (자유도 1단계 + 3단계)."""
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                {
                    "name": "load",
                    "source": {"connection": "s", "query": "{{ map.region }}"},
                    "sink": {"connection": "k", "table": "T", "mode": "append"},
                    "expand": {"region": "{{ params.regions }}"},
                }
            ],
        }
    )
    ctx = RuntimeContext(
        run_id="r",
        logical_date=datetime(2026, 6, 17, tzinfo=UTC),
        params={"regions": ["kr", "us", "jp"]},
    )
    # Load-time render (what the worker / CLI do before build_pipeline): the
    # ``params`` namespace resolves now; a whole-string ref preserves the list.
    rendered = render_config_templates(pc.model_dump(), ctx)
    pc2 = PipelineConfig.model_validate(rendered)
    pipeline, _ = build_pipeline(pc2, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})
    assert pipeline.tasks[0].expand == {"region": ["kr", "us", "jp"]}


def test_builder_forwards_literal_expand() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                {
                    "name": "load",
                    "source": {"connection": "s", "query": "{{ map.r }}"},
                    "sink": {"connection": "k", "table": "T", "mode": "append"},
                    "expand": {"r": ["a", "b"]},
                }
            ],
        }
    )
    pipeline, _ = build_pipeline(pc, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})
    assert pipeline.tasks[0].expand == {"r": ["a", "b"]}
