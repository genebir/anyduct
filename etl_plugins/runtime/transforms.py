"""Built-in transform implementations.

Each ``register_transform("name")`` registers a builder that turns a
:class:`~etl_plugins.config.models.TransformConfig` into a :class:`TransformFn`.
The Pipeline builder dispatches on ``TransformConfig.type``.

Built-ins (SPEC.md §5.4):
    * ``rename`` — rename keys (``mapping: {old: new, ...}``)
    * ``cast`` — coerce values (``columns: {col: int64|float64|str|bool|timestamp, ...}``)
    * ``filter`` — keep records where a Python expression is truthy (``expr: "..."``)
    * ``python`` — apply a user callable (``callable: "module:function"``)
    * ``custom_python`` — apply an inline Python ``transform(record)`` function
      (``code: <source>``); ADR-0041 I2.
    * ``assert`` — fail the run (or drop the row) when a data-quality
      condition isn't met; ADR-0041 K1.

External packages can add their own via :func:`register_transform`.
"""

from __future__ import annotations

import decimal
import importlib
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

from etl_plugins.config.models import TransformConfig
from etl_plugins.core.arrow import DEFAULT_BATCH_ROWS, records_to_batches
from etl_plugins.core.exceptions import AssertionFailedError, ConfigError, TransformError
from etl_plugins.core.pipeline import AnyTransformFn, DatasetTransformFn, TransformFn
from etl_plugins.core.record import Record

# A transform builder takes a TransformConfig and returns a transform —
# row-level (TransformFn) or dataset-level (DatasetTransformFn, ADR-0093).
BuiltinTransform = Callable[[TransformConfig], AnyTransformFn]

_REGISTRY: dict[str, BuiltinTransform] = {}


def register_transform(name: str) -> Callable[[BuiltinTransform], BuiltinTransform]:
    """Register a transform builder under ``name`` (raises on duplicate)."""

    def deco(builder: BuiltinTransform) -> BuiltinTransform:
        if name in _REGISTRY:
            raise ConfigError(f"transform '{name}' already registered")
        _REGISTRY[name] = builder
        return builder

    return deco


def build_transform(config: TransformConfig) -> AnyTransformFn:
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


# Single execution seam for user-authored Python (ADR-0041 I2).
# Today this is plain ``exec`` + a function call — the same threat model as
# the ``python`` transform (already arbitrary in-process execution). Future
# sandboxing (RestrictedPython / subprocess / gVisor) plugs in here without
# touching transform call sites.
_CUSTOM_PYTHON_ENTRYPOINT = "transform"


def _compile_custom_python(code: str) -> Callable[[Record], Record | None]:
    """Compile inline source + return the user's ``transform(record)`` callable.

    Raises :class:`ConfigError` on syntax errors or a missing/non-callable
    entrypoint, so build/dry-run reports the problem instead of dying at
    runtime.
    """
    try:
        compiled = compile(code, "<custom_python>", "exec")
    except SyntaxError as exc:
        raise ConfigError(f"custom_python: cannot compile code: {exc}") from exc
    namespace: dict[str, Any] = {}
    try:
        exec(compiled, namespace, namespace)
    except Exception as exc:
        raise ConfigError(f"custom_python: code raised during import: {exc}") from exc
    user_fn = namespace.get(_CUSTOM_PYTHON_ENTRYPOINT)
    if user_fn is None:
        raise ConfigError(
            f"custom_python: code must define a top-level "
            f"`{_CUSTOM_PYTHON_ENTRYPOINT}(record)` function"
        )
    if not callable(user_fn):
        raise ConfigError(f"custom_python: `{_CUSTOM_PYTHON_ENTRYPOINT}` is not callable")
    return user_fn  # type: ignore[no-any-return]


@register_transform("custom_python")
def _build_custom_python(config: TransformConfig) -> TransformFn:
    """Apply an inline Python ``transform(record)`` function (ADR-0041 I2).

    The user-authored source must define a top-level
    ``transform(record) -> Record | None`` function. The source is compiled
    + executed once at build time to extract the callable, then invoked per
    record.

    Security: ``custom_python`` runs arbitrary code in the worker process,
    the same threat model as the ``python`` transform (also unsandboxed
    in-process execution). The write APIs that persist pipeline config are
    Editor+ gated + audited; that is the entire trust boundary today.
    Future sandboxing plugs into :func:`_compile_custom_python` /
    ``_custom_python`` without touching callers.

    Example config::

        transform:
          type: custom_python
          code: |
            def transform(record):
                d = dict(record.data)
                d["upper"] = d.get("name", "").upper()
                return record.__class__(
                    data=d,
                    metadata=record.metadata,
                    schema_version=record.schema_version,
                )
    """
    code: str = _config_field(config, "code")
    if not isinstance(code, str) or not code.strip():
        raise ConfigError("custom_python: 'code' must be a non-empty string")
    user_fn = _compile_custom_python(code)

    def _custom_python(record: Record) -> Record | None:
        try:
            return user_fn(record)
        except Exception as exc:
            raise TransformError(f"custom_python: code raised: {exc}") from exc

    return _custom_python


