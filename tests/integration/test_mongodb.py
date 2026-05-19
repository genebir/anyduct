"""MongoDB connector integration tests [Step 5.3].

Runs the standard BatchSource / BatchSink / RoundTrip contracts against a
real mongo container, plus mongo-specific tests for upsert / overwrite /
filter / sort / error paths.
"""

from __future__ import annotations

import pymongo
import pytest

from etl_plugins.connectors.nosql.mongodb import MongoDBConnector
from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.exceptions import ConnectError, ReadError, WriteError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry
from tests.contracts.batch import (
    _BatchRoundTripContract,
    _BatchSinkContract,
    _BatchSourceContract,
)
from tests.contracts.cursor import _BatchSourceCursorContract

pytestmark = pytest.mark.it


# ---------- helpers --------------------------------------------------------


class _StripIdConnector(MongoDBConnector):
    """Subclass that drops ``_id`` from read records, matching contract test
    expectations (which compare against ``sample_records`` payloads that
    have no ``_id``).

    Production callers usually want to *keep* ``_id`` (it's the document
    primary key); this subclass exists only so the off-the-shelf contract
    tests can be reused without forking them.
    """

    def read(self, query=None, *, chunk_size=10_000, **options):  # type: ignore[override, no-untyped-def]
        for r in super().read(query, chunk_size=chunk_size, **options):
            data = {k: v for k, v in r.data.items() if k != "_id"}
            yield Record(data=data, metadata=r.metadata, schema_version=r.schema_version)

    def read_since(  # type: ignore[override, no-untyped-def]
        self, cursor_column, cursor_value, *, query=None, chunk_size=10_000, **options
    ):
        for r in super().read_since(
            cursor_column, cursor_value, query=query, chunk_size=chunk_size, **options
        ):
            data = {k: v for k, v in r.data.items() if k != "_id"}
            yield Record(data=data, metadata=r.metadata, schema_version=r.schema_version)


# ---------- contract: BatchSource ----------


class TestMongoBatchSource(_BatchSourceContract):
    @pytest.fixture
    def source(self, mongo_uri: str, mongo_seeded: str) -> BatchSource:
        return _StripIdConnector(uri=mongo_uri, database="test")

    @pytest.fixture
    def seeded_records(self, sample_records: list[Record]) -> list[Record]:
        return sample_records

    @pytest.fixture
    def read_kwargs(self, mongo_seeded: str) -> dict[str, object]:
        return {"query": mongo_seeded, "sort": ("id", 1)}


# ---------- contract: BatchSink ----------


class TestMongoBatchSink(_BatchSinkContract):
    @pytest.fixture
    def sink(self, mongo_uri: str, mongo_collection: str) -> BatchSink:
        return _StripIdConnector(uri=mongo_uri, database="test")

    @pytest.fixture
    def write_kwargs(self, mongo_collection: str) -> dict[str, object]:
        return {"table": mongo_collection}


# ---------- contract: round-trip ----------


class TestMongoRoundTrip(_BatchRoundTripContract):
    @pytest.fixture
    def round_trip_connector(self, mongo_uri: str, mongo_collection: str) -> BatchSource:
        return _StripIdConnector(uri=mongo_uri, database="test")

    @pytest.fixture
    def read_kwargs(self, mongo_collection: str) -> dict[str, object]:
        return {"query": mongo_collection, "sort": ("id", 1)}

    @pytest.fixture
    def write_kwargs(self, mongo_collection: str) -> dict[str, object]:
        return {"table": mongo_collection}


# ---------- contract: cursored reads ----------


class TestMongoCursorReads(_BatchSourceCursorContract):
    @pytest.fixture
    def cursor_source(self, mongo_uri: str, mongo_seeded: str) -> BatchSource:
        return _StripIdConnector(uri=mongo_uri, database="test")

    @pytest.fixture
    def cursor_seeded_records(self, sample_records: list[Record]) -> list[Record]:
        return sample_records

    @pytest.fixture
    def cursor_column(self) -> str:
        return "id"

    @pytest.fixture
    def read_since_kwargs(self, mongo_seeded: str) -> dict[str, object]:
        return {"query": mongo_seeded}


