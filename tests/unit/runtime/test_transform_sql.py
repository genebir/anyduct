"""Unit tests for the dataset-level ``sql`` transform (DuckDB; ADR-0093).

Covers the transform itself (aggregate / window / join-in-SQL / empty
input / bad SQL), its staged composition inside ``Pipeline._run_task``
(row transforms before AND after a dataset stage), the graph-node path,
and the batch-only contract (stream mode rejects it).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from etl_plugins.config.models import TransformConfig
from etl_plugins.connectors.rdbms.sqlite import SQLiteConnector
from etl_plugins.core.exceptions import ConfigError, TaskError, TransformError
from etl_plugins.core.pipeline import Pipeline, Task, is_dataset_transform
from etl_plugins.core.record import Record
from etl_plugins.runtime.transforms import build_transform


def _records(rows: list[dict[str, object]]) -> Iterator[Record]:
    return iter([Record(data=dict(r)) for r in rows])


def _sql(query: str, **extra: object) -> object:
    return build_transform(TransformConfig(type="sql", query=query, **extra))


class TestSqlTransform:
    def test_is_dataset_transform(self) -> None:
        fn = _sql("SELECT * FROM input")
        assert is_dataset_transform(fn)  # type: ignore[arg-type]
        rename = build_transform(TransformConfig(type="rename", mapping={"a": "b"}))
        assert not is_dataset_transform(rename)  # type: ignore[arg-type]

    def test_aggregate_group_by(self) -> None:
        fn = _sql(
            "SELECT region, SUM(amount) AS total, COUNT(*) AS n "
            "FROM input GROUP BY region ORDER BY region"
        )
        out = list(
            fn(  # type: ignore[operator]
                _records(
                    [
                        {"region": "EU", "amount": 10},
                        {"region": "US", "amount": 5},
                        {"region": "EU", "amount": 7},
                    ]
                )
            )
        )
        assert [r.data for r in out] == [
            {"region": "EU", "total": 17, "n": 2},
            {"region": "US", "total": 5, "n": 1},
        ]

    def test_window_function(self) -> None:
        fn = _sql(
            "SELECT id, amount, ROW_NUMBER() OVER (ORDER BY amount DESC) AS rank_no "
            "FROM input QUALIFY rank_no <= 2 ORDER BY rank_no"
        )
        out = list(fn(_records([{"id": i, "amount": i * 10} for i in range(1, 6)])))  # type: ignore[operator]
        assert [(r.data["id"], r.data["rank_no"]) for r in out] == [(5, 1), (4, 2)]

    def test_custom_view_name(self) -> None:
        fn = _sql("SELECT COUNT(*) AS n FROM rows_in", view="rows_in")
        out = list(fn(_records([{"a": 1}, {"a": 2}])))  # type: ignore[operator]
        assert out[0].data == {"n": 2}

    def test_empty_input_yields_nothing(self) -> None:
        fn = _sql("SELECT COUNT(*) AS n FROM input")
        assert list(fn(_records([]))) == []  # type: ignore[operator]

    def test_invalid_sql_raises_transform_error(self) -> None:
        fn = _sql("SELECT FROM WHERE nope")
        with pytest.raises(TransformError, match="query failed"):
            list(fn(_records([{"a": 1}])))  # type: ignore[operator]

    def test_missing_query_rejected(self) -> None:
        with pytest.raises(ConfigError):
            build_transform(TransformConfig(type="sql"))

    def test_bad_view_identifier_rejected(self) -> None:
        with pytest.raises(ConfigError, match="identifier"):
            build_transform(TransformConfig(type="sql", query="SELECT 1", view="in put; DROP"))


class TestPipelineStaging:
    """Row transforms before AND after a dataset stage compose correctly."""

    def _run(self, tmp_path: object, transforms: list[TransformConfig]) -> list[tuple]:
        src = f"{tmp_path}/src.db"
        dst = f"{tmp_path}/dst.db"
        con = sqlite3.connect(src)
        con.execute("CREATE TABLE t (region TEXT, amount INTEGER)")
        con.executemany(
            "INSERT INTO t VALUES (?,?)",
            [("EU", 10), ("US", 5), ("EU", 7), ("APAC", 1)],
        )
        con.commit()
        con.close()
        sc = SQLiteConnector(database=src)
        kc = SQLiteConnector(database=dst)
        sc.connect()
        kc.connect()
        kc.execute_statement("CREATE TABLE out (region TEXT, total REAL)")
        task = Task(
            name="agg",
            source="src",
            sink="dst",
            query="SELECT * FROM t",
            sink_table="out",
            transforms=[build_transform(tc) for tc in transforms],
        )
        Pipeline(name="p", tasks=[task]).run(connectors={"src": sc, "dst": kc})
        rows = (
            sqlite3.connect(dst).execute("SELECT region, total FROM out ORDER BY region").fetchall()
        )
        sc.close()
        kc.close()
        return rows

    def test_sql_stage_between_row_stages(self, tmp_path: object) -> None:
        rows = self._run(
            tmp_path,
            [
                # row stage 1: drop APAC before the aggregate
                TransformConfig(type="filter", expr="data['region'] != 'APAC'"),
                # dataset stage: aggregate
                TransformConfig(
                    type="sql",
                    query="SELECT region, SUM(amount) AS total FROM input GROUP BY region",
                ),
                # row stage 2: post-aggregate rename keeps flowing per-row
                TransformConfig(type="rename", mapping={"total": "total"}),
            ],
        )
        assert rows == [("EU", 17), ("US", 5)]

    def test_sql_only(self, tmp_path: object) -> None:
        rows = self._run(
            tmp_path,
            [
                TransformConfig(
                    type="sql",
                    query="SELECT region, SUM(amount) AS total FROM input GROUP BY region",
                )
            ],
        )
        assert rows == [("APAC", 1.0), ("EU", 17), ("US", 5)]


class TestGraphNode:
    def test_graph_transform_node_runs_dataset_sql(self) -> None:
        from etl_plugins.core.pipeline import GraphNode, execute_graph_node

        fn = _sql("SELECT region, COUNT(*) AS n FROM input GROUP BY region ORDER BY region")
        node = GraphNode(id="agg", kind="transform", transform_fn=fn)  # type: ignore[arg-type]
        recs = [Record(data={"region": "EU"}), Record(data={"region": "EU"})]
        result = execute_graph_node(node, [recs], {})
        assert [r.data for r in result.output] == [{"region": "EU", "n": 2}]


class TestStreamRejection:
    @pytest.mark.asyncio
    async def test_stream_task_rejects_dataset_transform(self) -> None:
        from etl_plugins.core.connector import StreamSink, StreamSource

        class _FakeStreamSource(StreamSource):
            def connect(self) -> None: ...
            def close(self) -> None: ...
            def health_check(self) -> bool:
                return True

            async def subscribe(self, topic: str, group_id: str | None = None):  # type: ignore[override]
                yield Record(data={})

            async def commit(self) -> None: ...

        class _FakeStreamSink(StreamSink):
            def connect(self) -> None: ...
            def close(self) -> None: ...
            def health_check(self) -> bool:
                return True

            async def publish(self, topic: str, record: Record) -> None: ...
            async def flush(self) -> None: ...

        task = Task(
            name="s",
            source="src",
            sink="dst",
            source_options={"topic": "in"},
            sink_options={"topic": "out"},
            transforms=[_sql("SELECT * FROM input")],  # type: ignore[list-item]
        )
        p = Pipeline(name="p", mode="stream", tasks=[task])
        with pytest.raises(TaskError, match="batch mode"):
            await p.arun_stream(connectors={"src": _FakeStreamSource(), "dst": _FakeStreamSink()})
