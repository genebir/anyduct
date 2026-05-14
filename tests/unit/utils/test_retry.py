"""@retryable 테스트."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from etl_plugins.observability.metrics import (
    Counter,
    Metrics,
    NoOpCounter,
    NoOpHistogram,
    NoOpMetrics,
    reset_metrics,
    set_metrics,
)
from etl_plugins.utils.retry import retryable

# 테스트에서 실제 sleep을 피하기 위해 가능한 한 짧은 delay 사용
FAST = {"initial_delay_seconds": 0.001, "max_delay_seconds": 0.01, "jitter": False}


# ---------- sync ----------


def test_succeeds_on_first_try() -> None:
    calls = 0

    @retryable(max_attempts=3, **FAST)
    def f() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    assert f() == "ok"
    assert calls == 1


def test_retries_until_success() -> None:
    calls = 0

    @retryable(max_attempts=4, **FAST)
    def f() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ValueError("transient")
        return "ok"

    assert f() == "ok"
    assert calls == 3


def test_exhausts_and_reraises_original_exception() -> None:
    calls = 0

    @retryable(max_attempts=2, **FAST)
    def f() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("always fails")

    with pytest.raises(ValueError, match="always fails"):
        f()
    assert calls == 2


def test_does_not_retry_unmatched_exception() -> None:
    calls = 0

    @retryable(max_attempts=5, on=ValueError, **FAST)
    def f() -> None:
        nonlocal calls
        calls += 1
        raise TypeError("wrong kind")

    with pytest.raises(TypeError):
        f()
    assert calls == 1  # 첫 시도 후 즉시 propagate


def test_retries_only_matched_exception_tuple() -> None:
    counts = {"v": 0, "r": 0}

    @retryable(max_attempts=3, on=(ValueError, KeyError), **FAST)
    def f(exc_type: type[BaseException]) -> None:
        counts[exc_type.__name__[0].lower()] = counts.get(exc_type.__name__[0].lower(), 0) + 1
        raise exc_type("nope")

    with pytest.raises(ValueError):
        f(ValueError)
    with pytest.raises(KeyError):
        f(KeyError)
    with pytest.raises(RuntimeError):
        f(RuntimeError)  # 매치 안됨 → 한 번만


def test_fixed_backoff() -> None:
    calls = 0

    @retryable(max_attempts=3, backoff="fixed", initial_delay_seconds=0.001, jitter=False)
    def f() -> str:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise ValueError("x")
        return "ok"

    assert f() == "ok"


def test_unknown_backoff_raises() -> None:
    with pytest.raises(ValueError, match="unknown backoff"):

        @retryable(max_attempts=3, backoff="parabolic", initial_delay_seconds=0.001)
        def f() -> None:
            pass


def test_bare_decorator_form() -> None:
    # @retryable (no parens)
    calls = 0

    @retryable
    def f() -> int:
        nonlocal calls
        calls += 1
        return 42

    assert f() == 42
    assert calls == 1


def test_wraps_function_metadata() -> None:
    @retryable(max_attempts=2, **FAST)
    def my_named_func() -> str:
        """docstring."""
        return "x"

    assert my_named_func.__name__ == "my_named_func"
    assert my_named_func.__doc__ == "docstring."


# ---------- async ----------


async def test_async_succeeds_on_first_try() -> None:
    @retryable(max_attempts=3, **FAST)
    async def f() -> str:
        return "ok"

    assert await f() == "ok"


async def test_async_retries_until_success() -> None:
    calls = 0

    @retryable(max_attempts=4, **FAST)
    async def f() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ValueError("transient")
        return "ok"

    assert await f() == "ok"
    assert calls == 3


async def test_async_exhausts_and_reraises() -> None:
    @retryable(max_attempts=2, **FAST)
    async def f() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        await f()


# ---------- observability hook ----------


class _CountingMetrics(Metrics):
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}

    def counter(self, name: str, description: str = "", unit: str = "") -> Counter:
        return _CountingCounter(name, self.counters)

    def histogram(self, name: str, description: str = "", unit: str = ""):  # type: ignore[no-untyped-def]
        return NoOpHistogram()


class _CountingCounter(NoOpCounter):
    def __init__(self, name: str, store: dict[str, int]) -> None:
        self._name = name
        self._store = store

    def add(self, value: int = 1, attributes=None) -> None:  # type: ignore[no-untyped-def]
        self._store[self._name] = self._store.get(self._name, 0) + value


@pytest.fixture
def counting_metrics() -> Iterator[_CountingMetrics]:
    m = _CountingMetrics()
    set_metrics(m)
    yield m
    reset_metrics()


def test_emits_metrics_on_retry(counting_metrics: _CountingMetrics) -> None:
    calls = 0

    @retryable(max_attempts=3, **FAST)
    def f() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ValueError("x")
        return "ok"

    f()
    # 3번 시도 → 2번의 before_sleep → 2번 카운트
    assert counting_metrics.counters.get("etl_plugins.errors", 0) == 2


def test_no_metric_on_success(counting_metrics: _CountingMetrics) -> None:
    @retryable(max_attempts=3, **FAST)
    def f() -> str:
        return "ok"

    f()
    assert counting_metrics.counters.get("etl_plugins.errors", 0) == 0


def test_default_metrics_is_noop() -> None:
    # Ensure default backend is NoOp (안 깨졌는지)
    assert isinstance(NoOpMetrics(), Metrics)