# ---------- assert (data-quality gate, ADR-0041 K1) ----------------------

_ASSERT_ACTIONS = {"fail", "drop"}


@register_transform("assert")
def _build_assert(config: TransformConfig) -> TransformFn:
    """Fail (or drop) records that don't satisfy a sandboxed condition.

    A data-quality gate that the pipeline trips automatically — no silent
    bad data. Same expression contract as ``filter``: ``data`` /
    ``metadata`` in scope, builtins blocked.

    Config::

        transform:
          type: assert
          condition: "data['amount'] >= 0"   # truthy = pass
          on_fail: fail                      # fail | drop  (default: fail)
          message: "amount must be non-negative"

    ``on_fail=fail`` (default) raises :class:`AssertionFailedError`, which
    propagates to the worker → run row status flips to ``failed`` with the
    rendered message in ``error_message``. ``on_fail=drop`` silently filters
    the offending record (handy when bad rows are expected occasionally and
    the run shouldn't die for them — the row count delta still tells you).
    """
    expr = _config_field(config, "condition")
    if not isinstance(expr, str) or not expr.strip():
        raise ConfigError("assert: 'condition' must be a non-empty string")
    on_fail = _config_field(config, "on_fail", required=False) or "fail"
    if on_fail not in _ASSERT_ACTIONS:
        raise ConfigError(
            f"assert: 'on_fail' must be one of {sorted(_ASSERT_ACTIONS)}, got {on_fail!r}"
        )
    message_template = _config_field(config, "message", required=False)
    if message_template is not None and not isinstance(message_template, str):
        raise ConfigError("assert: 'message' must be a string when set")

    try:
        compiled = compile(expr, "<assert:condition>", "eval")
    except SyntaxError as exc:
        raise ConfigError(f"assert: cannot compile 'condition': {exc}") from exc

    def _assert(record: Record) -> Record | None:
        try:
            passed = eval(
                compiled,
                {"__builtins__": {}},
                {"data": record.data, "metadata": record.metadata},
            )
        except Exception as exc:
            raise TransformError(f"assert: condition raised: {exc}") from exc
        if passed:
            return record
        if on_fail == "drop":
            return None
        # fail mode — short repr of the offending row helps debugging
        # without dumping unbounded payloads into the error message.
        snippet = repr(record.data)
        if len(snippet) > 200:
            snippet = snippet[:200] + "…"
        msg = message_template or f"assertion failed: {expr}"
        raise AssertionFailedError(f"{msg}\n  record: {snippet}")

    return _assert


# ---------- sql (dataset-level, DuckDB; ADR-0093) -------------------------

_SQL_DEFAULT_VIEW = "input"

#: DuckDB ``SET memory_limit`` values we accept — a number + unit. Validated
#: because the value is interpolated into a SET statement (no SQL splice).
_MEMORY_LIMIT_RE = re.compile(r"^\d+(\.\d+)?\s*(KB|MB|GB|TB|KiB|MiB|GiB|TiB)$", re.IGNORECASE)


def _plain_value(v: Any) -> Any:
    """Normalize DuckDB/Arrow result scalars to plain Python.

    SUM/AVG over integers come back as ``decimal.Decimal`` (DuckDB HUGEINT/
    DECIMAL) which drivers like sqlite3 can't bind — flatten to int/float so
    downstream sinks see the same primitive types row transforms produce.
    """
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


