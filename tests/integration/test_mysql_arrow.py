"""Integration tests — MySQL Arrow fast path (ADR-0093 P2 follow-up).

Real round-trips against a testcontainers MySQL 8. MySQL has no
client-streamable COPY, so ``read_arrow`` builds columnar batches from a
server-side tuple cursor (the win is skipping the per-row Record/pydantic
layer) and ``write_arrow`` binds multi-row ``executemany`` slices. Covers
type pinning (ints/floats/datetime pinned from field-type codes; TEXT and
DECIMAL inferred then locked), the NULL round-trip, partition slicing,
modes, the mysql→mysql Pipeline bulk path, and the cross-vendor
mysql→postgres interchange.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import psycopg
import pyarrow as pa
import pymysql
import pytest

from etl_plugins.connectors.rdbms.mysql import MySQLConnector
from etl_plugins.connectors.rdbms.postgres import PostgresConnector
from etl_plugins.core.arrow import Partition
from etl_plugins.core.exceptions import WriteError
from etl_plugins.core.pipeline import Pipeline, Task

pytestmark = pytest.mark.it


@pytest.fixture
def typed_table(mysql_conn_params: dict[str, Any]):
    """A table exercising the field-type → Arrow mapping (dropped on teardown)."""
    name = f"etl_arrow_{uuid4().hex[:8]}"
    with pymysql.connect(**mysql_conn_params) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE `{name}` ("
                "id BIGINT, label TEXT, score DOUBLE, amount DECIMAL(10,2), "
                "active BOOLEAN, created_at DATETIME)"
            )
        conn.commit()
    yield name
    with pymysql.connect(**mysql_conn_params) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS `{name}`")
        conn.commit()


@pytest.fixture
def amy(mysql_conn_params: dict[str, Any], typed_table: str) -> Any:
    """Connected connector; closed before the table drop (fixture order)."""
    my = MySQLConnector(**mysql_conn_params)
    my.connect()
    yield my
    my.close()


def _batch(rows: list[dict[str, Any]]) -> pa.RecordBatch:
    return pa.Table.from_pylist(rows).to_batches()[0]


class TestWriteArrow:
    def test_append_round_trip_with_nulls(self, amy: MySQLConnector, typed_table: str) -> None:
        ts = datetime(2026, 6, 12, 1, 30, 0)
        rows = [
            {
                "id": 1,
                "label": "alpha",
                "score": 1.5,
                "amount": Decimal("10.25"),
                "active": True,
                "created_at": ts,
            },
            {
                "id": 2,
                "label": "",
                "score": None,
                "amount": None,
                "active": False,
                "created_at": None,
            },
            {
                "id": 3,
                "label": None,
                "score": -0.25,
                "amount": Decimal("0.00"),
                "active": None,
                "created_at": ts,
            },
        ]
        written = amy.write_arrow(iter([_batch(rows)]), table=typed_table)
        assert written == 3
        got = list(amy.read(query=f"SELECT * FROM `{typed_table}` ORDER BY id"))
        assert got[0].data["label"] == "alpha"
        assert got[0].data["created_at"] == ts
        assert got[0].data["amount"] == Decimal("10.25")
        assert got[1].data["label"] == ""  # NOT null
        assert got[1].data["score"] is None
        assert got[2].data["label"] is None  # NOT ''
        assert got[2].data["active"] is None

    def test_overwrite_replaces(self, amy: MySQLConnector, typed_table: str) -> None:
        amy.write_arrow(iter([_batch([{"id": 1, "label": "old"}])]), table=typed_table)
        amy.write_arrow(
            iter([_batch([{"id": 9, "label": "new"}])]), table=typed_table, mode="overwrite"
        )
        got = list(amy.read(query=f"SELECT id FROM `{typed_table}`"))
        assert [r.data["id"] for r in got] == [9]

    def test_upsert_rejected(self, amy: MySQLConnector, typed_table: str) -> None:
        with pytest.raises(WriteError, match=r"append.*overwrite|overwrite.*append"):
            amy.write_arrow(
                iter([_batch([{"id": 1}])]), table=typed_table, mode="upsert", key_columns=["id"]
            )

    def test_empty_stream_writes_nothing(self, amy: MySQLConnector, typed_table: str) -> None:
        assert amy.write_arrow(iter([]), table=typed_table) == 0


class TestReadArrow:
    @pytest.fixture
    def seeded(self, amy: MySQLConnector, typed_table: str) -> str:
        ts = datetime(2026, 6, 12, 1, 30, 0)
        rows = [
            {
                "id": i,
                "label": f"L{i}",
                "score": i / 2,
                "amount": Decimal(f"{i}.50"),
                "active": i % 2 == 0,
                "created_at": ts,
            }
            for i in range(1, 11)
        ]
        amy.write_arrow(iter([_batch(rows)]), table=typed_table)
        return typed_table

    def test_typed_batches(self, amy: MySQLConnector, seeded: str) -> None:
        batches = list(amy.read_arrow(query=f"SELECT * FROM `{seeded}` ORDER BY id"))
        table = pa.Table.from_batches(batches)
        assert table.num_rows == 10
        assert table.schema.field("id").type == pa.int64()
        assert table.schema.field("score").type == pa.float64()
        assert str(table.schema.field("created_at").type).startswith("timestamp")
        # BOOLEAN is TINYINT(1) on the wire — int64 is the honest mapping.
        assert table.schema.field("active").type == pa.int64()
        # TEXT and DECIMAL are inferred from values, then locked.
        assert table.schema.field("label").type == pa.string()
        assert pa.types.is_decimal(table.schema.field("amount").type)
        assert table.column("id").to_pylist() == list(range(1, 11))

    def test_chunked_batches_keep_one_schema(self, amy: MySQLConnector, seeded: str) -> None:
        batches = list(amy.read_arrow(query=f"SELECT * FROM `{seeded}` ORDER BY id", chunk_size=3))
        assert len(batches) == 4  # 3+3+3+1
        schemas = {b.schema for b in batches}
        assert len(schemas) == 1  # inferred types locked after the first chunk

    def test_partition_slice_is_half_open(self, amy: MySQLConnector, seeded: str) -> None:
        batches = list(
            amy.read_arrow(
                query=f"SELECT * FROM `{seeded}`",
                partition=Partition("id", lower=3, upper=7),
            )
        )
        ids = sorted(pa.Table.from_batches(batches).column("id").to_pylist())
        assert ids == [4, 5, 6, 7]  # (3, 7]

    def test_partitions_cover_without_overlap(self, amy: MySQLConnector, seeded: str) -> None:
        parts = [Partition("id", None, 5), Partition("id", 5, None)]
        seen: list[int] = []
        for p in parts:
            for b in amy.read_arrow(query=f"SELECT * FROM `{seeded}`", partition=p):
                seen.extend(b.column("id").to_pylist())
        assert sorted(seen) == list(range(1, 11))


class TestPipelineFastPath:
    def test_mysql_to_mysql_bulk_pipeline(
        self,
        amy: MySQLConnector,
        mysql_conn_params: dict[str, Any],
        typed_table: str,
    ) -> None:
        """Both ends Arrow-capable + no transforms → Record plane bypassed."""
        dst_table = f"etl_arrow_dst_{uuid4().hex[:8]}"
        with pymysql.connect(**mysql_conn_params) as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE TABLE `{dst_table}` (id BIGINT, label TEXT)")
            conn.commit()
        try:
            amy.write_arrow(
                iter([_batch([{"id": i, "label": f"L{i}"} for i in range(200)])]),
                table=typed_table,
            )
            dst = MySQLConnector(**mysql_conn_params)
            dst.connect()

            def poison(*a: Any, **k: Any) -> Any:
                raise AssertionError("moved rows through the Record plane")

            amy.read = poison  # type: ignore[method-assign]
            dst.write = poison  # type: ignore[method-assign]
            task = Task(
                name="bulk",
                source="src",
                sink="dst",
                query=f"SELECT id, label FROM `{typed_table}` ORDER BY id",
                sink_table=dst_table,
            )
            try:
                result = Pipeline(name="my-bulk", tasks=[task]).run(
                    connectors={"src": amy, "dst": dst}
                )
            finally:
                dst.close()
            assert result.records_read == 200
            assert result.records_written == 200
            assert result.data_paths == {"bulk": "arrow"}
            with pymysql.connect(**mysql_conn_params) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM `{dst_table}`")
                assert cur.fetchone()[0] == 200
        finally:
            with pymysql.connect(**mysql_conn_params) as conn:
                with conn.cursor() as cur:
                    cur.execute(f"DROP TABLE IF EXISTS `{dst_table}`")
                conn.commit()

    def test_mysql_to_postgres_cross_vendor_bulk(
        self,
        amy: MySQLConnector,
        typed_table: str,
        pg_conn_params: dict[str, Any],
        pg_raw_kwargs: dict[str, Any],
    ) -> None:
        """Arrow is the interchange: mysql read_arrow → postgres COPY write."""
        dst_table = f"etl_my2pg_{uuid4().hex[:8]}"
        with psycopg.connect(**pg_raw_kwargs) as conn:
            with conn.cursor() as cur:
                cur.execute(f"CREATE TABLE {dst_table} (id BIGINT, label TEXT)")
            conn.commit()
        try:
            amy.write_arrow(
                iter([_batch([{"id": i, "label": f"L{i}"} for i in range(150)])]),
                table=typed_table,
            )
            pg = PostgresConnector(**pg_conn_params)
            pg.connect()
            task = Task(
                name="x",
                source="src",
                sink="dst",
                query=f"SELECT id, label FROM `{typed_table}` ORDER BY id",
                sink_table=dst_table,
            )
            try:
                result = Pipeline(name="my2pg", tasks=[task]).run(
                    connectors={"src": amy, "dst": pg}
                )
            finally:
                pg.close()
            assert result.data_paths == {"x": "arrow"}
            assert result.records_written == 150
            with psycopg.connect(**pg_raw_kwargs) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*), MIN(id), MAX(id) FROM {dst_table}")
                assert cur.fetchone() == (150, 0, 149)
        finally:
            with psycopg.connect(**pg_raw_kwargs) as conn:
                with conn.cursor() as cur:
                    cur.execute(f"DROP TABLE IF EXISTS {dst_table}")
                conn.commit()
