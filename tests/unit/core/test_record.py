"""Record 모델 테스트."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from etl_plugins.core.record import Record


def test_minimal_record() -> None:
    r = Record(data={"x": 1})
    assert r.data == {"x": 1}
    assert r.metadata == {}
    assert r.schema_version is None


def test_full_record() -> None:
    r = Record(
        data={"id": 42, "name": "x"},
        metadata={"source": "test", "offset": 100},
        schema_version="v1.0",
    )
    assert r.data["id"] == 42
    assert r.metadata["offset"] == 100
    assert r.schema_version == "v1.0"


def test_extra_field_rejected() -> None:
    # extra='forbid' — payload는 반드시 data/metadata/schema_version에만
    with pytest.raises(ValidationError):
        Record(data={"x": 1}, unknown_field="oops")  # type: ignore[call-arg]


def test_metadata_default_is_independent() -> None:
    # default_factory가 매 인스턴스마다 새 dict를 생성해야 한다
    r1 = Record(data={})
    r2 = Record(data={})
    r1.metadata["foo"] = "bar"
    assert "foo" not in r2.metadata
