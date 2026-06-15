"""Cursor / watermark abstraction for incremental reads (SPEC.md §4.4, ADR-0024).

A *cursor* is a (column, value) pair that marks progress through an
otherwise unbounded source. The next read returns records where
``column > value`` (strict — equal values are assumed already processed
to avoid double-reads on resume). ``value=None`` means "no progress yet,
read from the beginning".

Two pieces in this module:

* :class:`Cursor` — the value type. Immutable, comparable on ``value``.
* :class:`CursorState` — an ABC for a persistent keyed store of cursors,
  with two batteries-included implementations:

  * :class:`InMemoryCursorState` — dict-backed, useful for tests and
    single-shot pipelines.
  * :class:`FileCursorState` — a JSON file on disk; durable on a single
    host. Writes are atomic (temp file + ``os.replace``) so a crash
    mid-write can't corrupt the state.

The DB-backed implementation lives in ``services/anyduct-server`` so the
core stays orchestrator-agnostic (the runs table doubles as cursor
storage there).

This module does **not** dictate how connectors implement cursor reads —
that's :meth:`BatchSource.read_since` (still optional; default raises
``NotImplementedError``). Connectors that don't support cursored reads
keep the same shape as before.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from etl_plugins.core.record import Record

# Cursor.value supports anything JSON-serializable so it round-trips through
# FileCursorState without custom encoders. datetime values are stringified at
# the serialization boundary; comparison inside a single process uses the
# native Python objects, which works because Python's < / > are defined for
# datetime-vs-datetime and str-vs-str (which is what the file load returns).
CursorValue = int | float | str | bool | datetime | None


class Cursor(BaseModel):
    """A watermark — column name + last-seen value.

    The next call to :meth:`BatchSource.read_since` returns records whose
    ``column`` value is strictly greater than ``value``. ``value=None``
    means "start from the beginning".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    column: str
    value: CursorValue = None


def max_cursor_value(
    records: Iterable[Record], column: str, *, current: CursorValue = None
) -> CursorValue:
    """Return the maximum value of ``column`` across ``records``.

    ``current`` is the starting watermark — useful for "I just processed a
    batch, what's the new high-water mark?" flows where the caller wants
    to fold the new max into the previously-known one. Returns ``current``
    if no record carries ``column`` or all values are None.
    """
    best: CursorValue = current
    for r in records:
        v = r.data.get(column)
        if v is None:
            continue
        if best is None or v > best:
            best = v
    return best


class CursorState(ABC):
    """Persistent store for cursors, keyed by a stable string name.

    A typical key is ``"<pipeline_id>:<task_id>"`` or ``"<connector>:<table>"``
    — the runtime chooses; this ABC doesn't care, it just needs uniqueness.

    Implementations are responsible for atomicity: ``set()`` either
    persists the new value fully or leaves the prior value intact. Partial
    writes are not allowed (otherwise a crash mid-flush could rewind the
    cursor and replay records).
    """

    @abstractmethod
    def get(self, name: str) -> Cursor | None: ...

    @abstractmethod
    def set(self, name: str, cursor: Cursor) -> None: ...

    @abstractmethod
    def delete(self, name: str) -> None: ...

    def update(self, name: str, column: str, value: CursorValue) -> Cursor:
        """Shorthand for ``set(name, Cursor(column=column, value=value))``.

        Returns the freshly-stored cursor so the caller can also log it.
        """
        cursor = Cursor(column=column, value=value)
        self.set(name, cursor)
        return cursor


class InMemoryCursorState(CursorState):
    """Dict-backed CursorState — useful for tests and single-shot runs.

    Not durable: process exit drops state. For persistence across runs use
    :class:`FileCursorState` or the metadata-DB implementation in
    ``services/anyduct-server``.
    """

    def __init__(self, initial: dict[str, Cursor] | None = None) -> None:
        self._store: dict[str, Cursor] = dict(initial or {})

    def get(self, name: str) -> Cursor | None:
        return self._store.get(name)

    def set(self, name: str, cursor: Cursor) -> None:
        self._store[name] = cursor

    def delete(self, name: str) -> None:
        self._store.pop(name, None)


class FileCursorState(CursorState):
    """JSON-file-backed CursorState — durable on a single host.

    File layout::

        {
          "pipelines:orders": {"column": "id", "value": 4823},
          "pipelines:events": {"column": "ts", "value": "2026-05-19T03:00:00Z"}
        }

    Writes are atomic: the new state is serialized to a temp file in the
    same directory and then ``os.replace``d onto the target so a crash
    mid-write can't corrupt the store. ``datetime`` values are stored as
    ISO-8601 strings; reads return them as ``str``, so callers that need
    a real ``datetime`` must parse it themselves.

    Concurrent processes writing to the same file are NOT serialized —
    use the DB-backed state for multi-replica setups.
    """

    _ENCODING: ClassVar[str] = "utf-8"

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        # Parent directory must exist — otherwise atomic-write fails.
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {}
        try:
            data: dict[str, dict[str, Any]] = json.loads(
                self._path.read_text(encoding=self._ENCODING)
            )
        except json.JSONDecodeError:
            # Corrupted file: treat as empty so reads don't crash. The next
            # set() will atomically rewrite it.
            return {}
        return data

    def _dump(self, data: dict[str, dict[str, Any]]) -> None:
        # Atomic write: temp file in the same directory, then replace.
        directory = self._path.parent
        fd, tmp_path = tempfile.mkstemp(prefix=".cursor-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding=self._ENCODING) as f:
                json.dump(data, f, default=_json_default, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._path)
        except Exception:
            # Best-effort cleanup; ignore errors so the original exception
            # propagates as-is.
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    def get(self, name: str) -> Cursor | None:
        raw = self._load().get(name)
        if raw is None:
            return None
        return Cursor.model_validate(raw)

    def set(self, name: str, cursor: Cursor) -> None:
        data = self._load()
        data[name] = cursor.model_dump()
        self._dump(data)

    def delete(self, name: str) -> None:
        data = self._load()
        if name in data:
            data.pop(name)
            self._dump(data)


def _json_default(obj: Any) -> Any:
    """JSON default-encoder for ``datetime`` values."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Cursor value of type {type(obj).__name__} is not JSON-serializable")


__all__ = [
    "Cursor",
    "CursorState",
    "CursorValue",
    "FileCursorState",
    "InMemoryCursorState",
    "max_cursor_value",
]
