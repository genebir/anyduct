# Cursors & incremental sync

Cursors let a pipeline read **only the records that arrived since the
last run** — the most common ETL requirement after "extract everything
once." Step 6.1 ships the abstraction across four layers:

1. A typed `Cursor` value (`column` + `value`).
2. A pluggable `CursorState` store (`InMemory` / `File` / `DB-backed`).
3. An optional `BatchSource.read_since(...)` method on every batch
   connector (sqlite / postgres / mysql / mongodb implement it; HTTP
   and others raise `NotImplementedError` by default).
4. `Pipeline.run(cursor_from=..., cursor_to=...)` parameters that
   thread the watermark through the runtime + return the new high-water
   mark on `RunResult.new_cursor`.

## A complete resumable sync

```python
from etl_plugins import Pipeline, Task, FileCursorState

state = FileCursorState("/var/lib/anyduct/cursors.json")
prior = state.get("orders-nightly")          # Cursor | None

with source, sink:
    result = (
        Pipeline("orders-nightly")
        .add(
            Task.extract("pg", "SELECT id, total, ts FROM orders", cursor_column="ts")
                .load("warehouse", table="orders")
        )
        .run(
            connectors={"pg": source, "warehouse": sink},
            cursor_from=prior.value if prior else None,
        )
    )

if result.new_cursor is not None:
    state.update("orders-nightly", "ts", result.new_cursor)
```

The next run starts from `result.new_cursor` and reads only newer rows.

## Semantics

* `cursor_from` is **exclusive** — only records with
  `cursor_column > cursor_from`. Equal values are assumed already
  processed; otherwise a resume would replay them.
* `cursor_to` is **inclusive** — records with
  `cursor_column <= cursor_to`. Useful for bounded backfills.
* `cursor_from=None, cursor_to=None` → not a cursored run; reads via
  the normal `read()` path.
* `cursor_from=None, cursor_to=Y` → "initial load up to checkpoint Y."
* `cursor_from=X, cursor_to=None` → "tail everything strictly after X."
* `cursor_from=X, cursor_to=Y` → "window."

`Pipeline.run` requires `cursor_column` on every task when any cursor
bound is set; missing `cursor_column` raises `TaskError` at the start
of the run.

## State backends

| Backend | Module | Persists | Concurrency-safe |
|---------|--------|----------|------------------|
| `InMemoryCursorState` | `etl_plugins.core.cursor` | no | single process |
| `FileCursorState(path)` | `etl_plugins.core.cursor` | JSON file | single host |
| `DbCursorState(engine, *, workspace_id=…)` | `anyduct_server.cursors` (service layer) | Postgres `cursors` table | multi-replica |

The DB backend uses a dedicated sync psycopg engine (not the async one
the FastAPI app holds) because asyncpg pools are bound to their
creating event loop. See its docstring for the rationale.

### File backend

```python
from etl_plugins import FileCursorState, Cursor

state = FileCursorState("/var/lib/anyduct/cursors.json")
state.set("orders", Cursor(column="ts", value="2026-05-19T03:00:00+00:00"))
state.get("orders")  # → Cursor(column='ts', value='2026-05-19T03:00:00+00:00')
```

Writes are atomic (temp file + `os.replace` + `fsync`) so a crash
mid-write leaves the prior state intact. `datetime` values serialize
as ISO-8601 strings — the strict `>` semantics still work because
ISO-8601 lexicographic order matches chronological order.

### DB-backed (service layer)

```python
from anyduct_server.cursors import DbCursorState

state = DbCursorState.from_url(
    "postgresql+asyncpg://anyduct:…@db/anyduct",   # async URL is fine; suffix is swapped
    workspace_id=ws.id,
)
state.update("pipeline:123:task:1", "id", 4823)
```

Composite PK `(workspace_id, name)` — different workspaces can use the
same key without collision. `ON DELETE CASCADE` from `workspaces`
cleans up cursors when a workspace is deleted.

## Cursor contract for connector authors

`BatchSource.read_since` has four invariants (verified by
`tests.contracts.cursor._BatchSourceCursorContract`):

1. **`cursor_value=None` returns every record, ordered ascending by
   `cursor_column`.**
2. A mid-range cursor returns only rows where
   `cursor_column > cursor_value`.
3. A cursor at or above the max value returns nothing.
4. Two-batch resume is idempotent — the union covers the set with no
   overlap.

Records emitted by `read_since` should also carry
`metadata["cursor_column"]` so downstream transforms / sinks can see
which field is the watermark.

## What this does *not* (yet) cover

* **Lineage / provenance.** `Cursor` is a watermark, not a per-record
  trail; see ADR-0024 for the Asset / Lineage roadmap (v0.2.0).
* **Multi-column cursors.** Single column only for v0.1.0; if you need
  `(date, id)` tuples, encode them into a sortable string.
* **Stream pipelines.** Streams use connector-native offsets (Kafka
  offsets, etc.) — the cursor abstraction is for batch sources.
