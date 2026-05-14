"""``etlx`` CLI smoke tests via Typer's CliRunner."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from etl_plugins import __version__
from etl_plugins.cli import app
from etl_plugins.core.registry import ConnectorRegistry
from tests.fixtures.connectors import (
    InMemoryBatchSink,
    InMemoryBatchSource,
    InMemoryStreamSink,
    InMemoryStreamSource,
)

runner = CliRunner()


@pytest.fixture(autouse=True)
def _ensure_inmem_registered() -> Iterator[None]:
    names = {
        "cli-inmem-source": InMemoryBatchSource,
        "cli-inmem-sink": InMemoryBatchSink,
        "cli-stream-source": InMemoryStreamSource,
        "cli-stream-sink": InMemoryStreamSink,
    }
    originals = {n: ConnectorRegistry._registry.get(n) for n in names}
    for n, klass in names.items():
        ConnectorRegistry.register(n, replace=True)(klass)
    yield
    for n, orig in originals.items():
        if orig is None:
            ConnectorRegistry._registry.pop(n, None)


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
    for sub in (
        "run",
        "run-stream",
        "validate",
        "version",
        "list-connectors",
        "test-connection",
    ):
        assert sub in result.stdout


def test_run_stream_smoke(tmp_path: Path) -> None:
    """Stream-mode pipeline must run via CLI with stop-after-records bound."""
    # Register stable globals so YAML connection types resolve to our InMemory
    # stream classes — and seed the source with 3 records via the registry.
    src = InMemoryStreamSource(
        [
            __import__("etl_plugins.core.record", fromlist=["Record"]).Record(data={"i": i})
            for i in range(3)
        ]
    )
    snk = InMemoryStreamSink()
    # Replace the class-bound registry entry with a factory returning our seeded instances.
    ConnectorRegistry.register("cli-stream-source-seeded", replace=True)(lambda **_: src)
    ConnectorRegistry.register("cli-stream-sink-seeded", replace=True)(lambda **_: snk)

    p = tmp_path / "pipe.yaml"
    p.write_text(
        """\
name: cli-stream-test
mode: stream
source: { connection: src, topic: in }
sink: { connection: snk, topic: out, buffer: { max_records: 1 } }
"""
    )
    c = tmp_path / "conn.yaml"
    c.write_text(
        """\
connections:
  src: { type: cli-stream-source-seeded }
  snk: { type: cli-stream-sink-seeded }
"""
    )
    result = runner.invoke(
        app,
        [
            "run-stream",
            str(p),
            "--connections",
            str(c),
            "--stop-after-records",
            "3",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "read=3" in result.stdout
    assert "written=3" in result.stdout
    # Clean up the seeded registrations
    ConnectorRegistry._registry.pop("cli-stream-source-seeded", None)
    ConnectorRegistry._registry.pop("cli-stream-sink-seeded", None)
