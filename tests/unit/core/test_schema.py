"""Field / Schema 테스트."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from etl_plugins.core.schema import Field, Schema


def test_field_basic() -> None:
    f = Field(name="id", type="int64")
    assert f.name == "id"
    assert f.type == "int64"
    assert f.nullable is True


def test_field_non_nullable() -> None:
    f = Field(name="id", type="int64", nullable=False)
    assert f.nullable is False


def test_field_is_frozen() -> None:
    f = Field(name="id", type="int64")
    with pytest.raises(ValidationError):
        f.name = "other"  # type: ignore[misc]


def test_schema_column_names() -> None:
    s = Schema(
        fields=(
            Field(name="id", type="int64"),
            Field(name="name", type="string"),
            Field(name="created_at", type="timestamp"),
        )
    )
    assert s.column_names() == ["id", "name", "created_at"]


def test_schema_field_by_name() -> None:
    s = Schema(fields=(Field(name="id", type="int64"), Field(name="x", type="float64")))
    assert s.field_by_name("id") == Field(name="id", type="int64")
    assert s.field_by_name("missing") is None
