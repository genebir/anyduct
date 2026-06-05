"""SQS connector — StreamSource + StreamSink (Phase AGM, ADR-0084).

Amazon SQS (Simple Queue Service) via ``boto3``. Optional dependency::

    pip install 'etl-plugins[sqs]'

boto3 is synchronous, so the async StreamSource/StreamSink methods wrap
the blocking calls in ``asyncio.to_thread``.

* **subscribe** — long-polls ``receive_message`` and yields JSON-decoded
  Records. Each message's receipt handle is held pending until
  ``commit`` deletes it (SQS's at-least-once model: a message stays
  visible again after the visibility timeout until explicitly deleted).
* **publish** — ``send_message`` (JSON body).
* **commit** — ``delete_message_batch`` for the receipt handles yielded
  since the last commit. This is the SQS checkpoint analog — unlike
  Kinesis, deleting is how you acknowledge processing.
* **flush** — no-op (``send_message`` is synchronous per call).

The ``topic`` argument is a queue name (resolved to a URL via
``get_queue_url``) or a full queue URL. Driver imported lazily.
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


@ConnectorRegistry.register("sqs")
class SQSConnector(StreamSource, StreamSink):
    """SQS stream source + sink (boto3-backed)."""

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
        self._url_cache: dict[str, str] = {}
        # Receipt handles yielded since the last commit, grouped by queue url.
        self._pending: dict[str, list[str]] = {}

    # ---------- lifecycle ---------------------------------------------------

    def connect(self) -> None:
        if self._client is not None:
            return
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - import side effect
            raise ConnectError(
                "boto3 not installed. Install with: pip install 'etl-plugins[sqs]'"
            ) from exc
        try:
            self._client = boto3.client(
                "sqs",
                region_name=self.region,
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                **self._extra,
            )
        except Exception as exc:  # botocore errors are broad
            raise ConnectError(f"sqs connect failed: {exc}") from exc

    def close(self) -> None:
        self._client = None
        self._url_cache.clear()
        self._pending.clear()

    def health_check(self) -> bool:
        if self._client is None:
            return False
        try:
            self._client.list_queues(MaxResults=1)
            return True
        except Exception:
            return False

    async def aclose(self) -> None:
        self._client = None

    @property
    def client(self) -> Any:
        if self._client is None:
            raise ConnectError("SQSConnector is not connected")
        return self._client

    def _queue_url(self, topic: str) -> str:
        """Resolve a queue name to its URL (cached). A value that already
        looks like a URL is used directly."""
        if topic.startswith("http://") or topic.startswith("https://"):
            return topic
        cached = self._url_cache.get(topic)
        if cached is not None:
            return cached
        url = str(self.client.get_queue_url(QueueName=topic)["QueueUrl"])
        self._url_cache[topic] = url
        return url

    # ---------- StreamSource ----------------------------------------------

    async def subscribe(
        self,
        topic: str,
        *,
        group_id: str | None = None,
        **options: Any,
    ) -> AsyncIterator[Record]:
        if self._client is None:
            raise ConnectError("SQSConnector is not connected — call connect() first")
        wait_seconds = int(options.get("wait_seconds", 10))
        max_messages = int(options.get("max_messages", 10))
        try:
            url = await asyncio.to_thread(self._queue_url, topic)
        except Exception as exc:
            raise ConnectError(f"sqs subscribe (resolve queue) failed: {exc}") from exc

        while True:
            try:
                resp = await asyncio.to_thread(
                    self._client.receive_message,
                    QueueUrl=url,
                    MaxNumberOfMessages=max_messages,
                    WaitTimeSeconds=wait_seconds,
                )
            except Exception as exc:
                raise ReadError(f"sqs receive_message failed: {exc}") from exc
            for msg in resp.get("Messages", []):
                body = msg.get("Body") or ""
                try:
                    data = json.loads(body) if body else {}
                except json.JSONDecodeError as exc:
                    raise ReadError(
                        f"sqs message {msg.get('MessageId')} is not valid JSON: {exc}"
                    ) from exc
                self._pending.setdefault(url, []).append(msg["ReceiptHandle"])
                yield Record(
                    data=data,
                    metadata={
                        "source": "sqs",
                        "queue": topic,
                        "message_id": msg.get("MessageId"),
                    },
                )

    async def commit(self, offsets: Any = None) -> None:
        """Delete the messages yielded since the last commit (SQS ack)."""
        if self._client is None or not self._pending:
            return
        for url, handles in list(self._pending.items()):
            # delete_message_batch caps at 10 entries per call.
            for start in range(0, len(handles), 10):
                chunk = handles[start : start + 10]
                entries = [{"Id": str(i), "ReceiptHandle": h} for i, h in enumerate(chunk)]
                try:
                    await asyncio.to_thread(
                        self._client.delete_message_batch, QueueUrl=url, Entries=entries
                    )
                except Exception as exc:
                    raise ConnectError(f"sqs commit (delete) failed: {exc}") from exc
        self._pending.clear()

    # ---------- StreamSink ------------------------------------------------

    async def publish(
        self,
        topic: str,
        record: Record,
        key: bytes | None = None,
    ) -> None:
        if self._client is None:
            raise ConnectError("SQSConnector is not connected — call connect() first")
        try:
            url = await asyncio.to_thread(self._queue_url, topic)
            body = json.dumps(record.data, ensure_ascii=False, default=str)
            await asyncio.to_thread(self._client.send_message, QueueUrl=url, MessageBody=body)
        except Exception as exc:
            raise WriteError(f"sqs publish failed: {exc}") from exc

    async def flush(self) -> None:
        """No-op: ``send_message`` is synchronous per call."""
        return None
