# Observability API

Three subsystems share the same shape: an interface, a NoOp default, a
process-wide accessor (`get_*` / `set_*`), and an OTel adapter that
plugs in via `configure_otel(...)`.

## Logging

::: etl_plugins.observability.logging.configure_logging

## Metrics

::: etl_plugins.observability.metrics.Metrics

::: etl_plugins.observability.metrics.Counter

::: etl_plugins.observability.metrics.Histogram

::: etl_plugins.observability.metrics.get_metrics

::: etl_plugins.observability.metrics.set_metrics

### Standard metric names

::: etl_plugins.observability.metrics
    options:
      members:
        - RECORDS_READ_TOTAL
        - RECORDS_WRITTEN_TOTAL
        - ERRORS_TOTAL
        - LAG_SECONDS
        - DURATION_SECONDS

## Tracing

::: etl_plugins.observability.tracing.Tracer

::: etl_plugins.observability.tracing.Span

::: etl_plugins.observability.tracing.get_tracer

::: etl_plugins.observability.tracing.set_tracer

## OpenTelemetry adapter

::: etl_plugins.observability.otel.configure_otel

::: etl_plugins.observability.otel.OTelHandle

::: etl_plugins.observability.otel.OTelMetrics

::: etl_plugins.observability.otel.OTelTracer
