"""Built-in transform implementations.

Each ``register_transform("name")`` registers a builder that turns a
:class:`~etl_plugins.config.models.TransformConfig` into a :class:`TransformFn`.
The Pipeline builder dispatches on ``TransformConfig.type``.

Built-ins (SPEC.md §5.4):
    * ``rename`` — rename keys (``mapping: {old: new, ...}``)
    * ``cast`` — coerce values (``columns: {col: int64|float64|str|bool|timestamp, ...}``)
    * ``filter`` — keep records where a Python expression is truthy (``expr: "..."``)
    * ``python`` — apply a user callable (``callable: "module:function"``)

External packages can add their own via :func:`register_transform`.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from datetime import datetime
from typing import Any

from etl_plugins.config.models import TransformConfig
from etl_plugins.core.exceptions import ConfigError, TransformError
from etl_plugins.core.pipeline import TransformFn
from etl_plugins.core.record import Record

# A transform builder takes a TransformConfig and returns a TransformFn.
BuiltinTransform = Callable[[TransformConfig], TransformFn]

_REGISTRY: dict[str, BuiltinTransform] = {}


def register_transform(name: str) -> Callable[[BuiltinTransform], BuiltinTransform]:
    """Register a transform builder under ``name`` (raises on duplicate)."""

    def deco(builder: BuiltinTransform) -> BuiltinTransform:
        if name in _REGISTRY:
            raise ConfigError(f"transform '{name}' already registered")
        _REGISTRY[name] = builder
        return builder

    return deco


def build_transform(config: TransformConfig) -> TransformFn:
    """Resolve a transform builder by ``config.type`` and apply it to ``config``."""
    builder = _REGISTRY.get(config.type)
    if builder is None:
        raise ConfigError(
            f"unknown transform type: {config.type!r} (registered: {sorted(_REGISTRY)})"
        )
    return builder(config)


# =============================================================================
# Built-ins
# =============================================================================


def _config_field(config: TransformConfig, name: str, *, required: bool = True) -> Any:
    """Extract an extra=allow field from a TransformConfig."""
    data = config.model_dump()
    if name not in data and required:
        raise ConfigError(f"transform '{config.type}' requires '{name}'")
    return data.get(name)


@register_transform("rename")
def _build_rename(config: TransformConfig) -> TransformFn:
    mapping: dict[str, str] = _config_field(config, "mapping") or {}
    if not isinstance(mapping, dict):
        raise ConfigError("rename: 'mapping' must be a dict[str, str]")

    def _rename(record: Record) -> Record:
        new_data = {mapping.get(k, k): v for k, v in record.data.items()}
        return Record(data=new_data, metadata=record.metadata, schema_version=record.schema_version)

    return _rename


_CAST_FUNCTIONS: dict[str, Callable[[Any], Any]] = {
    "int": int,
    "int64": int,
    "float": float,
    "float64": float,
    "str": str,
    "string": str,
    "bool": bool,
    "timestamp": lambda v: datetime.fromisoformat(v) if isinstance(v, str) else v,
}


@register_transform("cast")
def _build_cast(config: TransformConfig) -> TransformFn:
    columns: dict[str, str] = _config_field(config, "columns") or {}
    if not isinstance(columns, dict):
        raise ConfigError("cast: 'columns' must be a dict[str, str]")
    casts: dict[str, Callable[[Any], Any]] = {}
    for col, type_name in columns.items():
        if type_name not in _CAST_FUNCTIONS:
            raise ConfigError(
                f"cast: unsupported type {type_name!r} for column {col!r} "
                f"(supported: {sorted(_CAST_FUNCTIONS)})"
            )
        casts[col] = _CAST_FUNCTIONS[type_name]

    def _cast(record: Record) -> Record:
        new_data = dict(record.data)
        for col, fn in casts.items():
            if col in new_data and new_data[col] is not None:
                try:
                    new_data[col] = fn(new_data[col])
                except (TypeError, ValueError) as exc:
                    raise TransformError(
                        f"cast: column {col!r} value {new_data[col]!r} → {type_name}: {exc}"
                    ) from exc
        return Record(data=new_data, metadata=record.metadata, schema_version=record.schema_version)

    return _cast


@register_transform("filter")
def _build_filter(config: TransformConfig) -> TransformFn:
    """Filter via a sandboxed Python expression.

    Available locals: ``data`` (the record's data dict), ``metadata``.
    Builtins are blocked — only literals, comparisons, ``in``/``and``/``or``/``not``,
    and the ``data``/``metadata`` references are usable.

    Example: ``filter: { expr: "data['type'] in ['a', 'b']" }``
    """
    expr: str = _config_field(config, "expr")
    if not isinstance(expr, str) or not expr.strip():
        raise ConfigError("filter: 'expr' must be a non-empty string")

    try:
        code = compile(expr, "<filter:expr>", "eval")
    except SyntaxError as exc:
        raise ConfigError(f"filter: cannot compile 'expr': {exc}") from exc

    def _filter(record: Record) -> Record | None:
        try:
            keep = eval(
                code,
                {"__builtins__": {}},
                {"data": record.data, "metadata": record.metadata},
            )
        except Exception as exc:
            raise TransformError(f"filter: expression failed: {exc}") from exc
        return record if keep else None

    return _filter


@register_transform("select")
def _build_select(config: TransformConfig) -> TransformFn:
    """Keep only the listed columns (``columns: [a, b, ...]``)."""
    columns = _config_field(config, "columns") or []
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
        raise ConfigError("select: 'columns' must be a list[str]")
    keep = set(columns)

    def _select(record: Record) -> Record:
        return Record(
            data={k: v for k, v in record.data.items() if k in keep},
            metadata=record.metadata,
            schema_version=record.schema_version,
        )

    return _select


@register_transform("drop")
def _build_drop(config: TransformConfig) -> TransformFn:
    """Remove the listed columns (``columns: [a, b, ...]``)."""
    columns = _config_field(config, "columns") or []
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
        raise ConfigError("drop: 'columns' must be a list[str]")
    drop = set(columns)

    def _drop(record: Record) -> Record:
        return Record(
            data={k: v for k, v in record.data.items() if k not in drop},
            metadata=record.metadata,
            schema_version=record.schema_version,
        )

    return _drop


@register_transform("add_constant")
def _build_add_constant(config: TransformConfig) -> TransformFn:
    """Set a column to a constant value (``column: name, value: <any>``)."""
    column = _config_field(config, "column")
    if not isinstance(column, str) or not column:
        raise ConfigError("add_constant: 'column' must be a non-empty string")
    value = _config_field(config, "value", required=False)

    def _add(record: Record) -> Record:
        return Record(
            data={**record.data, column: value},
            metadata=record.metadata,
            schema_version=record.schema_version,
        )

    return _add


@register_transform("dedupe")
def _build_dedupe(config: TransformConfig) -> TransformFn:
    """Drop records whose ``key_columns`` tuple was already seen in this run.

    Stateful within a single pipeline run (keeps a set of seen keys in memory);
    intended for moderate cardinality. ``key_columns: [a, b, ...]``.
    """
    key_columns = _config_field(config, "key_columns") or []
    if not isinstance(key_columns, list) or not key_columns:
        raise ConfigError("dedupe: 'key_columns' must be a non-empty list[str]")
    seen: set[tuple[Any, ...]] = set()

    def _dedupe(record: Record) -> Record | None:
        key = tuple(record.data.get(k) for k in key_columns)
        if key in seen:
            return None
        seen.add(key)
        return record

    return _dedupe


@register_transform("python")
def _build_python(config: TransformConfig) -> TransformFn:
    """Apply a user-supplied callable referenced as ``module:function``.

    The callable receives a Record and returns either a Record (kept) or None
    (filtered out).
    """
    spec: str = _config_field(config, "callable")
    if not isinstance(spec, str) or ":" not in spec:
        raise ConfigError("python: 'callable' must be 'module:function'")
    module_name, fn_name = spec.split(":", 1)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ConfigError(f"python: cannot import {module_name!r}: {exc}") from exc
    if not hasattr(module, fn_name):
        raise ConfigError(f"python: {module_name!r} has no attribute {fn_name!r}")
    user_fn: Callable[[Record], Record | None] = getattr(module, fn_name)

    def _python(record: Record) -> Record | None:
        try:
            return user_fn(record)
        except Exception as exc:
            raise TransformError(f"python: {spec!r} raised: {exc}") from exc

    return _python


__all__ = [
    "BuiltinTransform",
    "build_transform",
    "register_transform",
]
