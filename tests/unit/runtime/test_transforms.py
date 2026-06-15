"""Built-in transform tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from etl_plugins.config.models import TransformConfig
from etl_plugins.core.exceptions import AssertionFailedError, ConfigError, TransformError
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


# ---------- select / drop / add_constant / dedupe (Step 10.x) ----------


def test_select_keeps_only_listed_columns() -> None:
    fn = build_transform(TransformConfig.model_validate({"type": "select", "columns": ["a", "c"]}))
    out = fn(Record(data={"a": 1, "b": 2, "c": 3}))
    assert out is not None and out.data == {"a": 1, "c": 3}


def test_select_invalid_columns_raises() -> None:
    with pytest.raises(ConfigError, match="list\\[str\\]"):
        build_transform(TransformConfig.model_validate({"type": "select", "columns": "a"}))


def test_drop_removes_listed_columns() -> None:
    fn = build_transform(TransformConfig.model_validate({"type": "drop", "columns": ["b"]}))
    out = fn(Record(data={"a": 1, "b": 2, "c": 3}))
    assert out is not None and out.data == {"a": 1, "c": 3}


def test_add_constant_sets_column() -> None:
    fn = build_transform(
        TransformConfig.model_validate({"type": "add_constant", "column": "source", "value": "etl"})
    )
    out = fn(Record(data={"a": 1}))
    assert out is not None and out.data == {"a": 1, "source": "etl"}


def test_add_constant_empty_column_raises() -> None:
    with pytest.raises(ConfigError, match="non-empty string"):
        build_transform(
            TransformConfig.model_validate({"type": "add_constant", "column": "", "value": 1})
        )


def test_dedupe_drops_repeat_keys() -> None:
    fn = build_transform(TransformConfig.model_validate({"type": "dedupe", "key_columns": ["id"]}))
    rows = [
        Record(data={"id": 1, "v": "a"}),
        Record(data={"id": 2, "v": "b"}),
        Record(data={"id": 1, "v": "c"}),
    ]
    kept = [fn(r) for r in rows]
    assert [r.data["v"] for r in kept if r is not None] == ["a", "b"]


def test_dedupe_empty_key_columns_raises() -> None:
    with pytest.raises(ConfigError, match="non-empty list"):
        build_transform(TransformConfig.model_validate({"type": "dedupe", "key_columns": []}))


# ---------- custom_python (ADR-0041 I2) ----------


_CUSTOM_PY_HAPPY = """
def transform(record):
    d = dict(record.data)
    d["upper"] = d.get("name", "").upper()
    return record.__class__(
        data=d,
        metadata=record.metadata,
        schema_version=record.schema_version,
    )
