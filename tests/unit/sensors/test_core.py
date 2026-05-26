"""Sensor core ABC + registry (ADR-0041 K3a)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from etl_plugins.core.exceptions import ConfigError
from etl_plugins.core.sensor import (
    SensorBase,
    SensorResult,
    build_sensor,
    register_sensor,
    registered_sensor_types,
)


class _AlwaysFires(SensorBase):
    """Test sensor whose ``check()`` always returns ``triggered=True``."""

    def check(self) -> SensorResult:
        return SensorResult(triggered=True, message="ok")


def test_sensor_result_defaults_metadata_to_empty_dict() -> None:
    """An empty ``metadata`` must serialise cleanly (no None field) so the
    service layer can dump it to ``result_json`` without special-casing."""
    r = SensorResult(triggered=False)
    assert r.triggered is False
    assert r.message is None
    assert dict(r.metadata) == {}


def test_register_sensor_makes_it_buildable() -> None:
    @register_sensor("k3-core-test-1")
    def _b(cfg: Mapping[str, Any]) -> SensorBase:
        return _AlwaysFires()

    s = build_sensor("k3-core-test-1", {})
    assert isinstance(s, _AlwaysFires)
    assert s.check().triggered is True


def test_register_sensor_duplicate_raises() -> None:
    @register_sensor("k3-core-test-dup")
    def _b(cfg: Mapping[str, Any]) -> SensorBase:
        return _AlwaysFires()

    with pytest.raises(ConfigError, match="already registered"):

        @register_sensor("k3-core-test-dup")
        def _b2(cfg: Mapping[str, Any]) -> SensorBase:
            return _AlwaysFires()


def test_build_sensor_unknown_type_raises_with_helpful_list() -> None:
    with pytest.raises(ConfigError, match="unknown sensor type"):
        build_sensor("not-a-real-sensor", {})


def test_registered_sensor_types_includes_builtin_http() -> None:
    # Importing this triggers the http sensor's @register_sensor side-effect.
    import etl_plugins.sensors  # noqa: F401

    names = registered_sensor_types()
    assert "http" in names
    # Always sorted so the catalog endpoint reads predictably.
    assert names == sorted(names)
