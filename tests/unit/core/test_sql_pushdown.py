"""Unit tests for same-connection SQL pushdown (ADR-0093 P2c).

Uses a REAL sqlite file (no Docker): source and sink share one connector
instance, so the eligible task must collapse to a single in-database
``INSERT INTO … SELECT`` — proven by monkeypatching ``write`` to explode.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from etl_plugins.config.models import TransformConfig
from etl_plugins.connectors.rdbms.sqlite import SQLiteConnector
from etl_plugins.core.pipeline import Pipeline, Task
from etl_plugins.runtime.transforms import build_transform


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = str(tmp_path / "pushdown.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE src (id INTEGER, name TEXT)")
    con.executemany("INSERT INTO src VALUES (?, ?)", [(i, f"n{i}") for i in range(5)])
    con.execute("CREATE TABLE dst (id INTEGER, name TEXT)")
    con.commit()
    con.close()
    return path


def _rows(path: str) -> list[tuple[Any, ...]]:
    return sqlite3.connect(path).execute("SELECT id, name FROM dst ORDER BY id").fetchall()


def _task(**overrides: Any) -> Task:
    base: dict[str, Any] = {
        "name": "t",
        "source": "db",
        "sink": "db",
        "query": "SELECT id, name FROM src",
        "sink_table": "dst",
    }
    base.update(overrides)
    return Task(**base)


def test_pushdown_runs_in_database_without_moving_rows(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = SQLiteConnector(database=db)
    conn.connect()
    # If anything tries to move rows through Python, fail loudly.
    monkeypatch.setattr(
        SQLiteConnector, "write", lambda *a, **k: (_ for _ in ()).throw(AssertionError("write"))
    )
    monkeypatch.setattr(
        SQLiteConnector, "read", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read"))
    )
    result = Pipeline(name="p", tasks=[_task()]).run(connectors={"db": conn})
    assert result.records_read == 5
    assert result.records_written == 5
    assert result.data_paths == {"t": "pushdown"}
    assert _rows(db) == [(i, f"n{i}") for i in range(5)]
    conn.close()


def test_different_connections_fall_back(db: str, tmp_path: Path) -> None:
    other = str(tmp_path / "other.db")
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE dst (id INTEGER, name TEXT)")
    con.commit()
    con.close()
    src = SQLiteConnector(database=db)
    dst = SQLiteConnector(database=other)
    src.connect()
    dst.connect()
    task = _task(sink="other")
    result = Pipeline(name="p", tasks=[task]).run(connectors={"db": src, "other": dst})
    assert result.records_written == 5  # record path still works
    assert result.data_paths == {"t": "records"}
    src.close()
    dst.close()


@pytest.mark.parametrize(
    "overrides",
    [
        {"sink_mode": "overwrite"},  # v1 pushdown is append-only
        {"sink_pre_sql": "DELETE FROM dst"},  # pre_sql stays in-transaction on the record path
    ],
)
def test_ineligible_tasks_fall_back_to_record_path(db: str, overrides: dict[str, Any]) -> None:
    conn = SQLiteConnector(database=db)
    conn.connect()
    result = Pipeline(name="p", tasks=[_task(**overrides)]).run(connectors={"db": conn})
    assert result.records_written == 5
    conn.close()


def test_sql_splice_table_name_never_reaches_pushdown(db: str) -> None:
    """A non-plain identifier must NOT be inlined into INSERT INTO — the
    task falls back to the record path, whose driver rejects it cleanly.
    Either way: no splice executes, src survives."""
    from etl_plugins.core.exceptions import WriteError

    conn = SQLiteConnector(database=db)
    conn.connect()
    task = _task(sink_table='dst"; DROP TABLE src; --')
    with pytest.raises(WriteError):
        Pipeline(name="p", tasks=[task]).run(connectors={"db": conn})
    count = sqlite3.connect(db).execute("SELECT COUNT(*) FROM src").fetchone()[0]
    assert count == 5  # splice never ran
    conn.close()


def test_transform_falls_back(db: str) -> None:
    conn = SQLiteConnector(database=db)
    conn.connect()
    task = _task(
        transforms=[build_transform(TransformConfig(type="rename", mapping={}))],
    )
    result = Pipeline(name="p", tasks=[task]).run(connectors={"db": conn})
    assert result.records_written == 5
    assert _rows(db) == [(i, f"n{i}") for i in range(5)]
    conn.close()


# --- ELT pushdown (ADR-0094): sql transform with pushdown: true ------------


def _sql_transform(query: str, **extra: Any) -> dict[str, Any]:
    """Builder-shaped kwargs: compiled callable + raw spec side by side."""
    cfg = TransformConfig(type="sql", query=query, **extra)
    return {"transforms": [build_transform(cfg)], "transform_specs": [cfg.model_dump()]}


def test_elt_pushdown_runs_transform_in_database(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The lone ``sql`` transform composes into the INSERT's SELECT via a
    CTE — proven the same way as the plain pushdown: read/write poisoned,
    rows still transformed and landed."""
    conn = SQLiteConnector(database=db)
    conn.connect()
    monkeypatch.setattr(
        SQLiteConnector, "write", lambda *a, **k: (_ for _ in ()).throw(AssertionError("write"))
    )
    monkeypatch.setattr(
        SQLiteConnector, "read", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read"))
    )
    task = _task(**_sql_transform("SELECT id, UPPER(name) AS name FROM input", pushdown=True))
    result = Pipeline(name="p", tasks=[task]).run(connectors={"db": conn})
    assert result.data_paths == {"t": "pushdown"}
    assert _rows(db) == [(i, f"N{i}") for i in range(5)]
    conn.close()


