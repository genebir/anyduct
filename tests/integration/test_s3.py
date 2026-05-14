"""S3Connector integration tests (testcontainers + MinIO)."""

from __future__ import annotations

import io
import json
from typing import Any

import boto3
import pytest

from etl_plugins.connectors.object_storage.s3 import S3Connector
from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry
from tests.contracts.batch import (
    _BatchRoundTripContract,
    _BatchSinkContract,
    _BatchSourceContract,
)

pytestmark = pytest.mark.it


# ---------- contract: BatchSource ----------


class TestS3BatchSource(_BatchSourceContract):
    @pytest.fixture
    def source(self, s3_connector: S3Connector, s3_seeded: dict[str, str]) -> BatchSource:
        return s3_connector

    @pytest.fixture
    def seeded_records(self, sample_records: list[Record]) -> list[Record]:
        return sample_records

    @pytest.fixture
    def read_kwargs(self, s3_seeded: dict[str, str]) -> dict[str, object]:
        return {"query": s3_seeded["prefix"], "format": "jsonl"}


# ---------- contract: BatchSink ----------


class TestS3BatchSink(_BatchSinkContract):
    @pytest.fixture
    def sink(self, s3_connector: S3Connector) -> BatchSink:
        return s3_connector

    @pytest.fixture
    def write_kwargs(self) -> dict[str, object]:
        return {"key": "out/data.jsonl"}


# ---------- contract: round-trip ----------


class TestS3RoundTrip(_BatchRoundTripContract):
    @pytest.fixture
    def round_trip_connector(self, s3_connector: S3Connector) -> BatchSource:
        return s3_connector

    @pytest.fixture
    def write_kwargs(self) -> dict[str, object]:
        return {"key": "roundtrip/data.jsonl", "format": "jsonl"}

    @pytest.fixture
    def read_kwargs(self) -> dict[str, object]:
        return {"query": "roundtrip/", "format": "jsonl"}


# ---------- s3-specific tests ----------


def test_registry_resolves_s3() -> None:
    klass = ConnectorRegistry.get("s3")
    assert klass is S3Connector
    assert klass.name == "s3"


def test_health_check_false_before_connect(s3_conn_params: dict[str, Any]) -> None:
    s3 = S3Connector(bucket="anywhere", **s3_conn_params)
    assert s3.health_check() is False


def test_health_check_true_for_existing_bucket(s3_connector: S3Connector) -> None:
    with s3_connector:
        assert s3_connector.health_check() is True


def test_health_check_false_for_nonexistent_bucket(
    s3_conn_params: dict[str, Any],
) -> None:
    s3 = S3Connector(bucket="definitely-does-not-exist-xxx", **s3_conn_params)
    with s3:
        assert s3.health_check() is False


def test_write_without_key_raises(s3_connector: S3Connector) -> None:
    s3_connector.connect()
    with pytest.raises(WriteError, match="key"):
        s3_connector.write(iter([Record(data={"a": 1})]))


def test_write_without_bucket_raises(s3_conn_params: dict[str, Any]) -> None:
    s3 = S3Connector(bucket="", **s3_conn_params)
    s3.connect()
    with pytest.raises(WriteError, match="bucket"):
        s3.write(iter([Record(data={"a": 1})]), key="x.jsonl")


def test_write_upsert_rejected(s3_connector: S3Connector) -> None:
    s3_connector.connect()
    with pytest.raises(WriteError, match="upsert"):
        s3_connector.write(
            iter([Record(data={"a": 1})]),
            key="x.jsonl",
            mode="upsert",
        )


def test_write_unknown_mode_rejected(s3_connector: S3Connector) -> None:
    s3_connector.connect()
    with pytest.raises(WriteError, match="unknown s3 write mode"):
        s3_connector.write(iter([Record(data={"a": 1})]), key="x.jsonl", mode="weird")


def test_read_without_bucket_raises(s3_conn_params: dict[str, Any]) -> None:
    s3 = S3Connector(bucket="", **s3_conn_params)
    s3.connect()
    with pytest.raises(ReadError, match="bucket"):
        list(s3.read(query="x/"))


def test_read_without_connect_raises(s3_conn_params: dict[str, Any]) -> None:
    s3 = S3Connector(bucket="b", **s3_conn_params)
    with pytest.raises(ConnectError):
        list(s3.read())


def test_read_skips_unknown_extensions(
    s3_connector: S3Connector,
    s3_bucket: str,
    s3_boto_kwargs: dict[str, Any],
    sample_records: list[Record],
) -> None:
    # Seed: one data file + a _SUCCESS marker (no recognised extension)
    client = boto3.client("s3", **s3_boto_kwargs)
    body = ("\n".join(json.dumps(r.data) for r in sample_records) + "\n").encode()
    client.put_object(Bucket=s3_bucket, Key="data/part-0.jsonl", Body=body)
    client.put_object(Bucket=s3_bucket, Key="data/_SUCCESS", Body=b"")
    with s3_connector:
        records = list(s3_connector.read(query="data/"))
    assert len(records) == 3


