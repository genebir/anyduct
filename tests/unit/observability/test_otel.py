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
    handle = configure_otel(service_name="anyduct-unit-test", in_memory=True)
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


def test_configure_otel_rejects_no_backend() -> None:
    with pytest.raises(ValueError, match="at least one of"):
        configure_otel(in_memory=False)


def test_reset_restores_noop_backends(otel: OTelHandle) -> None:
    # Sanity: reset_metrics / reset_tracer should drop OTel and restore NoOp.
    reset_metrics()
    reset_tracer()
    assert isinstance(get_metrics(), NoOpMetrics)
    assert isinstance(get_tracer(), NoOpTracer)


# --- counters --------------------------------------------------------------


def test_counter_add_emits_to_otel_reader(otel: OTelHandle) -> None:
    counter = get_metrics().counter("anyduct.records.read", description="rows", unit="1")
    counter.add(3, {"pipeline": "p1"})
    counter.add(2, {"pipeline": "p1"})

    assert otel.metric_reader is not None
    data = otel.metric_reader.get_metrics_data()
    metrics_named = _flatten_metrics(data)
    assert "anyduct.records.read" in metrics_named
    total = sum(
        point.value
        for point in metrics_named["anyduct.records.read"]
        if dict(point.attributes) == {"pipeline": "p1"}
    )
    assert total == 5


def test_counter_caches_instrument_by_name(otel: OTelHandle) -> None:
    a = get_metrics().counter("anyduct.errors")
    b = get_metrics().counter("anyduct.errors")
    assert a is b  # same OTelCounter wrapper


def test_counter_accepts_no_attributes(otel: OTelHandle) -> None:
    counter = get_metrics().counter("anyduct.no-attrs")
    counter.add(7)
    data = otel.metric_reader.get_metrics_data() if otel.metric_reader else None
    assert data is not None
    metrics_named = _flatten_metrics(data)
    assert "anyduct.no-attrs" in metrics_named
    points = metrics_named["anyduct.no-attrs"]
    assert sum(p.value for p in points) == 7


# --- histograms ------------------------------------------------------------


def test_histogram_record_emits_to_otel_reader(otel: OTelHandle) -> None:
    hist = get_metrics().histogram("anyduct.duration", unit="s")
    for v in (0.1, 0.2, 0.3):
        hist.record(v, {"task": "extract"})

    assert otel.metric_reader is not None
    data = otel.metric_reader.get_metrics_data()
    metrics_named = _flatten_metrics(data)
    assert "anyduct.duration" in metrics_named
    # OTel histogram data points carry ``count`` and ``sum``.
    points = metrics_named["anyduct.duration"]
    matching = [p for p in points if dict(p.attributes) == {"task": "extract"}]
    assert matching
    assert matching[0].count == 3
    assert abs(matching[0].sum - 0.6) < 1e-9


def test_histogram_caches_instrument_by_name(otel: OTelHandle) -> None:
    a = get_metrics().histogram("anyduct.lat")
    b = get_metrics().histogram("anyduct.lat")
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
    c = get_metrics().counter("anyduct.test")
    assert isinstance(c, OTelCounter)


def test_otel_histogram_wrapper_is_a_histogram(otel: OTelHandle) -> None:
    h = get_metrics().histogram("anyduct.test-hist")
    assert isinstance(h, OTelHistogram)


def test_otel_span_wrapper_is_a_span(otel: OTelHandle) -> None:
    tracer = get_tracer()
    span = tracer.start_span("x")
    try:
        assert isinstance(span, OTelSpan)
    finally:
        span.end()


# --- Pipeline span emit (Step 6.2 follow-up) -------------------------------


