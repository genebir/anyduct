"""OpenTelemetry adapters for :mod:`etl_plugins.observability` (Step 6.2).

Bridges the core :class:`Metrics` / :class:`Tracer` interfaces onto an OTel
:class:`MeterProvider` / :class:`TracerProvider`. The SDK is **lazy
imported** so a plain ``pip install etl-plugins`` (without the
``[observability]`` extra) never pulls OpenTelemetry — only callers that
opt in via :func:`configure_otel` need the SDK installed.

Typical wiring at process startup::

    from etl_plugins.observability.otel import configure_otel

    configure_otel(
        service_name="etlx-worker",
        otlp_endpoint="http://otel-collector:4317",
    )

After that, every ``get_metrics().counter(...).add(...)`` and
``get_tracer().start_span(...)`` call ships data over OTLP/gRPC.

For tests, pass ``in_memory=True`` instead — it wires the InMemory
readers so assertions can introspect emitted records without standing up
a collector.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from etl_plugins.observability.metrics import (
    Attributes,
    Counter,
    Histogram,
    Metrics,
    set_metrics,
)
from etl_plugins.observability.tracing import (
    Span,
    Tracer,
    set_tracer,
)

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter as _OTelCounter
    from opentelemetry.metrics import Histogram as _OTelHistogram
    from opentelemetry.metrics import Meter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.trace import Span as _OTelSpan
    from opentelemetry.trace import Tracer as _OTelTracerType


# --- adapters -------------------------------------------------------------


class OTelCounter(Counter):
    """Wraps an OTel ``opentelemetry.metrics.Counter``."""

    def __init__(self, instrument: _OTelCounter) -> None:
        self._instrument = instrument

    def add(self, value: int = 1, attributes: Attributes | None = None) -> None:
        self._instrument.add(value, dict(attributes) if attributes else None)


class OTelHistogram(Histogram):
    """Wraps an OTel ``opentelemetry.metrics.Histogram``."""

    def __init__(self, instrument: _OTelHistogram) -> None:
        self._instrument = instrument

    def record(self, value: float, attributes: Attributes | None = None) -> None:
        self._instrument.record(value, dict(attributes) if attributes else None)


class OTelMetrics(Metrics):
    """:class:`Metrics` backed by an OTel ``Meter``.

    Counters and histograms are cached by name so repeated
    ``counter("etl_plugins.records.read")`` calls return the same
    instrument — OTel doesn't dedupe automatically and creating a new
    instrument per call leaks identifiers into the SDK's internal maps.
    """

    def __init__(self, meter: Meter) -> None:
        self._meter = meter
        self._counters: dict[str, OTelCounter] = {}
        self._histograms: dict[str, OTelHistogram] = {}

    def counter(self, name: str, description: str = "", unit: str = "") -> Counter:
        if name not in self._counters:
            self._counters[name] = OTelCounter(
                self._meter.create_counter(name, unit=unit, description=description)
            )
        return self._counters[name]

    def histogram(self, name: str, description: str = "", unit: str = "") -> Histogram:
        if name not in self._histograms:
            self._histograms[name] = OTelHistogram(
                self._meter.create_histogram(name, unit=unit, description=description)
            )
        return self._histograms[name]


class OTelSpan(Span):
    """Wraps an OTel ``trace.Span``. Lifetime: caller owns ``end()``."""

    def __init__(self, span: _OTelSpan) -> None:
        self._span = span

    def set_attribute(self, key: str, value: Any) -> None:
        self._span.set_attribute(key, value)

    def record_exception(self, exc: BaseException) -> None:
        self._span.record_exception(exc)

    def end(self) -> None:
        self._span.end()


class OTelTracer(Tracer):
    """:class:`Tracer` backed by an OTel ``trace.Tracer``."""

    def __init__(self, tracer: _OTelTracerType) -> None:
        self._tracer = tracer

    def start_span(self, name: str, attributes: Mapping[str, Any] | None = None) -> Span:
        otel_span = self._tracer.start_span(
            name, attributes=dict(attributes) if attributes else None
        )
        return OTelSpan(otel_span)


# --- configuration --------------------------------------------------------


class OTelHandle:
    """Return value of :func:`configure_otel`.

    Holds onto the providers (so callers can ``force_flush``/``shutdown``)
    and — for ``in_memory=True`` — the InMemory readers so tests can
    introspect emitted records without a collector.
    """

    def __init__(
        self,
        *,
        meter_provider: MeterProvider,
        tracer_provider: TracerProvider,
        metric_reader: InMemoryMetricReader | None = None,
        span_exporter: InMemorySpanExporter | None = None,
    ) -> None:
        self.meter_provider = meter_provider
        self.tracer_provider = tracer_provider
        self.metric_reader = metric_reader
        self.span_exporter = span_exporter

    def shutdown(self) -> None:
        """Flush and tear down both providers — call at process exit."""
        self.meter_provider.shutdown()
        self.tracer_provider.shutdown()


def configure_otel(
    *,
    service_name: str = "etl-plugins",
    otlp_endpoint: str | None = None,
    in_memory: bool = False,
    resource_attributes: Mapping[str, str] | None = None,
) -> OTelHandle:
    """Replace the active metrics + tracer backends with OTel adapters.

    Parameters
    ----------
    service_name
        Stamped onto every emitted metric / span as ``service.name``.
    otlp_endpoint
        OTLP/gRPC collector URL (e.g. ``http://otel-collector:4317``).
        Ignored when ``in_memory=True``.
    in_memory
        If True, use OTel's InMemory readers — no network. Suited for
        unit tests; the returned :class:`OTelHandle` exposes the readers
        so tests can inspect emitted records.
    resource_attributes
        Extra ``resource.attribute`` key/values stamped onto every emit.

    Returns
    -------
    OTelHandle
        Holds the providers + InMemory readers (when applicable). Call
        :meth:`OTelHandle.shutdown` to flush + tear down.
    """
    # Lazy imports: keep core install free of OTel.
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        InMemoryMetricReader,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    attrs: dict[str, str] = {"service.name": service_name}
    if resource_attributes:
        attrs.update(resource_attributes)
    resource = Resource.create(attrs)

    metric_reader_in_memory: InMemoryMetricReader | None = None
    span_exporter_in_memory: InMemorySpanExporter | None = None

    if in_memory:
        metric_reader_in_memory = InMemoryMetricReader()
        meter_provider = MeterProvider(
            metric_readers=[metric_reader_in_memory],
            resource=resource,
        )
        span_exporter_in_memory = InMemorySpanExporter()
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter_in_memory))
    else:
        if not otlp_endpoint:
            raise ValueError("configure_otel requires otlp_endpoint when in_memory=False")
        # Lazy-import the OTLP exporters; they're a separate package
        # (``opentelemetry-exporter-otlp-proto-grpc``).
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True)
        periodic_reader = PeriodicExportingMetricReader(metric_exporter)
        meter_provider = MeterProvider(metric_readers=[periodic_reader], resource=resource)

        span_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))

    meter = meter_provider.get_meter("etl_plugins")
    tracer = tracer_provider.get_tracer("etl_plugins")

    set_metrics(OTelMetrics(meter))
    set_tracer(OTelTracer(tracer))

    return OTelHandle(
        meter_provider=meter_provider,
        tracer_provider=tracer_provider,
        metric_reader=metric_reader_in_memory,
        span_exporter=span_exporter_in_memory,
    )


__all__ = [
    "OTelCounter",
    "OTelHandle",
    "OTelHistogram",
    "OTelMetrics",
    "OTelSpan",
    "OTelTracer",
    "configure_otel",
]
