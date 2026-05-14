"""Tracing 추상화 테스트."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from etl_plugins.observability.tracing import (
    NoOpSpan,
    NoOpTracer,
    Span,
    Tracer,
    get_tracer,
    reset_tracer,
    set_tracer,
)


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    reset_tracer()


def test_abstract_classes_cannot_instantiate() -> None:
    for cls in [Tracer, Span]:
        with pytest.raises(TypeError):
            cls()  # type: ignore[abstract]


def test_noop_span_is_context_manager() -> None:
    span: Span = NoOpSpan()
    with span as s:
        assert s is span
        s.set_attribute("key", "value")
        s.set_attribute("count", 42)


def test_noop_span_records_exception_on_exit() -> None:
    span = NoOpSpan()
    with pytest.raises(RuntimeError), span:
        raise RuntimeError("boom")


def test_noop_span_end_is_idempotent() -> None:
    s = NoOpSpan()
    s.end()
    s.end()  # 추가 호출도 안전


def test_noop_tracer_creates_span() -> None:
    t = NoOpTracer()
    span = t.start_span("task", {"connector": "postgres"})
    assert isinstance(span, NoOpSpan)


def test_default_tracer_is_noop() -> None:
    assert isinstance(get_tracer(), NoOpTracer)


def test_set_and_reset_tracer() -> None:
    class _Custom(Tracer):
        def start_span(self, name: str, attributes=None) -> Span:
            return NoOpSpan()

    custom = _Custom()
    set_tracer(custom)
    assert get_tracer() is custom
    reset_tracer()
    assert type(get_tracer()) is NoOpTracer


def test_span_records_exception_when_used_as_cm() -> None:
    """예외가 발생하면 record_exception이 호출되어야 한다."""
    captured: list[BaseException] = []

    class _SpyingSpan(NoOpSpan):
        def record_exception(self, exc: BaseException) -> None:
            captured.append(exc)

    with pytest.raises(ValueError), _SpyingSpan():
        raise ValueError("oops")

    assert len(captured) == 1
    assert isinstance(captured[0], ValueError)


def test_span_no_exception_no_record() -> None:
    captured: list[BaseException] = []

    class _SpyingSpan(NoOpSpan):
        def record_exception(self, exc: BaseException) -> None:
            captured.append(exc)

    with _SpyingSpan():
        pass

    assert captured == []