def test_pipeline_run_emits_run_and_task_spans(otel: OTelHandle) -> None:
    """``Pipeline.run`` opens a ``pipeline.run`` span with one nested
    ``pipeline.task`` per task, carrying the standard attrs."""
    from etl_plugins.core.pipeline import Pipeline, Task
    from etl_plugins.core.record import Record
    from tests.fixtures.connectors import InMemoryBatchSink, InMemoryBatchSource

    source = InMemoryBatchSource([Record(data={"id": 1}), Record(data={"id": 2})])
    sink = InMemoryBatchSink()

    Pipeline("orders-sync").add(
        Task.extract("src", "q", name="extract-users").load("snk", table="users")
    ).run(connectors={"src": source, "snk": sink})

    assert otel.span_exporter is not None
    otel.tracer_provider.force_flush()
    spans_by_name = {s.name: s for s in otel.span_exporter.get_finished_spans()}
    assert "pipeline.run" in spans_by_name
    assert "pipeline.task" in spans_by_name

    run_span = spans_by_name["pipeline.run"]
    assert run_span.attributes is not None
    assert run_span.attributes["pipeline"] == "orders-sync"
    assert run_span.attributes["mode"] == "batch"
    assert run_span.attributes["success"] is True
    assert run_span.attributes["records_read_total"] == 2
    assert run_span.attributes["records_written_total"] == 2

    task_span = spans_by_name["pipeline.task"]
    assert task_span.attributes is not None
    assert task_span.attributes["pipeline"] == "orders-sync"
    assert task_span.attributes["task"] == "extract-users"
    assert task_span.attributes["source"] == "src"
    assert task_span.attributes["sink"] == "snk"
    assert task_span.attributes["records_read"] == 2
    assert task_span.attributes["records_written"] == 2


def test_pipeline_run_failure_records_exception_on_run_span(
    otel: OTelHandle,
) -> None:
    """Transform errors propagate and surface as an exception event on the
    ``pipeline.run`` span; ``success`` flips to False."""
    from etl_plugins.core.exceptions import TransformError
    from etl_plugins.core.pipeline import Pipeline, Task
    from etl_plugins.core.record import Record
    from tests.fixtures.connectors import InMemoryBatchSink, InMemoryBatchSource

    def boom(_: Record) -> Record:
        raise ValueError("kaboom")

    source = InMemoryBatchSource([Record(data={"id": 1})])
    sink = InMemoryBatchSink()

    with pytest.raises(TransformError):
        Pipeline("broken").add(Task.extract("src", "q").transform(boom).load("snk", table="x")).run(
            connectors={"src": source, "snk": sink}
        )

    assert otel.span_exporter is not None
    otel.tracer_provider.force_flush()
    spans_by_name = {s.name: s for s in otel.span_exporter.get_finished_spans()}
    run_span = spans_by_name["pipeline.run"]
    assert run_span.attributes is not None
    assert run_span.attributes["success"] is False
    assert any(ev.name == "exception" for ev in run_span.events)
    # Task span also carries the same exception event.
    task_span = spans_by_name["pipeline.task"]
    assert any(ev.name == "exception" for ev in task_span.events)


def test_pipeline_run_span_carries_cursor_bounds_when_cursored(
    otel: OTelHandle,
) -> None:
    from etl_plugins.core.pipeline import Pipeline, Task
    from etl_plugins.core.record import Record
    from tests.fixtures.connectors import InMemoryBatchSink, InMemoryBatchSource

    source = InMemoryBatchSource(
        [Record(data={"id": 1}), Record(data={"id": 2}), Record(data={"id": 3})]
    )
    sink = InMemoryBatchSink()

    Pipeline("incr").add(Task.extract("src", "q", cursor_column="id").load("snk", table="x")).run(
        connectors={"src": source, "snk": sink}, cursor_from=1, cursor_to=2
    )

    assert otel.span_exporter is not None
    otel.tracer_provider.force_flush()
    run_span = next(s for s in otel.span_exporter.get_finished_spans() if s.name == "pipeline.run")
    assert run_span.attributes is not None
    assert run_span.attributes["cursor_from"] == "1"
    assert run_span.attributes["cursor_to"] == "2"


def test_pipeline_run_with_no_otel_backend_is_a_noop() -> None:
    """When the tracer is the default NoOp (no configure_otel call), Pipeline.run
    must not raise — i.e. the NoOp span path stays exercised."""
    from etl_plugins.core.pipeline import Pipeline, Task
    from etl_plugins.core.record import Record
    from tests.fixtures.connectors import InMemoryBatchSink, InMemoryBatchSource

    # Ensure we're on NoOp (other tests with `otel` fixture clean up; this
    # one runs without the fixture).
    reset_metrics()
    reset_tracer()
    source = InMemoryBatchSource([Record(data={"id": 1})])
    sink = InMemoryBatchSink()
    result = (
        Pipeline("p")
        .add(Task.extract("src", "q").load("snk", table="t"))
        .run(connectors={"src": source, "snk": sink})
    )
    assert result.success is True


# --- Prometheus exporter (Step 6.2) ---------------------------------------


