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

## Per-task retry & timeout (자유도 2단계)

Pipeline-level `retry` is the default, but any individual task can
override it and/or set an execution timeout — the Airflow
`retries` / `retry_delay` / `execution_timeout` shape, applied per task:

```yaml
name: orders
mode: batch
task_timeout_seconds: 600        # default budget for every task
tasks:
  - name: extract
    source: pg
    query: "SELECT * FROM orders"
    sink: warehouse
    sink_options: {table: orders, mode: append}
    timeout_seconds: 120         # this task gets a tighter budget
    retry:                       # overrides the pipeline default
      max_attempts: 5
      backoff: exponential
      initial_delay_seconds: 2
```

* `task.retry` wins over the pipeline `retry` for that task; tasks
  without one fall back to the pipeline default (or no retry).
* `task.timeout_seconds` wins over the pipeline `task_timeout_seconds`;
  `None`/absent means no timeout. The deadline is **cooperative** — for an
  `etl` task it's checked at each record boundary (a chunked read/transform
  stream is failed once it runs long); for a `sql` / `proc_call` operator it's
  checked **between statements** (a multi-statement step stops before the next
  statement once the budget is blown). Either way it raises `TaskTimeoutError`.
  It does *not* interrupt a single blocking driver call mid-fetch / mid-statement
  (Python can't kill a blocked thread). `TaskTimeoutError` is a `TaskError`
  subclass, so a configured retry will retry a slow task, each attempt getting a
  fresh window.

### Per-step monitoring in the run DAG

For a task-DAG run, the run-detail **DAG view** surfaces each step's outcome so
an operator can diagnose without opening logs:

* **status** — `succeeded` / `failed`, plus `skipped` (a `branch` deselected it)
  vs `upstream failed` (an upstream step broke) shown distinctly.
* **`↻ ×N`** — the step retried `N` times before settling (a flaky-upstream
  signal; shown only when `N > 1`).
* **duration** — per-step wall-clock (`D 3.2s`), so the slow step is obvious.
* **error class** — a failed step shows its `error_class` (e.g.
  `TaskTimeoutError`, `WriteError`) inline.
* **fan-out** — a mapped (`expand`) step shows its per-instance breakdown
  (`⑃ N instances · M failed`).

## Hooks

```python
def on_post_run(ctx, result):
    print(f"pipeline {ctx.pipeline_name} produced {result.records_written} rows")

pipeline.on("post_run", on_post_run)
```

Supported events: `pre_run`, `on_task_start`, `on_task_end`,
`on_error`, `post_run`. Hooks are called synchronously, in registration
order, after the runtime emits its standard metrics.

## Parameters & runtime templating (`{{ }}`)

The same pipeline can run for different dates / regions / windows
*without editing the config* — via **per-run parameters** and a
runtime templating layer. This is the dynamic counterpart to the static
`${var.name}` variables: variables resolve once at load time; templates
resolve *per execution*, after everything else.

Four substitution namespaces compose without clashing:

| Syntax | When | Source |
|--------|------|--------|
| `${ENV}` | load | environment variables |
| `!secret` / `${SECRET:...}` | resolve | secret backend |
| `${var.name}` | load | pipeline/workspace variables (static) |
| **`{{ expr }}`** | **run** | **runtime context (params + run metadata)** |

Declare default params on the pipeline, reference them with
`{{ params.name }}`, and use the run's logical date with `{{ ds }}`:

```yaml
name: daily_orders
params:
  region: kr            # default — overridable per run
source:
  connection: pg
  query: "SELECT * FROM orders WHERE region = '{{ params.region }}' AND day = '{{ ds }}'"
sink:
  connection: warehouse
  table: "orders_{{ params.region }}_{{ ds_nodash }}"
  mode: overwrite
```

Available context keys: `{{ ds }}` (logical date `YYYY-MM-DD`),
`{{ ds_nodash }}` (`YYYYMMDD`), `{{ ts }}` / `{{ logical_date }}`
(ISO-8601), `{{ run_id }}`, `{{ pipeline_name }}`, and
`{{ params.<key> }}` (nested paths like `{{ params.window.start }}` work).
A whole-string reference preserves the value's type
(`chunk_size: "{{ params.cs }}"` stays an int); embedded references
interpolate as text. An undefined reference fails the run with a clear
error (typos surface immediately).

Override params at run time:

```bash
# CLI — repeatable, values JSON-parsed (100 → int, [1,2] → list)
uv run anyduct run daily_orders.yaml -c connections.yaml \
  --param region=us --logical-date 2026-06-15
```

```bash
# REST — trigger with a params body (web UI shows a params dialog
# automatically when the pipeline declares params)
POST /workspaces/{ws}/pipelines/{id}/trigger
{ "params": { "region": "us" } }
```

**Security**: templating is *not* Jinja2 — it resolves dotted paths only,
with no function calls, arithmetic, or code execution (the same
sandboxed posture as filter/branch predicates). Catalog asset keys are
derived from the *rendered* config, so lineage matches the tables a run
actually wrote.

## Passing values between tasks — XCom (`{{ xcom.* }}`)

In a task-DAG (tasks with `depends_on`), a downstream task can pull an
upstream task's result via `{{ xcom.<task>.<key> }}` — the data-flow
counterpart to the ordering primitives (`depends_on` / `trigger_rule` /
`branch`). Each task **auto-publishes** a summary when it finishes:

| Key | Value |
|-----|-------|
| `records_read` | rows the task read |
| `records_written` | rows the task wrote |
| `success` | always `True` once it completes |
| `new_cursor` | max cursor value seen (cursored runs), else `null` |

```yaml
name: incremental_then_report
tasks:
  - name: load
    source: pg
    query: "SELECT * FROM events WHERE id > {{ params.since }}"
    cursor_column: id
    sink: warehouse
    sink_options: {table: events, mode: append}
  - name: report
    depends_on: [load]
    source: warehouse
    # pull the upstream's high-water mark
    query: "SELECT count(*) FROM events WHERE id > {{ xcom.load.new_cursor }}"
    sink: warehouse
    sink_options: {table: load_report, mode: overwrite}
```

References resolve **per task, at execution time** — after the upstream
has run — so the value is concrete by the time `report` reads. A
whole-string reference preserves the value's type
(`chunk_size: "{{ xcom.load.records_written }}"` stays an int). An
undefined key (typo, or a task not in `depends_on`) fails the run with a
clear error. Same security posture as `{{ params }}`: dotted paths only,
no code execution.

### Publishing a list — `push_xcom`

The auto summary is all scalars — handy for thresholds, but none of it is
a **list** to fan out over. `push_xcom` projects a column of the rows a
task processes into a list under `xcom.<task>.<key>`:

```yaml
tasks:
  - name: discover
    source: pg
    query: "SELECT DISTINCT region FROM orders"
    sink: staging                 # the rows still land here
    push_xcom:
      regions: {column: region, distinct: true}   # → xcom.discover.regions
  - name: load
    depends_on: [discover]
    expand:
      region: "{{ xcom.discover.regions }}"        # fan out over the list
    source: pg
    query: "SELECT * FROM orders WHERE region = '{{ map.region }}'"
    sink: warehouse
    sink_options: {table: "orders_{{ map.region }}", mode: overwrite}
```

`distinct: true` dedupes preserving first-seen order. A task with
`push_xcom` always takes the records data path (pushdown/Arrow never
materialise Python rows). Referencing a column the rows don't have fails
the task; an empty source publishes an empty list (zero fan-out).

> Note (slice 1): XCom is resolved in the *flat* task path (`query` /
> `source_options` / `sink_table` / `sink_options` / `sinks` / `pre_sql`).
> `push_xcom` projects a column; pushing arbitrary computed values and
> graph-node-level XCom are planned follow-ups. Catalog lineage is
> derived from the pre-XCom config, so an XCom reference inside a *table
> name* won't be reflected in lineage.

## Dynamic task mapping — `expand` (Airflow `.expand()`)

A task can **fan out at runtime** into one instance per element of a
list — Airflow's mapped tasks. Declare `expand` as `name → list`; each
instance exposes `{{ map.<name> }}` in its templatable fields. Multiple
keys form the cross product.

```yaml
name: per_region_load
params:
  regions: [kr, us, jp]      # overridable per run
tasks:
  - name: load
    expand:
      region: "{{ params.regions }}"   # ← drives the fan-out
    source: pg
    query: "SELECT * FROM orders WHERE region = '{{ map.region }}'"
    sink: warehouse
    sink_options: {table: "orders_{{ map.region }}", mode: overwrite}
```

The expansion list can come from three places, all via templating:

- a **literal** list (`expand: {region: [kr, us]}`),
- a **per-run param** list (`{{ params.regions }}` — resolved at load,
  so triggering with `--param regions='["kr","us"]'` or a REST `params`
  body changes the fan-out without editing the pipeline),
- an **upstream XCom** list (`{{ xcom.discover.items }}` — resolved at
  execution, after the producing task runs; the upstream publishes the
  list with [`push_xcom`](#publishing-a-list--push_xcom)).

Each instance runs the full single-task path independently (its own
retry / timeout / data-path selection). One instance failing does **not**
stop the others — the task is reported failed and the first error is
raised at the end. Records sum across instances, and the task publishes
the **aggregate** to XCom (`{{ xcom.load.records_written }}` = total). An
empty expansion list runs zero instances (success, 0 records).

> Note (slice 1): mapping applies to the flat task path; the per-instance
> element is injected into `query` / `source_options` / `sink_table` /
> `sink_options` / `sinks` / `pre_sql`. Per-instance run rows in the
> service layer (one `node_run` per mapped instance) and graph-node-level
> mapping are planned follow-ups; today the run records the aggregate.

### Catching unresolved `{{ map.* }}` / `{{ xcom.* }}` at dry-run

Because these namespaces resolve *per task at execution time*, a typo in
an `expand` key or an upstream task name isn't caught by ordinary config
validation — the token would silently reach the connector, or the
per-task render would fail mid-run. **Dry-run** surfaces these statically
as advisory warnings:

- `map_ref_without_expand` — a `{{ map.<key> }}` reference but the task
  declares no `expand`.
- `map_ref_unknown_key` — `{{ map.<key> }}` where `<key>` isn't one of
  the task's `expand` keys.
- `xcom_ref_unknown_task` — `{{ xcom.<task>.* }}` naming a task that
  doesn't exist.
- `xcom_ref_not_upstream` — pulling XCom from a task that isn't an
  upstream dependency (it may not have run yet — add it to `depends_on`).

These are advisory: they show up beside the connector health checks but
don't block the run.

## Orchestration — Operator DAG (ADR-0099)

A task-DAG can mix typed **operators**, not just `etl` (source→sink) tasks.
This is the right shape for legacy stored-procedure-style ETL — ordered SQL
steps with logging — where edges mean *run order* (`depends_on`) and data
passes between steps via XCom, not along the edges.

`kind` on a task selects the operator (default `etl`):

| `kind` | Does | Fields |
|--------|------|--------|
| `etl` (default) | source query → sink table | `source`, `sink`, `transforms` |
| `sql` | run statement(s) against a connection (DDL / DELETE / MERGE / log INSERT). Rows-affected → XCom `records_written`. | `connection`, `statements: [...]` |
| `proc_call` | `CALL <procedure>(<args>)` — args are SQL expressions | `connection`, `procedure`, `args: [...]` |

```yaml
tasks:
  - name: write_start_log         # a side-effect SQL step
    kind: sql
    connection: warehouse
    statements:
      - "INSERT INTO ops.batch_log (program, step) VALUES ('mart', 'START')"
  - name: load_mart               # the actual load (etl, default kind)
    depends_on: [write_start_log]
    source: {connection: warehouse, query: "SELECT ... GROUP BY region"}
    sink:
      connection: warehouse
      table: mart.daily
      mode: append
      pre_sql: "DELETE FROM mart.daily WHERE day = '{{ params.run_day }}'"
  - name: write_end_log
    kind: sql
    connection: warehouse
    depends_on: [load_mart]
    statements:                   # rows-affected straight from the load's XCom
      - "INSERT INTO ops.batch_log (step, n) VALUES ('END', {{ xcom.load_mart.records_written }})"
  - name: write_error_log         # runs EVEN IF an upstream step failed
    kind: sql
    connection: warehouse
    depends_on: [load_mart]
    trigger_rule: all_done
    statements:
      - "INSERT INTO ops.batch_log (step) VALUES ('AUDIT')"
```

Each operator gets the orchestration knobs uniformly: `depends_on` (order),
`trigger_rule` (`all_success` default; `all_done` for an error-log step),
`branch` (conditional routing, below), `expand` (dynamic fan-out, above),
per-step `retry` / `timeout_seconds`, and `{{ params.* }}` / `{{ xcom.* }}`
templating. Catalog lineage tracks every step — a `sql` step's INSERT target
becomes an output asset; the etl load's source/sink form the table + column
lineage. The web builder edits this on the same canvas in **orchestration
mode** (operator palette + dependency edges); `branch`, `expand`, `trigger_rule`,
`retry`, and `timeout` are all per-step fields in the properties panel — no YAML
required. Full examples:
[`operator_dag_mart.yaml`](https://github.com/anyduct/etl-plugins/blob/main/examples/operator_dag_mart.yaml)
and (branch + fan-out)
[`operator_dag_branch_fanout.yaml`](https://github.com/anyduct/etl-plugins/blob/main/examples/operator_dag_branch_fanout.yaml).

### Conditional routing — `branch` (Airflow `BranchPythonOperator`)

A step can **choose which of its downstream steps run** based on its own
outcome — the rest are skipped, and the skip propagates to *their* descendants.
Declare `branch` as an ordered list of `{when, to}` rules; the first whose
`when` predicate is truthy wins, and `when: null` is the default/else.

```yaml
- name: probe                       # stages the day's rows
  kind: sql
  connection: warehouse
  statements:
    - "INSERT INTO staging.probe SELECT id FROM fct.orders WHERE day = '{{ params.run_day }}'"
  branch:
    - when: "records_written > 0"   # had data → run the load
      to: [load_regions]
    - when: null                    # else → log the empty day
      to: [log_empty]
```

The predicate is sandboxed (no builtins), same contract as the `filter` /
`assert` transforms, over `records_read` / `records_written` / `success`. Each
`to` target must be a **direct downstream** (a step that `depends_on` this one) —
the builder and dry-run flag a target that isn't. A branch-skipped step shows
as `skipped` in the run DAG (distinct from an upstream-failure skip).

> **`sql_exec` graph node vs `sql` operator.** The dataflow graph builder has a
> legacy "Run SQL" node (`sql_exec`) for side-effect SQL *inside a graph*. For
> orchestration (ordered steps), prefer the `sql` operator — it gets the
> orchestration knobs (`depends_on` / `trigger_rule` / retry), publishes
> rows-affected to XCom, and registers its write target in the catalog. Both
> remain supported; new orchestration pipelines should use the operator.

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
