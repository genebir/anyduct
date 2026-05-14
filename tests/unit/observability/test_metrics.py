"""Metrics 추상화 테스트."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from etl_plugins.observability.metrics import (
    DURATION_SECONDS,
    ERRORS_TOTAL,
    LAG_SECONDS,
    RECORDS_READ_TOTAL,
    RECORDS_WRITTEN_TOTAL,
    Counter,
    Histogram,
    Metrics,
    NoOpCounter,
    NoOpHistogram,
    NoOpMetrics,
    get_metrics,
    reset_metrics,
    set_metrics,
)


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    reset_metrics()


def test_abstract_classes_cannot_instantiate() -> None:
    for cls in [Metrics, Counter, Histogram]:
        with pytest.raises(TypeError):
            cls()  # type: ignore[abstract]


def test_noop_counter_add() -> None:
    c = NoOpCounter()
    c.add()
    c.add(5)
    c.add(10, {"connector": "postgres"})


def test_noop_histogram_record() -> None:
    h = NoOpHistogram()
    h.record(0.0)
    h.record(1.5, {"task": "load"})


def test_noop_metrics_factory() -> None:
    m = NoOpMetrics()
    assert isinstance(m.counter("x"), Counter)
    assert isinstance(m.histogram("y"), Histogram)


def test_default_backend_is_noop() -> None:
    assert isinstance(get_metrics(), NoOpMetrics)


def test_set_metrics_swaps_backend() -> None:
    class _Custom(Metrics):
        def counter(self, name: str, description: str = "", unit: str = "") -> Counter:
            return NoOpCounter()

        def histogram(self, name: str, description: str = "", unit: str = "") -> Histogram:
            return NoOpHistogram()

    custom = _Custom()
    set_metrics(custom)
    assert get_metrics() is custom


def test_reset_metrics_restores_noop() -> None:
    class _C(NoOpMetrics):
        pass

    set_metrics(_C())
    reset_metrics()
    assert type(get_metrics()) is NoOpMetrics


def test_standard_metric_names_are_namespaced() -> None:
    for name in [
        RECORDS_READ_TOTAL,
        RECORDS_WRITTEN_TOTAL,
        ERRORS_TOTAL,
        LAG_SECONDS,
        DURATION_SECONDS,
    ]:
        assert name.startswith("etl_plugins.")


def test_noop_metrics_returns_independent_instruments() -> None:
    # NoOp이긴 하지만 같은 객체를 reuse해서 메모리 leak 같은 의도치 않은 동작은 없어야
    m = NoOpMetrics()
    c1 = m.counter("a")
    c2 = m.counter("b")
    assert isinstance(c1, NoOpCounter)
    assert isinstance(c2, NoOpCounter)
