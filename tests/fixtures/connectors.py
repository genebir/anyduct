"""In-memory reference implementations of the BatchSource / BatchSink ABCs.

Used by:
  * unit tests for `Pipeline`, etc. — as lightweight stand-ins for real connectors
  * the contract test suite (`tests/contracts/`) — to verify the contracts
    themselves are well-formed

Two variants:

* :class:`InMemoryBatchSource` — yields a list of records on ``read``
* :class:`InMemoryBatchSink` — appends to an internal list on ``write``
* :class:`InMemoryBatchSourceSink` — both interfaces over a single shared store;
  natural for round-trip contract tests.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.record import Record


class InMemoryBatchSource(BatchSource):
    """Yields a pre-loaded list of records. The list reference is kept live —
    mutating the list (e.g. via a paired sink) affects subsequent ``read``s."""

    name = "inmem-source"

    def __init__(self, records: list[Record] | None = None) -> None:
        self._records: list[Record] = records if records is not None else []
        self.connected = False
        self.last_query: str | None = None
        self.last_chunk_size: int | None = None

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def health_check(self) -> bool:
        return self.connected

    def read(
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        self.last_query = query
        self.last_chunk_size = chunk_size
        yield from self._records


class InMemoryBatchSink(BatchSink):
    """Collects records into ``self.records``."""

    name = "inmem-sink"

    def __init__(self) -> None:
        self.records: list[Record] = []
        self.last_mode: str = ""
        self.last_key_columns: list[str] | None = None
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def health_check(self) -> bool:
        return self.connected

    def write(
        self,
        records: Iterable[Record],
        *,
        mode: str = "append",
        key_columns: list[str] | None = None,
        **options: Any,
    ) -> int:
        self.last_mode = mode
        self.last_key_columns = key_columns
        if mode == "overwrite":
            self.records.clear()
        count = 0
        for r in records:
            self.records.append(r)
            count += 1
        return count


class InMemoryBatchSourceSink(BatchSource, BatchSink):
    """Combined source+sink sharing a single internal store.

    Use this when a test needs to write and then read back from the same
    "backing storage" — the InMemory equivalent of a real round-trip.
    """

    name = "inmem-combined"

    def __init__(self) -> None:
        self.records: list[Record] = []
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def health_check(self) -> bool:
        return self.connected

    def read(
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        yield from self.records

    def write(
        self,
        records: Iterable[Record],
        *,
        mode: str = "append",
        key_columns: list[str] | None = None,
        **options: Any,
    ) -> int:
        if mode == "overwrite":
            self.records.clear()
        count = 0
        for r in records:
            self.records.append(r)
            count += 1
        return count
