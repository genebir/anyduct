"""Operator DAG — ``sql`` / ``proc_call`` operator kinds (ADR-0099).

Promotes task-DAG into a typed Operator DAG: alongside the historical ``etl``
task (source→transforms→sink), a task can be a pure orchestration step that
runs a SQL statement (``sql``) or calls a stored procedure (``proc_call``)
against a connection — no dataflow. Rows-affected is published to XCom so a
downstream step (e.g. a batch log) can read ``{{ xcom.<op>.records_written }}``.
"""

from __future__ import annotations

import pytest

from etl_plugins.config.models import PipelineConfig
from etl_plugins.core.exceptions import TaskError
from etl_plugins.core.pipeline import Pipeline, Task
from etl_plugins.core.record import Record
from etl_plugins.runtime.builder import build_pipeline
from tests.fixtures.connectors import InMemoryBatchSink, InMemoryBatchSource


class _SqlConn:
    """Connector that is also a SqlExecutor — records the statements it ran and
    returns a scripted rows-affected per call."""

    def __init__(self, rowcounts: list[int] | None = None) -> None:
        self.statements: list[str] = []
        self._rowcounts = list(rowcounts or [])

    # Connector protocol
    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def health_check(self) -> bool:
        return True

    # SqlExecutor
    def execute_statement(self, statement: str) -> int:
        self.statements.append(statement)
        return self._rowcounts.pop(0) if self._rowcounts else 0


# ---------------------------- model validation ------------------------------


def test_sql_kind_requires_connection_and_statements() -> None:
    with pytest.raises(ValueError, match="needs non-empty 'statements'"):
        PipelineConfig.model_validate(
            {"name": "p", "tasks": [{"name": "d", "kind": "sql", "connection": "wh"}]}
        )


def test_proc_call_kind_requires_procedure() -> None:
    with pytest.raises(ValueError, match="needs a 'procedure'"):
        PipelineConfig.model_validate(
            {"name": "p", "tasks": [{"name": "d", "kind": "proc_call", "connection": "wh"}]}
        )


def test_operator_kind_rejects_source_sink() -> None:
    with pytest.raises(ValueError, match="takes no source/sink"):
        PipelineConfig.model_validate(
            {
                "name": "p",
                "tasks": [
                    {
                        "name": "d",
                        "kind": "sql",
                        "connection": "wh",
                        "statements": ["DELETE FROM t"],
                        "source": {"connection": "wh", "query": "SELECT 1"},
                    }
                ],
            }
        )


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        PipelineConfig.model_validate(
            {"name": "p", "tasks": [{"name": "d", "kind": "nope", "connection": "wh"}]}
        )


def test_etl_kind_still_requires_source() -> None:
    with pytest.raises(ValueError, match="kind 'etl' needs a 'source'"):
        PipelineConfig.model_validate(
            {"name": "p", "tasks": [{"name": "d", "sink": {"connection": "wh", "table": "t"}}]}
        )


# ------------------------------ execution -----------------------------------


def test_sql_operator_runs_statements_and_pushes_rowcount() -> None:
    conn = _SqlConn(rowcounts=[3, 0])
    task = Task(
        name="cleanup",
        kind="sql",
        op_connection="wh",
        statements=["DELETE FROM mart WHERE d = '20260601'", "ANALYZE mart"],
    )
    p = Pipeline("p").add(task)
    result = p.run(connectors={"wh": conn})

    assert result.success is True
    assert conn.statements == ["DELETE FROM mart WHERE d = '20260601'", "ANALYZE mart"]
    # rows-affected summed (3 + 0) and published to XCom.
    assert p._xcom["cleanup"]["records_written"] == 3
    assert result.data_paths.get("cleanup") == "sql"


class _SlowSqlConn(_SqlConn):
    """SqlExecutor whose first statement sleeps past a tiny deadline so the
    *next* statement trips the cooperative timeout check."""

    def execute_statement(self, statement: str) -> int:
        import time

        self.statements.append(statement)
        time.sleep(0.05)
        return 0