def test_elt_pushdown_aggregate_counts_result_rows(db: str) -> None:
    """An aggregate emits fewer rows than the source had — pushdown reports
    the statement's rowcount (the rows that landed), not the source size."""
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE agg_out (n INTEGER)")
    con.commit()
    con.close()
    conn = SQLiteConnector(database=db)
    conn.connect()
    task = _task(
        sink_table="agg_out",
        **_sql_transform("SELECT COUNT(*) AS n FROM input", pushdown=True),
    )
    result = Pipeline(name="p", tasks=[task]).run(connectors={"db": conn})
    assert result.data_paths == {"t": "pushdown"}
    assert result.records_written == 1
    assert sqlite3.connect(db).execute("SELECT n FROM agg_out").fetchall() == [(5,)]
    conn.close()


def test_elt_pushdown_custom_view_name(db: str) -> None:
    conn = SQLiteConnector(database=db)
    conn.connect()
    task = _task(**_sql_transform("SELECT id, name FROM src_rows", view="src_rows", pushdown=True))
    result = Pipeline(name="p", tasks=[task]).run(connectors={"db": conn})
    assert result.data_paths == {"t": "pushdown"}
    assert _rows(db) == [(i, f"n{i}") for i in range(5)]
    conn.close()


def test_sql_transform_without_flag_stays_local(db: str) -> None:
    """No ``pushdown: true`` → the established local DuckDB path. Opt-in
    only: the local and in-database dialects differ, so the user decides."""
    conn = SQLiteConnector(database=db)
    conn.connect()
    task = _task(**_sql_transform("SELECT id, name FROM input ORDER BY id"))
    result = Pipeline(name="p", tasks=[task]).run(connectors={"db": conn})
    assert result.data_paths == {"t": "records"}
    assert _rows(db) == [(i, f"n{i}") for i in range(5)]
    conn.close()


def test_elt_pushdown_different_connections_falls_back_to_local(db: str, tmp_path: Path) -> None:
    """pushdown: true across two databases can't compose — the transform
    still runs (locally) and the result is identical, just slower."""
    other = str(tmp_path / "other_elt.db")
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE dst (id INTEGER, name TEXT)")
    con.commit()
    con.close()
    src = SQLiteConnector(database=db)
    dst = SQLiteConnector(database=other)
    src.connect()
    dst.connect()
    task = _task(
        sink="other",
        **_sql_transform("SELECT id, UPPER(name) AS name FROM input", pushdown=True),
    )
    result = Pipeline(name="p", tasks=[task]).run(connectors={"db": src, "other": dst})
    assert result.data_paths == {"t": "records"}
    assert _rows(other) == [(i, f"N{i}") for i in range(5)]
    src.close()
    dst.close()


def test_elt_pushdown_extra_transform_falls_back_to_local(db: str) -> None:
    """A second transform in the chain disqualifies composition — both run
    locally in order."""
    sql_kwargs = _sql_transform("SELECT id, name FROM input", pushdown=True)
    rename_cfg = TransformConfig(type="rename", mapping={})
    task = _task(
        transforms=[build_transform(rename_cfg), *sql_kwargs["transforms"]],
        transform_specs=[rename_cfg.model_dump(), *sql_kwargs["transform_specs"]],
    )
    conn = SQLiteConnector(database=db)
    conn.connect()
    result = Pipeline(name="p", tasks=[task]).run(connectors={"db": conn})
    assert result.data_paths == {"t": "records"}
    assert _rows(db) == [(i, f"n{i}") for i in range(5)]
    conn.close()


# --- graph-chain ELT pushdown (ADR-0094): the builder UI emits graphs ------


def _graph_config(db: str, *, sink_connection: str = "db", view: str = "input") -> Any:
    from etl_plugins.config.models import PipelineConfig

    return PipelineConfig.model_validate(
        {
            "name": "gp",
            "graph": {
                "nodes": [
                    {
                        "id": "s",
                        "type": "source",
                        "connection": "db",
                        "query": "SELECT id, name FROM src",
                    },
                    {
                        "id": "x",
                        "type": "transform",
                        "transform": {
                            "type": "sql",
                            "query": f"SELECT id, UPPER(name) AS name FROM {view}",
                            "pushdown": True,
                            **({} if view == "input" else {"view": view}),
                        },
                    },
                    {"id": "k", "type": "sink", "connection": sink_connection, "table": "dst"},
                ],
                "edges": [
                    {"from_node": "s", "to_node": "x"},
                    {"from_node": "x", "to_node": "k"},
                ],
            },
        }
    )


