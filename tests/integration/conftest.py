"""Integration test fixtures (testcontainers-based).

Every test that uses one of these fixtures should be marked with
``@pytest.mark.it`` (or the containing module sets ``pytestmark = pytest.mark.it``)
so that plain ``pytest -m "not it"`` skips them. CI runs them in a dedicated job.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import boto3
import psycopg
import pytest
from testcontainers.kafka import KafkaContainer
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer

from etl_plugins.connectors.object_storage.s3 import S3Connector
from etl_plugins.connectors.rdbms.postgres import PostgresConnector
from etl_plugins.connectors.stream.kafka import KafkaConnector
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


# =============================================================================
# MinIO (S3-compatible) — Step 2.2
# =============================================================================


@pytest.fixture(scope="session")
def minio_container() -> Iterator[MinioContainer]:
    """Long-lived MinIO container shared across the test session."""
    with MinioContainer() as container:
        yield container


@pytest.fixture(scope="session")
def s3_conn_params(minio_container: MinioContainer) -> dict[str, Any]:
    """Params for ``S3Connector(**params)`` (excluding bucket)."""
    cfg = minio_container.get_config()
    return {
        "region": "us-east-1",
        "endpoint_url": f"http://{cfg['endpoint']}",
        "access_key": cfg["access_key"],
        "secret_key": cfg["secret_key"],
    }


@pytest.fixture(scope="session")
def s3_boto_kwargs(s3_conn_params: dict[str, Any]) -> dict[str, Any]:
    """boto3.client('s3', ...) kwargs (different naming from our connector params)."""
    return {
        "region_name": s3_conn_params["region"],
        "endpoint_url": s3_conn_params["endpoint_url"],
        "aws_access_key_id": s3_conn_params["access_key"],
        "aws_secret_access_key": s3_conn_params["secret_key"],
    }


@pytest.fixture
def s3_bucket(s3_boto_kwargs: dict[str, Any]) -> Iterator[str]:
    """Create a fresh bucket per test. Drops all contents and the bucket on teardown.

    Per-object delete (instead of batch DeleteObjects) — MinIO requires
    Content-MD5 for batch deletes, which modern boto3 omits by default.
    """
    bucket = f"etl-test-{uuid4().hex[:8]}"
    client = boto3.client("s3", **s3_boto_kwargs)
    client.create_bucket(Bucket=bucket)
    try:
        yield bucket
    finally:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                client.delete_object(Bucket=bucket, Key=obj["Key"])
        client.delete_bucket(Bucket=bucket)


@pytest.fixture
def s3_connector(s3_conn_params: dict[str, Any], s3_bucket: str) -> Iterator[S3Connector]:
    """Unconnected S3Connector pointed at ``s3_bucket``. Closed on teardown."""
    conn = S3Connector(bucket=s3_bucket, **s3_conn_params)
    yield conn
    conn.close()


@pytest.fixture
def s3_seeded(
    s3_boto_kwargs: dict[str, Any], s3_bucket: str, sample_records: list[Record]
) -> dict[str, str]:
    """Seed ``s3_bucket`` with sample_records as JSONL under prefix 'seed/'.

    Returns ``{"bucket": ..., "prefix": "seed/"}``.
    """
    import json

    prefix = "seed/"
    body = ("\n".join(json.dumps(r.data) for r in sample_records) + "\n").encode()
    client = boto3.client("s3", **s3_boto_kwargs)
    client.put_object(Bucket=s3_bucket, Key=f"{prefix}data.jsonl", Body=body)
    return {"bucket": s3_bucket, "prefix": prefix}


# =============================================================================
# Kafka — Step 2.3
# =============================================================================


@pytest.fixture(scope="session")
def kafka_container() -> Iterator[KafkaContainer]:
    """Long-lived Kafka (KRaft mode) container shared across the test session."""
    with KafkaContainer() as container:
        yield container


@pytest.fixture(scope="session")
def kafka_bootstrap(kafka_container: KafkaContainer) -> str:
    return str(kafka_container.get_bootstrap_server())


@pytest.fixture
def kafka_connector(kafka_bootstrap: str) -> Iterator[KafkaConnector]:
    """A fresh KafkaConnector instance. Caller is responsible for connect()."""
    kc = KafkaConnector(bootstrap_servers=kafka_bootstrap)
    yield kc
    # Best-effort sync cleanup; tests that exercise async lifecycle call aclose() themselves.
    kc.close()


@pytest.fixture
def kafka_topic() -> str:
    """A unique topic name per test. Auto-created by Kafka on first produce/subscribe."""
    return f"etl-test-{uuid4().hex[:8]}"


@pytest.fixture
async def kafka_seeded_topic(
    kafka_bootstrap: str, kafka_topic: str, sample_records: list[Record]
) -> str:
    """Pre-publish ``sample_records`` to ``kafka_topic`` and return the topic name.

    Uses its own short-lived producer (separate from the connector under test)
    so the test starts with a clean producer state.
    """
    import json as _json

    from aiokafka import AIOKafkaProducer

    producer = AIOKafkaProducer(bootstrap_servers=kafka_bootstrap)
    await producer.start()
    try:
        for r in sample_records:
            await producer.send_and_wait(
                kafka_topic,
                value=_json.dumps(r.data).encode("utf-8"),
            )
    finally:
        await producer.stop()
    return kafka_topic
