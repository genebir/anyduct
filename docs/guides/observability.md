# Observability (OpenTelemetry)

`etl-plugins` ships three observability hooks behind small interfaces:

| Surface | Module | Default |
|---------|--------|---------|
| Structured logging | `etl_plugins.observability.logging` | structlog → stdout |
| Metrics | `etl_plugins.observability.metrics` | NoOp |
| Tracing | `etl_plugins.observability.tracing` | NoOp |

Without the `[observability]` extra, calls to `get_metrics()` /
`get_tracer()` are free — they hit the NoOp implementations and return
immediately. Opt in by installing the extra and calling
`configure_otel(...)` once at process startup.

## Wiring OTLP/gRPC

```python
from etl_plugins.observability.otel import configure_otel

handle = configure_otel(
    service_name="etlx-worker",
    otlp_endpoint="http://otel-collector:4317",
    resource_attributes={"deployment.environment": "prod"},
)

# ... run pipelines ...

handle.shutdown()  # flush + tear down both providers at process exit
```

`configure_otel` swaps the active backends globally — every subsequent
`get_metrics().counter(...).add(...)` and `get_tracer().start_span(...)`
emit lands on the OTel SDK, which batches + ships over OTLP/gRPC.

## What the runtime emits for free

For every pipeline run:

| Signal | Name / span | Attributes |
|--------|-------------|------------|
| Counter | `etl_plugins.records.read` | `pipeline`, `task`, `source` |
| Counter | `etl_plugins.records.written` | `pipeline`, `task`, `sink` |
| Counter | `etl_plugins.errors` | `pipeline`, `task`, `phase` |
| Histogram | `etl_plugins.duration.seconds` | `pipeline`, `task` |
| Span (root) | `pipeline.run` | `pipeline`, `mode`, `run_id`, `success`, totals, `cursor_from`/`cursor_to` |
| Span (child) | `pipeline.task` | `pipeline`, `task`, `source`, `sink`, `records_read`, `records_written` |

The root span wraps every task span, so a single trace shows the entire
pipeline. Failures are recorded via `span.record_exception(...)` and
`span.set_status(StatusCode.ERROR, ...)`.

## In-memory mode (for tests)

```python
from etl_plugins.observability.otel import configure_otel

handle = configure_otel(service_name="t", in_memory=True)

# ... run code under test ...

metrics = handle.metric_reader.get_metrics_data()
spans = handle.span_exporter.get_finished_spans()
```

`in_memory=True` wires `InMemoryMetricReader` + `InMemorySpanExporter`
instead of OTLP. Useful for unit tests that need to assert
"this span was emitted with these attributes" without standing up a
collector.

## Custom counters / spans

The pipeline runtime emits the standard metrics for you. To add your
own, ask the global factory:

```python
from etl_plugins.observability import get_metrics, get_tracer

dlq_counter = get_metrics().counter(
    "myapp.dlq.routed",
    description="Records routed to DLQ",
    unit="1",
)
dlq_counter.add(1, {"reason": "schema_mismatch"})

with get_tracer().start_span("myapp.expensive_lookup", attributes={"id": pk}):
    do_lookup(pk)
```

Same code works under NoOp (default) and OTel (after `configure_otel`)
— no flag-gating needed at the call site.

## Logging

`configure_logging(level, *, fmt, extra_processors=None)` returns a
structlog logger pre-wired with JSON output (production) or console
output (development). The worker pre-installs a processor that captures
log events for the active run id so the API's
`/runs/{id}/logs/stream` SSE endpoint can tail them.

```python
from etl_plugins.observability import configure_logging

log = configure_logging("INFO", fmt="json")
log.info("pipeline.starting", pipeline="orders", mode="batch")
```

Secrets are masked by a structlog processor before they reach stdout.

## Where to next

* [Cursors & incremental sync](cursors.md) — watermarks emitted as span
  attributes during cursored runs.
* [Reference: Observability API](../reference/observability.md) — exact
  signatures for every public symbol.
