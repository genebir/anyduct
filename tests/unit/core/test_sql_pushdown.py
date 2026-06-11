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
