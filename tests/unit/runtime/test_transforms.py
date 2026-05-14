"""Built-in transform tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from etl_plugins.config.models import TransformConfig
from etl_plugins.core.exceptions import ConfigError, TransformError
from etl_plugins.core.record import Record
from etl_plugins.runtime.transforms import build_transform, register_transform

# ---------- dispatcher ----------


def test_unknown_transform_type_raises() -> None:
    cfg = TransformConfig(type="does-not-exist")
    with pytest.raises(ConfigError, match="unknown transform type"):
        build_transform(cfg)


def test_register_transform_duplicate_raises() -> None:
    @register_transform("rename-test-dup")
    def _b(cfg: TransformConfig):  # type: ignore[no-untyped-def]
        return lambda r: r

    with pytest.raises(ConfigError, match="already registered"):

        @register_transform("rename-test-dup")
        def _b2(cfg: TransformConfig):  # type: ignore[no-untyped-def]
            return lambda r: r


# ---------- rename ----------


def test_rename_remaps_keys() -> None:
    fn = build_transform(
        TransformConfig.model_validate({"type": "rename", "mapping": {"a": "A", "b": "B"}})
    )
    out = fn(Record(data={"a": 1, "b": 2, "c": 3}))
    assert isinstance(out, Record)
    assert out.data == {"A": 1, "B": 2, "c": 3}


def test_rename_preserves_metadata() -> None:
    fn = build_transform(TransformConfig.model_validate({"type": "rename", "mapping": {"x": "X"}}))
    out = fn(Record(data={"x": 1}, metadata={"source": "test"}))
    assert isinstance(out, Record)
    assert out.metadata == {"source": "test"}


def test_rename_missing_mapping_raises() -> None:
    with pytest.raises(ConfigError, match="rename"):
        build_transform(TransformConfig.model_validate({"type": "rename"}))


# ---------- cast ----------


def test_cast_int_string_to_int() -> None:
    fn = build_transform(TransformConfig.model_validate({"type": "cast", "columns": {"id": "int"}}))
    out = fn(Record(data={"id": "42", "name": "x"}))
    assert isinstance(out, Record)
    assert out.data == {"id": 42, "name": "x"}


def test_cast_float_and_str_and_bool() -> None:
    fn = build_transform(
        TransformConfig.model_validate(
            {
                "type": "cast",
                "columns": {"x": "float", "y": "str", "z": "bool"},
            }
        )
    )
    out = fn(Record(data={"x": "1.5", "y": 7, "z": 1}))
    assert isinstance(out, Record)
    assert out.data == {"x": 1.5, "y": "7", "z": True}


def test_cast_timestamp_from_isoformat() -> None:
    fn = build_transform(
        TransformConfig.model_validate({"type": "cast", "columns": {"ts": "timestamp"}})
    )
    out = fn(Record(data={"ts": "2026-01-01T12:00:00+00:00"}))
    assert isinstance(out, Record)
    assert out.data["ts"] == datetime(2026, 1, 1, 12, tzinfo=UTC)


def test_cast_skips_missing_column() -> None:
    fn = build_transform(
        TransformConfig.model_validate({"type": "cast", "columns": {"missing": "int"}})
    )
    out = fn(Record(data={"x": 1}))
    assert isinstance(out, Record)
    assert out.data == {"x": 1}


def test_cast_skips_none_values() -> None:
    fn = build_transform(TransformConfig.model_validate({"type": "cast", "columns": {"x": "int"}}))
    out = fn(Record(data={"x": None}))
    assert isinstance(out, Record)
    assert out.data["x"] is None


def test_cast_unsupported_type_raises() -> None:
    with pytest.raises(ConfigError, match="unsupported type"):
        build_transform(
            TransformConfig.model_validate({"type": "cast", "columns": {"x": "rocket"}})
        )


def test_cast_invalid_value_raises_transform_error() -> None:
    fn = build_transform(TransformConfig.model_validate({"type": "cast", "columns": {"x": "int"}}))
    with pytest.raises(TransformError, match="cast"):
        fn(Record(data={"x": "not-a-number"}))


# ---------- filter ----------


def test_filter_keeps_matching() -> None:
    fn = build_transform(
        TransformConfig.model_validate({"type": "filter", "expr": "data['kind'] == 'keep'"})
    )
    keep = fn(Record(data={"kind": "keep", "id": 1}))
    drop = fn(Record(data={"kind": "drop", "id": 2}))
    assert isinstance(keep, Record)
    assert drop is None


def test_filter_in_list_expression() -> None:
    fn = build_transform(
        TransformConfig.model_validate({"type": "filter", "expr": "data['type'] in ['a', 'b']"})
    )
    assert fn(Record(data={"type": "a"})) is not None
    assert fn(Record(data={"type": "c"})) is None


def test_filter_blocks_builtins() -> None:
    """eval은 빌트인 차단된 환경에서 실행 — open() 등 호출 불가."""
    fn = build_transform(
        TransformConfig.model_validate({"type": "filter", "expr": "open('/etc/passwd')"})
    )
    with pytest.raises(TransformError, match="expression failed"):
        fn(Record(data={}))


def test_filter_invalid_syntax_raises() -> None:
    with pytest.raises(ConfigError, match="cannot compile"):
        build_transform(TransformConfig.model_validate({"type": "filter", "expr": "data['x'"}))


def test_filter_missing_expr_raises() -> None:
    with pytest.raises(ConfigError, match="non-empty"):
        build_transform(TransformConfig.model_validate({"type": "filter", "expr": ""}))


# ---------- python ----------


def test_python_callable_applied(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed module can be referenced via 'module:function'."""
    # Use a stdlib module's function for safety
    fn = build_transform(
        TransformConfig.model_validate(
            {
                "type": "python",
                "callable": "tests.unit.runtime.test_transforms:_uppercase_name",
            }
        )
    )
    out = fn(Record(data={"name": "alice"}))
    assert isinstance(out, Record)
    assert out.data == {"name": "ALICE"}


def _uppercase_name(record: Record) -> Record:
    """Test helper used by test_python_callable_applied."""
    return Record(
        data={**record.data, "name": record.data["name"].upper()},
        metadata=record.metadata,
    )


def test_python_module_not_found_raises() -> None:
    with pytest.raises(ConfigError, match="cannot import"):
        build_transform(
            TransformConfig.model_validate({"type": "python", "callable": "no_such_module:fn"})
        )


def test_python_attribute_not_found_raises() -> None:
    with pytest.raises(ConfigError, match="has no attribute"):
        build_transform(
            TransformConfig.model_validate(
                {"type": "python", "callable": "os:no_such_attribute_xxx"}
            )
        )


def test_python_callable_exception_wrapped() -> None:
    fn = build_transform(
        TransformConfig.model_validate(
            {
                "type": "python",
                "callable": "tests.unit.runtime.test_transforms:_always_fails",
            }
        )
    )
    with pytest.raises(TransformError, match="raised"):
        fn(Record(data={}))


def _always_fails(record: Record) -> Record:
    raise ValueError("nope")


def test_python_invalid_callable_spec_raises() -> None:
    with pytest.raises(ConfigError, match="module:function"):
        build_transform(TransformConfig.model_validate({"type": "python", "callable": "no_colon"}))


def _records_for(data_list: list[dict]) -> Iterator[Record]:
    for d in data_list:
        yield Record(data=d)
