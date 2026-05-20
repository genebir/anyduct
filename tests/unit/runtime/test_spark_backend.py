"""Spark backend (ADR-0031/0032).

Predicate-translation tests are pure Python and always run. The execution tests
need ``pyspark`` + a JVM (``JAVA_HOME``/``java`` on PATH) and are skipped
otherwise — in CI the ``[spark]`` extra + a JDK enable them (ADR-0032 ops).
"""

from __future__ import annotations

import os
import shutil

import pytest

from etl_plugins.config.models import ConnectionConfig, PipelineConfig
from etl_plugins.core.exceptions import ConfigError
from etl_plugins.runtime.spark.backend import SparkBackend
from etl_plugins.runtime.spark.predicate import to_spark_sql

# ---------- predicate translation (pure, always runs) ----------


def test_translate_comparison() -> None:
    assert to_spark_sql("data['amount'] > 0") == "`amount` > 0"


def test_translate_string_eq_escapes() -> None:
    assert to_spark_sql("data['t'] == 'a'") == "`t` = 'a'"
    assert to_spark_sql("data['n'] == \"o'brien\"") == "`n` = 'o''brien'"


def test_translate_and_join() -> None:
    out = to_spark_sql("data['a'] >= 1 and data['b'] == 'x'")
    assert out == "`a` >= 1 AND `b` = 'x'"


def test_translate_empty_notempty() -> None:
    assert to_spark_sql("not data['c']") == "`c` IS NULL"
    assert to_spark_sql("data['c']") == "`c` IS NOT NULL"


def test_translate_contains() -> None:
    assert to_spark_sql("'foo' in data['name']") == "`name` LIKE '%foo%'"


def test_translate_bool_and_none() -> None:
    assert to_spark_sql("data['ok'] == true") == "`ok` = true"
    assert to_spark_sql("data['x'] == None") == "`x` IS NULL"


def test_translate_empty_is_none() -> None:
    assert to_spark_sql("") is None
    assert to_spark_sql(None) is None


def test_translate_unsupported_raises() -> None:
    with pytest.raises(ValueError, match="cannot translate"):
        to_spark_sql("len(data['x']) > 3")  # function call — not in grammar


def test_apply_transform_rejects_python() -> None:
    from etl_plugins.config.models import TransformConfig
    from etl_plugins.runtime.spark.backend import apply_transform

    with pytest.raises(ConfigError, match="does not support the 'python' transform"):
        apply_transform(
            object(), TransformConfig.model_validate({"type": "python", "callable": "m:f"})
        )  # type: ignore[arg-type]


# ---------- Spark execution (gated on pyspark + JVM, per-test via fixture) ----------


@pytest.fixture(scope="module")
def spark():  # type: ignore[no-untyped-def]
    pytest.importorskip("pyspark")
    if not (os.environ.get("JAVA_HOME") or shutil.which("java")):
        pytest.skip("no JVM (JAVA_HOME/java) for Spark execution tests")
    from pyspark.sql import SparkSession

    s = (
        SparkSession.builder.master("local[1]")
        .appName("etlx-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .getOrCreate()
    )
    yield s
    s.stop()


def _conn(path: str) -> ConnectionConfig:
    return ConnectionConfig.model_validate({"type": "file", "path": path, "format": "parquet"})


def _seed_parquet(spark, path: str, rows: list[dict]) -> None:  # type: ignore[no-untyped-def]
    spark.createDataFrame(rows).write.mode("overwrite").parquet(path)


def test_spark_linear_pipeline(spark, tmp_path) -> None:  # type: ignore[no-untyped-def]
    src = str(tmp_path / "in")
    out = str(tmp_path / "out")
    _seed_parquet(
        spark, src, [{"id": 1, "amount": 5}, {"id": 2, "amount": 0}, {"id": 3, "amount": 9}]
    )

    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "engine": "spark",
            "source": {"connection": "src"},
            "transforms": [{"type": "filter", "expr": "data['amount'] > 0"}],
            "sink": {"connection": "dst"},
        }
    )
    backend = SparkBackend(spark=spark)
    result = backend.run(cfg, connections={"src": _conn(src), "dst": _conn(out)})
    assert result.success is True
    assert result.records_read == 3
    assert result.records_written == 2
    ids = sorted(r["id"] for r in spark.read.parquet(out).collect())
    assert ids == [1, 3]


