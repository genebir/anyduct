"""Integration test fixtures (testcontainers-based).

Every test that uses one of these fixtures should be marked with
``@pytest.mark.it`` (or the containing module sets ``pytestmark = pytest.mark.it``)
so that plain ``pytest -m "not it"`` skips them. CI runs them in a dedicated job.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

from etl_plugins.connectors.rdbms.postgres import PostgresConnector
from etl_plugins.core.record import Record


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    """A long-lived postgres:16-alpine container shared across the test session."""
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def pg_conn_params(pg_container: PostgresContainer) -> dict[str, Any]:
    """Params for ``PostgresConnector(**params)`` (uses ``database`` key)."""
    return {
        "host": pg_container.get_container_host_ip(),
        "port": int(pg_container.get_exposed_port(5432)),
        "database": pg_container.dbname,
        "user": pg_container.username,
        "password": pg_container.password,
    }


@pytest.fixture(scope="session")
def pg_raw_kwargs(pg_conn_params: dict[str, Any]) -> dict[str, Any]:
    """psycopg.connect-style kwargs (``dbname`` instead of ``database``)."""
    return {
        "host": pg_conn_params["host"],
        "port": pg_conn_params["port"],
        "dbname": pg_conn_params["database"],
        "user": pg_conn_params["user"],
        "password": pg_conn_params["password"],
    }


@pytest.fixture
def pg_table(pg_raw_kwargs: dict[str, Any]) -> Iterator[str]:
    """Create a fresh table with the sample_records schema. Dropped on teardown.

    Uses a short-lived raw psycopg connection — separate from the
    PostgresConnector under test, so the test starts with an unconnected
    connector.
    """
    name = f"etl_test_{uuid4().hex[:8]}"
    create_stmt = f"CREATE TABLE {name} (id INT PRIMARY KEY, name TEXT, age INT, active BOOLEAN)"
    with psycopg.connect(**pg_raw_kwargs) as conn:
        with conn.cursor() as cur:
            cur.execute(create_stmt)
        conn.commit()
    yield name
    with psycopg.connect(**pg_raw_kwargs) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {name}")
        conn.commit()


@pytest.fixture
def pg_seeded(pg_raw_kwargs: dict[str, Any], pg_table: str, sample_records: list[Record]) -> str:
    """Seed ``pg_table`` with ``sample_records`` and return the table name."""
    with psycopg.connect(**pg_raw_kwargs) as conn:
        with conn.cursor() as cur:
            for r in sample_records:
                cur.execute(
                    f"INSERT INTO {pg_table} VALUES (%s, %s, %s, %s)",
                    (r.data["id"], r.data["name"], r.data["age"], r.data["active"]),
                )
        conn.commit()
    return pg_table


@pytest.fixture
def pg_connector(pg_conn_params: dict[str, Any]) -> Iterator[PostgresConnector]:
    """A fresh, unconnected PostgresConnector. Closed on teardown."""
    pg = PostgresConnector(**pg_conn_params)
    yield pg
    pg.close()
