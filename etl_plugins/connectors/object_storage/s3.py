"""S3 (and S3-compatible) object storage connector. SPEC.md §6.

Supports jsonl / csv / parquet formats. Built on boto3 (sync).

Optional dependency::

    pip install 'etl-plugins[s3]'

The same connector targets S3-compatible services (MinIO, Cloudflare R2,
DigitalOcean Spaces, ...) via the ``endpoint_url`` parameter.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Iterator
from typing import Any

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from botocore.exceptions import BotoCoreError, ClientError

from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry

SUPPORTED_FORMATS = ("jsonl", "csv", "parquet")


@ConnectorRegistry.register("s3")
class S3Connector(BatchSource, BatchSink):
    """S3 / S3-compatible batch source + sink (jsonl / csv / parquet)."""

    def __init__(
        self,
        bucket: str = "",
        *,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        session_token: str | None = None,
        # default format used when neither write(format=...) nor key suffix specify one
        default_format: str = "jsonl",
        **extra: Any,
    ) -> None:
        self.bucket = bucket
        self.region = region
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.session_token = session_token
        self.default_format = default_format
        self._extra: dict[str, Any] = extra
        self._client: Any = None

    # ---------- lifecycle --------------------------------------------------

    def connect(self) -> None:
        if self._client is not None:
            return
        try:
            self._client = boto3.client(
                "s3",
                region_name=self.region,
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                aws_session_token=self.session_token,
                **self._extra,
            )
        except (BotoCoreError, ClientError) as exc:
            raise ConnectError(f"s3 connect failed: {exc}") from exc

    def close(self) -> None:
        # boto3 clients hold pooled connections but expose no public close().
        # Dropping the reference releases resources.
        self._client = None

    def health_check(self) -> bool:
        if self._client is None or not self.bucket:
            return False
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return True
        except (BotoCoreError, ClientError):
            return False

    @property
    def client(self) -> Any:
        if self._client is None:
            raise ConnectError("S3Connector is not connected")
        return self._client

    # ---------- BatchSource ------------------------------------------------

    def read(
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
        bucket: str | None = None,
        format: str | None = None,
        **options: Any,
    ) -> Iterator[Record]:
        """List objects under ``query`` (interpreted as key prefix) and yield Records.

        Format is taken from the explicit ``format`` arg, or detected from the
        object key extension (.jsonl / .csv / .parquet). Objects whose key has
        no recognised extension are skipped (so directories with mixed files
        won't blow up).
        """
        if self._client is None:
            raise ConnectError("S3Connector is not connected")
        target_bucket = bucket or self.bucket
        if not target_bucket:
            raise ReadError("S3Connector.read requires 'bucket'")
        prefix = query or str(options.get("prefix", ""))

        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=target_bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key: str = obj["Key"]
                    fmt = format or detect_format(key)
                    if fmt is None:
                        continue  # skip non-data files (e.g. _SUCCESS markers)
                    resp = self._client.get_object(Bucket=target_bucket, Key=key)
                    body = resp["Body"]
                    yield from _parse_object(body, key, fmt)
        except (BotoCoreError, ClientError) as exc:
            raise ReadError(f"s3 read failed: {exc}") from exc

    # ---------- BatchSink --------------------------------------------------

    def write(
        self,
        records: Iterable[Record],
        *,
        mode: str = "append",
        key_columns: list[str] | None = None,
        bucket: str | None = None,
        key: str | None = None,
        format: str | None = None,
        **options: Any,
    ) -> int:
        """Serialize all records and upload as a single object.

        ``mode`` is largely advisory — S3 PUT always overwrites. ``upsert`` /
        ``key_columns`` semantics don't apply to raw object storage and are
        rejected to avoid silent surprise.
        """
        if self._client is None:
            raise ConnectError("S3Connector is not connected")
        target_bucket = bucket or self.bucket
        if not target_bucket:
            raise WriteError("S3Connector.write requires 'bucket'")
        if not key:
            raise WriteError("S3Connector.write requires 'key'")
        if mode == "upsert":
            raise WriteError(
                "S3Connector does not support mode='upsert' — object storage has no row identity"
            )
        if mode not in ("append", "overwrite"):
            raise WriteError(f"unknown s3 write mode: {mode!r} (use 'append' or 'overwrite')")

        all_records = list(records)
        if not all_records:
            return 0

        fmt = format or detect_format(key) or self.default_format
        if fmt not in SUPPORTED_FORMATS:
            raise WriteError(
                f"unsupported s3 write format: {fmt!r} (supported: {SUPPORTED_FORMATS})"
            )

        body = _serialize(all_records, fmt)
        try:
            self._client.put_object(Bucket=target_bucket, Key=key, Body=body)
        except (BotoCoreError, ClientError) as exc:
            raise WriteError(f"s3 write failed: {exc}") from exc
        return len(all_records)


# ============================================================================
# Format helpers — pure functions, unit-testable without an S3 client
# ============================================================================


def detect_format(key: str) -> str | None:
    """Return ``"jsonl"`` / ``"csv"`` / ``"parquet"`` from the key's extension, else None."""
    lower = key.lower()
    if lower.endswith((".jsonl", ".ndjson")):
        return "jsonl"
    if lower.endswith(".csv"):
        return "csv"
    if lower.endswith(".parquet") or lower.endswith(".pq"):
        return "parquet"
    return None


def _serialize(records: list[Record], fmt: str) -> bytes:
    if fmt == "jsonl":
        return _serialize_jsonl(records)
    if fmt == "csv":
        return _serialize_csv(records)
    if fmt == "parquet":
        return _serialize_parquet(records)
    raise WriteError(f"unsupported format: {fmt!r}")


def _serialize_jsonl(records: list[Record]) -> bytes:
    lines = (json.dumps(r.data, ensure_ascii=False, default=str) for r in records)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _serialize_csv(records: list[Record]) -> bytes:
    if not records:
        return b""
    # Collect the union of keys so missing fields in later records become "".
    seen: dict[str, None] = {}
    for r in records:
        for k in r.data:
            seen[k] = None
    columns = list(seen.keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for r in records:
        writer.writerow({k: r.data.get(k, "") for k in columns})
    return buf.getvalue().encode("utf-8")


def _serialize_parquet(records: list[Record]) -> bytes:
    table = pa.Table.from_pylist([r.data for r in records])
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink)  # type: ignore[no-untyped-call]
    return bytes(sink.getvalue().to_pybytes())


def _parse_object(body: Any, key: str, fmt: str) -> Iterator[Record]:
    if fmt == "jsonl":
        yield from _parse_jsonl(body, key)
    elif fmt == "csv":
        yield from _parse_csv(body, key)
    elif fmt == "parquet":
        yield from _parse_parquet(body, key)
    else:
        raise ReadError(f"unsupported format: {fmt!r}")


def _parse_jsonl(body: Any, key: str) -> Iterator[Record]:
    # ``body`` may be a botocore StreamingBody or a plain BinaryIO.
    if hasattr(body, "iter_lines"):
        line_iter: Iterable[bytes] = body.iter_lines()
    else:
        line_iter = iter(body.readline, b"")
    for raw_line in line_iter:
        line = raw_line.strip() if isinstance(raw_line, bytes) else raw_line.encode().strip()
        if not line:
            continue
        yield Record(
            data=json.loads(line),
            metadata={"source": "s3", "key": key},
        )


def _parse_csv(body: Any, key: str) -> Iterator[Record]:
    raw: bytes = body.read() if hasattr(body, "read") else bytes(body)
    text = raw.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        yield Record(
            data=dict(row),
            metadata={"source": "s3", "key": key},
        )


def _parse_parquet(body: Any, key: str) -> Iterator[Record]:
    raw: bytes = body.read() if hasattr(body, "read") else bytes(body)
    table = pq.read_table(pa.BufferReader(raw))  # type: ignore[no-untyped-call]
    for row in table.to_pylist():
        yield Record(
            data=row,
            metadata={"source": "s3", "key": key},
        )


__all__ = ["SUPPORTED_FORMATS", "S3Connector", "detect_format"]
