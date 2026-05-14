"""Shared helpers for contract tests."""

from __future__ import annotations

from etl_plugins.core.record import Record


def normalize_payloads(records: list[Record]) -> list[tuple]:
    """Convert a list of Records into a canonical, order-independent form.

    Returns a sorted list of ``(sorted_data_items_tuple,)``. Two record lists
    with the same payloads (regardless of order) produce equal results.

    Values must be hashable / sortable for this to work — fine for our
    standard test datasets which use scalar types + small lists/dicts.
    """

    def _key(r: Record) -> tuple:
        # nested dict/list values aren't hashable, so canonicalize by repr
        return tuple(sorted((k, repr(v)) for k, v in r.data.items()))

    return sorted(_key(r) for r in records)
