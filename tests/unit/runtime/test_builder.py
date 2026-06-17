"""Pipeline builder tests (YAML → Pipeline + Connectors)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest

from etl_plugins.config.models import (
    ConnectionConfig,
    ConnectionsConfig,
    PipelineConfig,
)
from etl_plugins.core.context import Context
from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.pipeline import SqlAction
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry
from etl_plugins.runtime.builder import (
    build_connector,
    build_connectors,
    build_pipeline,
    build_pipeline_from_yaml,
)
from tests.fixtures.connectors import InMemoryBatchSink, InMemoryBatchSource

# ---------- build_connector ----------


@pytest.fixture(autouse=True)
def _ensure_inmem_registered() -> Iterator[None]:
    """Register InMemory classes under stable names for the builder tests."""
    src_orig = ConnectorRegistry._registry.get("test-inmem-source")
    snk_orig = ConnectorRegistry._registry.get("test-inmem-sink")
    ConnectorRegistry.register("test-inmem-source", replace=True)(InMemoryBatchSource)
    ConnectorRegistry.register("test-inmem-sink", replace=True)(InMemoryBatchSink)
    yield
    # Restore previous state (or remove if not present before)
    if src_orig is None:
        ConnectorRegistry._registry.pop("test-inmem-source", None)
    else:
        ConnectorRegistry._registry["test-inmem-source"] = src_orig
    if snk_orig is None:
        ConnectorRegistry._registry.pop("test-inmem-sink", None)
    else:
        ConnectorRegistry._registry["test-inmem-sink"] = snk_orig


def test_build_connector_instantiates_registered_class() -> None:
    config = ConnectionConfig.model_validate({"type": "test-inmem-source"})
    instance = build_connector("src", config)
    assert isinstance(instance, InMemoryBatchSource)


def test_build_connector_invalid_kwargs_raises_configerror() -> None:
    config = ConnectionConfig.model_validate(
        {"type": "test-inmem-source", "totally_unknown_kwarg": 42}
    )
    with pytest.raises(ConfigError, match="failed to construct"):
        build_connector("src", config)


def test_build_connectors_makes_named_dict() -> None:
    cc = ConnectionsConfig.model_validate(
        {
            "connections": {
                "s": {"type": "test-inmem-source"},
                "k": {"type": "test-inmem-sink"},
            }
        }
    )
    out = build_connectors(cc)
    assert set(out.keys()) == {"s", "k"}
    assert isinstance(out["s"], InMemoryBatchSource)
    assert isinstance(out["k"], InMemoryBatchSink)


# ---------- build_pipeline ----------


def _basic_pipeline_config() -> PipelineConfig:
    return PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "s", "query": "SELECT 1"},
            "sink": {"connection": "k", "table": "T", "mode": "append"},
        }
    )


def test_build_pipeline_minimum() -> None:
    pc = _basic_pipeline_config()
    src = InMemoryBatchSource()
    snk = InMemoryBatchSink()
    pipeline, connectors = build_pipeline(pc, {"s": src, "k": snk})
    assert pipeline.name == "p"
    assert pipeline.mode == "batch"
    assert len(pipeline.tasks) == 1
    task = pipeline.tasks[0]
    assert task.source == "s"
    assert task.sink == "k"
    assert task.sink_table == "T"
    assert task.sink_mode == "append"
    assert connectors == {"s": src, "k": snk}


def test_build_pipeline_forwards_per_task_retry_and_timeout() -> None:
    """TaskConfig.retry/timeout_seconds + PipelineConfig.task_timeout_seconds
    reach the core Task/Pipeline (자유도 2단계)."""
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "task_timeout_seconds": 30.0,
            "tasks": [
                {
                    "name": "t",
                    "source": {"connection": "s", "query": "SELECT 1"},
                    "sink": {"connection": "k", "table": "T", "mode": "append"},
                    "timeout_seconds": 5.0,
                    "retry": {
                        "max_attempts": 4,
                        "backoff": "fixed",
                        "initial_delay_seconds": 0.0,
                    },
                }
            ],
        }
    )
    pipeline, _ = build_pipeline(pc, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})
    assert pipeline.task_timeout_seconds == 30.0
    task = pipeline.tasks[0]
    assert task.timeout_seconds == 5.0
    assert task.retry is not None
    assert task.retry.max_attempts == 4


def test_build_pipeline_splits_shared_source_sink_connection() -> None:
    """A sink reusing the source's connection gets its own instance (a separate
    physical connection) so the streaming read cursor and the write don't
    deadlock on one shared connection."""
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "db", "query": "SELECT 1"},
            "sink": {"connection": "db", "table": "T", "mode": "append"},
        }
    )
    shared = InMemoryBatchSink()
    made: list[str] = []

    def factory(name: str) -> Any:
        made.append(name)
        return InMemoryBatchSink()

    pipeline, connectors = build_pipeline(pc, {"db": shared}, connector_factory=factory)
    task = pipeline.tasks[0]
    assert task.source == "db"
    assert task.sink == "db::sink"
    assert made == ["db"]
    assert connectors["db"] is shared
    assert connectors["db::sink"] is not shared


def test_build_pipeline_shared_connection_without_factory_keeps_single() -> None:
    """Without a factory we can't split — preserve the original single-instance
    behaviour (drivers that tolerate concurrent read/write, mocks, tests)."""
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "db", "query": "SELECT 1"},
            "sink": {"connection": "db", "table": "T", "mode": "append"},
        }
    )
    inst = InMemoryBatchSink()
    pipeline, connectors = build_pipeline(pc, {"db": inst})
    assert pipeline.tasks[0].sink == "db"
    assert set(connectors) == {"db"}


def test_build_pipeline_fanout_splits_only_shared_sink() -> None:
    """In fan-out, only the sink that reuses the source connection is split."""
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "db", "query": "SELECT 1"},
            "sinks": [
                {"connection": "db", "table": "T1", "mode": "append"},
                {"connection": "other", "table": "T2", "mode": "append"},
            ],
        }
    )

    def factory(name: str) -> Any:
        return InMemoryBatchSink()

    pipeline, connectors = build_pipeline(
        pc, {"db": InMemoryBatchSink(), "other": InMemoryBatchSink()}, connector_factory=factory
    )
    sink_names = [s.name for s in pipeline.tasks[0].sinks]
    assert sink_names == ["db::sink", "other"]
    assert "db::sink" in connectors


# ---------- sql_exec pre-load action (ADR-0035) ----------


def test_sql_exec_becomes_pre_sql_not_a_transform() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "src", "query": "SELECT 1"},
            "transforms": [
                {"type": "sql_exec", "connection": "dst", "statement": "DELETE FROM dst"},
            ],
            "sink": {"connection": "dst", "table": "dst", "mode": "append"},
        }
    )
    pipeline, _ = build_pipeline(pc, {"src": InMemoryBatchSource(), "dst": InMemoryBatchSink()})
    task = pipeline.tasks[0]
    assert task.pre_sql == [SqlAction(connection="dst", statement="DELETE FROM dst")]
    assert task.transforms == []  # not run per-record


def test_sql_exec_requires_statement() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "src", "query": "SELECT 1"},
            "transforms": [{"type": "sql_exec", "connection": "dst"}],
            "sink": {"connection": "dst", "table": "dst", "mode": "append"},
        }
    )
    with pytest.raises(ConfigError, match="sql_exec"):
        build_pipeline(pc, {"src": InMemoryBatchSource(), "dst": InMemoryBatchSink()})


def test_sql_exec_unknown_connection_raises() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "src", "query": "SELECT 1"},
            "transforms": [{"type": "sql_exec", "connection": "nope", "statement": "DELETE"}],
            "sink": {"connection": "dst", "table": "dst", "mode": "append"},
        }
    )
    with pytest.raises(ConfigError):
        build_pipeline(pc, {"src": InMemoryBatchSource(), "dst": InMemoryBatchSink()})


def test_pre_sql_delete_makes_pipeline_idempotent(tmp_path: Path) -> None:
    """Run a delete-then-insert pipeline twice; the target must not accumulate
    duplicates — the DELETE pre-action clears the prior load each run."""
    import sqlite3

    from etl_plugins.connectors.rdbms.sqlite import SQLiteConnector

    db = tmp_path / "idem.db"
    with sqlite3.connect(db) as raw:
        raw.execute("CREATE TABLE src (id INTEGER)")
        raw.executemany("INSERT INTO src VALUES (?)", [(1,), (2,), (3,)])
        raw.execute("CREATE TABLE dst (id INTEGER)")
        raw.execute("INSERT INTO dst VALUES (99)")  # stale row from a prior run
        raw.commit()

    pc = PipelineConfig.model_validate(
        {
            "name": "idem",
            "source": {"connection": "src", "query": "SELECT id FROM src"},
            "transforms": [
                {"type": "sql_exec", "connection": "dst", "statement": "DELETE FROM dst"},
            ],
            "sink": {"connection": "dst", "table": "dst", "mode": "append"},
        }
    )

    for _ in range(2):
        connectors = {
            "src": SQLiteConnector(database=str(db)),
            "dst": SQLiteConnector(database=str(db)),
        }
        pipeline, connectors = build_pipeline(pc, connectors)
        for c in connectors.values():
            c.connect()
        try:
            pipeline.run(Context(pipeline_name="idem", run_id="r"), connectors=connectors)
        finally:
            for c in connectors.values():
                c.close()

    with sqlite3.connect(db) as raw:
        rows = sorted(r[0] for r in raw.execute("SELECT id FROM dst"))
    assert rows == [1, 2, 3]  # 99 deleted, 1/2/3 present once (not duplicated)


def test_build_pipeline_wires_cursor_column() -> None:
    """source.cursor_column flows to Task.cursor_column (backfill, ADR-0039)
    and isn't leaked into source_options."""
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "s", "query": "SELECT 1", "cursor_column": "updated_at"},
            "sink": {"connection": "k", "table": "T", "mode": "append"},
        }
    )
    pipeline, _ = build_pipeline(pc, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})
    task = pipeline.tasks[0]
    assert task.cursor_column == "updated_at"
    assert "cursor_column" not in task.source_options