def test_csv_write_then_read(s3_connector: S3Connector) -> None:
    records = [Record(data={"id": 1, "name": "A"}), Record(data={"id": 2, "name": "B"})]
    with s3_connector:
        n = s3_connector.write(iter(records), key="csv/data.csv", format="csv")
        assert n == 2
        read = list(s3_connector.read(query="csv/", format="csv"))
    # CSV는 타입을 보존 안 함 - 모든 값이 문자열
    assert [r.data for r in read] == [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]


def test_parquet_round_trip_preserves_types(s3_connector: S3Connector) -> None:
    records = [
        Record(data={"id": 1, "name": "Alice", "age": 30, "active": True}),
        Record(data={"id": 2, "name": "Bob", "age": 25, "active": False}),
    ]
    with s3_connector:
        s3_connector.write(iter(records), key="pq/data.parquet", format="parquet")
        read = list(s3_connector.read(query="pq/", format="parquet"))
    assert [r.data for r in read] == [r.data for r in records]


def test_metadata_includes_source_and_key(
    s3_connector: S3Connector, s3_seeded: dict[str, str]
) -> None:
    with s3_connector:
        records = list(s3_connector.read(query=s3_seeded["prefix"]))
    assert all(r.metadata["source"] == "s3" for r in records)
    assert all(r.metadata["key"] == "seed/data.jsonl" for r in records)


def test_write_empty_input_returns_zero(s3_connector: S3Connector) -> None:
    with s3_connector:
        assert s3_connector.write(iter([]), key="empty.jsonl") == 0


def test_overwrite_mode_replaces_object(
    s3_connector: S3Connector,
    s3_bucket: str,
    s3_boto_kwargs: dict[str, Any],
) -> None:
    """``mode='overwrite'``로 같은 key에 두 번 쓰면 두 번째 내용만 남아야 한다."""
    with s3_connector:
        s3_connector.write(iter([Record(data={"v": 1})]), key="x.jsonl", mode="overwrite")
        s3_connector.write(iter([Record(data={"v": 2})]), key="x.jsonl", mode="overwrite")
    client = boto3.client("s3", **s3_boto_kwargs)
    body = client.get_object(Bucket=s3_bucket, Key="x.jsonl")["Body"].read()
    assert body == b'{"v": 2}\n'


def test_read_handles_multiple_objects(s3_connector: S3Connector) -> None:
    """동일 prefix 아래 여러 객체에 걸쳐 read가 누락 없이 전체를 yield."""
    parts = [
        [Record(data={"i": 0}), Record(data={"i": 1})],
        [Record(data={"i": 2})],
        [Record(data={"i": 3}), Record(data={"i": 4})],
    ]
    with s3_connector:
        for idx, batch in enumerate(parts):
            s3_connector.write(iter(batch), key=f"multi/part-{idx}.jsonl")
        records = list(s3_connector.read(query="multi/"))
    assert {r.data["i"] for r in records} == {0, 1, 2, 3, 4}


def test_default_format_used_when_no_extension(s3_connector: S3Connector) -> None:
    """key에 인식 가능 확장자 없으면 default_format이 적용된다."""
    records = [Record(data={"x": 1})]
    with s3_connector:
        s3_connector.write(iter(records), key="raw/no_ext")  # default jsonl
        read = list(s3_connector.read(query="raw/", format="jsonl"))
    assert [r.data for r in read] == [r.data for r in records]


def test_write_unsupported_format_rejected(s3_connector: S3Connector) -> None:
    s3_connector.connect()
    with pytest.raises(WriteError, match="unsupported s3 write format"):
        s3_connector.write(iter([Record(data={"a": 1})]), key="x.foo", format="xml")


def test_jsonl_handles_streaming_body(
    s3_connector: S3Connector,
    s3_bucket: str,
    s3_boto_kwargs: dict[str, Any],
) -> None:
    """boto3 StreamingBody 객체에서도 iter_lines가 작동해야 한다."""
    body = io.BytesIO()
    for i in range(100):
        body.write(json.dumps({"i": i}).encode() + b"\n")
    body.seek(0)
    client = boto3.client("s3", **s3_boto_kwargs)
    client.put_object(Bucket=s3_bucket, Key="stream/big.jsonl", Body=body.getvalue())
    with s3_connector:
        records = list(s3_connector.read(query="stream/"))
    assert len(records) == 100
    assert {r.data["i"] for r in records} == set(range(100))
