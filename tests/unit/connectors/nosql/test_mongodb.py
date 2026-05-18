"""Unit tests for MongoDBConnector (Step 5.3).

Real pymongo behavior is exercised by ``tests/integration/test_mongodb.py``
against a testcontainers MongoDB. These unit tests focus on construction
validation, option handling, mode dispatch, and error mapping — using a
hand-rolled fake client injected via the ``client=`` constructor kwarg so
we don't depend on mongomock.
"""

from __future__ import annotations

from typing import Any

import pytest
from pymongo.errors import (
    BulkWriteError,
    ConnectionFailure,
    DuplicateKeyError,
)

from etl_plugins.connectors.nosql import MongoDBConnector
from etl_plugins.core.exceptions import ConfigError, ConnectError, ReadError, WriteError
from etl_plugins.core.record import Record

# --- fakes -----------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = list(docs)
        self._limit: int | None = None
        self._sort: list[tuple[str, int]] | None = None
        self.batch_size_seen: int | None = None

    def __iter__(self) -> Any:
        docs = self._docs
        if self._sort:
            for field, direction in reversed(self._sort):
                docs = sorted(docs, key=lambda d: d.get(field), reverse=(direction < 0))
        if self._limit is not None:
            docs = docs[: self._limit]
        return iter(docs)

    def sort(self, spec: list[tuple[str, int]]) -> _FakeCursor:
        self._sort = list(spec)
        return self

    def limit(self, n: int) -> _FakeCursor:
        self._limit = n
        return self


class _FakeCollection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.docs: list[dict[str, Any]] = []
        self.insert_calls: list[tuple[list[dict[str, Any]], bool]] = []
        self.bulk_calls: list[list[Any]] = []
        self.dropped: bool = False
        # By default ``find`` returns whatever was seeded into ``docs``; tests
        # can override by setting ``_find_docs`` directly.
        self._find_docs: list[dict[str, Any]] | None = None
        self.find_filter: dict[str, Any] | None = None
        self.find_projection: dict[str, Any] | None = None
        self.find_batch_size: int | None = None
        # Make insert_many raise on demand.
        self.insert_raises: Exception | None = None

    def find(
        self,
        filter_: dict[str, Any],
        *,
        projection: Any = None,
        batch_size: int = 0,
    ) -> _FakeCursor:
        self.find_filter = filter_
        self.find_projection = projection
        self.find_batch_size = batch_size
        source = self.docs if self._find_docs is None else self._find_docs
        return _FakeCursor(source)

    def insert_many(self, docs: list[dict[str, Any]], *, ordered: bool = True) -> Any:
        if self.insert_raises is not None:
            raise self.insert_raises
        self.insert_calls.append((list(docs), ordered))
        self.docs.extend(docs)
        return type("R", (), {"inserted_ids": [d.get("_id") for d in docs]})()

    def bulk_write(self, ops: list[Any], *, ordered: bool = False) -> Any:
        self.bulk_calls.append(list(ops))
        # Count upserts vs updates by examining the ReplaceOne payload via
        # the public _filter / _doc / _upsert attributes (pymongo public API
        # is dataclass-ish but we just need totals here).
        upserted = sum(1 for op in ops if getattr(op, "_upsert", False))
        return type("R", (), {"upserted_count": upserted, "modified_count": 0})()

    def drop(self) -> None:
        self.dropped = True
        self.docs.clear()


class _FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        if name not in self.collections:
            self.collections[name] = _FakeCollection(name)
        return self.collections[name]


class _FakeAdmin:
    def __init__(self, ping_response: Any = None, ping_error: Exception | None = None) -> None:
        self._response = ping_response if ping_response is not None else {"ok": 1.0}
        self._error = ping_error
        self.ping_calls = 0

    def command(self, cmd: str) -> Any:
        self.ping_calls += 1
        if self._error is not None:
            raise self._error
        return self._response


