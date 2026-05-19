"""In-memory reference implementations of the connector ABCs.

Used by:
  * unit tests for `Pipeline`, etc. — as lightweight stand-ins for real connectors
  * the contract test suite (`tests/contracts/`) — to verify the contracts
    themselves are well-formed

Variants:

* :class:`InMemoryBatchSource` — yields a list of records on ``read``
* :class:`InMemoryBatchSink` — appends to an internal list on ``write``
* :class:`InMemoryBatchSourceSink` — both batch interfaces over one shared store
* :class:`InMemoryStreamSource` — yields a pre-loaded list as an async iterator
* :class:`InMemoryStreamSink` — collects ``(topic, record)`` pairs
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any

from etl_plugins.core.connector import BatchSink, BatchSource, StreamSink, StreamSource
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

    def read_since(
        self,
        cursor_column: str,
        cursor_value: Any,
        *,
        query: str | None = None,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        """Return records strictly greater than ``cursor_value`` on
        ``cursor_column``, ordered ascending — Step 6.1 contract."""
        self.last_query = query
        self.last_chunk_size = chunk_size
        rows = [r for r in self._records if cursor_column in r.data]
        rows.sort(key=lambda r: r.data[cursor_column])
        for r in rows:
            v = r.data[cursor_column]
            if cursor_value is not None and not (v > cursor_value):
                continue
            yield Record(
                data=r.data,
                metadata={**r.metadata, "cursor_column": cursor_column},
                schema_version=r.schema_version,
            )


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


class InMemoryStreamSource(StreamSource):
    """Yields a pre-loaded list as an async iterator; tracks commit() calls.

    Useful for stream-pipeline unit tests without booting Kafka.
    """

    name = "inmem-stream-source"

    def __init__(self, records: list[Record] | None = None) -> None:
        self._records: list[Record] = list(records or [])
        self.connected = False
        self.commits: list[Any] = []
        self.last_topic: str | None = None
        self.last_group_id: str | None = None

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def health_check(self) -> bool:
        return self.connected

    async def subscribe(
        self,
        topic: str,
        *,
        group_id: str | None = None,
        **options: Any,
    ) -> AsyncIterator[Record]:
        self.last_topic = topic
        self.last_group_id = group_id
        for idx, r in enumerate(self._records):
            yield Record(
                data=dict(r.data),
                metadata={**r.metadata, "topic": topic, "offset": idx},
                schema_version=r.schema_version,
            )

    async def commit(self, offsets: Any = None) -> None:
        self.commits.append(offsets)


class InMemoryStreamSink(StreamSink):
    """Collects ``(topic, Record)`` pairs and counts ``flush()`` calls."""

    name = "inmem-stream-sink"

    def __init__(self) -> None:
        self.published: list[tuple[str, Record]] = []
        self.flush_calls = 0
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def health_check(self) -> bool:
        return self.connected

    async def publish(
        self,
        topic: str,
        record: Record,
        key: bytes | None = None,
    ) -> None:
        self.published.append((topic, record))

    async def flush(self) -> None:
        self.flush_calls += 1