# ---------- mongo-specific tests ----------


def test_registry_resolves_mongodb() -> None:
    klass = ConnectorRegistry.get("mongodb")
    assert klass is MongoDBConnector
    assert klass.name == "mongodb"


def test_health_check_false_before_connect(mongo_uri: str) -> None:
    m = MongoDBConnector(uri=mongo_uri, database="test")
    assert m.health_check() is False


def test_health_check_true_after_connect(mongo_uri: str) -> None:
    with MongoDBConnector(uri=mongo_uri, database="test") as m:
        assert m.health_check() is True


def test_health_check_false_on_bad_host() -> None:
    m = MongoDBConnector(
        uri="mongodb://localhost:1",  # nothing listening
        database="test",
        timeout_ms=500,
    )
    m.connect()
    try:
        assert m.health_check() is False
    finally:
        m.close()


def test_read_without_collection_raises(mongo_connector: MongoDBConnector) -> None:
    with mongo_connector, pytest.raises(ReadError, match="collection"):
        list(mongo_connector.read())


def test_filter_returns_matching_documents(mongo_uri: str, mongo_seeded: str) -> None:
    with MongoDBConnector(uri=mongo_uri, database="test") as m:
        records = list(m.read(query=mongo_seeded, filter={"active": True}))
    payloads = sorted(
        ({k: v for k, v in r.data.items() if k != "_id"} for r in records),
        key=lambda d: d["id"],
    )
    assert payloads == [
        {"id": 1, "name": "Alice", "age": 30, "active": True},
        {"id": 3, "name": "Carol", "age": 35, "active": True},
    ]


def test_sort_and_limit(mongo_uri: str, mongo_seeded: str) -> None:
    with MongoDBConnector(uri=mongo_uri, database="test") as m:
        records = list(m.read(query=mongo_seeded, sort=[("age", -1)], limit=2))
    ages = [r.data["age"] for r in records]
    assert ages == [35, 30]


def test_projection_drops_fields(mongo_uri: str, mongo_seeded: str) -> None:
    with MongoDBConnector(uri=mongo_uri, database="test") as m:
        records = list(m.read(query=mongo_seeded, projection={"name": 1, "_id": 0}))
    keys = {tuple(sorted(r.data.keys())) for r in records}
    assert keys == {("name",)}


def test_overwrite_drops_collection_first(
    mongo_uri: str, mongo_collection: str, sample_records: list[Record]
) -> None:
    # Seed with one document we don't want to see after overwrite.
    with pymongo.MongoClient(mongo_uri) as client:
        client["test"][mongo_collection].insert_one({"id": 999, "name": "stale"})

    with MongoDBConnector(uri=mongo_uri, database="test") as m:
        n = m.write(sample_records, mode="overwrite", table=mongo_collection)

    assert n == len(sample_records)
    with pymongo.MongoClient(mongo_uri) as client:
        docs = list(client["test"][mongo_collection].find({}, {"_id": 0}))
    ids = sorted(d["id"] for d in docs)
    assert ids == [1, 2, 3]  # 999 is gone


def test_upsert_inserts_new_and_updates_existing(mongo_uri: str, mongo_collection: str) -> None:
    # First write: insert.
    with MongoDBConnector(uri=mongo_uri, database="test") as m:
        m.write(
            [Record(data={"id": 1, "v": "a"}), Record(data={"id": 2, "v": "b"})],
            mode="upsert",
            key_columns=["id"],
            table=mongo_collection,
        )
    # Second write: id=1 update, id=3 insert.
    with MongoDBConnector(uri=mongo_uri, database="test") as m:
        m.write(
            [Record(data={"id": 1, "v": "A"}), Record(data={"id": 3, "v": "C"})],
            mode="upsert",
            key_columns=["id"],
            table=mongo_collection,
        )

    with pymongo.MongoClient(mongo_uri) as client:
        docs = list(client["test"][mongo_collection].find({}, {"_id": 0}).sort([("id", 1)]))
    assert docs == [
        {"id": 1, "v": "A"},
        {"id": 2, "v": "b"},
        {"id": 3, "v": "C"},
    ]


