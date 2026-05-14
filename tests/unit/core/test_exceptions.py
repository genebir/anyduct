"""ETLError 계층 테스트."""

from __future__ import annotations

import pytest

from etl_plugins.core import exceptions as e


def test_all_errors_descend_from_etlerror() -> None:
    for cls in [
        e.ConfigError,
        e.RegistryError,
        e.ConnectorError,
        e.ConnectError,
        e.ReadError,
        e.WriteError,
        e.RecordError,
        e.PipelineError,
        e.TaskError,
        e.TransformError,
        e.SecretError,
    ]:
        assert issubclass(cls, e.ETLError)


def test_connector_subclass_hierarchy() -> None:
    assert issubclass(e.ConnectError, e.ConnectorError)
    assert issubclass(e.ReadError, e.ConnectorError)
    assert issubclass(e.WriteError, e.ConnectorError)


def test_pipeline_subclass_hierarchy() -> None:
    assert issubclass(e.TaskError, e.PipelineError)
    assert issubclass(e.TransformError, e.PipelineError)


def test_connect_error_does_not_shadow_builtin() -> None:
    # 표준 ConnectionError와 우리 ConnectError는 별개여야 한다
    assert e.ConnectError is not ConnectionError
    assert not issubclass(e.ConnectError, ConnectionError)


def test_raise_and_catch() -> None:
    with pytest.raises(e.ETLError):
        raise e.TaskError("boom")
    with pytest.raises(e.PipelineError):
        raise e.TransformError("nope")
