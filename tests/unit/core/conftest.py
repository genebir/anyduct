"""Core 테스트용 픽스처: 인메모리 BatchSource / BatchSink."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import pytest

from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.record import Record


class InMemoryBatchSource(BatchSource):
    """미리 로드된 records를 그대로 yield 하는 테스트용 source."""

    name = "inmem-source"

    def __init__(self, records: list[Record] | None = None) -> None:
        self._records: list[Record] = records or []
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
    """records를 리스트에 모으는 테스트용 sink."""

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
        count = 0
        for r in records:
            self.records.append(r)
            count += 1
        return count


@pytest.fixture
def sample_records() -> list[Record]:
    return [
        Record(data={"id": 1, "name": "Alice"}),
        Record(data={"id": 2, "name": "Bob"}),
        Record(data={"id": 3, "name": "Carol"}),
    ]


@pytest.fixture
def in_memory_source(sample_records: list[Record]) -> InMemoryBatchSource:
    return InMemoryBatchSource(sample_records)


@pytest.fixture
def in_memory_sink() -> InMemoryBatchSink:
    return InMemoryBatchSink()
