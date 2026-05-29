"""Cross-DB replication integration scenarios (Phase VV, ADR-0066, 2026-05-29).

The unit + server e2e tests already cover sqlite-to-sqlite type
translation. This module exercises the *real* cross-DB path: postgres
source → sqlite sink (the canonical "OLTP into local analytics
sandbox" flow) using testcontainers postgres.

Type translations the scenario validates end-to-end:

* ``BIGINT`` → sqlite ``INTEGER`` (type-affinity collapse).
* ``NUMERIC(10,2)`` → sqlite ``NUMERIC(10,2)``.
* ``TIMESTAMPTZ`` → sqlite ``TEXT`` (ISO-8601 strings round-trip best).
* ``JSONB`` → sqlite ``TEXT``.
* ``VARCHAR(64)`` → sqlite ``TEXT`` (length spec dropped).
* ``BOOLEAN`` → sqlite ``INTEGER`` (1/0).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import psycopg
import pytest

from etl_plugins.connectors.rdbms.postgres import PostgresConnector
from etl_plugins.connectors.rdbms.sqlite import SQLiteConnector
from etl_plugins.core.record import Record


@pytest.mark.it
def test_postgres_to_sqlite_auto_create_table_and_replicate(
    pg_raw_kwargs: dict[str, Any],
    pg_conn_params: dict[str, Any],
    tmp_path: Path,
) -> None:
    """The engineer's daily flow: a postgres OLTP table, a sqlite
    analytics sandbox, and a one-config replication that *also* creates
    the destination table from the source schema.

    We seed postgres with a deliberately wide type palette so the
    translator gets exercised. After the run the sqlite table is
    inspected for affinity-correct types + actual row payload.
    """
    pg_table = "pg2sqlite_orders"
    sqlite_path = tmp_path / "analytics.db"

    # ---- Set up the postgres source side ----
    with psycopg.connect(**pg_raw_kwargs, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {pg_table}")
        cur.execute(
            f"CREATE TABLE {pg_table} ("
            "id BIGINT PRIMARY KEY, "
            "amount NUMERIC(10,2), "
            "created_at TIMESTAMPTZ, "
            "payload JSONB, "
            "customer VARCHAR(64), "
            "active BOOLEAN)"
        )
        cur.execute(
            f"INSERT INTO {pg_table} VALUES "
            "(1, 100.50, '2026-05-01T00:00:00Z', '{\"a\": 1}'::jsonb, 'alice', TRUE), "
            "(2, 250.75, '2026-05-02T00:00:00Z', '{\"b\": 2}'::jsonb, 'bob', FALSE)"
        )

    # ---- Read columns from postgres and translate to a sqlite DDL ----
    pg = PostgresConnector(**pg_conn_params)
    with pg:
        cols = pg.list_columns(pg_table)
    # information_schema names live in lower-case for unquoted columns.
    by_name = {c.name: c.type for c in cols}
    # Phase VV postgres ``list_columns`` folds precision/scale +
    # character_maximum_length back into the type string.
    assert by_name["id"] == "bigint"
    assert by_name["amount"] == "numeric(10,2)"
    assert by_name["created_at"] in ("timestamp with time zone", "timestamptz")
    assert by_name["payload"] == "jsonb"
    assert by_name["customer"] == "character varying(64)"
    assert by_name["active"] == "boolean"

    # ---- Sink: call ensure_table on a fresh sqlite, then write rows ----
    sink = SQLiteConnector(database=str(sqlite_path))
    with sink:
        sink.ensure_table("orders_copy", cols)
        # Pull the actual rows from postgres and stream them through the
        # sink. Reuses the pg connection above; in production this is
        # what Pipeline.run does between source.read and sink.write.
        with pg:
            records = list(pg.read(query=f"SELECT * FROM {pg_table}"))
        assert len(records) == 2
        # Drop ``created_at`` to text for sqlite (sqlite stores datetimes
        # as text; the dst column was declared TEXT by ensure_table).
        norm: list[Record] = []
        for r in records:
            d = dict(r.data)
            if d.get("created_at") is not None:
                d["created_at"] = d["created_at"].isoformat()
            # JSON dicts → JSON strings for sqlite TEXT storage.
            if d.get("payload") is not None and not isinstance(d["payload"], str):
                import json as _json

                d["payload"] = _json.dumps(d["payload"])
            # NUMERIC values come back as Decimal; sqlite NUMERIC accepts
            # text — convert so the driver doesn't trip over the type
            # adapter.
            from decimal import Decimal as _Decimal

            if isinstance(d.get("amount"), _Decimal):
                d["amount"] = float(d["amount"])
            norm.append(Record(data=d, metadata=r.metadata))
        n = sink.write(iter(norm), table="orders_copy")
    assert n == 2

    # ---- Assert sqlite shape + payload ----
    out = sqlite3.connect(str(sqlite_path))
    try:
        info = out.execute('PRAGMA table_info("orders_copy")').fetchall()
        sqlite_types = {row[1]: row[2] for row in info}
        assert sqlite_types == {
            "id": "INTEGER",
            "amount": "NUMERIC(10,2)",
            "created_at": "TEXT",
            "payload": "TEXT",
            "customer": "TEXT",
            "active": "INTEGER",
        }
        rows = sorted(
            out.execute("SELECT id, amount, customer, active FROM orders_copy").fetchall()
        )
    finally:
        out.close()
    assert rows == [(1, 100.5, "alice", 1), (2, 250.75, "bob", 0)]


@pytest.mark.it
def test_sqlite_to_postgres_auto_create_table_and_replicate(
    pg_raw_kwargs: dict[str, Any],
    pg_conn_params: dict[str, Any],
    tmp_path: Path,
) -> None:
    """The reverse path: a sqlite source replicated into postgres with
    auto-created destination. Validates that sqlite's lax type strings
    (``INTEGER``, ``TEXT``, ``REAL``) get the right postgres
    equivalents (``INTEGER``, ``TEXT``, ``REAL``), and that ``BIGINT``
    survives the round-trip when explicitly declared."""
    src_db = tmp_path / "src.db"
    raw = sqlite3.connect(str(src_db))
    try:
        raw.execute(
            "CREATE TABLE sales ("
            "id INTEGER PRIMARY KEY, "
            "qty BIGINT, "
            "unit_price REAL, "
            "label TEXT, "
            "active BOOLEAN)"
        )
        raw.executemany(
            "INSERT INTO sales VALUES (?, ?, ?, ?, ?)",
            [(1, 5, 1.99, "alpha", 1), (2, 10, 2.49, "beta", 0)],
        )
        raw.commit()
    finally:
        raw.close()

    src = SQLiteConnector(database=str(src_db))
    with src:
        cols = src.list_columns("sales")

    pg_table = "sqlite2pg_sales"
    sink = PostgresConnector(**pg_conn_params)
    try:
        with sink:
            # Wipe any leftover from a previous run.
            sink.execute_statement(f"DROP TABLE IF EXISTS {pg_table}")
            sink.ensure_table(pg_table, cols)
            with src:
                records = list(src.read(query="SELECT * FROM sales"))
            n = sink.write(iter(records), table=pg_table)
        assert n == 2

        # Read back through psycopg directly to assert postgres types.
        with psycopg.connect(**pg_raw_kwargs) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (pg_table,),
            )
            pg_types = dict(cur.fetchall())
            assert pg_types == {
                "id": "integer",
                # sqlite's BIGINT declaration normalises through the
                # translator and emits postgres BIGINT.
                "qty": "bigint",
                "unit_price": "real",
                "label": "text",
                "active": "boolean",
            }
            cur.execute(f"SELECT id, qty, unit_price, label, active FROM {pg_table} ORDER BY id")
            rows = cur.fetchall()
            assert rows == [(1, 5, 1.99, "alpha", True), (2, 10, 2.49, "beta", False)]
    finally:
        # Teardown so the next test session starts fresh.
        with psycopg.connect(**pg_raw_kwargs, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {pg_table}")
