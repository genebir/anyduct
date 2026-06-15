# Pipelines & tasks

A `Pipeline` is a named sequence of `Task` objects sharing a single
`mode` (`batch` or `stream`). Each task is one extract → transform\* →
load slice.

## Building from code

```python
from etl_plugins import Pipeline, Task

pipeline = Pipeline("orders-nightly").add(
    Task.extract("pg", "SELECT id, total FROM orders")
        .transform(lambda r: r.model_copy(update={"data": {**r.data, "total": float(r.data["total"])}}))
        .load("warehouse", table="orders", mode="append")
)
```

The fluent helpers map onto `Task` fields:

| Helper | Sets |
|--------|------|
| `Task.extract(source, query, *, name, cursor_column, **opts)` | `source`, `query`, `name`, `cursor_column`, `source_options` |
| `.transform(fn)` | appends to `transforms` |
| `.load(sink, *, table, mode, key_columns, **opts)` | `sink`, `sink_table`, `sink_mode`, `sink_key_columns`, `sink_options` |

`Pipeline.add(task)` chains.

## Running

```python
with source_conn, sink_conn:
    result = pipeline.run(connectors={"pg": source_conn, "warehouse": sink_conn})
```

`run()` returns a `RunResult` with:

* `success: bool` — `True` only if every task completed.
* `records_read` / `records_written` — totals across all tasks.
* `duration_seconds` — wall time.
* `error: BaseException | None` — populated on failure.
* `new_cursor: CursorValue | None` — max cursor value seen across all
  tasks during a cursored run.

## Stream mode

Set `Pipeline(mode="stream", ...)` and call `arun_stream()` instead of
`run()`. Stop conditions:

```python
await pipeline.arun_stream(
    connectors=...,
    stop_after_records=10_000,
    stop_after_seconds=60.0,
)
```

Either condition triggers a graceful shutdown — the source `commit()`s
its offsets (Kafka), the sink flushes, then the function returns.

## Retry + DLQ

```python
from etl_plugins.config.models import RetryConfig, DlqConfig

pipeline = Pipeline(
    "orders",
    retry=RetryConfig(max_attempts=3, backoff="exponential", initial_delay_seconds=2),
    dlq=DlqConfig(connection="dlq-bucket", topic="bad-orders", mode="append"),
)
```

* `retry` — wraps each task in tenacity's `Retrying`. Exhausted retries
  propagate the last exception.
* `dlq` — when a transform raises `TransformError`, the offending
  `Record` is best-effort written to the DLQ sink and the pipeline
  continues. A counter `etl_plugins.errors{phase=transform, routed=dlq}`
  is incremented on every routed record.

## Hooks

```python
def on_post_run(ctx, result):
    print(f"pipeline {ctx.pipeline_name} produced {result.records_written} rows")

pipeline.on("post_run", on_post_run)
```

Supported events: `pre_run`, `on_task_start`, `on_task_end`,
`on_error`, `post_run`. Hooks are called synchronously, in registration
order, after the runtime emits its standard metrics.

## Data paths — pushdown / Arrow / records (ADR-0093/0094)

The runtime picks the cheapest data path per task, automatically, in
this order:

1. **SQL pushdown (no data movement)** — when the source and sink are
   the *same connection* and the task is a plain `source → sink` append
   with no transforms, the whole task collapses to one
   `INSERT INTO <table> <select>` executed inside the database. Also
   available explicitly as an ELT transform:
   `{type: sql, pushdown: true}` runs your SQL *in the warehouse*
   (dbt-style) instead of locally. Ineligible configs fall back
   silently — the `sql_pushdown_ineligible` lint explains why in
   dry-run.
2. **Arrow bulk interchange** — when source and sink both implement the
   Arrow fast-path and the task has no transforms, a single sink, and
   `append`/`overwrite` mode, data moves as columnar
   `pyarrow.RecordBatch`es, skipping the per-row `Record` layer
   entirely (PG→PG 500k rows measured 3.4× faster). Supported
   connectors: **postgres** (COPY-based), **mysql**, **vertica**,
   **mssql** (server-side cursor + columnar assembly). `upsert`, `when`
   routing, fan-out, and transforms route back to the Record path.
3. **Records** — the default row-at-a-time path. Everything supports
   it; transforms, DLQ routing, and `when` predicates live here.

Builder (graph-shape) pipelines get the same treatment: a trivial
`source → sink` two-node chain is routed through the linear fast-path
helpers.

Which path a run actually took is recorded per task in
`RunResult.data_paths` (`pushdown` / `arrow` / `records` / `graph`) and
shown as a chip on the run detail page — no guessing.

If `pyarrow` isn't installed the Arrow path is skipped (clean fallback,
no error). Install any extra that pulls it (e.g. `[duckdb]`, `[s3]`).

## YAML alternative

Anything you can build in code maps to YAML — the CLI loads it
directly:

```yaml
name: orders
mode: batch
tasks:
  - source: pg
    query: "SELECT * FROM orders"
    sink: warehouse
    sink_options: {table: orders, mode: upsert, key_columns: [id]}
retry:
  max_attempts: 3
  backoff: exponential
```

Then:

```bash
uv run anyduct run configs/pipelines/orders.yaml --connections configs/connections.yaml
```

See [Quickstart](../getting-started/quickstart.md) for the full
end-to-end flow.