"""


def test_custom_python_applies_inline_transform() -> None:
    fn = build_transform(
        TransformConfig.model_validate({"type": "custom_python", "code": _CUSTOM_PY_HAPPY})
    )
    out = fn(Record(data={"name": "ada"}))
    assert out is not None
    assert out.data == {"name": "ada", "upper": "ADA"}


def test_custom_python_returning_none_filters_record() -> None:
    code = "def transform(record):\n    return None if record.data.get('drop') else record\n"
    fn = build_transform(TransformConfig.model_validate({"type": "custom_python", "code": code}))
    assert fn(Record(data={"drop": True})) is None
    assert fn(Record(data={"keep": 1})) is not None


def test_custom_python_blank_code_raises() -> None:
    with pytest.raises(ConfigError, match="non-empty string"):
        build_transform(TransformConfig.model_validate({"type": "custom_python", "code": "   "}))


def test_custom_python_syntax_error_raises_configerror() -> None:
    with pytest.raises(ConfigError, match="cannot compile"):
        build_transform(
            TransformConfig.model_validate({"type": "custom_python", "code": "def transform(\n"})
        )


def test_custom_python_missing_entrypoint_raises() -> None:
    code = "def not_transform(record):\n    return record\n"
    with pytest.raises(ConfigError, match=r"`transform\(record\)` function"):
        build_transform(TransformConfig.model_validate({"type": "custom_python", "code": code}))


def test_custom_python_non_callable_entrypoint_raises() -> None:
    code = "transform = 42\n"
    with pytest.raises(ConfigError, match="not callable"):
        build_transform(TransformConfig.model_validate({"type": "custom_python", "code": code}))


def test_custom_python_runtime_error_wrapped_in_transformerror() -> None:
    code = "def transform(record):\n    raise ValueError('nope')\n"
    fn = build_transform(TransformConfig.model_validate({"type": "custom_python", "code": code}))
    with pytest.raises(TransformError, match="custom_python: code raised"):
        fn(Record(data={}))


def test_custom_python_import_time_error_raises_configerror() -> None:
    """Errors during module-level execution should surface at build, not runtime."""
    code = "raise RuntimeError('boom at import')\n"
    with pytest.raises(ConfigError, match="raised during import"):
        build_transform(TransformConfig.model_validate({"type": "custom_python", "code": code}))


# ---------- assert (ADR-0041 K1) ----------


def test_assert_passes_truthy_record() -> None:
    fn = build_transform(
        TransformConfig.model_validate({"type": "assert", "condition": "data['amount'] >= 0"})
    )
    out = fn(Record(data={"amount": 10}))
    assert out is not None and out.data["amount"] == 10


def test_assert_fail_default_raises_with_message() -> None:
    fn = build_transform(
        TransformConfig.model_validate(
            {
                "type": "assert",
                "condition": "data['amount'] >= 0",
                "message": "amount must be non-negative",
            }
        )
    )
    with pytest.raises(AssertionFailedError, match="amount must be non-negative") as exc:
        fn(Record(data={"amount": -5}))
    # offending row is included in the rendered message for debugging
    assert "amount" in str(exc.value) and "-5" in str(exc.value)


def test_assert_fail_default_uses_condition_when_no_message() -> None:
    fn = build_transform(
        TransformConfig.model_validate({"type": "assert", "condition": "data['x'] > 0"})
    )
    with pytest.raises(AssertionFailedError, match="data\\['x'\\] > 0"):
        fn(Record(data={"x": -1}))


def test_assert_drop_mode_filters_silently() -> None:
    fn = build_transform(
        TransformConfig.model_validate(
            {
                "type": "assert",
                "condition": "data['amount'] >= 0",
                "on_fail": "drop",
            }
        )
    )
    assert fn(Record(data={"amount": 10})) is not None
    assert fn(Record(data={"amount": -1})) is None


def test_assert_unknown_on_fail_raises_configerror() -> None:
    with pytest.raises(ConfigError, match="on_fail"):
        build_transform(
            TransformConfig.model_validate(
                {"type": "assert", "condition": "True", "on_fail": "warn"}
            )
        )


def test_assert_empty_condition_raises_configerror() -> None:
    with pytest.raises(ConfigError, match="non-empty"):
        build_transform(TransformConfig.model_validate({"type": "assert", "condition": "  "}))


def test_assert_invalid_syntax_raises_configerror() -> None:
    with pytest.raises(ConfigError, match="cannot compile"):
        build_transform(TransformConfig.model_validate({"type": "assert", "condition": "data['"}))


def test_assert_blocks_builtins() -> None:
    fn = build_transform(
        TransformConfig.model_validate({"type": "assert", "condition": "open('/etc/passwd')"})
    )
    with pytest.raises(TransformError, match="condition raised"):
        fn(Record(data={}))


def test_assert_long_record_repr_is_truncated() -> None:
    """error_message stays bounded so the runs.error_message column doesn't bloat."""
    fn = build_transform(
        TransformConfig.model_validate({"type": "assert", "condition": "data['ok']"})
    )
    big_payload = {"junk": "x" * 5000, "ok": False}
    with pytest.raises(AssertionFailedError) as exc:
        fn(Record(data=big_payload))
    assert "…" in str(exc.value) or len(str(exc.value)) < 600