def _free_port() -> int:
    """Pick a free TCP port for the in-process Prometheus scrape server.

    Closes the socket immediately so the OS can re-bind it from another
    thread — there's a brief TOCTOU window but it's been good enough for
    test infra everywhere I've seen this pattern."""
    import socket

    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _scrape(port: int) -> str:
    """GET http://127.0.0.1:<port>/metrics and return the body."""
    import urllib.request

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2.0) as resp:
        return resp.read().decode("utf-8")


@pytest.fixture
def otel_prom() -> Iterator[tuple[OTelHandle, int]]:
    """Configure OTel with an in-process Prometheus scrape server on a
    free port. Tear down everything after the test — the scrape server
    is a daemon thread but explicit shutdown avoids port leaks across
    rapid-fire test runs."""
    port = _free_port()
    handle = configure_otel(
        service_name="anyduct-prom-test",
        prometheus_port=port,
        prometheus_addr="127.0.0.1",
    )
    yield handle, port
    handle.shutdown()
    reset_metrics()
    reset_tracer()


def test_configure_otel_starts_prometheus_scrape_server(
    otel_prom: tuple[OTelHandle, int],
) -> None:
    handle, port = otel_prom
    assert handle.prometheus_reader is not None
    assert handle.prometheus_server is not None
    # Endpoint responds; basic OpenMetrics-style headers/body.
    body = _scrape(port)
    # Either an empty registry returns "" or HELP/TYPE comments. Both fine.
    assert isinstance(body, str)


def test_prometheus_scrape_exposes_emitted_counter(
    otel_prom: tuple[OTelHandle, int],
) -> None:
    handle, port = otel_prom
    counter = get_metrics().counter("anyduct.records.read", description="rows", unit="1")
    counter.add(4, {"pipeline": "p1"})
    counter.add(7, {"pipeline": "p1"})
    # Force-collect so the reader pushes into prometheus_client REGISTRY.
    assert handle.prometheus_reader is not None
    handle.prometheus_reader.collect()

    body = _scrape(port)
    # Prometheus normalizes "." to "_". A counter named anyduct.records.read
    # becomes anyduct_records_read_total (the exporter appends `_total` for
    # OTel monotonic counters per OpenMetrics convention).
    assert "anyduct_records_read" in body
    # The 11 records (4 + 7) should sum into a single labeled series.
    assert 'pipeline="p1"' in body
    assert "11" in body  # sum across both adds


def test_prometheus_only_no_otlp_no_in_memory_works() -> None:
    """Prometheus alone (no OTLP, no InMemory) is a valid configuration."""
    port = _free_port()
    handle = configure_otel(prometheus_port=port, prometheus_addr="127.0.0.1")
    try:
        assert handle.prometheus_reader is not None
        assert handle.metric_reader is None  # no InMemory
        assert handle.span_exporter is None  # no InMemory
        # Tracer is still an OTelTracer but with no exporter — spans are
        # created and dropped, which is fine for prometheus-only setups.
        assert isinstance(get_tracer(), OTelTracer)
    finally:
        handle.shutdown()
        reset_metrics()
        reset_tracer()


def test_prometheus_coexists_with_in_memory() -> None:
    """Setting both prometheus_port and in_memory=True attaches both
    readers to the same MeterProvider — used in tests that want to
    inspect emits in-process *and* verify the Prometheus surface."""
    port = _free_port()
    handle = configure_otel(in_memory=True, prometheus_port=port, prometheus_addr="127.0.0.1")
    try:
        assert handle.metric_reader is not None
        assert handle.prometheus_reader is not None

        counter = get_metrics().counter("anyduct.coexist")
        counter.add(3)

        # Both surfaces see the emit.
        in_mem = _flatten_metrics(handle.metric_reader.get_metrics_data())
        assert "anyduct.coexist" in in_mem
        handle.prometheus_reader.collect()
        body = _scrape(port)
        assert "anyduct_coexist" in body
    finally:
        handle.shutdown()
        reset_metrics()
        reset_tracer()


def test_prometheus_shutdown_closes_server() -> None:
    """OTelHandle.shutdown() tears down the WSGI server cleanly; the
    port is reusable immediately after."""
    import urllib.error

    port = _free_port()
    handle = configure_otel(prometheus_port=port, prometheus_addr="127.0.0.1")
    # Verify it's up.
    _scrape(port)
    handle.shutdown()
    reset_metrics()
    reset_tracer()
    # After shutdown the server should refuse / fail to connect.
    with pytest.raises((urllib.error.URLError, ConnectionRefusedError, OSError)):
        _scrape(port)


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
