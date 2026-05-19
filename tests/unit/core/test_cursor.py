"""Unit tests for the cursor abstraction (Step 6.1)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from etl_plugins.core.cursor import (
    Cursor,
    FileCursorState,
    InMemoryCursorState,
    max_cursor_value,
)
from etl_plugins.core.record import Record

# --- Cursor model ----------------------------------------------------------


def test_cursor_requires_column() -> None:
    with pytest.raises(ValidationError):
        Cursor()  # type: ignore[call-arg]


def test_cursor_is_frozen() -> None:
    c = Cursor(column="id", value=42)
    with pytest.raises(ValidationError):
        c.value = 99  # type: ignore[misc]


def test_cursor_supports_int_str_datetime_none() -> None:
    Cursor(column="id", value=7)
    Cursor(column="uid", value="abc")
    Cursor(column="ts", value=datetime(2026, 5, 19, tzinfo=UTC))
    Cursor(column="any", value=None)


def test_cursor_rejects_unknown_types() -> None:
    """The CursorValue union doesn't include dict / list."""
    with pytest.raises(ValidationError):
        Cursor(column="bad", value={"nested": 1})  # type: ignore[arg-type]


# --- max_cursor_value ------------------------------------------------------


def test_max_cursor_value_returns_largest() -> None:
    records = [
        Record(data={"id": 3}),
        Record(data={"id": 7}),
        Record(data={"id": 5}),
    ]
    assert max_cursor_value(records, "id") == 7


def test_max_cursor_value_folds_in_current() -> None:
    records = [Record(data={"id": 3}), Record(data={"id": 7})]
    # Existing watermark is higher than anything in the batch.
    assert max_cursor_value(records, "id", current=100) == 100


def test_max_cursor_value_ignores_missing_column() -> None:
    records = [Record(data={"id": 1}), Record(data={"other": 99})]
    assert max_cursor_value(records, "id") == 1


def test_max_cursor_value_returns_current_when_no_records() -> None:
    assert max_cursor_value([], "id", current=42) == 42
    assert max_cursor_value([], "id") is None


def test_max_cursor_value_ignores_none_values() -> None:
    records = [Record(data={"id": None}), Record(data={"id": 5})]
    assert max_cursor_value(records, "id") == 5


def test_max_cursor_value_with_datetime() -> None:
    a = datetime(2026, 1, 1, tzinfo=UTC)
    b = datetime(2026, 5, 19, tzinfo=UTC)
    records = [Record(data={"ts": a}), Record(data={"ts": b})]
    assert max_cursor_value(records, "ts") == b


# --- InMemoryCursorState ---------------------------------------------------


def test_in_memory_get_missing_returns_none() -> None:
    state = InMemoryCursorState()
    assert state.get("nope") is None


def test_in_memory_set_then_get() -> None:
    state = InMemoryCursorState()
    state.set("orders", Cursor(column="id", value=42))
    got = state.get("orders")
    assert got is not None
    assert got.column == "id"
    assert got.value == 42


def test_in_memory_set_overwrites() -> None:
    state = InMemoryCursorState()
    state.set("orders", Cursor(column="id", value=1))
    state.set("orders", Cursor(column="id", value=99))
    assert state.get("orders") == Cursor(column="id", value=99)


def test_in_memory_delete_is_idempotent() -> None:
    state = InMemoryCursorState()
    state.set("orders", Cursor(column="id", value=1))
    state.delete("orders")
    state.delete("orders")  # second delete is a no-op
    assert state.get("orders") is None


def test_in_memory_update_helper() -> None:
    state = InMemoryCursorState()
    cur = state.update("orders", "id", 7)
    assert cur == Cursor(column="id", value=7)
    assert state.get("orders") == cur


def test_in_memory_initial_seed() -> None:
    state = InMemoryCursorState({"orders": Cursor(column="id", value=5)})
    assert state.get("orders") == Cursor(column="id", value=5)


# --- FileCursorState -------------------------------------------------------


def test_file_get_missing_returns_none(tmp_path: Path) -> None:
    state = FileCursorState(tmp_path / "cursors.json")
    assert state.get("nope") is None


