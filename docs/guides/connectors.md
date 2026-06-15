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

A **migration target** implements `SchemaInspector` + `SchemaWriter`
(`ensure_table`), so cross-DB migrations can auto-create the destination
and translate column types through the canonical type mapping. The
SQL-capable migration targets form a 10×10 cross-DB matrix.

### RDBMS / Data Warehouse (batch, migration targets)

"Arrow" marks the bulk fast-path (`read_arrow`/`write_arrow`, ADR-0093) —
eligible transform-less pipelines bypass the per-row Record plane (see
[Data paths](pipelines.md#data-paths-pushdown-arrow-records-adr-00930094)).

| Connector | Extra | Migration target | Arrow | Notes |
|-----------|-------|:---:|:---:|-------|
| PostgreSQL | `[postgres]` | ✅ | ✅ | COPY-based Arrow path |
| MySQL / MariaDB | `[mysql]` | ✅ | ✅ | server-side cursor Arrow path |
| SQLite | — (stdlib) | ✅ | — | type affinity |
| Vertica | `[vertica]` | ✅ | ✅ | analytical column-store |
| SQL Server / Azure SQL | `[mssql]` | ✅ | ✅ | `NVARCHAR(MAX)`, `BIT`, `DATETIME2` |
| Snowflake | `[snowflake]` | ✅ | — | `NUMBER`, `VARIANT`, `TIMESTAMP_TZ` |
| BigQuery | `[bigquery]` | ✅ | — | backtick quoting, PK `NOT ENFORCED`, multi-row DML |
| Redshift | `[redshift]` | ✅ | — | `SUPER`, `VARBYTE`; MERGE upsert (2023+) |
| ClickHouse | `[clickhouse]` | ✅ | — | `MergeTree` engine; **no row-level upsert** |

### NoSQL

| Connector | Extra | Implements | Notes |
|-----------|-------|------------|-------|
| MongoDB | `[mongodb]` | `BatchSource`, `BatchSink` | filter / sort / projection |
| DynamoDB | `[dynamodb]` | `BatchSource`, `BatchSink` | schemaless (scan); `put` = upsert by key |
| Cassandra | `[cassandra]` | `BatchSource`, `BatchSink`, migration target ✅ | CQL; INSERT = upsert; `decimal` no precision |
| Redis | `[redis]` | `StreamSource`, `StreamSink` | Redis Streams (XADD / XREADGROUP / XACK) |

### Object storage / Streaming / HTTP

| Connector | Extra | Implements | Notes |
|-----------|-------|------------|-------|
| S3 / MinIO | `[s3]` | `BatchSource`, `BatchSink` | parquet / csv / jsonl |
| Kafka | `[kafka]` | `StreamSource`, `StreamSink` | aiokafka; offset commit |
| Kinesis | `[kinesis]` | `StreamSource`, `StreamSink` | boto3; shard polling; commit no-op |
| SQS | `[sqs]` | `StreamSource`, `StreamSink` | boto3; commit = message delete (ack) |
| RabbitMQ | `[rabbitmq]` | `StreamSource`, `StreamSink` | aio-pika (AMQP); commit = ack |
| NATS | `[nats]` | `StreamSource`, `StreamSink` | nats-py JetStream; durable pull; commit = ack |
| HTTP / REST | — (httpx, core dep) | `BatchSource` | paginated GET |

### Testing maturity

Every connector ships driver-free **unit tests** (registry resolution,
protocol surface, generated SQL/messages, the "driver not installed"
error). Beyond that, a subset has **live integration tests** that
exercise the real driver against a container:

- **Integration-verified** (testcontainers): PostgreSQL, MySQL, SQLite,
  MongoDB, S3/MinIO, Kafka, **DynamoDB, Kinesis, SQS** (the last three
  via LocalStack).
- **Unit + live-gated**: Vertica, MSSQL, Snowflake, BigQuery, Redshift,
  ClickHouse, Cassandra, Redis, RabbitMQ, NATS. These have no local
  container (managed SaaS, heavy/native drivers), so their fake-client
  unit tests verify the connector's *model* of the driver API — validate
  against a real server before relying on them in production.

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
| `auto_create_table` | migration sinks | inspect source schema + `CREATE TABLE` on first run |
| `auto_create_if_exists` | migration sinks | `skip` / `drop` / `error` when the table already exists |
| `topic` | stream source/sink | Kafka/Redis topic, Kinesis stream, SQS queue (name or URL) |
| `group_id` | stream sources | consumer group (Kafka / Redis Streams) |
| `iterator_type` | Kinesis source | `TRIM_HORIZON` (oldest) / `LATEST` |
| `wait_seconds` | SQS source | long-poll wait per `receive_message` |

See the [API reference](../reference/connectors.md) for the exact
signatures.