class _FakeMongoClient:
    def __init__(
        self,
        *,
        ping_error: Exception | None = None,
    ) -> None:
        self.admin = _FakeAdmin(ping_error=ping_error)
        self.databases: dict[str, _FakeDatabase] = {}
        self.closed = False

    def __getitem__(self, name: str) -> _FakeDatabase:
        if name not in self.databases:
            self.databases[name] = _FakeDatabase()
        return self.databases[name]

    def close(self) -> None:
        self.closed = True


# --- construction ----------------------------------------------------------


def test_rejects_missing_uri() -> None:
    with pytest.raises(ConfigError, match="uri"):
        MongoDBConnector(uri="", database="db")


def test_rejects_non_mongo_scheme() -> None:
    with pytest.raises(ConfigError, match="mongodb://"):
        MongoDBConnector(uri="http://example.com", database="db")


def test_rejects_missing_database() -> None:
    with pytest.raises(ConfigError, match="database"):
        MongoDBConnector(uri="mongodb://localhost:27017", database="")


def test_accepts_mongodb_srv_scheme() -> None:
    conn = MongoDBConnector(
        uri="mongodb+srv://cluster.example.net",
        database="prod",
        client=_FakeMongoClient(),
    )
    conn.connect()
    conn.close()


# --- lifecycle -------------------------------------------------------------


def test_injected_client_not_closed_on_close() -> None:
    fake = _FakeMongoClient()
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    with conn:
        pass
    assert fake.closed is False


def test_client_property_raises_when_not_connected() -> None:
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=_FakeMongoClient())
    with pytest.raises(ConnectError, match="not connected"):
        _ = conn.client


# --- health check ----------------------------------------------------------


def test_health_check_returns_false_before_connect() -> None:
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=_FakeMongoClient())
    assert conn.health_check() is False


def test_health_check_returns_true_after_connect() -> None:
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=_FakeMongoClient())
    with conn:
        assert conn.health_check() is True
    assert conn.health_check() is False  # closed


def test_health_check_returns_false_on_connection_failure() -> None:
    fake = _FakeMongoClient(ping_error=ConnectionFailure("server down"))
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    with conn:
        assert conn.health_check() is False


# --- read ------------------------------------------------------------------


def test_read_requires_collection_name() -> None:
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=_FakeMongoClient())
    with conn, pytest.raises(ReadError, match="collection"):
        list(conn.read())


def test_read_yields_records_from_cursor() -> None:
    fake = _FakeMongoClient()
    fake["d"]["users"].docs = [{"_id": 1, "name": "a"}, {"_id": 2, "name": "b"}]
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    with conn:
        records = list(conn.read(query="users"))
    assert [r.data for r in records] == [
        {"_id": 1, "name": "a"},
        {"_id": 2, "name": "b"},
    ]
    assert records[0].metadata["source"] == "mongodb"


def test_read_passes_filter_projection_and_batch_size() -> None:
    fake = _FakeMongoClient()
    coll = fake["d"]["users"]
    coll.docs = [{"_id": 1}]
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    with conn:
        list(
            conn.read(
                query="users",
                chunk_size=5000,
                filter={"active": True},
                projection={"name": 1},
                batch_size=250,
            )
        )
    assert coll.find_filter == {"active": True}
    assert coll.find_projection == {"name": 1}
    assert coll.find_batch_size == 250


def test_read_applies_sort_and_limit() -> None:
    fake = _FakeMongoClient()
    coll = fake["d"]["events"]
    coll.docs = [
        {"_id": 1, "ts": 3},
        {"_id": 2, "ts": 1},
        {"_id": 3, "ts": 2},
    ]
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    with conn:
        records = list(conn.read(query="events", sort=("ts", 1), limit=2))
    assert [r.data["ts"] for r in records] == [1, 2]


def test_read_chunk_size_used_when_batch_size_unset() -> None:
    fake = _FakeMongoClient()
    coll = fake["d"]["users"]
    coll.docs = [{"_id": 1}]
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    with conn:
        list(conn.read(query="users", chunk_size=777))
    assert coll.find_batch_size == 777


# --- write -----------------------------------------------------------------


def test_write_requires_table_option() -> None:
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=_FakeMongoClient())
    with conn, pytest.raises(WriteError, match="table"):
        conn.write([Record(data={"a": 1})], mode="append")