def test_file_set_creates_file(tmp_path: Path) -> None:
    p = tmp_path / "subdir" / "cursors.json"
    state = FileCursorState(p)
    state.set("orders", Cursor(column="id", value=42))
    assert p.exists()
    payload = json.loads(p.read_text())
    assert payload == {"orders": {"column": "id", "value": 42}}


def test_file_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "cursors.json"
    state = FileCursorState(p)
    state.set("orders", Cursor(column="id", value=42))
    state.set("events", Cursor(column="ts", value="2026-05-19T03:00:00Z"))

    # Fresh handle reads what the first wrote.
    again = FileCursorState(p)
    assert again.get("orders") == Cursor(column="id", value=42)
    assert again.get("events") == Cursor(column="ts", value="2026-05-19T03:00:00Z")


def test_file_persists_datetime_as_isoformat(tmp_path: Path) -> None:
    p = tmp_path / "cursors.json"
    state = FileCursorState(p)
    ts = datetime(2026, 5, 19, 3, 0, tzinfo=UTC)
    state.set("events", Cursor(column="ts", value=ts))

    raw = json.loads(p.read_text())
    assert raw["events"]["value"] == ts.isoformat()
    # And it can be read back (as a string — caller parses if needed).
    again = FileCursorState(p)
    cur = again.get("events")
    assert cur is not None
    assert cur.value == ts.isoformat()


def test_file_delete_removes_key(tmp_path: Path) -> None:
    p = tmp_path / "cursors.json"
    state = FileCursorState(p)
    state.set("a", Cursor(column="id", value=1))
    state.set("b", Cursor(column="id", value=2))
    state.delete("a")

    assert state.get("a") is None
    assert state.get("b") == Cursor(column="id", value=2)
    raw = json.loads(p.read_text())
    assert "a" not in raw
    assert "b" in raw


def test_file_delete_missing_is_noop(tmp_path: Path) -> None:
    p = tmp_path / "cursors.json"
    state = FileCursorState(p)
    state.delete("never-existed")  # must not raise
    assert not p.exists() or json.loads(p.read_text()) == {}


def test_file_corrupted_file_is_treated_as_empty(tmp_path: Path) -> None:
    p = tmp_path / "cursors.json"
    p.write_text("{not valid json")
    state = FileCursorState(p)
    assert state.get("anything") is None
    # The next set() rewrites the file with a clean payload.
    state.set("ok", Cursor(column="id", value=1))
    assert json.loads(p.read_text()) == {"ok": {"column": "id", "value": 1}}


def test_file_atomic_write_no_tempfile_leak(tmp_path: Path) -> None:
    """After a successful set, the directory should hold just the target
    file and no leftover ``.cursor-*.json`` temp file."""
    p = tmp_path / "cursors.json"
    state = FileCursorState(p)
    state.set("orders", Cursor(column="id", value=1))
    leftovers = [f for f in os.listdir(tmp_path) if f.startswith(".cursor-")]
    assert leftovers == []


def test_file_supports_concurrent_writes_to_different_keys_in_one_handle(
    tmp_path: Path,
) -> None:
    """Sequential writes to different names accumulate, not clobber."""
    p = tmp_path / "cursors.json"
    state = FileCursorState(p)
    for i, name in enumerate(["a", "b", "c", "d"]):
        state.set(name, Cursor(column="id", value=i))
    assert {
        k: v.value for k, v in ((n, state.get(n)) for n in ["a", "b", "c", "d"]) if v is not None
    } == {"a": 0, "b": 1, "c": 2, "d": 3}


# --- BatchSource.read_since default ---------------------------------------


def test_read_since_default_raises_not_implemented() -> None:
    """Existing connectors don't break, but a caller that asks for a
    cursored read on a non-supporting source gets a clear error."""
    from collections.abc import Iterator

    from etl_plugins.core.connector import BatchSource

    class DummySource(BatchSource):
        def connect(self) -> None: ...
        def close(self) -> None: ...
        def health_check(self) -> bool:
            return True

        def read(
            self,
            query: str | None = None,
            *,
            chunk_size: int = 10_000,
            **options: Any,
        ) -> Iterator[Record]:
            return iter([])

    s = DummySource()
    with pytest.raises(NotImplementedError, match="DummySource"):
        list(s.read_since(cursor_column="id", cursor_value=None))