def test_sql_operator_honors_timeout_between_statements() -> None:
    """``timeout_seconds`` on a sql operator is enforced cooperatively between
    statements (자유도 2단계). Regression: it used to be silently ignored — a
    runaway multi-statement step ran to completion regardless (2026-06-22)."""
    from etl_plugins.core.exceptions import TaskTimeoutError

    conn = _SlowSqlConn()
    task = Task(
        name="slow",
        kind="sql",
        op_connection="wh",
        statements=["SELECT pg_sleep_a()", "SELECT pg_sleep_b()", "SELECT pg_sleep_c()"],
        timeout_seconds=0.01,
    )
    p = Pipeline("p").add(task)
    with pytest.raises(TaskTimeoutError, match="exceeded timeout_seconds"):
        p.run(connectors={"wh": conn})
    # First statement ran (deadline checked before each); the timeout tripped
    # before the second, so not all three ran.
    assert len(conn.statements) < 3


def test_proc_call_operator_builds_call_statement() -> None:
    conn = _SqlConn()
    task = Task(
        name="log",
        kind="proc_call",
        op_connection="wh",
        procedure="BDA_DM_DB.PID_LOG",
        proc_args=["'PROCEDURE'", "'PID_X'", "1"],
    )
    p = Pipeline("p").add(task)
    result = p.run(connectors={"wh": conn})

    assert result.success is True
    assert conn.statements == ["CALL BDA_DM_DB.PID_LOG('PROCEDURE', 'PID_X', 1)"]
    assert result.data_paths.get("log") == "proc_call"


def test_sql_operator_missing_executor_fails() -> None:
    # An InMemory source is not a SqlExecutor.
    task = Task(name="d", kind="sql", op_connection="wh", statements=["DELETE FROM t"])
    p = Pipeline("p").add(task)
    with pytest.raises(TaskError, match="does not support execute_statement"):
        p.run(connectors={"wh": InMemoryBatchSource([])})


def test_proc_call_reads_upstream_xcom_in_args() -> None:
    """The BSASTS102 shape: an etl load publishes records_written; a downstream
    proc_call reads it via {{ xcom.load.records_written }} in its CALL args."""
    src = InMemoryBatchSource([Record(data={"i": 1}), Record(data={"i": 2})])
    sink = InMemoryBatchSink()
    log = _SqlConn()

    load = Task(name="load", source="s", sink="k")
    end_log = Task(
        name="end_log",
        kind="proc_call",
        op_connection="logdb",
        procedure="PID_LOG",
        proc_args=["'END'", "{{ xcom.load.records_written }}"],
        depends_on=["load"],
    )
    p = Pipeline("p").add(load).add(end_log)
    src.connect()
    sink.connect()
    result = p.run(connectors={"s": src, "k": sink, "logdb": log})

    assert result.success is True
    # The upstream load wrote 2 rows → rendered into the CALL args.
    assert log.statements == ["CALL PID_LOG('END', 2)"]


def test_operator_kinds_build_via_builder() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "tasks": [
                {
                    "name": "start_log",
                    "kind": "proc_call",
                    "connection": "wh",
                    "procedure": "PID_LOG",
                    "args": ["'START'"],
                },
                {
                    "name": "cleanup",
                    "kind": "sql",
                    "connection": "wh",
                    "statements": ["DELETE FROM mart"],
                    "depends_on": ["start_log"],
                },
            ],
        }
    )
    conn = _SqlConn()
    pipeline, _ = build_pipeline(cfg, {"wh": conn})
    assert [t.kind for t in pipeline.tasks] == ["proc_call", "sql"]
    result = pipeline.run(connectors={"wh": conn})
    assert result.success is True
    assert conn.statements == ["CALL PID_LOG('START')", "DELETE FROM mart"]
