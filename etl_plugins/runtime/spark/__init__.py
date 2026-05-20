"""Spark execution backend (ADR-0031/0032).

Compiles a :class:`PipelineConfig` to Spark DataFrame operations. ``pyspark`` is
imported lazily (only when the backend actually runs) so the ``[spark]`` extra
stays optional and the ``local`` backend never pulls a JVM.
"""

from etl_plugins.runtime.spark.backend import SparkBackend, jdbc_packages, jdbc_url
from etl_plugins.runtime.spark.predicate import to_spark_sql

__all__ = ["SparkBackend", "jdbc_packages", "jdbc_url", "to_spark_sql"]