def test_build_pipeline_with_transforms() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "s"},
            "transforms": [
                {"type": "rename", "mapping": {"a": "A"}},
                {"type": "cast", "columns": {"A": "int"}},
            ],
            "sink": {"connection": "k", "table": "T"},
        }
    )
    pipeline, _ = build_pipeline(pc, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})
    assert len(pipeline.tasks[0].transforms) == 2


def test_build_pipeline_missing_source_raises() -> None:
    pc = _basic_pipeline_config()
    with pytest.raises(ConfigError, match="source connection"):
        build_pipeline(pc, {"k": InMemoryBatchSink()})


def test_build_pipeline_missing_sink_raises() -> None:
    pc = _basic_pipeline_config()
    with pytest.raises(ConfigError, match="sink connection"):
        build_pipeline(pc, {"s": InMemoryBatchSource()})


# ---------- fan-out (ADR-0026) ----------


def test_pipeline_config_rejects_both_sink_and_sinks() -> None:
    with pytest.raises(ValueError, match="not both"):
        PipelineConfig.model_validate(
            {
                "name": "p",
                "source": {"connection": "s"},
                "sink": {"connection": "k"},
                "sinks": [{"connection": "k2"}],
            }
        )


def test_pipeline_config_rejects_no_sink() -> None:
    with pytest.raises(ValueError, match="needs a 'sink'"):
        PipelineConfig.model_validate({"name": "p", "source": {"connection": "s"}})


