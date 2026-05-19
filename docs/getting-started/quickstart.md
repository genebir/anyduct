# Quickstart

Five minutes from `pip install` to a running pipeline.

## 1. Install

```bash
pip install 'etl-plugins[postgres,s3]'
```

## 2. Define connections (YAML)

`configs/connections.yaml`:

```yaml
connections:
  prod-orders:
    type: postgres
    host: db.prod.example.com
    port: 5432
    database: orders
    user: etl
    password: ${SECRET:db/etl/password}   # resolved by the secret backend
    sslmode: require

  warehouse:
    type: s3
    bucket: acme-warehouse
    region: us-east-1
    access_key: ${SECRET:s3/access_key}
    secret_key: ${SECRET:s3/secret_key}
```

## 3. Define the pipeline (YAML)

`configs/pipelines/orders-nightly.yaml`:

```yaml
name: orders-nightly
mode: batch
tasks:
  - name: extract-orders
    source: prod-orders
    query: "SELECT id, customer_id, amount, created_at FROM orders WHERE created_at >= now() - interval '1 day'"
    sink: warehouse
    sink_options:
      key: "orders/year={year}/month={month}/day={day}.parquet"
      format: parquet
retry:
  max_attempts: 3
  backoff: exponential
  initial_delay_seconds: 2
```

## 4. Run from the CLI

```bash
uv run etlx run configs/pipelines/orders-nightly.yaml \
    --connections configs/connections.yaml
```

That command:

1. Loads YAML → Pydantic models (`ConnectionsConfig`, `PipelineConfig`).
2. Resolves every `${SECRET:…}` placeholder through the active secret
   backend.
3. Instantiates the two connectors from the connector registry.
4. Builds a `Pipeline` and calls `.run()`.
5. Streams structured logs to stdout, emits metrics + traces if a
   collector is wired.

## 5. Or run programmatically

```python
from etl_plugins import Pipeline, Task, load_pipeline, load_connections, resolve_secrets
from etl_plugins.config.secrets import get_secret_backend
from etl_plugins.core.registry import ConnectorRegistry

# 1. Parse YAML.
conns = load_connections("configs/connections.yaml")
pipe = load_pipeline("configs/pipelines/orders-nightly.yaml")

# 2. Resolve secrets.
backend = get_secret_backend()
resolved = resolve_secrets(conns, backend)

# 3. Instantiate connectors from the registry.
connectors = {}
for name, cfg in resolved.connections.items():
    cls = ConnectorRegistry.get(cfg.type)
    connectors[name] = cls(**cfg.config)

# 4. Run.
with connectors["prod-orders"], connectors["warehouse"]:
    result = pipe.to_runtime().run(connectors=connectors)

print(f"read={result.records_read} written={result.records_written}")
```

## Where to next

* Adding new sources / sinks? See [Connectors](../guides/connectors.md).
* Daily syncs that only pick up new rows? See
  [Cursors & incremental sync](../guides/cursors.md).
* Want metrics + traces in your collector? See
  [Observability](../guides/observability.md).