def test_spark_fanout_branch_by_when(spark, tmp_path) -> None:  # type: ignore[no-untyped-def]
    src = str(tmp_path / "in")
    hi, lo = str(tmp_path / "hi"), str(tmp_path / "lo")
    _seed_parquet(
        spark, src, [{"id": 1, "kind": "hi"}, {"id": 2, "kind": "lo"}, {"id": 3, "kind": "hi"}]
    )

    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "engine": "spark",
            "source": {"connection": "src"},
            "sinks": [
                {"connection": "hi", "when": "data['kind'] == 'hi'"},
                {"connection": "lo", "when": "data['kind'] == 'lo'"},
            ],
        }
    )
    backend = SparkBackend(spark=spark)
    result = backend.run(cfg, connections={"src": _conn(src), "hi": _conn(hi), "lo": _conn(lo)})
    assert result.success is True
    # source read once (cached), branches share the scan.
    assert result.records_read == 3
    assert sorted(r["id"] for r in spark.read.parquet(hi).collect()) == [1, 3]
    assert sorted(r["id"] for r in spark.read.parquet(lo).collect()) == [2]


def test_spark_transforms(spark, tmp_path) -> None:  # type: ignore[no-untyped-def]
    src = str(tmp_path / "in")
    out = str(tmp_path / "out")
    _seed_parquet(spark, src, [{"a": 1, "b": "x"}, {"a": 1, "b": "x"}, {"a": 2, "b": "y"}])
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "engine": "spark",
            "source": {"connection": "src"},
            "transforms": [
                {"type": "dedupe", "key_columns": ["a", "b"]},
                {"type": "add_constant", "column": "src_sys", "value": "etl"},
                {"type": "select", "columns": ["a", "src_sys"]},
            ],
            "sink": {"connection": "dst"},
        }
    )
    backend = SparkBackend(spark=spark)
    result = backend.run(cfg, connections={"src": _conn(src), "dst": _conn(out)})
    assert result.records_written == 2
    rows = spark.read.parquet(out).collect()
    assert all(r["src_sys"] == "etl" for r in rows)
    assert set(rows[0].asDict().keys()) == {"a", "src_sys"}


def test_spark_graph_branch_mid_pipeline(spark, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Dataflow graph on Spark (ADR-0030/P3.3): source → transform → branch to 2 sinks."""
    src = str(tmp_path / "in")
    big, small = str(tmp_path / "big"), str(tmp_path / "small")
    _seed_parquet(spark, src, [{"id": 1, "n": 5}, {"id": 2, "n": 0}, {"id": 3, "n": 9}])
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "engine": "spark",
            "graph": {
                "nodes": [
                    {"id": "s", "type": "source", "connection": "src"},
                    {
                        "id": "t",
                        "type": "transform",
                        "transform": {"type": "add_constant", "column": "tag", "value": "x"},
                    },
                    {"id": "big", "type": "sink", "connection": "big"},
                    {"id": "small", "type": "sink", "connection": "small"},
                ],
                "edges": [
                    {"from_node": "s", "to_node": "t"},
                    {"from_node": "t", "to_node": "big", "when": "data['n'] >= 5"},
                    {"from_node": "t", "to_node": "small", "when": "data['n'] < 5"},
                ],
            },
        }
    )
    backend = SparkBackend(spark=spark)
    result = backend.run(
        cfg, connections={"src": _conn(src), "big": _conn(big), "small": _conn(small)}
    )
    assert result.success is True and result.records_read == 3
    big_rows = spark.read.parquet(big).collect()
    small_rows = spark.read.parquet(small).collect()
    assert sorted(r["id"] for r in big_rows) == [1, 3]
    assert sorted(r["id"] for r in small_rows) == [2]
    # the transform applied on the branch path
    assert all(r["tag"] == "x" for r in big_rows + small_rows)


def test_jdbc_url_and_packages() -> None:
    from etl_plugins.runtime.spark.backend import jdbc_packages, jdbc_url

    assert jdbc_url("postgres", {"host": "db", "port": 5432, "database": "etl"}) == (
        "jdbc:postgresql://db:5432/etl"
    )
    assert jdbc_url("mysql", {"host": "h", "database": "d"}) == "jdbc:mysql://h:3306/d"
    pkgs = jdbc_packages(
        {
            "a": ConnectionConfig.model_validate({"type": "postgres", "database": "x"}),
            "b": ConnectionConfig.model_validate({"type": "mysql", "database": "y"}),
            "c": ConnectionConfig.model_validate({"type": "file", "path": "/tmp"}),
        }
    )
    assert "org.postgresql:postgresql" in pkgs and "mysql-connector-j" in pkgs