def test_build_pipeline_fanout_populates_sinks() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "s"},
            "sinks": [
                {"connection": "k1", "table": "T1"},
                {"connection": "k2", "table": "T2", "mode": "upsert", "key_columns": ["id"]},
            ],
        }
    )
    pipeline, _ = build_pipeline(
        pc, {"s": InMemoryBatchSource(), "k1": InMemoryBatchSink(), "k2": InMemoryBatchSink()}
    )
    task = pipeline.tasks[0]
    assert task.sink is None
    assert [s.name for s in task.sinks] == ["k1", "k2"]
    assert task.sinks[1].mode == "upsert"
    assert task.sinks[1].key_columns == ["id"]


def test_build_pipeline_fanout_missing_one_sink_raises() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "s"},
            "sinks": [{"connection": "k1"}, {"connection": "k2"}],
        }
    )
    with pytest.raises(ConfigError, match="k2"):
        build_pipeline(pc, {"s": InMemoryBatchSource(), "k1": InMemoryBatchSink()})


def test_build_pipeline_stream_fanout_rejected() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "mode": "stream",
            "source": {"connection": "s"},
            "sinks": [{"connection": "k1"}, {"connection": "k2"}],
        }
    )
    with pytest.raises(ConfigError, match="not supported in stream mode"):
        build_pipeline(
            pc, {"s": InMemoryBatchSource(), "k1": InMemoryBatchSink(), "k2": InMemoryBatchSink()}
        )


