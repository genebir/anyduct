# Install

`etl-plugins` targets Python **3.11+** and is published on PyPI.

## Core install

```bash
pip install etl-plugins
```

The core install pulls only pure-Python dependencies — Pydantic,
structlog, PyYAML, Jinja2, tenacity, httpx, typer. No database driver is
included; you pick the ones you need via *extras*.

## Connector extras

Each connector that needs a third-party driver ships as an opt-in extra,
so a Postgres-only user doesn't pay for `boto3` and a Mongo user doesn't
pay for `aiokafka`.

```bash
pip install 'etl-plugins[postgres]'         # psycopg v3
pip install 'etl-plugins[mysql]'            # PyMySQL
pip install 'etl-plugins[mongodb]'          # pymongo
pip install 'etl-plugins[s3]'               # boto3 + pyarrow
pip install 'etl-plugins[kafka]'            # aiokafka

# Combine as needed:
pip install 'etl-plugins[postgres,s3,kafka]'
```

SQLite uses the stdlib `sqlite3` module — no extra needed. The HTTP
connector uses `httpx`, which is already a core dependency.

## Orchestrator extras

```bash
pip install 'etl-plugins[airflow]'   # Airflow 2.10+
pip install 'etl-plugins[dagster]'   # Dagster 1.9+
pip install 'etl-plugins[prefect]'   # Prefect 3.0+
```

These pull the orchestrator itself; the matching adapter in
`etl_plugins.adapters.*` is lazy-imported, so installing one doesn't
force you to import all three.

## Observability extras

```bash
pip install 'etl-plugins[observability]'    # OTel SDK + OTLP/gRPC exporter
```

Without this extra, `Metrics` and `Tracer` default to NoOp — calls are
free and the dependency footprint stays minimal.

## Secret backend extras

The `${SECRET:<key>}` placeholder is resolved by a pluggable backend.
Each remote backend is its own extra:

```bash
pip install 'etl-plugins[vault]'    # HashiCorp Vault (hvac)
pip install 'etl-plugins[aws-sm]'   # AWS Secrets Manager (boto3)
pip install 'etl-plugins[gcp-sm]'   # Google Cloud Secret Manager
```

`File` and `Env` backends are batteries-included — no extra.

## Development install

If you cloned the repo and want to run the full test suite:

```bash
git clone https://github.com/duarl/etl-plugins
cd etl-plugins
./scripts/bootstrap.sh          # uv sync + pre-commit + docker compose up
uv run pytest                   # unit tests
uv run pytest -m it             # integration tests (testcontainers)
```

`scripts/bootstrap.sh` uses `uv` exclusively — `pip`/`poetry`/`conda` are
**not** supported. See [Architecture Decisions](../decisions.md) for the
rationale.
