"""Unit tests for the OpenTelemetry backend (Step 6.2).

Uses OTel's InMemory readers via ``configure_otel(in_memory=True)`` so
no collector / network is needed. Each test sets up its own handle and
tears it down — there's a single module-level mutable backend in
:mod:`etl_plugins.observability`, so leaking state between tests would
poison everything that touches metrics / tracing.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from etl_plugins.observability.metrics import (
    NoOpMetrics,
    get_metrics,
    reset_metrics,
)
from etl_plugins.observability.otel import (
    OTelCounter,
    OTelHandle,
    OTelHistogram,
    OTelMetrics,
    OTelSpan,
    OTelTracer,
    configure_otel,
)
from etl_plugins.observability.tracing import (
    NoOpTracer,
    get_tracer,
    reset_tracer,
)


@pytest.fixture
def otel() -> Iterator[OTelHandle]:
    """A per-test InMemory OTel backend. Tear down + reset to NoOp after."""
    handle = configure_otel(service_name="etlx-unit-test", in_memory=True)
    yield handle
    handle.shutdown()
    reset_metrics()
    reset_tracer()


# --- backend swapping ------------------------------------------------------


def test_configure_otel_swaps_global_backends(otel: OTelHandle) -> None:
    assert isinstance(get_metrics(), OTelMetrics)
    assert isinstance(get_tracer(), OTelTracer)


def test_configure_otel_in_memory_returns_readers() -> None:
    handle = configure_otel(in_memory=True)
    try:
        assert handle.metric_reader is not None
        assert handle.span_exporter is not None
    finally:
        handle.shutdown()
        reset_metrics()
        reset_tracer()


def test_configure_otel_rejects_no_endpoint_when_not_in_memory() -> None:
    with pytest.raises(ValueError, match="otlp_endpoint"):
        configure_otel(in_memory=False)


def test_reset_restores_noop_backends(otel: OTelHandle) -> None:
    # Sanity: reset_metrics / reset_tracer should drop OTel and restore NoOp.
    reset_metrics()
    reset_tracer()
    assert isinstance(get_metrics(), NoOpMetrics)
    assert isinstance(get_tracer(), NoOpTracer)


# --- counters --------------------------------------------------------------


def test_counter_add_emits_to_otel_reader(otel: OTelHandle) -> None:
    counter = get_metrics().counter("etlx.records.read", description="rows", unit="1")
    counter.add(3, {"pipeline": "p1"})
    counter.add(2, {"pipeline": "p1"})

    assert otel.metric_reader is not None
    data = otel.metric_reader.get_metrics_data()
    metrics_named = _flatten_metrics(data)
    assert "etlx.records.read" in metrics_named
    total = sum(
        point.value
        for point in metrics_named["etlx.records.read"]
        if dict(point.attributes) == {"pipeline": "p1"}
    )
    assert total == 5


def test_counter_caches_instrument_by_name(otel: OTelHandle) -> None:
    a = get_metrics().counter("etlx.errors")
    b = get_metrics().counter("etlx.errors")
    assert a is b  # same OTelCounter wrapper


def test_counter_accepts_no_attributes(otel: OTelHandle) -> None:
    counter = get_metrics().counter("etlx.no-attrs")
    counter.add(7)
    data = otel.metric_reader.get_metrics_data() if otel.metric_reader else None
    assert data is not None
    metrics_named = _flatten_metrics(data)
    assert "etlx.no-attrs" in metrics_named
    points = metrics_named["etlx.no-attrs"]
    assert sum(p.value for p in points) == 7


# --- histograms ------------------------------------------------------------


def test_histogram_record_emits_to_otel_reader(otel: OTelHandle) -> None:
    hist = get_metrics().histogram("etlx.duration", unit="s")
    for v in (0.1, 0.2, 0.3):
        hist.record(v, {"task": "extract"})

    assert otel.metric_reader is not None
    data = otel.metric_reader.get_metrics_data()
    metrics_named = _flatten_metrics(data)
    assert "etlx.duration" in metrics_named
    # OTel histogram data points carry ``count`` and ``sum``.
    points = metrics_named["etlx.duration"]
    matching = [p for p in points if dict(p.attributes) == {"task": "extract"}]
    assert matching
    assert matching[0].count == 3
    assert abs(matching[0].sum - 0.6) < 1e-9


def test_histogram_caches_instrument_by_name(otel: OTelHandle) -> None:
    a = get_metrics().histogram("etlx.lat")
    b = get_metrics().histogram("etlx.lat")
    assert a is b


# --- spans -----------------------------------------------------------------


def test_span_lifecycle_records_to_in_memory_exporter(otel: OTelHandle) -> None:
    tracer = get_tracer()
    with tracer.start_span("orders.extract", attributes={"source": "pg"}) as span:
        span.set_attribute("rows", 42)

    assert otel.span_exporter is not None
    # BatchSpanProcessor flushes on shutdown — force-flush now so the
    # InMemory exporter has the span ready.
    otel.tracer_provider.force_flush()
    spans = otel.span_exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert "orders.extract" in names
    rec = next(s for s in spans if s.name == "orders.extract")
    assert rec.attributes is not None
    assert rec.attributes["source"] == "pg"
    assert rec.attributes["rows"] == 42


def test_span_records_exception_on_exit(otel: OTelHandle) -> None:
    tracer = get_tracer()
    with pytest.raises(ValueError, match="boom"), tracer.start_span("op"):
        raise ValueError("boom")

    assert otel.span_exporter is not None
    otel.tracer_provider.force_flush()
    spans = otel.span_exporter.get_finished_spans()
    op = next(s for s in spans if s.name == "op")
    # Exception is attached as an event on the span.
    assert any(ev.name == "exception" for ev in op.events)


# --- adapter wrappers ------------------------------------------------------


def test_otel_counter_wrapper_is_a_counter(otel: OTelHandle) -> None:
    c = get_metrics().counter("etlx.test")
    assert isinstance(c, OTelCounter)


def test_otel_histogram_wrapper_is_a_histogram(otel: OTelHandle) -> None:
    h = get_metrics().histogram("etlx.test-hist")
    assert isinstance(h, OTelHistogram)


def test_otel_span_wrapper_is_a_span(otel: OTelHandle) -> None:
    tracer = get_tracer()
    span = tracer.start_span("x")
    try:
        assert isinstance(span, OTelSpan)
    finally:
        span.end()


# --- helpers ---------------------------------------------------------------


def _flatten_metrics(data: object) -> dict[str, list[object]]:
    """Pull data points out of OTel's MetricsData → ScopeMetrics → Metric tree."""
    out: dict[str, list[object]] = {}
    # ``data`` is a MetricsData with resource_metrics: list[ResourceMetrics].
    for rm in getattr(data, "resource_metrics", []):
        for sm in getattr(rm, "scope_metrics", []):
            for metric in getattr(sm, "metrics", []):
                points = list(getattr(metric.data, "data_points", []))
                out.setdefault(metric.name, []).extend(points)
    return out
