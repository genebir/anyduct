"""Metrics abstraction. SPEC.md §9.2.

Step 1.6 ships an interface + NoOp default. A real backend (OpenTelemetry,
Prometheus) plugs in via :func:`set_metrics`. Pipeline / connector code calls
``get_metrics().counter(...).add(1, attrs)`` without knowing which backend
is active.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

# --- 표준 메트릭 이름 (SPEC.md §9.2) ---------------------------------------

RECORDS_READ_TOTAL = "etl_plugins.records.read"
RECORDS_WRITTEN_TOTAL = "etl_plugins.records.written"
ERRORS_TOTAL = "etl_plugins.errors"
LAG_SECONDS = "etl_plugins.lag.seconds"
DURATION_SECONDS = "etl_plugins.duration.seconds"


# --- 인터페이스 -------------------------------------------------------------


AttributeValue = str | int | float | bool
Attributes = Mapping[str, AttributeValue]


class Counter(ABC):
    """Monotonically increasing counter."""

    @abstractmethod
    def add(self, value: int = 1, attributes: Attributes | None = None) -> None: ...


class Histogram(ABC):
    """Distribution of values (latencies, sizes, ...)."""

    @abstractmethod
    def record(self, value: float, attributes: Attributes | None = None) -> None: ...


class Metrics(ABC):
    """Factory for typed metric instruments."""

    @abstractmethod
    def counter(self, name: str, description: str = "", unit: str = "") -> Counter: ...

    @abstractmethod
    def histogram(self, name: str, description: str = "", unit: str = "") -> Histogram: ...


# --- NoOp 구현 --------------------------------------------------------------


class NoOpCounter(Counter):
    def add(self, value: int = 1, attributes: Attributes | None = None) -> None:
        pass


class NoOpHistogram(Histogram):
    def record(self, value: float, attributes: Attributes | None = None) -> None:
        pass


class NoOpMetrics(Metrics):
    """Default backend — discards every value silently."""

    def counter(self, name: str, description: str = "", unit: str = "") -> Counter:
        return NoOpCounter()

    def histogram(self, name: str, description: str = "", unit: str = "") -> Histogram:
        return NoOpHistogram()


# --- 모듈 단위 싱글톤 -------------------------------------------------------

_metrics: Metrics = NoOpMetrics()


def get_metrics() -> Metrics:
    """Return the active metrics backend (NoOp by default)."""
    return _metrics


def set_metrics(backend: Metrics) -> None:
    """Replace the active metrics backend. Typically called once at startup."""
    global _metrics
    _metrics = backend


def reset_metrics() -> None:
    """Restore the NoOp backend. Useful in tests."""
    set_metrics(NoOpMetrics())