# ---------- conditional routing (ADR-0027) ----------


def test_build_pipeline_routing_when_uses_sinks_list() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "s"},
            "sinks": [
                {"connection": "k1", "when": "data['x'] > 0"},
                {"connection": "k2"},
            ],
        }
    )
    pipeline, _ = build_pipeline(
        pc, {"s": InMemoryBatchSource(), "k1": InMemoryBatchSink(), "k2": InMemoryBatchSink()}
    )
    task = pipeline.tasks[0]
    assert [s.when for s in task.sinks] == ["data['x'] > 0", None]
    # `when` is not forwarded to the connector write() options.
    assert "when" not in task.sinks[0].options


def test_build_pipeline_single_conditional_sink_uses_sinks_list() -> None:
    """A lone sink with a `when` can't use the flat path (it has no `when`)."""
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "s"},
            "sink": {"connection": "k", "when": "data['ok']"},
        }
    )
    pipeline, _ = build_pipeline(pc, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})
    task = pipeline.tasks[0]
    assert task.sink is None
    assert [s.when for s in task.sinks] == ["data['ok']"]


def test_build_pipeline_invalid_when_raises() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "s"},
            "sink": {"connection": "k", "when": "data["},
        }
    )
    with pytest.raises(ConfigError, match="invalid routing 'when'"):
        build_pipeline(pc, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})


def test_build_pipeline_stream_when_rejected() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "mode": "stream",
            "source": {"connection": "s"},
            "sink": {"connection": "k", "when": "data['x']"},
        }
    )
    with pytest.raises(ConfigError, match="conditional sink routing"):
        build_pipeline(pc, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})


# ---------- Task-orchestration DAG (ADR-0028) ----------


def _dag_config() -> PipelineConfig:
    return PipelineConfig.model_validate(
        {
            "name": "dag",
            "tasks": [
                {
                    "name": "load",
                    "source": {"connection": "s"},
                    "sink": {"connection": "k", "table": "t"},
                    "depends_on": ["extract"],
                },
                {
                    "name": "extract",
                    "source": {"connection": "s"},
                    "sink": {"connection": "k", "table": "raw"},
                },
            ],
        }
    )


def test_build_pipeline_dag_orders_tasks() -> None:
    pc = _dag_config()
    pipeline, _ = build_pipeline(pc, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})
    assert len(pipeline.tasks) == 2
    # _ordered_tasks resolves the dependency: extract before load.
    assert [t.name for t in pipeline._ordered_tasks()] == ["extract", "load"]