def test_write_rejects_unknown_mode() -> None:
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=_FakeMongoClient())
    with conn, pytest.raises(WriteError, match="unknown write mode"):
        conn.write([Record(data={"a": 1})], mode="merge", table="t")


def test_write_upsert_requires_key_columns() -> None:
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=_FakeMongoClient())
    with conn, pytest.raises(WriteError, match="key_columns"):
        conn.write([Record(data={"a": 1})], mode="upsert", table="t")


def test_write_append_calls_insert_many_with_unordered() -> None:
    fake = _FakeMongoClient()
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    with conn:
        n = conn.write(
            [Record(data={"_id": 1}), Record(data={"_id": 2})],
            mode="append",
            table="users",
        )
    coll = fake["d"]["users"]
    assert n == 2
    assert len(coll.insert_calls) == 1
    docs, ordered = coll.insert_calls[0]
    assert ordered is False
    assert [d["_id"] for d in docs] == [1, 2]


def test_write_append_respects_batch_size() -> None:
    fake = _FakeMongoClient()
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    records = [Record(data={"_id": i}) for i in range(5)]
    with conn:
        n = conn.write(records, mode="append", table="users", batch_size=2)
    assert n == 5
    coll = fake["d"]["users"]
    # 5 records / batch 2 = 3 calls (2, 2, 1)
    assert [len(call[0]) for call in coll.insert_calls] == [2, 2, 1]


def test_write_empty_iterable_is_noop() -> None:
    fake = _FakeMongoClient()
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    with conn:
        n = conn.write([], mode="append", table="users")
    assert n == 0
    assert fake["d"]["users"].insert_calls == []


def test_write_overwrite_drops_then_inserts() -> None:
    fake = _FakeMongoClient()
    fake["d"]["users"].docs = [{"_id": 99}]  # pre-existing row
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    with conn:
        n = conn.write([Record(data={"_id": 1})], mode="overwrite", table="users")
    coll = fake["d"]["users"]
    assert n == 1
    assert coll.dropped is True
    assert [d["_id"] for d in coll.docs] == [1]


def test_write_upsert_emits_replace_one_bulk() -> None:
    fake = _FakeMongoClient()
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    with conn:
        n = conn.write(
            [Record(data={"_id": 1, "name": "a"}), Record(data={"_id": 2, "name": "b"})],
            mode="upsert",
            key_columns=["_id"],
            table="users",
        )
    coll = fake["d"]["users"]
    assert n == 2  # upserted_count from fake
    assert len(coll.bulk_calls) == 1
    ops = coll.bulk_calls[0]
    assert len(ops) == 2
    # Each ReplaceOne carries _filter (private but stable in pymongo)
    assert [op._filter for op in ops] == [{"_id": 1}, {"_id": 2}]


def test_write_upsert_rejects_record_missing_key() -> None:
    fake = _FakeMongoClient()
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    with conn, pytest.raises(WriteError, match="missing key column"):
        conn.write(
            [Record(data={"name": "no-id"})],
            mode="upsert",
            key_columns=["_id"],
            table="users",
        )


def test_write_wraps_bulk_write_error() -> None:
    fake = _FakeMongoClient()
    coll = fake["d"]["users"]
    coll.insert_raises = BulkWriteError({"writeErrors": [{"code": 11000}]})
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    with conn, pytest.raises(WriteError, match="bulk write"):
        conn.write([Record(data={"_id": 1})], mode="append", table="users")


def test_write_wraps_other_pymongo_errors() -> None:
    fake = _FakeMongoClient()
    coll = fake["d"]["users"]
    coll.insert_raises = DuplicateKeyError("dup")
    conn = MongoDBConnector(uri="mongodb://x", database="d", client=fake)
    with conn, pytest.raises(WriteError, match="write to 'users'"):
        conn.write([Record(data={"_id": 1})], mode="append", table="users")


# --- registry --------------------------------------------------------------


def test_registered_as_mongodb() -> None:
    from etl_plugins.core.registry import ConnectorRegistry

    cls = ConnectorRegistry.get("mongodb")
    assert cls is MongoDBConnector
