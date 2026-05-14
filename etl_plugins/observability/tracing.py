"""Tracing abstraction. SPEC.md §9.2.

Step 1.6 ships an interface + NoOp default. Real backends (OTel OTLP exporter)
will plug in via :func:`set_tracer` once the Step 6 strengthening lands.

Usage::

    with get_tracer().start_span("orders_to_dw.extract") as span:
        span.set_attribute("source", "pg_prod")
        ...

The span is a context manager — it ends on ``__exit__``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from types import TracebackType

AttributeValue = str | int | float | bool
Attributes = Mapping[str, AttributeValue]


class Span(ABC):
    """A single unit of traced work. Use as a context manager.

    ``__exit__`` always ends the span; ``record_exception`` may be called
    before exit to attach error information.
    """

    @abstractmethod
    def set_attribute(self, key: str, value: AttributeValue) -> None: ...

    @abstractmethod
    def record_exception(self, exc: BaseException) -> None: ...

    @abstractmethod
    def end(self) -> None: ...

    def __enter__(self) -> Span:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is not None:
            self.record_exception(exc)
        self.end()


class Tracer(ABC):
    """Factory for spans."""

    @abstractmethod
    def start_span(
        self,
        name: str,
        attributes: Attributes | None = None,
    ) -> Span: ...


# --- NoOp 구현 --------------------------------------------------------------


class NoOpSpan(Span):
    def set_attribute(self, key: str, value: AttributeValue) -> None:
        pass

    def record_exception(self, exc: BaseException) -> None:
        pass

    def end(self) -> None:
        pass


class NoOpTracer(Tracer):
    """Default backend — every span is a no-op."""

    def start_span(self, name: str, attributes: Attributes | None = None) -> Span:
        return NoOpSpan()


# --- 모듈 단위 싱글톤 -------------------------------------------------------

_tracer: Tracer = NoOpTracer()


def get_tracer() -> Tracer:
    """Return the active tracer (NoOp by default)."""
    return _tracer


def set_tracer(tracer: Tracer) -> None:
    """Replace the active tracer. Typically called once at startup."""
    global _tracer
    _tracer = tracer


def reset_tracer() -> None:
    """Restore the NoOp tracer. Useful in tests."""
    set_tracer(NoOpTracer())