def test_build_pipeline_dag_missing_dependency_raises() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "dag",
            "tasks": [
                {
                    "name": "a",
                    "source": {"connection": "s"},
                    "sink": {"connection": "k"},
                    "depends_on": ["ghost"],
                }
            ],
        }
    )
    with pytest.raises(ConfigError, match="unknown task 'ghost'"):
        build_pipeline(pc, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})


def test_build_pipeline_dag_cycle_raises() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "dag",
            "tasks": [
                {
                    "name": "a",
                    "source": {"connection": "s"},
                    "sink": {"connection": "k"},
                    "depends_on": ["b"],
                },
                {
                    "name": "b",
                    "source": {"connection": "s"},
                    "sink": {"connection": "k"},
                    "depends_on": ["a"],
                },
            ],
        }
    )
    with pytest.raises(Exception, match="cycle"):
        build_pipeline(pc, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})


def test_pipeline_config_rejects_both_single_and_tasks() -> None:
    with pytest.raises(ValueError, match="exactly one of"):
        PipelineConfig.model_validate(
            {
                "name": "x",
                "source": {"connection": "s"},
                "sink": {"connection": "k"},
                "tasks": [
                    {"name": "t", "source": {"connection": "s"}, "sink": {"connection": "k"}}
                ],
            }
        )


def test_pipeline_config_rejects_neither_source_nor_tasks() -> None:
    with pytest.raises(ValueError, match="needs a 'source'"):
        PipelineConfig.model_validate({"name": "x"})


def test_build_pipeline_stream_multitask_rejected() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "dag",
            "mode": "stream",
            "tasks": [
                {"name": "a", "source": {"connection": "s"}, "sink": {"connection": "k"}},
                {"name": "b", "source": {"connection": "s"}, "sink": {"connection": "k"}},
            ],
        }
    )
    with pytest.raises(ConfigError, match="multi-task DAGs are not supported in stream"):
        build_pipeline(pc, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})


def test_build_pipeline_branch_maps_rules() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "dag",
            "tasks": [
                {
                    "name": "branch",
                    "source": {"connection": "s"},
                    "sink": {"connection": "k"},
                    "branch": [
                        {"when": "records_written > 0", "to": ["big"]},
                        {"when": None, "to": ["small"]},
                    ],
                },
                {
                    "name": "big",
                    "source": {"connection": "s"},
                    "sink": {"connection": "k"},
                    "depends_on": ["branch"],
                },
                {
                    "name": "small",
                    "source": {"connection": "s"},
                    "sink": {"connection": "k"},
                    "depends_on": ["branch"],
                },
            ],
        }
    )
    pipeline, _ = build_pipeline(pc, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})
    branch_task = next(t for t in pipeline.tasks if t.name == "branch")
    assert [b.to for b in branch_task.branch] == [["big"], ["small"]]


def test_build_pipeline_branch_target_not_downstream_raises() -> None:
    pc = PipelineConfig.model_validate(
        {
            "name": "dag",
            "tasks": [
                {
                    "name": "branch",
                    "source": {"connection": "s"},
                    "sink": {"connection": "k"},
                    "branch": [{"when": None, "to": ["orphan"]}],
                },
                {"name": "orphan", "source": {"connection": "s"}, "sink": {"connection": "k"}},
            ],
        }
    )
    with pytest.raises(ConfigError, match="not a direct downstream"):
        build_pipeline(pc, {"s": InMemoryBatchSource(), "k": InMemoryBatchSink()})


def test_task_config_rejects_unknown_trigger_rule() -> None:
    with pytest.raises(ValueError, match="unknown trigger_rule"):
        PipelineConfig.model_validate(
            {
                "name": "dag",
                "tasks": [
                    {
                        "name": "a",
                        "source": {"connection": "s"},
                        "sink": {"connection": "k"},
                        "trigger_rule": "bogus",
                    }
                ],
            }
        )


# ---------- build_pipeline_from_yaml ----------


