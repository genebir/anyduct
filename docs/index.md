# etl-plugins

**Backend-agnostic, orchestrator-agnostic Python ETL plugin library.**

Write your pipeline once. Run it from a script, from Airflow, from Dagster,
from Prefect, or from the bundled `etlx-server` service — same connector,
same `Pipeline.run()` call, same observability.

```python
from etl_plugins import Pipeline, Task
from etl_plugins.connectors.rdbms.postgres import PostgresConnector
from etl_plugins.connectors.object_storage.s3 import S3Connector

source = PostgresConnector(host="db", user="etl", password="…", database="ops")
sink = S3Connector(bucket="warehouse", region="us-east-1")

with source, sink:
    Pipeline("orders-nightly").add(
        Task.extract("pg", "SELECT * FROM orders").load("s3", key="orders.parquet")
    ).run(connectors={"pg": source, "s3": sink})
```

## What you get

* **A unified `BatchSource` / `BatchSink` / `StreamSource` / `StreamSink`
  contract** that every connector satisfies — change the database, not
  the pipeline code.
* **Built-in connectors** — PostgreSQL, MySQL, SQLite, MongoDB, S3 /
  MinIO, Kafka, HTTP/REST. Adding a new one is a single subclass +
  one entry-point line.
* **YAML-driven configs** (`connections.yaml` + `pipelines/*.yaml`) so
  the same definition flows from a developer laptop to staging to prod.
* **Incremental sync** via `BatchSource.read_since(...)` plus a typed
  `Cursor` / `CursorState` abstraction (in-memory / JSON file /
  Postgres-backed).
* **Drop-in observability** — structured logs, OpenTelemetry metrics +
  traces, retry / DLQ wired into every `Pipeline.run`.
* **Orchestrator adapters** (Airflow / Dagster / Prefect) — keep your
  scheduling story, change the data layer underneath.
* **Optional service layer** (`services/etlx-server` + `services/etlx-web`)
  for teams who want a web UI, RBAC, audit log, and a managed worker
  queue — bolted on top of the same core, never required.

## Where to next

* New here? Start with the [install guide](getting-started/install.md)
  and the [quickstart](getting-started/quickstart.md).
* Already building? See the [connector catalog](guides/connectors.md)
  for what's shipped, and the [cursor guide](guides/cursors.md) for
  incremental syncs.
* Plugging into a collector? The
  [observability guide](guides/observability.md) covers OpenTelemetry.

## Design principles

1. **Backend-agnostic.** Connectors implement a small ABC; everything
   above (pipelines, retry, DLQ, observability) is connector-shape-
   blind.
2. **Orchestrator-agnostic.** Core + adapters; no `import airflow` in
   the core path.
3. **Config-driven.** YAML in, Pydantic models out. No magic strings.
4. **Batch and stream are first-class peers.** Stream pipelines aren't
   a tacked-on mode — they share the same `Pipeline` type with a
   single `mode` flag.
5. **Service layer is optional.** The library stands alone; the web
   service is a separate package that depends on it, not vice versa.

See the [Architecture Decisions](decisions.md) for the full design
record (ADRs 0001 through 0024).
