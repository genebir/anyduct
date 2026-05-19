# Connectors

Every connector implements one or more of four ABCs:

| ABC | Method | Used by |
|-----|--------|---------|
| `BatchSource` | `read(query, *, chunk_size, **opts)` | extract step |
| `BatchSource` | `read_since(cursor_column, cursor_value, *, query, ...)` | incremental extract (Step 6.1) |
| `BatchSink` | `write(records, *, mode, key_columns, **opts)` | load step |
| `StreamSource` | `subscribe(topic, *, group_id, **opts)` | stream pipeline |
| `StreamSink` | `publish(topic, record, key)` | stream pipeline |

All four extend `Connector`, which contributes `connect()` /
`close()` / `health_check()` + context-manager support.

## Built-in catalog

| Connector | Extra | Implements |
|-----------|-------|------------|
| PostgreSQL | `[postgres]` | `BatchSource`, `BatchSink` |
| MySQL / MariaDB | `[mysql]` | `BatchSource`, `BatchSink` |
| SQLite | — (stdlib) | `BatchSource`, `BatchSink` |
| MongoDB | `[mongodb]` | `BatchSource`, `BatchSink` |
| S3 / MinIO | `[s3]` | `BatchSource`, `BatchSink` |
| Kafka | `[kafka]` | `StreamSource`, `StreamSink` |
| HTTP / REST | — (httpx, core dep) | `BatchSource` |

## Picking + naming a connector

Connectors are looked up via `ConnectorRegistry` by the string set on
the `@ConnectorRegistry.register("...")` decorator. The YAML
`type` field uses that same string:

```yaml
connections:
  warehouse:
    type: postgres       # ← matches @ConnectorRegistry.register("postgres")
    host: ...
```

The registry auto-discovers connectors via the
`etl_plugins.connectors` entry-point group, so dropping a new
connector package into an environment makes it usable without code
changes.

## Adding your own connector

1. Subclass the appropriate ABC, decorate with
   `@ConnectorRegistry.register("my-thing")`.
2. Implement the required methods. Stream connectors must accept the
   commit strategy described in
   [Pipelines & tasks](pipelines.md#stream-mode).
3. Add an entry to your package's `[project.entry-points."etl_plugins.connectors"]`.
4. Run the contract test suite against your connector — see
   [contracts in the repo](https://github.com/duarl/etl-plugins/tree/main/tests/contracts).

```python
from collections.abc import Iterator
from typing import Any
from etl_plugins.core.connector import BatchSource
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


@ConnectorRegistry.register("my-thing")
class MyThingConnector(BatchSource):
    def __init__(self, endpoint: str, **extra: Any) -> None:
        self.endpoint = endpoint
        self._client = None

    def connect(self) -> None:
        self._client = open_client(self.endpoint)

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def health_check(self) -> bool:
        return self._client is not None and self._client.ping()

    def read(self, query: str | None = None, *, chunk_size: int = 10_000, **options: Any) -> Iterator[Record]:
        for raw in self._client.fetch(query, chunk_size=chunk_size):
            yield Record(data=dict(raw))
```

## Connector option conventions

Across the built-ins, these option names are reserved:

| Option | Connectors | Meaning |
|--------|-----------|---------|
| `query` | SQL sources | full SELECT statement (placeholders bind via `**options`) |
| `query` | Mongo / HTTP | collection / path |
| `table` | RDBMS sinks | target table (schema-qualified OK) |
| `mode` | sinks | `append` / `overwrite` / `upsert` |
| `key_columns` | sinks | required when `mode="upsert"` |
| `batch_size` | sinks | rows per executemany / insert_many call |
| `filter` | Mongo source | find filter dict |
| `projection` | Mongo source | find projection dict |
| `sort` | Mongo source | (field, direction) tuple or list |
| `limit` | Mongo / HTTP | cap on rows returned |
| `format` | S3 / Kafka | `parquet` / `csv` / `jsonl` / `json` / `avro` |
| `records_field` | HTTP source | key in the JSON response object holding the array |
| `page_param` | HTTP source | query param name for page-number pagination |

See the [API reference](../reference/connectors.md) for the exact
signatures.