def test_build_pipeline_from_yaml_e2e(tmp_path: Path, sample_records: list[Record]) -> None:
    """End-to-end: write YAML files → build → run with extra in-memory connectors."""
    # Connections YAML — uses our test-inmem types
    conn_yaml = tmp_path / "connections.yaml"
    conn_yaml.write_text(
        """\
connections:
  src: { type: test-inmem-source }
  snk: { type: test-inmem-sink }
"""
    )
    # Pipeline YAML
    pipe_yaml = tmp_path / "pipe.yaml"
    pipe_yaml.write_text(
        """\
name: e2e_test
source: { connection: src }
transforms:
  - type: rename
    mapping: { name: full_name }
sink: { connection: snk, table: ignored }
"""
    )

    # Pre-seeded connectors — pass via extra_connectors (override the freshly-built ones)
    seeded_src = InMemoryBatchSource(sample_records)
    snk = InMemoryBatchSink()
    pipeline, connectors = build_pipeline_from_yaml(
        pipe_yaml,
        connections_path=conn_yaml,
        extra_connectors={"src": seeded_src, "snk": snk},
    )

    # Open + run
    for c in connectors.values():
        c.connect()
    try:
        result = pipeline.run(connectors=connectors)
    finally:
        for c in connectors.values():
            c.close()

    assert result.success is True
    assert result.records_read == len(sample_records)
    assert result.records_written == len(sample_records)
    assert all("full_name" in r.data and "name" not in r.data for r in snk.records)


def test_build_pipeline_from_yaml_without_connections(tmp_path: Path) -> None:
    """Caller can omit connections.yaml and supply everything via extra_connectors."""
    pipe_yaml = tmp_path / "pipe.yaml"
    pipe_yaml.write_text(
        """\
name: bare
source: { connection: src }
sink: { connection: snk, table: T }
"""
    )
    src = InMemoryBatchSource()
    snk = InMemoryBatchSink()
    pipeline, connectors = build_pipeline_from_yaml(
        pipe_yaml,
        extra_connectors={"src": src, "snk": snk},
    )
    assert pipeline.name == "bare"
    assert set(connectors) == {"src", "snk"}


def _passthrough(_records: Iterable[Record]) -> Iterator[Record]:
    yield from _records


# --- column lineage attached at build time (ADR-0041 K5b) ------------------


def test_build_pipeline_attaches_column_lineage_for_sql_source() -> None:
    """Builder derives static column lineage from the config + stashes on the
    Pipeline so emitters (OpenLineage K5) can attach a ``columnLineage``
    facet without re-parsing the config."""
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "s", "query": "SELECT a AS id, c AS city FROM users"},
            "sink": {"connection": "k", "table": "customers", "mode": "append"},
        }
    )
    src = InMemoryBatchSource()
    snk = InMemoryBatchSink()
    pipeline, _ = build_pipeline(pc, {"s": src, "k": snk})
    assert pipeline.column_lineage is not None
    edges = {(e.downstream.column, e.upstreams[0].column) for e in pipeline.column_lineage.edges}
    assert ("id", "a") in edges and ("city", "c") in edges


def test_build_pipeline_column_lineage_opaque_for_python_transform() -> None:
    """``python`` transforms are opaque — the column-lineage derivation
    marks the output asset opaque rather than producing wrong edges. The
    Pipeline still builds (best-effort attach)."""
    pc = PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "s", "query": "SELECT id FROM users"},
            "transforms": [
                {"type": "python", "callable": "tests.unit.runtime.test_builder:_passthrough"}
            ],
            "sink": {"connection": "k", "table": "customers", "mode": "append"},
        }
    )
    src = InMemoryBatchSource()
    snk = InMemoryBatchSink()
    pipeline, _ = build_pipeline(pc, {"s": src, "k": snk})
    assert pipeline.column_lineage is not None
    assert pipeline.column_lineage.edges == []
    opaque_keys = {str(k) for k in pipeline.column_lineage.opaque_assets}
    assert "k/customers" in opaque_keys


def _ignore_types(_: Any) -> None:
    """Suppress unused-import lint on Iterable type used only as default Iterable[Record]."""
    pass
