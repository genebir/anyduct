"""Iterator chunking helpers. SPEC.md §9.4 (memory-safe iterator/generator)."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from itertools import islice
from typing import TypeVar

T = TypeVar("T")


def chunked(iterable: Iterable[T], size: int) -> Iterator[list[T]]:
    """Yield successive lists of up to ``size`` items from ``iterable``.

    The final list may be shorter than ``size``. Materializes each chunk as
    a list — safe to consume independently of the upstream iterator state.

    Raises
    ------
    ValueError
        if ``size <= 0``.

    Example::

        for batch in chunked(records, 1000):
            sink.write(batch)
    """
    if size <= 0:
        raise ValueError(f"chunk size must be positive, got {size}")
    it = iter(iterable)
    while batch := list(islice(it, size)):
        yield batch


def take(iterable: Iterable[T], n: int) -> list[T]:
    """Return the first ``n`` items of ``iterable`` as a list."""
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    return list(islice(iter(iterable), n))


def drop(iterable: Iterable[T], n: int) -> Iterator[T]:
    """Skip the first ``n`` items, yield the rest."""
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    return islice(iter(iterable), n, None)
