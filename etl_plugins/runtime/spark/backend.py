"""Spark ExecutionBackend (ADR-0031/0032).

Compiles a pipeline (single-task **or** dataflow graph) to Spark DataFrame
operations and runs it on a ``SparkSession``. For fan-out / branching the
post-source DataFrame is ``cache()``d, so the source is read **once** and the
branches share the scan — the re-read problem of the local engine (ADR-0026/0030)
is gone on Spark.

Deployment / minimal-config (ADR-0032): JDBC driver JARs are auto-fetched via
``spark.jars.packages`` from the connectors a pipeline uses — operators don't
hand-wire JARs. The only host requirement is a JVM (a portable JRE + the
``[spark]`` extra is enough for ``local[*]``; clusters bring their own Spark).

v1 scope (parquet/csv/json + JDBC postgres/mysql; declarative transforms;
single source). upsert/MERGE, cursor backfill, multi-task DAG, and cluster
submission are later slices.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

from etl_plugins.config.models import ConnectionConfig, PipelineConfig, TransformConfig
from etl_plugins.core.connector import Connector
from etl_plugins.core.context import Context
from etl_plugins.core.cursor import CursorValue
from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.pipeline import RunResult
from etl_plugins.runtime.backends import ExecutionBackend
from etl_plugins.runtime.spark.predicate import to_spark_sql

if TYPE_CHECKING:
    from pyspark.sql import DataFrame, SparkSession

_CAST_TYPES = {
    "int": "int",
    "int64": "bigint",
    "float": "double",
    "float64": "double",
    "str": "string",
    "string": "string",
    "bool": "boolean",
    "timestamp": "timestamp",
}
_FILE_FORMATS = {"parquet", "csv", "json"}

# connector type → (jdbc url scheme, default port, Maven coordinate for auto-fetch)
_JDBC = {
    "postgres": ("jdbc:postgresql", 5432, "org.postgresql:postgresql:42.7.4"),
    "mysql": ("jdbc:mysql", 3306, "com.mysql:mysql-connector-j:9.0.0"),
}


def jdbc_url(conn_type: str, opts: dict[str, Any]) -> str:
    """Build a JDBC URL from connection options (pure; unit-tested)."""
    scheme, default_port, _ = _JDBC[conn_type]
    host = opts.get("host", "localhost")
    port = opts.get("port", default_port)
    db = opts.get("database") or opts.get("dbname") or opts.get("db")
    if not db:
        raise ConfigError(f"jdbc connection needs a 'database' (type={conn_type})")
    return f"{scheme}://{host}:{port}/{db}"


def jdbc_packages(connections: dict[str, ConnectionConfig]) -> str:
    """Comma-joined Maven coords for the JDBC drivers used — auto-fetched by Spark.

    This is the "no manual JAR" piece (ADR-0032): we know each connector's
    driver coordinate, so the user configures nothing.
    """
    coords: list[str] = []
    for conn in connections.values():
        spec = _JDBC.get(conn.type)
        if spec and spec[2] not in coords:
            coords.append(spec[2])
    return ",".join(coords)


def apply_transform(df: DataFrame, tc: TransformConfig) -> DataFrame:
    """Apply one declarative transform as Spark DataFrame ops."""
    from pyspark.sql import functions as F  # noqa: N812 — `F` is the Spark convention

    d: dict[str, Any] = tc.model_dump()
    t = tc.type
    if t == "rename":
        for old, new in (d.get("mapping") or {}).items():
            df = df.withColumnRenamed(old, new)
        return df
    if t == "cast":
        for col, tp in (d.get("columns") or {}).items():
            df = df.withColumn(col, F.col(col).cast(_CAST_TYPES.get(tp, tp)))
        return df
    if t == "select":
        return df.select(*(d.get("columns") or []))
    if t == "drop":
        return df.drop(*(d.get("columns") or []))
    if t == "add_constant":
        return df.withColumn(d["column"], F.lit(d.get("value")))
    if t == "dedupe":
        keys = d.get("key_columns") or []
        return df.dropDuplicates(keys) if keys else df.dropDuplicates()
    if t == "filter":
        sql = to_spark_sql(d.get("expr"))
        return df.filter(sql) if sql else df
    if t == "python":
        raise ConfigError(
            "spark backend does not support the 'python' transform (ADR-0031); "
            "use a declarative transform or run on the local engine"
        )
    raise ConfigError(f"spark backend: unknown transform type {t!r}")


class SparkBackend(ExecutionBackend):
    """Execution backend that compiles pipelines to Spark.

    ``master`` builds a ``SparkSession`` (default ``local[*]``); pass a
    pre-built ``spark`` to reuse one (tests / shared driver). Connection configs
    are supplied per run via ``run(..., connections=...)``.
    """

    name = "spark"

    def __init__(self, *, master: str = "local[*]", spark: SparkSession | None = None) -> None:
        self._master = master
        self._spark = spark

    def _session(self, app_name: str, connections: dict[str, ConnectionConfig]) -> SparkSession:
        if self._spark is not None:
            return self._spark
        from pyspark.sql import SparkSession

        builder = (
            SparkSession.builder.master(self._master)
            .appName(app_name)
            .config("spark.ui.enabled", "false")
        )
        pkgs = jdbc_packages(connections)
        if pkgs:
            builder = builder.config("spark.jars.packages", pkgs)
        return builder.getOrCreate()

    def _conn(self, connections: dict[str, ConnectionConfig], name: str) -> ConnectionConfig:
        conn = connections.get(name)
        if conn is None:
            raise ConfigError(f"spark backend: connection {name!r} not provided")
        return conn

    def _read(
        self,
        spark: SparkSession,
        connections: dict[str, ConnectionConfig],
        name: str,
        node_opts: dict[str, Any],
    ) -> DataFrame:
        conn = self._conn(connections, name)
        opts = conn.options()
        if conn.type in _JDBC:
            url = jdbc_url(conn.type, opts)
            query = node_opts.get("query")
            table = node_opts.get("table") or opts.get("table")
            dbtable = f"({query}) AS _src" if query else table
            if not dbtable:
                raise ConfigError(f"jdbc source {name!r} needs a 'query' or 'table'")
            reader = spark.read.format("jdbc").option("url", url).option("dbtable", dbtable)
            if opts.get("user") or opts.get("username"):
                reader = reader.option("user", opts.get("user") or opts.get("username"))
            if opts.get("password"):
                reader = reader.option("password", opts["password"])
            # Optional partitioned parallel read (ADR-0032).
            pcol = node_opts.get("partition_column") or opts.get("partition_column")
            if pcol:
                reader = (
                    reader.option("partitionColumn", pcol)
                    .option("numPartitions", str(node_opts.get("num_partitions", 4)))
                    .option("lowerBound", str(node_opts.get("lower_bound", 0)))
                    .option("upperBound", str(node_opts.get("upper_bound", 0)))
                )
            return reader.load()
        # file IO
        fmt = str(node_opts.get("format") or opts.get("format") or "parquet")
        if fmt not in _FILE_FORMATS:
            raise ConfigError(
                f"spark backend v1 file formats: {sorted(_FILE_FORMATS)}; got {fmt!r}"
            )
        base = opts.get("path")
        if not base:
            raise ConfigError(
                f"spark backend: connection {name!r} needs a JDBC type or a file 'path'"
            )
        sub = node_opts.get("key") or node_opts.get("path") or node_opts.get("table")
        path = os.path.join(str(base), str(sub)) if sub else str(base)
        reader = spark.read.format(fmt)
        if fmt == "csv":
            reader = reader.option("header", "true").option("inferSchema", "true")
        return reader.load(path)

    def _write(
        self,
        df: DataFrame,
        connections: dict[str, ConnectionConfig],
        name: str,
        *,
        mode: str,
        node_opts: dict[str, Any],
    ) -> int:
        if mode == "upsert":
            raise ConfigError(
                "spark backend v1 supports append/overwrite, not upsert (MERGE is a later slice, ADR-0032)"
            )
        write_mode = "overwrite" if mode == "overwrite" else "append"
        n = int(df.count())
        conn = self._conn(connections, name)
        opts = conn.options()
        if conn.type in _JDBC:
            url = jdbc_url(conn.type, opts)
            table = node_opts.get("table") or opts.get("table")
            if not table:
                raise ConfigError(f"jdbc sink {name!r} needs a 'table'")
            writer = (
                df.write.mode(write_mode).format("jdbc").option("url", url).option("dbtable", table)
            )
            if opts.get("user") or opts.get("username"):
                writer = writer.option("user", opts.get("user") or opts.get("username"))
            if opts.get("password"):
                writer = writer.option("password", opts["password"])
            writer.save()
            return n
        fmt = str(node_opts.get("format") or opts.get("format") or "parquet")
        if fmt not in _FILE_FORMATS:
            raise ConfigError(
                f"spark backend v1 file formats: {sorted(_FILE_FORMATS)}; got {fmt!r}"
            )
        base = opts.get("path")
        if not base:
            raise ConfigError(
                f"spark backend: connection {name!r} needs a JDBC type or a file 'path'"
            )
        sub = node_opts.get("key") or node_opts.get("path") or node_opts.get("table")
        path = os.path.join(str(base), str(sub)) if sub else str(base)
        writer = df.write.mode(write_mode).format(fmt)
        if fmt == "csv":
            writer = writer.option("header", "true")
        writer.save(path)
        return n

    def run(
        self,
        config: PipelineConfig,
        *,
        connectors: dict[str, Connector] | None = None,  # unused: Spark reads natively
        connections: dict[str, ConnectionConfig] | None = None,
        context: Context | None = None,
        cursor_from: CursorValue = None,
        cursor_to: CursorValue = None,
    ) -> RunResult:
        if cursor_from is not None or cursor_to is not None:
            raise ConfigError("spark backend does not support cursor backfill yet (ADR-0032)")
        if config.mode != "batch":
            raise ConfigError("spark backend is batch-only")
        if config.tasks:
            raise ConfigError(
                "spark backend v1 supports single-task or graph pipelines; multi-task "
                "DAG is a later slice (ADR-0032)"
            )
        conns = connections or {}
        ctx = context or Context(pipeline_name=config.name)
        result = RunResult(run_id=ctx.run_id, pipeline_name=config.name, success=False)
        start = time.monotonic()
        spark = self._session(config.name, conns)
        try:
            if config.graph is not None:
                read_n, written = self._run_graph(spark, config, conns)
            else:
                read_n, written = self._run_linear(spark, config, conns)
            result.records_read = read_n
            result.records_written = written
            result.success = True
        except Exception as exc:
            result.error = exc
            raise
        finally:
            result.duration_seconds = time.monotonic() - start
            if self._spark is None:
                spark.stop()
        return result

    def _run_linear(
        self, spark: SparkSession, config: PipelineConfig, conns: dict[str, ConnectionConfig]
    ) -> tuple[int, int]:
        if config.source is None:
            raise ConfigError("spark backend: pipeline has no source")
        src = config.source
        src_df = self._read(spark, conns, src.connection, src.model_dump(exclude={"connection"}))
        src_df = src_df.cache()
        read_n = int(src_df.count())
        df = src_df
        for tc in config.transforms:
            df = apply_transform(df, tc)
        sinks = config.effective_sinks()
        if len(sinks) > 1:
            df = df.cache()
        written = 0
        for snk in sinks:
            branch = df
            when = getattr(snk, "when", None)
            if when:
                sql = to_spark_sql(when)
                if sql:
                    branch = branch.filter(sql)
            opts = snk.model_dump(exclude={"connection", "mode", "when", "key_columns"})
            written += self._write(branch, conns, snk.connection, mode=snk.mode, node_opts=opts)
        return read_n, written

    def _run_graph(
        self, spark: SparkSession, config: PipelineConfig, conns: dict[str, ConnectionConfig]
    ) -> tuple[int, int]:
        """Dataflow graph (ADR-0030) on Spark — source cached once; each sink is its
        unique path (transform nodes + edge ``when`` filters) off the cached scan."""
        g = config.graph
        assert g is not None
        by_id = {n.id: n for n in g.nodes}
        incoming = {e.to_node: e for e in g.edges}
        sources = [n for n in g.nodes if n.type == "source"]
        sinks = [n for n in g.nodes if n.type == "sink"]
        if len(sources) != 1:
            raise ConfigError("spark graph: exactly one source required")
        src_node = sources[0]
        src_df = self._read(
            spark,
            conns,
            src_node.connection or "",
            src_node.model_dump(exclude={"id", "type", "connection"}),
        ).cache()
        read_n = int(src_df.count())

        def path_to(sink_id: str) -> list[str]:
            chain: list[str] = []
            cur = sink_id
            while cur != src_node.id:
                edge = incoming.get(cur)
                if edge is None:
                    raise ConfigError(f"spark graph: node {cur!r} not reachable from source")
                chain.append(cur)
                cur = edge.from_node
            chain.reverse()
            return chain

        written = 0
        for sink_node in sinks:
            df = src_df
            for node_id in path_to(sink_node.id):
                edge = incoming[node_id]
                if edge.when:
                    sql = to_spark_sql(edge.when)
                    if sql:
                        df = df.filter(sql)
                node = by_id[node_id]
                if node.type == "transform" and node.transform is not None:
                    df = apply_transform(df, node.transform)
            opts = sink_node.model_dump(exclude={"id", "type", "connection", "mode", "key_columns"})
            written += self._write(
                df, conns, sink_node.connection or "", mode=sink_node.mode, node_opts=opts
            )
        return read_n, written


__all__ = ["SparkBackend", "apply_transform", "jdbc_packages", "jdbc_url"]
