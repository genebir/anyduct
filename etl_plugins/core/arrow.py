"""Arrow interchange plane (ADR-0093, Phase 2).

The row-based ``Record`` plane tops out around 10^5 rows/s because every
value crosses Python object land. Phase 2 moves bulk data as Arrow
``RecordBatch``es instead: connectors that can produce/consume Arrow
natively (COPY, driver Arrow APIs) declare the optional capabilities
below, and everything else interoperates through the
``records_to_batches`` / ``batches_to_records`` adapters.

Arrow is also the clustering story (ADR-0093 Phase 3): a ``Partition``
describes one slice of a big read, so a coordinator can split a single
run into N partition sub-reads fanned out across worker replicas — and,
if a true distributed engine is ever warranted (Tier 3), Arrow is the
interchange those engines already speak.

pyarrow is imported lazily (it ships with the ``[duckdb]`` / ``[s3]``
extras); importing this module costs nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from etl_plugins.core.record import Record

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pyarrow as pa

#: Default rows per RecordBatch — large enough to amortize per-batch
#: overhead, small enough to keep peak memory per chunk modest.
DEFAULT_BATCH_ROWS = 50_000

__all__ = [
    "DEFAULT_BATCH_ROWS",
    "ArrowReadable",
    "ArrowWritable",
    "Partition",
    "batches_to_records",
    "records_to_batches",
]


@dataclass(frozen=True)
class Partition:
    """One half-open slice ``(lower, upper]`` of ``column``.

    The unit of single-run scale-out: a coordinator splits a large read
    into partitions (by PK / cursor ranges) and hands each to a worker
    replica. ``lower=None`` means unbounded below, ``upper=None``
    unbounded above — ``Partition("id", None, None)`` is the whole table.
    Matches the cursor contract (``read_since``): half-open so adjacent
    partitions never overlap and never lose the boundary row.
    """

    column: str
    lower: Any | None = None
    upper: Any | None = None


@runtime_checkable
class ArrowReadable(Protocol):
    """Optional connector capability: bulk reads as Arrow RecordBatches.

    The Arrow twin of :class:`~etl_plugins.core.connector.BatchSource`'s
    ``read`` — same ``query``/options semantics, columnar output. The
    ``partition`` narrows the read to one :class:`Partition` slice so N
    workers can split a table without overlap.
    """

    def read_arrow(
        self,
        *,
        query: str | None = None,
        partition: Partition | None = None,
        **options: Any,
    ) -> Iterator[pa.RecordBatch]: ...


@runtime_checkable
class ArrowWritable(Protocol):
    """Optional connector capability: bulk writes from Arrow RecordBatches.

    The Arrow twin of ``BatchSink.write`` — same ``table``/``mode``/
    ``key_columns`` semantics, columnar input. Returns rows written.
    """

    def write_arrow(
        self,
        batches: Iterable[pa.RecordBatch],
        *,
        table: str | None = None,
        mode: str = "append",
        key_columns: list[str] | None = None,
        **options: Any,
    ) -> int: ...


def records_to_batches(
    records: Iterable[Record],
    *,
    batch_rows: int = DEFAULT_BATCH_ROWS,
) -> Iterator[pa.RecordBatch]:
    """Chunk a Record stream into Arrow RecordBatches (bounded memory).

    Schema is inferred per chunk (keys missing in some rows become
    nulls). Consumers that need ONE schema across all batches must
    unify downstream — uniform sources (RDBMS rows) are uniform here
    too. ``Record.metadata`` does not survive the columnar hop.
    """
    import pyarrow as pa

    if batch_rows <= 0:
        raise ValueError(f"batch_rows must be positive, got {batch_rows}")
    buf: list[dict[str, Any]] = []
    for record in records:
        buf.append(record.data)
        if len(buf) >= batch_rows:
            yield from pa.Table.from_pylist(buf).to_batches()
            buf = []
    if buf:
        yield from pa.Table.from_pylist(buf).to_batches()


def batches_to_records(batches: Iterable[pa.RecordBatch]) -> Iterator[Record]:
    """Re-materialize Arrow RecordBatches as Records (fresh metadata)."""
    for batch in batches:
        for data in batch.to_pylist():
            yield Record(data=data)
