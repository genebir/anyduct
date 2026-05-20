"""Execution backend abstraction (ADR-0031)."""

from __future__ import annotations

import pytest

from etl_plugins.config.models import PipelineConfig
from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.record import Record
from etl_plugins.runtime.backends import (
    ExecutionBackend,
    LocalBackend,
    get_backend,
    register_backend,
    run_config,
)
from tests.fixtures.connectors import InMemoryBatchSink, InMemoryBatchSource


def _config() -> PipelineConfig:
    return PipelineConfig.model_validate(
        {
            "name": "p",
            "source": {"connection": "s"},
            "sink": {"connection": "k", "table": "t"},
        }
    )


def test_default_engine_is_local() -> None:
    assert _config().engine == "local"


def test_get_backend_local() -> None:
    assert isinstance(get_backend("local"), LocalBackend)


def test_get_backend_unknown_raises() -> None:
    with pytest.raises(ConfigError, match="unknown execution engine 'nope'"):
        get_backend("nope")


def test_run_config_local_executes() -> None:
    src = InMemoryBatchSource([Record(data={"id": 1}), Record(data={"id": 2})])
    snk = InMemoryBatchSink()
    result = run_config(_config(), connectors={"s": src, "k": snk})
    assert result.success is True
    assert result.records_read == 2
    assert result.records_written == 2


def test_local_backend_requires_connectors() -> None:
    with pytest.raises(ConfigError, match="requires pre-built 'connectors'"):
        run_config(_config())


def test_run_config_routes_by_config_engine() -> None:
    cfg = PipelineConfig.model_validate(
        {
            "name": "p",
            "engine": "unknown-engine",
            "source": {"connection": "s"},
            "sink": {"connection": "k", "table": "t"},
        }
    )
    with pytest.raises(ConfigError, match="unknown execution engine 'unknown-engine'"):
        run_config(cfg)


def test_register_custom_backend() -> None:
    class _Recording(ExecutionBackend):
        name = "recording-test"
        called = False

        def run(
            self,
            config,
            *,
            connectors=None,
            connections=None,
            context=None,
            cursor_from=None,
            cursor_to=None,
        ):  # type: ignore[no-untyped-def]
            _Recording.called = True
            return LocalBackend().run(config, connectors=connectors)

    register_backend(_Recording())
    src = InMemoryBatchSource([Record(data={"id": 1})])
    snk = InMemoryBatchSink()
    result = run_config(_config(), connectors={"s": src, "k": snk}, engine="recording-test")
    assert result.success is True
    assert _Recording.called is True


def test_spark_backend_registered_lazily() -> None:
    from etl_plugins.runtime.backends import get_backend

    assert get_backend("spark").name == "spark"