def test_graph_chain_pushdown_runs_in_database(db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """source → sql(pushdown) → sink as a GRAPH (the shape the builder UI
    saves) composes the same one-statement path as the linear twin."""
    from etl_plugins.runtime.builder import build_pipeline

    conn = SQLiteConnector(database=db)
    conn.connect()
    pipeline, built = build_pipeline(_graph_config(db), connectors={"db": conn})
    monkeypatch.setattr(
        SQLiteConnector, "write", lambda *a, **k: (_ for _ in ()).throw(AssertionError("write"))
    )
    monkeypatch.setattr(
        SQLiteConnector, "read", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read"))
    )
    result = pipeline.run(connectors=built)
    assert result.data_paths == {"gp": "pushdown"}
    assert result.records_written == 5
    assert _rows(db) == [(i, f"N{i}") for i in range(5)]
    conn.close()


def test_graph_chain_pushdown_with_minted_sink_instance(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a connector_factory the graph builder mints a SECOND instance
    for the sink (deadlock guard) — pushdown must still recognise both
    ends as the same database (connection_name, not instance identity)."""
    from etl_plugins.runtime.builder import build_pipeline

    conn = SQLiteConnector(database=db)
    conn.connect()
    pipeline, built = build_pipeline(
        _graph_config(db),
        connectors={"db": conn},
        connector_factory=lambda name: SQLiteConnector(database=db),
    )
    assert len(built) == 2  # the minted sink instance exists…
    for c in built.values():
        c.connect()
    monkeypatch.setattr(
        SQLiteConnector, "write", lambda *a, **k: (_ for _ in ()).throw(AssertionError("write"))
    )
    monkeypatch.setattr(
        SQLiteConnector, "read", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read"))
    )
    result = pipeline.run(connectors=built)  # …but no rows flow through it
    assert result.data_paths == {"gp": "pushdown"}
    assert _rows(db) == [(i, f"N{i}") for i in range(5)]
    for c in built.values():
        c.close()


def test_graph_two_node_same_connection_pushes_down_automatically(
    db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plain source → sink graph chain on ONE connection takes the P2c
    pushdown automatically (no flag) — the graph twin of
    ``test_pushdown_runs_in_database_without_moving_rows``."""
    from etl_plugins.config.models import PipelineConfig
    from etl_plugins.runtime.builder import build_pipeline

    cfg = PipelineConfig.model_validate(
        {
            "name": "g2",
            "graph": {
                "nodes": [
                    {
                        "id": "s",
                        "type": "source",
                        "connection": "db",
                        "query": "SELECT id, name FROM src",
                    },
                    {"id": "k", "type": "sink", "connection": "db", "table": "dst"},
                ],
                "edges": [{"from_node": "s", "to_node": "k"}],
            },
        }
    )
    conn = SQLiteConnector(database=db)
    conn.connect()
    pipeline, built = build_pipeline(cfg, connectors={"db": conn})
    monkeypatch.setattr(
        SQLiteConnector, "write", lambda *a, **k: (_ for _ in ()).throw(AssertionError("write"))
    )
    monkeypatch.setattr(
        SQLiteConnector, "read", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read"))
    )
    result = pipeline.run(connectors=built)
    assert result.data_paths == {"g2": "pushdown"}
    assert result.records_written == 5
    assert _rows(db) == [(i, f"n{i}") for i in range(5)]
    conn.close()


def test_graph_chain_cross_connection_falls_back_to_materialize(db: str, tmp_path: Path) -> None:
    other = str(tmp_path / "other_graph.db")
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE dst (id INTEGER, name TEXT)")
    con.commit()
    con.close()
    from etl_plugins.runtime.builder import build_pipeline

    src = SQLiteConnector(database=db)
    dst = SQLiteConnector(database=other)
    src.connect()
    dst.connect()
    pipeline, built = build_pipeline(
        _graph_config(db, sink_connection="other"), connectors={"db": src, "other": dst}
    )
    result = pipeline.run(connectors=built)
    assert result.data_paths == {"gp": "graph"}  # materialize engine, local DuckDB
    assert _rows(other) == [(i, f"N{i}") for i in range(5)]
    src.close()
    dst.close()


def test_pushdown_flag_typo_rejected_at_build() -> None:
    """``pushdown: "yes"`` would silently never engage — reject it."""
    from etl_plugins.core.exceptions import ConfigError

    with pytest.raises(ConfigError, match="pushdown"):
        build_transform(TransformConfig(type="sql", query="SELECT 1", pushdown="yes"))
