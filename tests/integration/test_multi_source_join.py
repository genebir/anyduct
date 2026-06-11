"""Multi-source JOIN e2e (Phase P1c, ADR-0093).

Two *different database vendors* fan into one dataflow graph:

    postgres orders ──┐
                      ├─ join(customer_id) ─ sql dataset transform ─ postgres sink
    mysql customers ──┘

This is the canonical "JOIN data that lives in two systems" flow that the
row-by-row plane alone could never express — graph fan-in (ADR-0041)
merges the streams, then the DuckDB ``sql`` transform (P1a) runs a real
GROUP BY over the joined dataset. The whole shape is authored as a plain
``PipelineConfig`` (the wire shape the builder UI emits) and executed
against real testcontainers databases.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pymysql
import pytest

from etl_plugins.config.models import PipelineConfig
from etl_plugins.connectors.rdbms.mysql import MySQLConnector
from etl_plugins.connectors.rdbms.postgres import PostgresConnector
from etl_plugins.runtime.builder import build_pipeline

pytestmark = pytest.mark.it

_ORDERS = "p1c_orders"
_CUSTOMERS = "p1c_customers"
_OUT = "p1c_region_totals"


def _seed_postgres(pg_raw_kwargs: dict[str, Any]) -> None:
    with psycopg.connect(**pg_raw_kwargs, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {_ORDERS}")
        cur.execute(f"DROP TABLE IF EXISTS {_OUT}")
        cur.execute(f"CREATE TABLE {_ORDERS} (customer_id BIGINT, amount DOUBLE PRECISION)")
        # Customer 99 has no customer record — the inner join must drop it.
        cur.execute(
            f"INSERT INTO {_ORDERS} VALUES "
            "(1, 10.5), (1, 4.5), (2, 3.0), (3, 7.0), (3, 8.0), (99, 100.0)"
        )
        cur.execute(f"CREATE TABLE {_OUT} (region TEXT, total DOUBLE PRECISION, n BIGINT)")


def _seed_mysql(mysql_conn_params: dict[str, Any]) -> None:
    with pymysql.connect(**mysql_conn_params) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {_CUSTOMERS}")
            cur.execute(
                f"CREATE TABLE {_CUSTOMERS} "
                "(customer_id BIGINT, name VARCHAR(64), region VARCHAR(16))"
            )
            # Customer 4 has no orders — the inner join must drop them too.
            cur.execute(
                f"INSERT INTO {_CUSTOMERS} VALUES "
                "(1, 'alice', 'EU'), (2, 'bob', 'US'), (3, 'carol', 'EU'), (4, 'dave', 'APAC')"
            )
        conn.commit()


def _cleanup(pg_raw_kwargs: dict[str, Any], mysql_conn_params: dict[str, Any]) -> None:
    with psycopg.connect(**pg_raw_kwargs, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {_ORDERS}")
        cur.execute(f"DROP TABLE IF EXISTS {_OUT}")
    with pymysql.connect(**mysql_conn_params) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {_CUSTOMERS}")
        conn.commit()


def test_postgres_mysql_fan_in_join_sql_transform(
    pg_raw_kwargs: dict[str, Any],
    pg_conn_params: dict[str, Any],
    mysql_conn_params: dict[str, Any],
) -> None:
    """postgres orders x mysql customers → join → DuckDB GROUP BY → postgres.

    Inner-join semantics across vendors: orders without a customer (99)
    and customers without orders (dave) both vanish; the dataset SQL then
    aggregates the surviving 5 joined rows into per-region totals.
    """
    _seed_postgres(pg_raw_kwargs)
    _seed_mysql(mysql_conn_params)

    cfg = PipelineConfig.model_validate(
        {
            "name": "p1c_multi_source_join",
            "graph": {
                "nodes": [
                    {
                        "id": "orders",
                        "type": "source",
                        "connection": "pg",
                        "query": f"SELECT customer_id, amount FROM {_ORDERS}",
                    },
                    {
                        "id": "customers",
                        "type": "source",
                        "connection": "my",
                        "query": f"SELECT customer_id, name, region FROM {_CUSTOMERS}",
                    },
                    {"id": "j", "type": "join", "on": ["customer_id"], "how": "inner"},
                    {
                        "id": "agg",
                        "type": "transform",
                        "transform": {
                            "type": "sql",
                            "query": (
                                "SELECT region, SUM(amount) AS total, COUNT(*) AS n "
                                "FROM input GROUP BY region ORDER BY region"
                            ),
                        },
                    },
                    {
                        "id": "out",
                        "type": "sink",
                        "connection": "pg_out",
                        "table": _OUT,
                        "mode": "append",
                    },
                ],
                "edges": [
                    {"from_node": "orders", "to_node": "j"},
                    {"from_node": "customers", "to_node": "j"},
                    {"from_node": "j", "to_node": "agg"},
                    {"from_node": "agg", "to_node": "out"},
                ],
            },
        }
    )

    connectors = {
        "pg": PostgresConnector(**pg_conn_params),
        "my": MySQLConnector(**mysql_conn_params),
        # Dedicated write connection so the sink never shares the read
        # connection's transaction state.
        "pg_out": PostgresConnector(**pg_conn_params),
    }
    pipeline, built = build_pipeline(cfg, connectors=connectors)
    try:
        for c in built.values():
            c.connect()
        result = pipeline.run(connectors=built)
    finally:
        for c in built.values():
            c.close()

    assert result.success
    # Graph tasks report the materialize engine as their data path (P2d).
    assert result.data_paths == {"p1c_multi_source_join": "graph"}

    with psycopg.connect(**pg_raw_kwargs) as conn, conn.cursor() as cur:
        cur.execute(f"SELECT region, total, n FROM {_OUT} ORDER BY region")
        rows = cur.fetchall()
    # EU = alice's 2 orders + carol's 2 orders; US = bob's single order.
    assert rows == [("EU", 30.0, 4), ("US", 3.0, 1)]

    _cleanup(pg_raw_kwargs, mysql_conn_params)
