"""S3Connector format helpers — pure-function unit tests (no boto3 client)."""

from __future__ import annotations

import io

import pytest

from etl_plugins.connectors.object_storage.s3 import (
    SUPPORTED_FORMATS,
    _parse_csv,
    _parse_jsonl,
    _parse_parquet,
    _serialize_csv,
    _serialize_jsonl,
    _serialize_parquet,
    detect_format,
)
from etl_plugins.core.record import Record

# ---------- detect_format ----------


@pytest.mark.parametrize(
    "key, expected",
    [
        ("path/to/file.jsonl", "jsonl"),
        ("path/to/file.ndjson", "jsonl"),
        ("path/to/file.JSONL", "jsonl"),
        ("a/b/c.csv", "csv"),
        ("X/Y/Z.CSV", "csv"),
        ("data.parquet", "parquet"),
        ("data.pq", "parquet"),
        ("data.PARQUET", "parquet"),
        ("README.md", None),
        ("no-extension", None),
        ("", None),
    ],
)
def test_detect_format(key: str, expected: str | None) -> None:
    assert detect_format(key) == expected


def test_supported_formats_are_known() -> None:
    assert set(SUPPORTED_FORMATS) == {"jsonl", "csv", "parquet"}


# ---------- jsonl round-trip ----------


def test_jsonl_round_trip_preserves_types() -> None:
    records = [
        Record(data={"id": 1, "name": "Alice", "age": 30, "active": True}),
        Record(data={"id": 2, "name": "Bob", "age": 25, "active": False, "extra": None}),
    ]
    body = _serialize_jsonl(records)
    assert body.endswith(b"\n")
    back = list(_parse_jsonl(io.BytesIO(body), "test.jsonl"))
    assert [r.data for r in back] == [r.data for r in records]


def test_jsonl_metadata_includes_source_and_key() -> None:
    body = _serialize_jsonl([Record(data={"x": 1})])
    back = list(_parse_jsonl(io.BytesIO(body), "a/b.jsonl"))
    assert back[0].metadata == {"source": "s3", "key": "a/b.jsonl"}


def test_jsonl_handles_unicode() -> None:
    records = [Record(data={"name": "한글", "emoji": "🚀"})]
    body = _serialize_jsonl(records)
    back = list(_parse_jsonl(io.BytesIO(body), "u.jsonl"))
    assert back[0].data == records[0].data


def test_jsonl_skips_blank_lines() -> None:
    body = b'{"a": 1}\n\n{"b": 2}\n'
    back = list(_parse_jsonl(io.BytesIO(body), "test.jsonl"))
    assert [r.data for r in back] == [{"a": 1}, {"b": 2}]


# ---------- csv round-trip ----------


def test_csv_round_trip_values_become_strings() -> None:
    records = [
        Record(data={"id": 1, "name": "Alice"}),
        Record(data={"id": 2, "name": "Bob"}),
    ]
    body = _serialize_csv(records)
    text = body.decode("utf-8")
    assert text.splitlines()[0] == "id,name"
    back = list(_parse_csv(io.BytesIO(body), "test.csv"))
    # CSV는 타입을 보존하지 않음 — 모두 문자열로 돌아온다
    assert [r.data for r in back] == [
        {"id": "1", "name": "Alice"},
        {"id": "2", "name": "Bob"},
    ]


def test_csv_uses_union_of_keys_across_records() -> None:
    records = [
        Record(data={"a": 1}),
        Record(data={"b": 2, "a": 3}),
    ]
    body = _serialize_csv(records)
    header = body.decode("utf-8").splitlines()[0]
    assert set(header.split(",")) == {"a", "b"}


def test_csv_empty_records_returns_empty_bytes() -> None:
    assert _serialize_csv([]) == b""


# ---------- parquet round-trip ----------


def test_parquet_round_trip_preserves_types() -> None:
    records = [
        Record(data={"id": 1, "name": "Alice", "age": 30, "active": True}),
        Record(data={"id": 2, "name": "Bob", "age": 25, "active": False}),
    ]
    body = _serialize_parquet(records)
    back = list(_parse_parquet(io.BytesIO(body), "test.parquet"))
    assert [r.data for r in back] == [r.data for r in records]


def test_parquet_handles_nulls() -> None:
    records = [
        Record(data={"id": 1, "name": "Alice"}),
        Record(data={"id": 2, "name": None}),
    ]
    body = _serialize_parquet(records)
    back = list(_parse_parquet(io.BytesIO(body), "n.parquet"))
    assert back[0].data["name"] == "Alice"
    assert back[1].data["name"] is None