def test_upsert_rejects_record_missing_key_column(mongo_uri: str, mongo_collection: str) -> None:
    with (
        MongoDBConnector(uri=mongo_uri, database="test") as m,
        pytest.raises(WriteError, match="missing key column"),
    ):
        m.write(
            [Record(data={"v": "no id"})],
            mode="upsert",
            key_columns=["id"],
            table=mongo_collection,
        )


def test_write_without_table_raises(mongo_connector: MongoDBConnector) -> None:
    with mongo_connector, pytest.raises(WriteError, match="table"):
        mongo_connector.write([Record(data={"x": 1})], mode="append")


def test_bad_connection_uri_raises_on_read(mongo_uri: str) -> None:
    """Connector connects lazily; the failure should surface as ReadError
    once we actually try to talk to the server."""
    m = MongoDBConnector(
        uri="mongodb://localhost:1",  # nothing listening
        database="test",
        timeout_ms=500,
    )
    with m, pytest.raises(ReadError):
        list(m.read(query="nope"))


def test_round_trip_via_pipeline_records_unchanged(mongo_uri: str, mongo_collection: str) -> None:
    """Write records then read them back; payloads (minus _id) survive."""
    records = [
        Record(data={"id": 1, "name": "alpha", "nested": {"k": [1, 2, 3]}}),
        Record(data={"id": 2, "name": "beta", "nested": {"k": []}}),
    ]
    with MongoDBConnector(uri=mongo_uri, database="test") as m:
        m.write(records, mode="append", table=mongo_collection)

    with MongoDBConnector(uri=mongo_uri, database="test") as m:
        read_back = list(m.read(query=mongo_collection, sort=("id", 1)))

    cleaned = [{k: v for k, v in r.data.items() if k != "_id"} for r in read_back]
    assert cleaned == [r.data for r in records]


def test_client_property_raises_when_not_connected(mongo_uri: str) -> None:
    m = MongoDBConnector(uri=mongo_uri, database="test")
    with pytest.raises(ConnectError, match="not connected"):
        _ = m.client


def test_uri_with_unreachable_host_does_not_block_close() -> None:
    """``close()`` should be safe even if the client never received a response."""
    m = MongoDBConnector(
        uri="mongodb://localhost:1",
        database="test",
        timeout_ms=500,
    )
    m.connect()
    m.close()  # must not hang or raise
    assert m._client is None  # type: ignore[unreachable]


def test_extra_kwargs_forwarded_to_mongo_client(mongo_uri: str) -> None:
    """Unknown kwargs should reach MongoClient as-is (e.g. ``appname``)."""
    m = MongoDBConnector(
        uri=mongo_uri,
        database="test",
        appname="etl-test-suite",  # forwarded via **extra
    )
    with m:
        # If MongoClient had rejected the kwarg, connect() would have raised.
        assert m.health_check() is True


def test_concurrent_collections_isolated(mongo_uri: str, mongo_collection: str) -> None:
    """Writes to one collection don't leak into another in the same database."""
    other = mongo_collection + "_other"
    with MongoDBConnector(uri=mongo_uri, database="test") as m:
        m.write([Record(data={"id": 1})], mode="append", table=mongo_collection)
        m.write([Record(data={"id": 99})], mode="append", table=other)
    with pymongo.MongoClient(mongo_uri) as client:
        a = sorted(d["id"] for d in client["test"][mongo_collection].find({}, {"_id": 0}))
        b = sorted(d["id"] for d in client["test"][other].find({}, {"_id": 0}))
    try:
        assert a == [1]
        assert b == [99]
    finally:
        with pymongo.MongoClient(mongo_uri) as client:
            client["test"].drop_collection(other)


def test_explicit_id_round_trips_unchanged(mongo_uri: str, mongo_collection: str) -> None:
    """When a record carries its own ``_id``, Mongo must honor it."""
    with MongoDBConnector(uri=mongo_uri, database="test") as m:
        m.write(
            [Record(data={"_id": "my-key", "v": 1})],
            mode="append",
            table=mongo_collection,
        )
        read_back = list(m.read(query=mongo_collection))
    assert read_back[0].data["_id"] == "my-key"
    assert read_back[0].data["v"] == 1
