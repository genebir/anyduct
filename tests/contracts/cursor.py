"""Contract mixin for cursored BatchSource reads (Step 6.1 / ADR-0024).

Any :class:`BatchSource` that overrides :meth:`BatchSource.read_since`
should inherit from :class:`_BatchSourceCursorContract` and provide the
required fixtures::

    class TestMySourceCursor(_BatchSourceCursorContract):
        @pytest.fixture
        def cursor_source(self) -> BatchSource:
            return MySource(...)

        @pytest.fixture
        def cursor_seeded_records(self) -> list[Record]:
            # Sorted ascending by ``cursor_column``. Must have >= 3 records.
            return [...]

        @pytest.fixture
        def cursor_column(self) -> str:
            return "id"

        @pytest.fixture
        def read_since_kwargs(self) -> dict[str, object]:
            # Forwarded as kwargs to every ``cursor_source.read_since(...)``.
            return {"query": "SELECT id, name FROM t"}

The contract verifies the four invariants from ADR-0024:

1. ``cursor_value=None`` returns every record, ordered ascending by cursor.
2. A mid-range cursor returns only strictly-greater rows.
3. A cursor at or above the max value returns nothing.
4. Resume across two batches is idempotent: ``batch1 + batch2`` equals the
   full set with no overlap (no record is emitted twice across the join).
"""

from __future__ import annotations

import pytest

from etl_plugins.core.connector import BatchSource
from etl_plugins.core.cursor import max_cursor_value
from etl_plugins.core.record import Record
from tests.contracts._helpers import normalize_payloads


class _BatchSourceCursorContract:
    """Every BatchSource that implements read_since must satisfy these."""

    # ---------- abstract fixtures (subclass overrides) -----------------

    @pytest.fixture
    def cursor_source(self) -> BatchSource:
        raise NotImplementedError("subclass must provide a 'cursor_source' fixture")

    @pytest.fixture
    def cursor_seeded_records(self) -> list[Record]:
        raise NotImplementedError(
            "subclass must provide a 'cursor_seeded_records' fixture "
            "(>= 3 records, sorted ascending by cursor_column)"
        )

    @pytest.fixture
    def cursor_column(self) -> str:
        raise NotImplementedError("subclass must provide a 'cursor_column' fixture")

    @pytest.fixture
    def read_since_kwargs(self) -> dict[str, object]:
        return {}

    # ---------- contract -----------------------------------------------

    def test_read_since_none_returns_all_records_ordered(
        self,
        cursor_source: BatchSource,
        cursor_seeded_records: list[Record],
        cursor_column: str,
        read_since_kwargs: dict[str, object],
    ) -> None:
        with cursor_source:
            result = list(cursor_source.read_since(cursor_column, None, **read_since_kwargs))
        assert normalize_payloads(result) == normalize_payloads(cursor_seeded_records)
        # Ascending order by cursor column.
        values = [r.data[cursor_column] for r in result]
        assert values == sorted(values)

    def test_read_since_midrange_excludes_equal_and_lower(
        self,
        cursor_source: BatchSource,
        cursor_seeded_records: list[Record],
        cursor_column: str,
        read_since_kwargs: dict[str, object],
    ) -> None:
        sorted_records = sorted(cursor_seeded_records, key=lambda r: r.data[cursor_column])
        midpoint = sorted_records[len(sorted_records) // 2 - 1].data[cursor_column]
        expected = [r for r in sorted_records if r.data[cursor_column] > midpoint]

        with cursor_source:
            result = list(cursor_source.read_since(cursor_column, midpoint, **read_since_kwargs))

        assert normalize_payloads(result) == normalize_payloads(expected)
        # And nothing at-or-below the watermark is in the result.
        for r in result:
            assert r.data[cursor_column] > midpoint

    def test_read_since_at_max_returns_empty(
        self,
        cursor_source: BatchSource,
        cursor_seeded_records: list[Record],
        cursor_column: str,
        read_since_kwargs: dict[str, object],
    ) -> None:
        top = max_cursor_value(cursor_seeded_records, cursor_column)
        assert top is not None  # seed must have at least one value

        with cursor_source:
            result = list(cursor_source.read_since(cursor_column, top, **read_since_kwargs))
        assert result == []

    def test_read_since_stamps_cursor_column_metadata(
        self,
        cursor_source: BatchSource,
        cursor_seeded_records: list[Record],
        cursor_column: str,
        read_since_kwargs: dict[str, object],
    ) -> None:
        """Every record emitted by ``read_since`` must carry
        ``metadata['cursor_column']`` so downstream transforms / sinks can
        identify the watermark column without re-parsing the query.
        Documented in ``docs/guides/cursors.md`` and enforced here."""
        with cursor_source:
            result = list(cursor_source.read_since(cursor_column, None, **read_since_kwargs))
        assert result, "seed must yield at least one record"
        for r in result:
            assert (
                r.metadata.get("cursor_column") == cursor_column
            ), f"record emitted without cursor_column metadata: {r!r}"

    def test_read_since_resume_is_idempotent(
        self,
        cursor_source: BatchSource,
        cursor_seeded_records: list[Record],
        cursor_column: str,
        read_since_kwargs: dict[str, object],
    ) -> None:
        """Two-batch resume covers the whole set exactly once."""
        # First batch: a cursor at the second-smallest value reads everything
        # strictly greater. Pick that, then resume from its max.
        sorted_records = sorted(cursor_seeded_records, key=lambda r: r.data[cursor_column])
        first_watermark = sorted_records[0].data[cursor_column]

        with cursor_source:
            batch1 = list(
                cursor_source.read_since(cursor_column, first_watermark, **read_since_kwargs)
            )
        assert batch1, "batch1 should not be empty if seed has >= 2 rows"
        second_watermark = max_cursor_value(batch1, cursor_column)

        with cursor_source:
            batch2 = list(
                cursor_source.read_since(cursor_column, second_watermark, **read_since_kwargs)
            )
        assert batch2 == [], "resuming from the max of batch1 should yield nothing"

        # Combined batch1 covers everything strictly greater than first_watermark.
        expected = [r for r in sorted_records if r.data[cursor_column] > first_watermark]
        assert normalize_payloads(batch1) == normalize_payloads(expected)
