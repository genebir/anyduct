"""``etlx`` CLI smoke tests via Typer's CliRunner."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from etl_plugins import __version__
from etl_plugins.cli import app
from etl_plugins.core.registry import ConnectorRegistry
from tests.fixtures.connectors import InMemoryBatchSink, InMemoryBatchSource

runner = CliRunner()


@pytest.fixture(autouse=True)
def _ensure_inmem_registered() -> Iterator[None]:
    src_orig = ConnectorRegistry._registry.get("cli-inmem-source")
    snk_orig = ConnectorRegistry._registry.get("cli-inmem-sink")
    ConnectorRegistry.register("cli-inmem-source", replace=True)(InMemoryBatchSource)
    ConnectorRegistry.register("cli-inmem-sink", replace=True)(InMemoryBatchSink)
    yield
    if src_orig is None:
        ConnectorRegistry._registry.pop("cli-inmem-source", None)
    if snk_orig is None:
        ConnectorRegistry._registry.pop("cli-inmem-sink", None)


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_list_connectors() -> None:
    result = runner.invoke(app, ["list-connectors"])
    assert result.exit_code == 0
    # Built-in connectors should be there
    for name in ("postgres", "s3", "kafka"):
        assert name in result.stdout


def test_validate_pipeline_ok(tmp_path: Path) -> None:
    p = tmp_path / "pipe.yaml"
    p.write_text(
        """\
name: pv
source: { connection: cli-inmem-source }
sink: { connection: cli-inmem-sink, table: T }
"""
    )
    result = runner.invoke(app, ["validate", str(p)])
    assert result.exit_code == 0, result.stdout
    assert "pv" in result.stdout


def test_validate_pipeline_missing_file() -> None:
    result = runner.invoke(app, ["validate", "/does/not/exist.yaml"])
    assert result.exit_code != 0


def test_validate_with_connections(tmp_path: Path) -> None:
    p = tmp_path / "pipe.yaml"
    p.write_text(
        """\
name: pwc
source: { connection: cli-inmem-source }
sink: { connection: cli-inmem-sink, table: T }
"""
    )
    c = tmp_path / "conn.yaml"
    c.write_text(
        """\
connections:
  cli-inmem-source: { type: cli-inmem-source }
  cli-inmem-sink: { type: cli-inmem-sink }
"""
    )
    result = runner.invoke(app, ["validate", str(p), "--connections", str(c)])
    assert result.exit_code == 0, result.stdout
    assert "connections" in result.stdout


def test_validate_rejects_invalid_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    # Missing required 'sink'
    bad.write_text(
        """\
name: bad
source: { connection: x }
"""
    )
    result = runner.invoke(app, ["validate", str(bad)])
    assert result.exit_code != 0


def test_help_shows_subcommands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for sub in ("run", "validate", "version", "list-connectors", "test-connection"):
        assert sub in result.stdout
