"""Integration tests — Postgres Arrow fast path (ADR-0093 P2b).

Real COPY csv round-trips against a testcontainers Postgres: type
fidelity (incl. the NULL vs '' CSV trap), partition slicing, modes,
and the end-to-end Pipeline bulk path with both ends on Postgres.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
import pyarrow as pa
import pytest

from etl_plugins.connectors.rdbms.postgres import PostgresConnector
from etl_plugins.core.arrow import Partition
from etl_plugins.core.exceptions import WriteError
from etl_plugins.core.pipeline import Pipeline, Task

pytestmark = pytest.mark.it


@pytest.fixture
def typed_table(pg_raw_kwargs: dict[str, Any]):
    """A table exercising the OID→Arrow mapping (dropped on teardown)."""
    name = f"etl_arrow_{uuid4().hex[:8]}"
    with psycopg.connect(**pg_raw_kwargs) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE {name} ("
                "id BIGINT, label TEXT, score DOUBLE PRECISION, "
                "active BOOLEAN, created_at TIMESTAMPTZ)"
            )
        conn.commit()
    yield name
    with psycopg.connect(**pg_raw_kwargs) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {name}")
        conn.commit()


def _batch(rows: list[dict[str, Any]]) -> pa.RecordBatch:
    return pa.Table.from_pylist(rows).to_batches()[0]


def _end_txn(pg: PostgresConnector) -> None:
    """Release the read transaction so fixture DROP TABLE doesn't block.

    ``read()``/``read_arrow()`` leave the (autocommit-off) psycopg
    connection idle-in-transaction holding ACCESS SHARE; the teardown's
    ACCESS EXCLUSIVE DROP would wait on it forever.
    """
    pg.connection.rollback()


@pytest.fixture
def apg(pg_conn_params: dict[str, Any], typed_table: str) -> Any:
    """Connected connector that depends on ``typed_table`` so teardown
    closes the connection (releasing any open read transaction) BEFORE
    the table drop — a failing test can't wedge the fixture chain."""
    pg = PostgresConnector(**pg_conn_params)
    pg.connect()
    yield pg
    pg.close()


class TestWriteArrow:
    def test_append_round_trip_with_null_vs_empty_string(
        self, apg: PostgresConnector, typed_table: str
    ) -> None:
        ts = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
        rows = [
            {"id": 1, "label": "alpha", "score": 1.5, "active": True, "created_at": ts},
            # the CSV trap: '' must stay '', None must stay NULL
            {"id": 2, "label": "", "score": None, "active": False, "created_at": None},
            {"id": 3, "label": None, "score": -0.25, "active": None, "created_at": ts},
        ]

        written = apg.write_arrow(iter([_batch(rows)]), table=typed_table)
        assert written == 3
        got = list(apg.read(query=f"SELECT * FROM {typed_table} ORDER BY id"))
        assert got[0].data["label"] == "alpha"
        assert got[0].data["created_at"] == ts
        assert got[1].data["label"] == ""  # NOT null
        assert got[1].data["score"] is None
        assert got[2].data["label"] is None  # NOT ''
        assert got[2].data["active"] is None
        _end_txn(apg)

    def test_overwrite_replaces(self, apg: PostgresConnector, typed_table: str) -> None:
        apg.write_arrow(iter([_batch([{"id": 1, "label": "old"}])]), table=typed_table)
        apg.write_arrow(
            iter([_batch([{"id": 9, "label": "new"}])]), table=typed_table, mode="overwrite"
        )
        got = list(apg.read(query=f"SELECT id FROM {typed_table}"))
        assert [r.data["id"] for r in got] == [9]
        _end_txn(apg)

    def test_upsert_rejected(self, apg: PostgresConnector, typed_table: str) -> None:
        with pytest.raises(WriteError, match=r"append.*overwrite|overwrite.*append"):
            apg.write_arrow(
                iter([_batch([{"id": 1}])]), table=typed_table, mode="upsert", key_columns=["id"]
            )

    def test_empty_stream_writes_nothing(self, apg: PostgresConnector, typed_table: str) -> None:
        assert apg.write_arrow(iter([]), table=typed_table) == 0


