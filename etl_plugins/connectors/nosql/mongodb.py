"""MongoDB connector — BatchSource + BatchSink. SPEC.md §6.

Synchronous, pymongo-backed. Documents map naturally onto :class:`Record` —
the BSON document becomes ``Record.data`` verbatim, with ``_id`` retained so
upsert / dedup logic can reference it. Mongo's flexible schema means
non-string keys at the top level are unusual but possible; pipeline code is
responsible for any normalization before it hits this sink.

Read options (passed via ``**options`` to :meth:`read`):

* ``filter`` — Mongo find filter dict (default ``{}``).
* ``projection`` — Mongo projection dict (default ``None`` — return full
  documents).
* ``sort`` — list of ``(field, direction)`` tuples or a single
  ``(field, direction)`` tuple.
* ``limit`` — cap the cursor (default ``0`` = no limit).
* ``batch_size`` — wire-level batch size hint. Distinct from ``chunk_size``
  — both default to 10k and ``batch_size`` overrides if given.

Write modes (:meth:`write`):

* ``append`` (default) — ``insert_many`` with ``ordered=False`` so a single
  duplicate ``_id`` doesn't abort the rest of the batch.
* ``overwrite`` — ``drop_collection`` then ``insert_many``.
* ``upsert`` — bulk ``replace_one(filter=<keys>, upsert=True)`` per record.
  ``key_columns`` is required; documents must contain every key column.

Why pymongo (sync) and not motor (async): the rest of the BatchSource /
BatchSink stack is sync. Pipeline.run drives it from a thread, so a sync
driver is the right shape. Stream support (change streams) is a separate
slice and not covered here.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import pymongo
from pymongo.errors import BulkWriteError, ConnectionFailure, PyMongoError

from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConfigError, ConnectError, ReadError, WriteError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


@ConnectorRegistry.register("mongodb")
class MongoDBConnector(BatchSource, BatchSink):
    """MongoDB batch source + sink (sync, pymongo-backed)."""

    def __init__(
        self,
        uri: str = "",
        database: str = "",
        *,
        timeout_ms: int = 30_000,
        username: str | None = None,
        password: str | None = None,
        auth_source: str | None = None,
        tls: bool = False,
        # ``client`` is used by unit tests to inject a fake MongoClient;
        # production callers leave it None.
        client: Any | None = None,
        **extra: Any,
    ) -> None:
        if not uri:
            raise ConfigError("mongodb connector requires 'uri'")
        if not uri.startswith(("mongodb://", "mongodb+srv://")):
            raise ConfigError(
                f"mongodb connector: 'uri' must start with mongodb:// or mongodb+srv:// (got {uri!r})"
            )
        if not database:
            raise ConfigError("mongodb connector requires 'database'")
        self.uri = uri
        self.database_name = database
        self.timeout_ms = timeout_ms
        self.username = username
        self.password = password
        self.auth_source = auth_source
        self.tls = tls
        self._extra: dict[str, Any] = extra
        self._injected_client = client
        self._client: Any | None = None

    # --- Connector ABC ----------------------------------------------------

    def connect(self) -> None:
        if self._client is not None:
            return
        if self._injected_client is not None:
            self._client = self._injected_client
            return
        kwargs: dict[str, Any] = {
            "serverSelectionTimeoutMS": self.timeout_ms,
            "tls": self.tls,
        }
        if self.username is not None:
            kwargs["username"] = self.username
        if self.password is not None:
            kwargs["password"] = self.password
        if self.auth_source is not None:
            kwargs["authSource"] = self.auth_source
        kwargs.update(self._extra)
        try:
            self._client = pymongo.MongoClient(self.uri, **kwargs)
        except PyMongoError as e:  # pragma: no cover — defensive
            raise ConnectError(f"mongodb: failed to open client: {e}") from e

    def close(self) -> None:
        if self._client is None:
            return
        # Only close clients we created. Injected clients are owned by the
        # caller (test code or upstream wiring).
        if self._injected_client is None:
            self._client.close()
        self._client = None

    def health_check(self) -> bool:
        """Issue an ``admin.command('ping')`` against the bound client.

        Returns False if the connector has not been connected (mirrors the
        sqlite / postgres / mysql contract behavior).
        ConnectionFailure (server unreachable / auth failure on ping) also
        flips to False; everything else propagates so config errors aren't
        silently masked as health failures.
        """
        if self._client is None:
            return False
        try:
            self._client.admin.command("ping")
        except ConnectionFailure:
            return False
        return True

    @property
    def client(self) -> Any:
        """Underlying ``MongoClient``. Raises if not connected."""
        if self._client is None:
            raise ConnectError("MongoDBConnector is not connected")
        return self._client

    @property
    def database(self) -> Any:
        """Bound ``Database`` handle for ``self.database_name``."""
        return self.client[self.database_name]

    # --- BatchSource ------------------------------------------------------

    def read(
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        """Yield records from a collection.

        Parameters
        ----------
        query
            Collection name. Required.
        chunk_size
            Default wire-level batch size hint; overridden by
            ``options["batch_size"]`` if given. The Python iterator yields
            one record at a time regardless.
        options
            * ``filter``: Mongo find filter (default ``{}``).
            * ``projection``: Mongo projection dict.
            * ``sort``: ``(field, direction)`` tuple or list of tuples.
            * ``limit``: cap on documents returned (default 0 = unbounded).
            * ``batch_size``: wire batch hint, overrides ``chunk_size``.
        """
        if not query:
            raise ReadError("MongoDBConnector.read requires a collection name (query)")
        self.connect()
        coll = self.database[query]
        filter_ = options.get("filter") or {}
        projection = options.get("projection")
        sort_opt = options.get("sort")
        limit = int(options.get("limit", 0))
        batch_size = int(options.get("batch_size", chunk_size))

        try:
            cursor = coll.find(filter_, projection=projection, batch_size=batch_size)
            if sort_opt is not None:
                if isinstance(sort_opt, tuple):
                    cursor = cursor.sort([sort_opt])
                else:
                    cursor = cursor.sort(list(sort_opt))
            if limit > 0:
                cursor = cursor.limit(limit)
            for doc in cursor:
                yield Record(data=dict(doc), metadata={"source": "mongodb"})
        except PyMongoError as e:
            raise ReadError(f"mongodb: read from {query!r} failed: {e}") from e

    # --- BatchSource: cursored --------------------------------------------

    def read_since(
        self,
        cursor_column: str,
        cursor_value: Any,
        *,
        query: str | None = None,
        chunk_size: int = 10_000,
        **options: Any,
    ) -> Iterator[Record]:
        """Read documents whose ``cursor_column`` is strictly greater than
        ``cursor_value``, sorted ascending — Step 6.1 / ADR-0024 contract.

        ``query`` is the collection name (matching :meth:`read`). Any extra
        ``filter`` in ``options`` is merged into the cursor filter via an
        ``$and`` so users can still constrain the resultset further. Other
        read options (``projection``, ``batch_size``, ``limit``) are
        forwarded verbatim; the sort is forced to ``[(cursor_column, 1)]``
        to satisfy the contract.
        """
        if not query:
            raise ReadError("MongoDBConnector.read_since requires a collection name (query)")
        self.connect()
        coll = self.database[query]
        projection = options.get("projection")
        batch_size = int(options.get("batch_size", chunk_size))
        limit = int(options.get("limit", 0))

        cursor_filter: dict[str, Any] = {}
        if cursor_value is not None:
            cursor_filter = {cursor_column: {"$gt": cursor_value}}
        extra_filter = options.get("filter") or {}
        if extra_filter and cursor_filter:
            combined: dict[str, Any] = {"$and": [cursor_filter, extra_filter]}
        else:
            combined = extra_filter or cursor_filter

        try:
            mongo_cursor = coll.find(combined, projection=projection, batch_size=batch_size)
            mongo_cursor = mongo_cursor.sort([(cursor_column, 1)])
            if limit > 0:
                mongo_cursor = mongo_cursor.limit(limit)
            for doc in mongo_cursor:
                yield Record(
                    data=dict(doc),
                    metadata={"source": "mongodb", "cursor_column": cursor_column},
                )
        except PyMongoError as e:
            raise ReadError(f"mongodb: read_since on {query!r} failed: {e}") from e

    # --- BatchSink --------------------------------------------------------

    def write(
        self,
        records: Iterable[Record],
        *,
        mode: str = "append",
        key_columns: list[str] | None = None,
        **options: Any,
    ) -> int:
        """Write records to a collection.

        Parameters
        ----------
        records
            Iterable of :class:`Record`. Empty input is a no-op (returns 0).
        mode
            ``append`` / ``overwrite`` / ``upsert``.
        key_columns
            Required for ``upsert``. Each document must carry every key.
        options
            * ``table``: collection name. Required (forwarded by Pipeline as
              ``task.sink_table``).
            * ``batch_size``: max documents per insert / bulk call (default
              ``1000``).
            * ``ordered``: for append mode, whether duplicate-key errors
              abort the batch. Default ``False``.
        """
        table = options.get("table")
        if not table:
            raise WriteError("MongoDBConnector.write requires 'table' (collection name)")
        if mode not in ("append", "overwrite", "upsert"):
            raise WriteError(
                f"unknown write mode: {mode!r} (use 'append', 'overwrite', or 'upsert')"
            )
        if mode == "upsert" and not key_columns:
            raise WriteError("mode='upsert' requires non-empty 'key_columns'")

        batch_size = int(options.get("batch_size", 1000))
        ordered = bool(options.get("ordered", False))

        self.connect()
        coll = self.database[table]

        try:
            if mode == "overwrite":
                coll.drop()

            if mode == "upsert":
                assert key_columns is not None
                return self._bulk_upsert(coll, key_columns, records, batch_size)
            return self._bulk_insert(coll, records, batch_size, ordered=ordered)
        except BulkWriteError as e:
            raise WriteError(f"mongodb: bulk write to {table!r} failed: {e.details}") from e
        except PyMongoError as e:
            raise WriteError(f"mongodb: write to {table!r} failed: {e}") from e

    # --- helpers ----------------------------------------------------------

    def _bulk_insert(
        self,
        coll: Any,
        records: Iterable[Record],
        batch_size: int,
        *,
        ordered: bool,
    ) -> int:
        count = 0
        buf: list[dict[str, Any]] = []
        for record in records:
            buf.append(dict(record.data))
            if len(buf) >= batch_size:
                coll.insert_many(buf, ordered=ordered)
                count += len(buf)
                buf.clear()
        if buf:
            coll.insert_many(buf, ordered=ordered)
            count += len(buf)
        return count

    def _bulk_upsert(
        self,
        coll: Any,
        key_columns: list[str],
        records: Iterable[Record],
        batch_size: int,
    ) -> int:
        # pymongo.ReplaceOne is the bulk-friendly upsert primitive.
        from pymongo import ReplaceOne

        count = 0
        buf: list[Any] = []
        for record in records:
            data = dict(record.data)
            try:
                filter_ = {k: data[k] for k in key_columns}
            except KeyError as e:
                raise WriteError(f"mongodb upsert: record missing key column {e.args[0]!r}") from e
            buf.append(ReplaceOne(filter_, data, upsert=True))
            if len(buf) >= batch_size:
                result = coll.bulk_write(buf, ordered=False)
                count += result.upserted_count + result.modified_count
                buf.clear()
        if buf:
            result = coll.bulk_write(buf, ordered=False)
            count += result.upserted_count + result.modified_count
        return count


__all__ = ["MongoDBConnector"]
