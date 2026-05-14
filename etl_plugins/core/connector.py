"""Abstract connector interfaces. SPEC.md §4.1.

Hybrid connectors (e.g. Kafka, which both produces and consumes) inherit from
multiple bases — typically ``StreamSource + StreamSink`` or ``BatchSource + BatchSink``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable, Iterator
from types import TracebackType
from typing import Any, ClassVar, Self

from etl_plugins.core.record import Record


class Connector(ABC):
    """Base class for every connector.

    The ``name`` class attribute is set by ``ConnectorRegistry.register("...")``;
    its value identifies the connector in ``connections.yaml``.
    """

    name: ClassVar[str] = ""

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def health_check(self) -> bool: ...

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class BatchSource(Connector):
    """Reads records in chunks via a synchronous iterator."""

    @abstractmethod
    def read(
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]: ...


class BatchSink(Connector):
    """Writes records in chunks. Returns the number of records written."""

    @abstractmethod
    def write(
        self,
        records: Iterable[Record],
        *,
        mode: str = "append",  # append | overwrite | upsert
        key_columns: list[str] | None = None,
        **options: Any,
    ) -> int: ...


class StreamSource(Connector):
    """Subscribes to a streaming source via an async iterator."""

    @abstractmethod
    def subscribe(
        self,
        topic: str,
        *,
        group_id: str | None = None,
        **options: Any,
    ) -> AsyncIterator[Record]: ...

    @abstractmethod
    async def commit(self, offsets: Any = None) -> None:
        """Commit progress (e.g., Kafka offsets, Kinesis sequence numbers).

        Async because real client libs (aiokafka, ...) are async. ``offsets``
        is connector-specific: ``None`` means "commit the current internal
        position", a dict means "commit these specific positions". Pipeline
        runtime invokes ``await source.commit()`` after each successful sink
        flush when ``commit.strategy: after_sink_flush``.
        """
        ...


class StreamSink(Connector):
    """Publishes records to a streaming sink."""

    @abstractmethod
    async def publish(
        self,
        topic: str,
        record: Record,
        key: bytes | None = None,
    ) -> None: ...

    @abstractmethod
    async def flush(self) -> None: ...