class TestReadArrow:
    @pytest.fixture
    def seeded(self, apg: PostgresConnector, typed_table: str) -> str:
        ts = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UTC)
        rows = [
            {"id": i, "label": f"L{i}", "score": i / 2, "active": i % 2 == 0, "created_at": ts}
            for i in range(1, 11)
        ]
        apg.write_arrow(iter([_batch(rows)]), table=typed_table)
        return typed_table

    def test_typed_batches(self, apg: PostgresConnector, seeded: str) -> None:
        batches = list(apg.read_arrow(query=f"SELECT * FROM {seeded} ORDER BY id"))
        _end_txn(apg)
        table = pa.Table.from_batches(batches)
        assert table.num_rows == 10
        assert table.schema.field("id").type == pa.int64()
        assert table.schema.field("label").type == pa.string()
        assert table.schema.field("score").type == pa.float64()
        assert table.schema.field("active").type == pa.bool_()
        assert str(table.schema.field("created_at").type).startswith("timestamp")
        assert table.column("id").to_pylist() == list(range(1, 11))

    def test_partition_slice_is_half_open(self, apg: PostgresConnector, seeded: str) -> None:
        batches = list(
            apg.read_arrow(
                query=f"SELECT * FROM {seeded}",
                partition=Partition("id", lower=3, upper=7),
            )
        )
        _end_txn(apg)
        ids = sorted(pa.Table.from_batches(batches).column("id").to_pylist())
        assert ids == [4, 5, 6, 7]  # (3, 7]

    def test_partitions_cover_without_overlap(self, apg: PostgresConnector, seeded: str) -> None:
        parts = [Partition("id", None, 5), Partition("id", 5, None)]
        seen: list[int] = []
        for p in parts:
            for b in apg.read_arrow(query=f"SELECT * FROM {seeded}", partition=p):
                seen.extend(b.column("id").to_pylist())
        _end_txn(apg)
        assert sorted(seen) == list(range(1, 11))


class TestPipelineFastPath:
    def test_pg_to_pg_bulk_pipeline(
        self,
        apg: PostgresConnector,
        pg_conn_params: dict[str, Any],
        typed_table: str,
        pg_raw_kwargs: dict[str, Any],
    ) -> None:
        """Both ends Arrow-capable + no transforms → Record plane bypassed."""
        dst_table = f"etl_arrow_dst_{uuid4().hex[:8]}"
        with psycopg.connect(**pg_raw_kwargs) as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE TABLE {dst_table} (id BIGINT, label TEXT)")
            conn.commit()
        try:
            src = apg
            src.write_arrow(
                iter([_batch([{"id": i, "label": f"L{i}"} for i in range(500)])]),
                table=typed_table,
            )
            dst = PostgresConnector(**pg_conn_params)
            dst.connect()
            task = Task(
                name="bulk",
                source="src",
                sink="dst",
                query=f"SELECT id, label FROM {typed_table} ORDER BY id",
                sink_table=dst_table,
            )
            result = Pipeline(name="bulk-p", tasks=[task]).run(connectors={"src": src, "dst": dst})
            assert result.records_read == 500
            assert result.records_written == 500
            got = list(dst.read(query=f"SELECT COUNT(*) AS n FROM {dst_table}"))
            assert got[0].data["n"] == 500
            dst.close()
            _end_txn(src)
        finally:
            with psycopg.connect(**pg_raw_kwargs) as conn:
                with conn.cursor() as cur:
                    cur.execute(f"DROP TABLE IF EXISTS {dst_table}")
                conn.commit()


class TestGraphChainFastPath:
    """ADR-0093 P2 follow-up: the builder saves pipelines as graphs — a
    trivial source → sink graph chain must take the same bulk COPY path."""

    def test_graph_two_node_chain_bulk_copies(
        self,
        apg: PostgresConnector,
        pg_conn_params: dict[str, Any],
        typed_table: str,
        pg_raw_kwargs: dict[str, Any],
    ) -> None:
        from etl_plugins.config.models import PipelineConfig
        from etl_plugins.runtime.builder import build_pipeline

        dst_table = f"etl_graph_dst_{uuid4().hex[:8]}"
        with psycopg.connect(**pg_raw_kwargs) as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE TABLE {dst_table} (id BIGINT, label TEXT)")
            conn.commit()
        try:
            apg.write_arrow(
                iter([_batch([{"id": i, "label": f"L{i}"} for i in range(300)])]),
                table=typed_table,
            )
            cfg = PipelineConfig.model_validate(
                {
                    "name": "graph-bulk",
                    "graph": {
                        "nodes": [
                            {
                                "id": "s",
                                "type": "source",
                                "connection": "src",
                                "query": f"SELECT id, label FROM {typed_table}",
                            },
                            {"id": "k", "type": "sink", "connection": "dst", "table": dst_table},
                        ],
                        "edges": [{"from_node": "s", "to_node": "k"}],
                    },
                }
            )
            dst = PostgresConnector(**pg_conn_params)
            dst.connect()
            try:
                pipeline, built = build_pipeline(cfg, connectors={"src": apg, "dst": dst})
                result = pipeline.run(connectors=built)
                assert result.data_paths == {"graph-bulk": "arrow"}
                assert result.records_read == 300
                assert result.records_written == 300
            finally:
                dst.close()
            _end_txn(apg)
            with psycopg.connect(**pg_raw_kwargs) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {dst_table}")
                row = cur.fetchone()
                assert row is not None and row[0] == 300
        finally:
            with psycopg.connect(**pg_raw_kwargs) as conn:
                with conn.cursor() as cur:
                    cur.execute(f"DROP TABLE IF EXISTS {dst_table}")
                conn.commit()


