"""Async IO helpers. SPEC.md §9.4.

I/O bound work can run via ``asyncio`` or via a thread pool. These helpers
bridge sync↔async without leaking either model into the rest of the codebase.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")
T = TypeVar("T")


async def run_sync_in_thread(
    fn: Callable[P, R],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    """Run a blocking ``fn(*args, **kwargs)`` in the default thread executor.

    Thin wrapper over :func:`asyncio.to_thread` — kept here so callers don't
    have to think about which stdlib function to use.
    """
    return await asyncio.to_thread(fn, *args, **kwargs)


async def gather_with_concurrency(
    *coros: Awaitable[T],
    limit: int = 10,
) -> list[T]:
    """Like :func:`asyncio.gather` but bounded by a semaphore.

    Useful when fanning out N requests but at most ``limit`` should run
    concurrently (rate limits, connection pool size, etc.).

    Raises
    ------
    ValueError
        if ``limit <= 0``.
    """
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")
    sem = asyncio.Semaphore(limit)

    async def _run(awaitable: Awaitable[T]) -> T:
        async with sem:
            return await awaitable

    return await asyncio.gather(*(_run(c) for c in coros))


async def iter_to_async(
    iterable: Iterable[T],
    *,
    in_thread: bool = False,
) -> AsyncIterator[T]:
    """Wrap a sync iterable as an async iterator.

    If ``in_thread=True``, each ``next()`` call runs in :func:`asyncio.to_thread`,
    so a blocking iterator (e.g. a DB cursor) does not stall the event loop.
    """
    if not in_thread:
        for x in iterable:
            yield x
        return

    it = iter(iterable)
    _sentinel: object = object()

    while True:
        v = await asyncio.to_thread(next, it, _sentinel)
        if v is _sentinel:
            return
        yield v  # type: ignore[misc]
