"""DynamoDB connector — BatchSource + BatchSink (Phase AGJ, ADR-0081).

Amazon DynamoDB is a serverless key-value / document store. Built on
``boto3``. Optional dependency::

    pip install 'etl-plugins[dynamodb]'

* **read** — ``Table.scan`` with automatic pagination; ``query`` is the
  table name (falls back to the connector's default ``table``).
* **write** — ``Table.batch_writer`` (auto-batches 25 items + retries).
  DynamoDB ``put_item`` replaces by primary key, so it's an upsert by
  nature; ``append`` and ``upsert`` modes both put. ``overwrite`` is
  rejected (DynamoDB has no cheap truncate).

DynamoDB is schemaless, so there is no ``SchemaInspector`` /
``SchemaWriter`` (no ``ensure_table`` / cross-DB migration target).

Type handling: boto3's DynamoDB resource requires ``Decimal`` for
numbers (it rejects ``float``), so writes convert ``float`` → ``Decimal``
and reads convert ``Decimal`` → ``int``/``float`` for downstream
friendliness.

The driver is imported **lazily** inside :meth:`connect`.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from decimal import Decimal
from typing import Any

from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


def _to_dynamo(value: Any) -> Any:
    """Recursively convert ``float`` → ``Decimal`` (DynamoDB rejects
    floats). Dicts/lists are walked; other types pass through."""
    if isinstance(value, float):
        # Round-trip through str so 0.1 doesn't become 0.1000000000000000055.
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value: Any) -> Any:
    """Recursively convert ``Decimal`` → ``int``/``float`` so downstream
    sinks (and JSON serialization) don't choke on ``Decimal``."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


@ConnectorRegistry.register("dynamodb")
class DynamoDBConnector(BatchSource, BatchSink):
    """DynamoDB batch source + sink (boto3-backed)."""

    def __init__(
        self,
        region: str = "us-east-1",
        table: str = "",
        *,
        endpoint_url: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        **extra: Any,
    ) -> None:
        self.region = region
        self.table = table
        self.endpoint_url = endpoint_url
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self._extra: dict[str, Any] = extra
        self._resource: Any = None

    # ---------- lifecycle ---------------------------------------------------

    def connect(self) -> None:
        if self._resource is not None:
            return
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - import side effect
            raise ConnectError(
                "boto3 not installed. Install with: pip install 'etl-plugins[dynamodb]'"
            ) from exc
        try:
            self._resource = boto3.resource(
                "dynamodb",
                region_name=self.region,
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                **self._extra,
            )
        except Exception as exc:  # botocore errors are broad
            raise ConnectError(f"dynamodb connect failed: {exc}") from exc

    def close(self) -> None:
        self._resource = None

    def health_check(self) -> bool:
        if self._resource is None:
            return False
        try:
            self._resource.meta.client.list_tables(Limit=1)
            return True
        except Exception:
            return False

    @property
    def resource(self) -> Any:
        if self._resource is None:
            raise ConnectError("DynamoDBConnector is not connected")
        return self._resource

    def _table_name(self, query: str | None) -> str:
        name = query or self.table
        if not name:
            raise WriteError("DynamoDBConnector requires a table name")
        return name

    # ---------- BatchSource -------------------------------------------------

    def read(
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        table = self._table_name(query)
        tbl = self.resource.Table(table)
        scan_kwargs: dict[str, Any] = {}
        try:
            while True:
                resp = tbl.scan(**scan_kwargs)
                for item in resp.get("Items", []):
                    yield Record(
                        data=_from_dynamo(item),
                        metadata={"source": "dynamodb", "table": table},
                    )
                last_key = resp.get("LastEvaluatedKey")
                if not last_key:
                    break
                scan_kwargs["ExclusiveStartKey"] = last_key
        except Exception as exc:
            raise ReadError(f"dynamodb scan failed: {exc}") from exc

    # ---------- BatchSink ---------------------------------------------------

    def write(
        self,
        records: Iterable[Record],
        *,
        mode: str = "append",
        key_columns: list[str] | None = None,
        table: str | None = None,
        **options: Any,
    ) -> int:
        name = self._table_name(table)
        if mode == "overwrite":
            raise WriteError(
                "DynamoDBConnector does not support mode='overwrite' "
                "(DynamoDB has no cheap truncate). Use 'append'/'upsert' "
                "(put_item replaces by primary key)."
            )
        if mode not in ("append", "upsert"):
            raise WriteError(
                f"unknown write mode: {mode!r} (use 'append' or 'upsert'; "
                "both put_item, which replaces by primary key)"
            )
        tbl = self.resource.Table(name)
        count = 0
        try:
            with tbl.batch_writer() as batch:
                for r in records:
                    batch.put_item(Item=_to_dynamo(dict(r.data)))
                    count += 1
        except Exception as exc:
            raise WriteError(f"dynamodb write failed: {exc}") from exc
        return count
