"""Kinesis connector — StreamSource + StreamSink (Phase AGL, ADR-0083).

Amazon Kinesis Data Streams via ``boto3``. Optional dependency::

    pip install 'etl-plugins[kinesis]'

boto3 is synchronous, so the async StreamSource/StreamSink methods wrap
the blocking calls in ``asyncio.to_thread`` to stay loop-friendly.

* **subscribe** — enumerates the stream's shards, gets a shard iterator
  per shard (``TRIM_HORIZON`` by default), then polls ``get_records`` in
  a round-robin loop, yielding JSON-decoded Records. Unbounded — the
  caller stops by breaking the ``async for``.
* **publish** — ``put_record`` (JSON-encoded value, partition key).
* **commit** — no-op: the raw Kinesis API has no server-side checkpoint
  (KCL stores progress in DynamoDB; out of scope here).
* **flush** — no-op: ``put_record`` is synchronous per call.

``connect`` builds the boto3 client; the actual network calls happen in
the async methods. Driver imported lazily.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from etl_plugins.core.connector import StreamSink, StreamSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


@ConnectorRegistry.register("kinesis")
class KinesisConnector(StreamSource, StreamSink):
    """Kinesis stream source + sink (boto3-backed)."""

    def __init__(
        self,
        region: str = "us-east-1",
        *,
        endpoint_url: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        **extra: Any,
    ) -> None:
        self.region = region
        self.endpoint_url = endpoint_url
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self._extra: dict[str, Any] = extra
        self._client: Any = None

    # ---------- lifecycle ---------------------------------------------------

    def connect(self) -> None:
        if self._client is not None:
            return
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - import side effect
            raise ConnectError(
                "boto3 not installed. Install with: pip install 'etl-plugins[kinesis]'"
            ) from exc
        try:
            self._client = boto3.client(
                "kinesis",
                region_name=self.region,
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                **self._extra,
            )
        except Exception as exc:  # botocore errors are broad
            raise ConnectError(f"kinesis connect failed: {exc}") from exc

    def close(self) -> None:
        self._client = None

    def health_check(self) -> bool:
        if self._client is None:
            return False
        try:
            self._client.list_streams(Limit=1)
            return True
        except Exception:
            return False

    async def aclose(self) -> None:
        """Symmetry with the Kafka connector; boto3 has no async teardown."""
        self._client = None

    @property
    def client(self) -> Any:
        if self._client is None:
            raise ConnectError("KinesisConnector is not connected")
        return self._client

    # ---------- StreamSource ----------------------------------------------

    async def subscribe(
        self,
        topic: str,
        *,
        group_id: str | None = None,
        **options: Any,
    ) -> AsyncIterator[Record]:
        if self._client is None:
            raise ConnectError("KinesisConnector is not connected — call connect() first")
        iterator_type = str(options.get("iterator_type", "TRIM_HORIZON"))
        poll_interval = float(options.get("poll_interval", 1.0))
        limit = int(options.get("limit", 100))

        try:
            desc = await asyncio.to_thread(self._client.describe_stream, StreamName=topic)
            shards = desc["StreamDescription"]["Shards"]
            iterators: dict[str, str | None] = {}
            for shard in shards:
                sid = shard["ShardId"]
                resp = await asyncio.to_thread(
                    self._client.get_shard_iterator,
                    StreamName=topic,
                    ShardId=sid,
                    ShardIteratorType=iterator_type,
                )
                iterators[sid] = resp["ShardIterator"]
        except Exception as exc:
            raise ConnectError(f"kinesis subscribe (shard setup) failed: {exc}") from exc

        while True:
            got_any = False
            for sid, shard_it in list(iterators.items()):
                if shard_it is None:
                    continue
                try:
                    resp = await asyncio.to_thread(
                        self._client.get_records, ShardIterator=shard_it, Limit=limit
                    )
                except Exception as exc:
                    raise ReadError(f"kinesis get_records failed on {sid}: {exc}") from exc
                iterators[sid] = resp.get("NextShardIterator")
                for rec in resp.get("Records", []):
                    got_any = True
                    raw = rec.get("Data") or b""
                    try:
                        data = json.loads(raw.decode("utf-8")) if raw else {}
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ReadError(
                            f"kinesis record {rec.get('SequenceNumber')} is not valid JSON: {exc}"
                        ) from exc
                    yield Record(
                        data=data,
                        metadata={
                            "source": "kinesis",
                            "stream": topic,
                            "shard_id": sid,
                            "sequence_number": rec.get("SequenceNumber"),
                            "partition_key": rec.get("PartitionKey"),
                        },
                    )
            if not got_any:
                await asyncio.sleep(poll_interval)

    async def commit(self, offsets: Any = None) -> None:
        """No-op: raw Kinesis has no server-side checkpoint (KCL uses
        DynamoDB). Present to satisfy the StreamSource contract."""
        return None

    # ---------- StreamSink ------------------------------------------------

    async def publish(
        self,
        topic: str,
        record: Record,
        key: bytes | None = None,
    ) -> None:
        if self._client is None:
            raise ConnectError("KinesisConnector is not connected — call connect() first")
        value = json.dumps(record.data, ensure_ascii=False, default=str).encode("utf-8")
        partition_key = key.decode("utf-8") if isinstance(key, bytes) else (key or "0")
        try:
            await asyncio.to_thread(
                self._client.put_record,
                StreamName=topic,
                Data=value,
                PartitionKey=partition_key,
            )
        except Exception as exc:
            raise WriteError(f"kinesis publish failed: {exc}") from exc

    async def flush(self) -> None:
        """No-op: ``put_record`` is synchronous per call."""
        return None