class TestSqlPushdown:
    """ADR-0093 P2c: source==sink connection collapses to INSERT…SELECT."""

    def test_same_connection_pushdown(
        self, apg: PostgresConnector, typed_table: str, pg_raw_kwargs: dict[str, Any]
    ) -> None:
        dst_table = f"etl_push_dst_{uuid4().hex[:8]}"
        with psycopg.connect(**pg_raw_kwargs) as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE TABLE {dst_table} (id BIGINT, label TEXT)")
            conn.commit()
        try:
            apg.write_arrow(
                iter([_batch([{"id": i, "label": f"L{i}"} for i in range(100)])]),
                table=typed_table,
            )

            # Same connection name on both ends → in-database INSERT…SELECT.
            # Prove no rows moved through Python: poison read/write/Arrow.
            def poison(*a: Any, **k: Any) -> Any:
                raise AssertionError("moved rows")

            for meth in ("read", "write", "read_arrow", "write_arrow"):
                setattr(apg, meth, poison)
            task = Task(
                name="push",
                source="db",
                sink="db",
                query=f"SELECT id, label FROM {typed_table}",
                sink_table=dst_table,
            )
            result = Pipeline(name="push-p", tasks=[task]).run(connectors={"db": apg})
            assert result.records_read == 100
            assert result.records_written == 100
            with psycopg.connect(**pg_raw_kwargs) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {dst_table}")
                row = cur.fetchone()
                assert row is not None and row[0] == 100
        finally:
            with psycopg.connect(**pg_raw_kwargs) as conn:
                with conn.cursor() as cur:
                    cur.execute(f"DROP TABLE IF EXISTS {dst_table}")
                conn.commit()

    def test_elt_pushdown_runs_sql_transform_in_database(
        self, apg: PostgresConnector, typed_table: str, pg_raw_kwargs: dict[str, Any]
    ) -> None:
        """ADR-0094: ``{type: sql, pushdown: true}`` composes into
        ``INSERT INTO … WITH input AS (<source>) <query>`` and Postgres
        runs the GROUP BY itself — read/write poisoned, so any row
        leaving the database fails the test."""
        from etl_plugins.config.models import TransformConfig
        from etl_plugins.runtime.transforms import build_transform

        dst_table = f"etl_elt_dst_{uuid4().hex[:8]}"
        with psycopg.connect(**pg_raw_kwargs) as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE TABLE {dst_table} (active BOOLEAN, n BIGINT)")
            conn.commit()
        try:
            apg.write_arrow(
                iter(
                    [_batch([{"id": i, "label": f"L{i}", "active": i % 2 == 0} for i in range(50)])]
                ),
                table=typed_table,
            )

            def poison(*a: Any, **k: Any) -> Any:
                raise AssertionError("moved rows")

            for meth in ("read", "write", "read_arrow", "write_arrow"):
                setattr(apg, meth, poison)
            tc = TransformConfig(
                type="sql",
                query="SELECT active, COUNT(*) AS n FROM input GROUP BY active",
                pushdown=True,
            )
            task = Task(
                name="elt",
                source="db",
                sink="db",
                query=f"SELECT id, active FROM {typed_table}",
                sink_table=dst_table,
                transforms=[build_transform(tc)],
                transform_specs=[tc.model_dump()],
            )
            result = Pipeline(name="elt-p", tasks=[task]).run(connectors={"db": apg})
            assert result.data_paths == {"elt": "pushdown"}
            assert result.records_written == 2  # one row per active group
            with psycopg.connect(**pg_raw_kwargs) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT active, n FROM {dst_table} ORDER BY active")
                assert cur.fetchall() == [(False, 25), (True, 25)]
        finally:
            with psycopg.connect(**pg_raw_kwargs) as conn:
                with conn.cursor() as cur:
                    cur.execute(f"DROP TABLE IF EXISTS {dst_table}")
                conn.commit()