@register_transform("sql")
def _build_sql(config: TransformConfig) -> DatasetTransformFn:
    """Run arbitrary SQL over the in-flight dataset (DuckDB, in-process).

    The whole record stream is materialized as a DuckDB relation named
    ``input`` (override with ``view``), the ``query`` runs against it with
    full SQL freedom — joins (against other CTEs / values), GROUP BY,
    window functions, ORDER BY, QUALIFY, … — and the result rows continue
    down the pipeline. Vectorized execution: orders of magnitude faster
    than per-row Python for set operations.

    Config::

        transform:
          type: sql
          query: |
            SELECT region, SUM(amount) AS total
            FROM input GROUP BY region
          view: input        # optional table name for the incoming rows

    Notes:
      * **Batch mode only** — an unbounded stream has no complete dataset
        (the pipeline rejects it with a clear error).
      * The result is a NEW dataset: record ``metadata`` does not survive
        (an aggregate row has no single source record).
      * Empty input ⇒ empty output (the query is skipped — there is no
        schema to register a zero-row relation with).
      * **Larger-than-memory works** (Phase P2a): rows stream into a
        file-backed DuckDB table in Arrow chunks, so the dataset spills
        to a temp directory instead of living in one Python list. Cap
        DuckDB's buffer pool with ``memory_limit: "1GB"`` (default:
        DuckDB's own ~80% of RAM); tune ingest chunking with
        ``chunk_rows`` (default 50k).
      * Requires the ``duckdb`` extra: ``pip install etl-plugins[duckdb]``.
    """
    query = _config_field(config, "query")
    if not isinstance(query, str) or not query.strip():
        raise ConfigError("sql: 'query' must be a non-empty string")
    view = _config_field(config, "view", required=False) or _SQL_DEFAULT_VIEW
    if not isinstance(view, str) or not view.replace("_", "").isalnum():
        raise ConfigError(f"sql: 'view' must be a simple identifier, got {view!r}")
    if view == "__chunk":
        raise ConfigError("sql: 'view' name '__chunk' is reserved for the ingest staging relation")
    memory_limit = _config_field(config, "memory_limit", required=False)
    if memory_limit is not None and (
        not isinstance(memory_limit, str) or not _MEMORY_LIMIT_RE.match(memory_limit.strip())
    ):
        raise ConfigError(
            f"sql: 'memory_limit' must look like '512MB' / '2GB', got {memory_limit!r}"
        )
    chunk_rows = _config_field(config, "chunk_rows", required=False)
    if chunk_rows is None:
        chunk_rows = DEFAULT_BATCH_ROWS
    if not isinstance(chunk_rows, int) or isinstance(chunk_rows, bool) or chunk_rows <= 0:
        raise ConfigError(f"sql: 'chunk_rows' must be a positive integer, got {chunk_rows!r}")
    # ELT pushdown opt-in (ADR-0094). The flag is consumed by the runtime
    # (``Pipeline._try_sql_pushdown``), not here — but a typo like
    # ``pushdown: "yes"`` would silently never engage, so reject non-bools
    # at build time.
    pushdown = _config_field(config, "pushdown", required=False)
    if pushdown is not None and not isinstance(pushdown, bool):
        raise ConfigError(f"sql: 'pushdown' must be true or false, got {pushdown!r}")

    def _sql(records: Iterator[Record]) -> Iterator[Record]:
        try:
            import duckdb
            import pyarrow as pa
        except ImportError as exc:  # pragma: no cover - exercised only without extras
            raise ConfigError(
                "transform 'sql' requires the [duckdb] extra: uv add 'etl-plugins[duckdb]'"
            ) from exc

        # File-backed database in a throwaway directory: base-table pages
        # evict to disk under memory pressure, so the dataset may exceed
        # RAM (an in-memory DuckDB can spill operators but not tables).
        with tempfile.TemporaryDirectory(prefix="etlx-sql-") as tmpdir:
            con = duckdb.connect(os.path.join(tmpdir, "work.duckdb"))
            try:
                if memory_limit is not None:
                    con.execute(f"SET memory_limit='{memory_limit.strip()}'")
                created = False
                try:
                    batches = records_to_batches(records, batch_rows=chunk_rows)
                    for batch in batches:
                        con.register("__chunk", pa.Table.from_batches([batch]))
                        if not created:
                            con.execute(f'CREATE TABLE "{view}" AS SELECT * FROM __chunk')
                            created = True
                        else:
                            # BY NAME: later chunks may order/omit columns
                            # differently; missing ones become NULL. A chunk
                            # introducing a NEW column still fails — records
                            # must keep a consistent shape.
                            con.execute(f'INSERT INTO "{view}" BY NAME SELECT * FROM __chunk')
                        con.unregister("__chunk")
                except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
                    raise TransformError(
                        f"sql: cannot infer Arrow schema from records: {exc}"
                    ) from exc
                except duckdb.Error as exc:
                    raise TransformError(
                        f"sql: ingest failed (records must keep a consistent column set): {exc}"
                    ) from exc
                if not created:
                    return
                try:
                    result = con.execute(query)
                    # duckdb ≥1.4 renamed fetch_record_batch → to_arrow_reader.
                    to_reader = (
                        getattr(result, "to_arrow_reader", None) or result.fetch_record_batch
                    )
                    reader = to_reader()
                except duckdb.Error as exc:
                    raise TransformError(f"sql: query failed: {exc}") from exc
                for out_batch in reader:
                    for data in out_batch.to_pylist():
                        yield Record(data={k: _plain_value(v) for k, v in data.items()})
            finally:
                con.close()

    # Marker consumed by Pipeline._run_task / execute_graph_node staging.
    _sql.dataset_transform = True  # type: ignore[attr-defined]
    return _sql


__all__ = [
    "BuiltinTransform",
    "build_transform",
    "register_transform",
]
