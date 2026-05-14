"""async_io helpers 테스트."""

from __future__ import annotations

import asyncio
import time

import pytest

from etl_plugins.utils.async_io import (
    gather_with_concurrency,
    iter_to_async,
    run_sync_in_thread,
)

# ---------- run_sync_in_thread ----------


async def test_run_sync_in_thread_returns_value() -> None:
    def slow_add(a: int, b: int) -> int:
        return a + b

    assert await run_sync_in_thread(slow_add, 2, 3) == 5


async def test_run_sync_in_thread_propagates_exception() -> None:
    def bad() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        await run_sync_in_thread(bad)


async def test_run_sync_in_thread_kwargs() -> None:
    def f(a: int, b: int = 0) -> int:
        return a + b

    assert await run_sync_in_thread(f, 1, b=10) == 11


# ---------- gather_with_concurrency ----------


async def test_gather_with_concurrency_returns_results() -> None:
    async def f(x: int) -> int:
        return x * 2

    results = await gather_with_concurrency(f(1), f(2), f(3), limit=2)
    assert results == [2, 4, 6]


async def test_gather_with_concurrency_respects_limit() -> None:
    # 동시 실행 슬롯이 2인지 확인 — 각 coroutine이 자신을 시작할 때
    # 현재 active 카운트를 기록한다
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def worker(_: int) -> int:
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.02)
        async with lock:
            active -= 1
        return 0

    await gather_with_concurrency(*(worker(i) for i in range(8)), limit=2)
    assert peak <= 2


async def test_gather_with_concurrency_invalid_limit() -> None:
    async def f() -> int:
        return 1

    with pytest.raises(ValueError, match="positive"):
        await gather_with_concurrency(f(), limit=0)


async def test_gather_with_concurrency_propagates_exception() -> None:
    async def ok() -> int:
        return 1

    async def bad() -> int:
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        await gather_with_concurrency(ok(), bad(), limit=2)


# ---------- iter_to_async ----------


async def test_iter_to_async_basic() -> None:
    result: list[int] = []
    async for x in iter_to_async([1, 2, 3]):
        result.append(x)
    assert result == [1, 2, 3]


async def test_iter_to_async_empty() -> None:
    result: list[int] = []
    async for x in iter_to_async([]):
        result.append(x)
    assert result == []


async def test_iter_to_async_with_generator() -> None:
    def gen() -> object:
        yield from range(4)

    result: list[int] = []
    async for x in iter_to_async(gen()):
        result.append(x)
    assert result == [0, 1, 2, 3]


async def test_iter_to_async_in_thread_mode() -> None:
    # in_thread=True도 같은 결과를 내야 한다 (실제 blocking 동작 보장은 별도)
    result: list[int] = []
    async for x in iter_to_async([10, 20, 30], in_thread=True):
        result.append(x)
    assert result == [10, 20, 30]


async def test_iter_to_async_in_thread_does_not_block_event_loop() -> None:
    """blocking iterator를 thread로 돌리는 동안 다른 태스크가 진행되는지 sanity check."""

    def blocking_gen() -> object:
        for i in range(3):
            time.sleep(0.01)
            yield i

    counter = 0

    async def busy() -> None:
        nonlocal counter
        for _ in range(5):
            await asyncio.sleep(0.005)
            counter += 1

    busy_task = asyncio.create_task(busy())
    result: list[int] = []
    async for x in iter_to_async(blocking_gen(), in_thread=True):
        result.append(x)
    await busy_task

    assert result == [0, 1, 2]
    # busy()는 iter 도중 진행되어야 한다 (in_thread=False라면 차단됨)
    assert counter > 0
