"""Driver-free tests for the DynamoDB connector (Phase AGJ, ADR-0081).

The boto3 round-trip is covered by an integration test (LocalStack); here
we pin the pure logic — registry, protocol surface, the float↔Decimal
conversions DynamoDB requires, mode validation, and the "driver missing"
error.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from etl_plugins.connectors.nosql.dynamodb import (
    DynamoDBConnector,
    _from_dynamo,
    _to_dynamo,
)
from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, WriteError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


def test_dynamodb_resolves_through_registry() -> None:
    assert ConnectorRegistry.get("dynamodb") is DynamoDBConnector


def test_dynamodb_implements_batch_protocols() -> None:
    c = DynamoDBConnector(region="us-east-1", table="t")
    assert isinstance(c, BatchSource)
    assert isinstance(c, BatchSink)


def test_dynamodb_connect_error_when_driver_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "boto3", None)
    c = DynamoDBConnector(table="t")
    with pytest.raises(ConnectError) as excinfo:
        c.connect()
    msg = str(excinfo.value)
    assert "boto3 not installed" in msg
    assert "pip install" in msg


# ---------- type conversion (the DynamoDB Decimal gotcha) --------------


def test_to_dynamo_converts_float_to_decimal() -> None:
    out = _to_dynamo({"price": 0.1, "qty": 3, "name": "a"})
    assert out["price"] == Decimal("0.1")
    assert isinstance(out["price"], Decimal)
    # ints and strings pass through unchanged.
    assert out["qty"] == 3
    assert out["name"] == "a"


def test_to_dynamo_walks_nested_structures() -> None:
    out = _to_dynamo({"items": [{"x": 1.5}], "meta": {"y": 2.0}})
    assert out["items"][0]["x"] == Decimal("1.5")
    assert out["meta"]["y"] == Decimal("2.0")


def test_from_dynamo_converts_decimal_back() -> None:
    out = _from_dynamo({"price": Decimal("0.1"), "qty": Decimal("3")})
    # Integral Decimal → int; fractional → float.
    assert out["qty"] == 3
    assert isinstance(out["qty"], int)
    assert out["price"] == pytest.approx(0.1)
    assert isinstance(out["price"], float)


def test_from_dynamo_walks_nested() -> None:
    out = _from_dynamo({"rows": [{"n": Decimal("2")}]})
    assert out["rows"][0]["n"] == 2
    assert isinstance(out["rows"][0]["n"], int)


# ---------- mode + table validation ------------------------------------


def test_write_rejects_overwrite() -> None:
    c = DynamoDBConnector(table="t")
    with pytest.raises(WriteError, match="overwrite"):
        c.write([Record(data={"id": 1})], table="t", mode="overwrite")


def test_write_requires_table_name() -> None:
    c = DynamoDBConnector()  # no default table
    with pytest.raises(WriteError, match="requires a table name"):
        c.write([Record(data={"id": 1})], table=None)
