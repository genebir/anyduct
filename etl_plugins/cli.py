"""``anyduct`` command-line interface (Typer based).

Available subcommands (Step 3.1 MVP):

    anyduct version
    anyduct list-connectors
    anyduct validate <pipeline.yaml> [--connections <path>]
    anyduct run <pipeline.yaml> [--connections <path>] [--env-file <path>]
    anyduct test-connection <name> --connections <path>

``anyduct run-stream`` and ``anyduct schema`` will come in Step 3.2+ with stream
runtime and connector schema introspection.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from typing import Annotated

import typer

from etl_plugins import __version__
from etl_plugins.config.loader import load_connections, load_dotenv, load_pipeline
from etl_plugins.config.secrets import get_secret_backend
from etl_plugins.core.exceptions import ConfigError, ETLError
from etl_plugins.core.registry import ConnectorRegistry
from etl_plugins.observability.logging import configure_logging
from etl_plugins.runtime.builder import build_connectors
from etl_plugins.runtime.runner import arun_stream_pipeline_yaml, run_pipeline_yaml
from etl_plugins.runtime.templating import RuntimeContext


def _build_runtime_context(
    params: list[str] | None, logical_date: str | None
) -> RuntimeContext | None:
    """Assemble a :class:`RuntimeContext` from ``--param KEY=VALUE`` options
    and ``--logical-date``. Returns ``None`` when neither is given (no
    templating overhead). Values are parsed as JSON when possible (so
    ``--param limit=100`` is an int, ``--param tags=[1,2]`` a list), else
    kept as the raw string."""
    import json
    import uuid
    from datetime import UTC, datetime

    if not params and not logical_date:
        return None
    parsed: dict[str, object] = {}
    for item in params or []:
        if "=" not in item:
            _err(f"--param must be KEY=VALUE, got {item!r}")
        key, _, raw = item.partition("=")
        try:
            parsed[key.strip()] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            parsed[key.strip()] = raw
    if logical_date:
        try:
            ld = datetime.fromisoformat(logical_date)
        except ValueError:
            _err(f"--logical-date must be ISO-8601, got {logical_date!r}")
    else:
        ld = datetime.now(UTC)
    return RuntimeContext(run_id=uuid.uuid4().hex, logical_date=ld, params=parsed)


app = typer.Typer(
    name="anyduct",
    help="ETL Plugins CLI — run YAML pipelines, validate config, inspect connectors.",
    no_args_is_help=True,
)


@app.callback()
def _global_options(
    log_format: Annotated[
        str,
        typer.Option(
            "--log-format",
            help="Log output format: 'json' (default, structured) or 'console' (dev-friendly)",
        ),
    ] = "json",
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Log level (DEBUG, INFO, WARNING, ERROR)"),
    ] = "INFO",
) -> None:
    """Configure structlog before any subcommand runs."""
    if log_format not in ("json", "console"):
        typer.secho(
            f"error: --log-format must be 'json' or 'console', got {log_format!r}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    configure_logging(level=log_level, json=log_format == "json")


# ---------- generic helpers -------------------------------------------------


def _err(msg: str, code: int = 1) -> None:
    typer.secho(f"error: {msg}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=code)


def _resolve_backend() -> object:  # returns SecretBackend but Typer hates that
    return get_secret_backend()


# ---------- subcommands -----------------------------------------------------


@app.command()
def version() -> None:
    """Print the etl-plugins package version."""
    typer.echo(__version__)


@app.command("list-connectors")
def list_connectors() -> None:
    """List every connector available in the registry (built-in + entry-points)."""
    for name in ConnectorRegistry.list_connectors():
        typer.echo(name)


@app.command()
def validate(
    pipeline_yaml: Annotated[Path, typer.Argument(help="Pipeline YAML file")],
    connections: Annotated[
        Path | None,
        typer.Option("--connections", "-c", help="connections.yaml (optional)"),
    ] = None,
) -> None:
    """Validate that a pipeline YAML (and optional connections) parses cleanly."""
    backend = get_secret_backend()
    try:
        pc = load_pipeline(pipeline_yaml, secret_backend=backend)
    except ETLError as exc:
        _err(f"pipeline validation failed: {exc}")
    typer.echo(f"pipeline: {pc.name} (mode={pc.mode}) ✓")
    if connections is not None:
        try:
            cc = load_connections(connections, secret_backend=backend)
            build_connectors(cc)  # construct each connector class — checks param shapes
        except ETLError as exc:
            _err(f"connections validation failed: {exc}")
        typer.echo(f"connections: {len(cc.connections)} entries ✓")


@app.command()
def run(
    pipeline_yaml: Annotated[Path, typer.Argument(help="Pipeline YAML file")],
    connections: Annotated[
        Path | None,
        typer.Option("--connections", "-c", help="connections.yaml"),
    ] = None,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Load env vars from this .env file before running"),
    ] = None,
    param: Annotated[
        list[str] | None,
        typer.Option(
            "--param",
            "-p",
            help="Run parameter KEY=VALUE (repeatable). Reference as {{ params.KEY }} "
            "in queries/options. Overrides the pipeline's declared 'params' default.",
        ),
    ] = None,
    logical_date: Annotated[
        str | None,
        typer.Option(
            "--logical-date",
            help="Logical/execution date for {{ ds }} / {{ ts }} (ISO-8601). "
            "Defaults to now (UTC).",
        ),
    ] = None,
) -> None:
    """Run a pipeline end-to-end (load → build → connect → run → close)."""
    if env_file is not None:
        load_dotenv(env_file)
    runtime_context = _build_runtime_context(param, logical_date)
    try:
        result = run_pipeline_yaml(
            pipeline_yaml,
            connections_path=connections,
            secret_backend=get_secret_backend(),
            runtime_context=runtime_context,
        )
    except ETLError as exc:
        _err(f"pipeline failed: {exc}")
    except Exception as exc:
        _err(f"unexpected: {type(exc).__name__}: {exc}")

    typer.secho(
        f"run {result.run_id}: success={result.success} "
        f"read={result.records_read} written={result.records_written} "
        f"duration={result.duration_seconds:.3f}s",
        fg=typer.colors.GREEN if result.success else typer.colors.RED,
    )


@app.command("run-stream")
def run_stream(
    pipeline_yaml: Annotated[Path, typer.Argument(help="Pipeline YAML (mode: stream)")],
    connections: Annotated[
        Path | None,
        typer.Option("--connections", "-c", help="connections.yaml"),
    ] = None,
    env_file: Annotated[
        Path | None,
        typer.Option("--env-file", help="Load env vars from this .env file before running"),
    ] = None,
    stop_after_records: Annotated[
        int | None,
        typer.Option(
            "--stop-after-records",
            help="Exit after consuming this many records (useful for tests / dry runs)",
        ),
    ] = None,
    stop_after_seconds: Annotated[
        float | None,
        typer.Option(
            "--stop-after-seconds",
            help="Exit after this many seconds of wall time",
        ),
    ] = None,
) -> None:
    """Run a streaming pipeline. Runs until a ``--stop-after-*`` limit or until interrupted."""
    if env_file is not None:
        load_dotenv(env_file)
    try:
        result = asyncio.run(
            arun_stream_pipeline_yaml(
                pipeline_yaml,
                connections_path=connections,
                secret_backend=get_secret_backend(),
                stop_after_records=stop_after_records,
                stop_after_seconds=stop_after_seconds,
            )
        )
    except ETLError as exc:
        _err(f"stream pipeline failed: {exc}")
    except KeyboardInterrupt:
        typer.secho("interrupted", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=130) from None
    except Exception as exc:
        _err(f"unexpected: {type(exc).__name__}: {exc}")

    typer.secho(
        f"stream {result.run_id}: success={result.success} "
        f"read={result.records_read} written={result.records_written} "
        f"duration={result.duration_seconds:.3f}s",
        fg=typer.colors.GREEN if result.success else typer.colors.RED,
    )


@app.command("test-connection")
def test_connection(
    name: Annotated[str, typer.Argument(help="Connection name (or '--all')")],
    connections: Annotated[
        Path,
        typer.Option("--connections", "-c", help="connections.yaml (required)"),
    ],
) -> None:
    """Open a single connection (or all) and run its ``health_check()``."""
    try:
        cc = load_connections(connections, secret_backend=get_secret_backend())
        connectors = build_connectors(cc)
    except ConfigError as exc:
        _err(f"failed to load connections: {exc}")

    targets = sorted(connectors) if name == "--all" else [name]
    if name != "--all" and name not in connectors:
        _err(f"connection {name!r} not found (available: {sorted(connectors)})")

    failures = 0
    for n in targets:
        c = connectors[n]
        try:
            c.connect()
            ok = c.health_check()
        except Exception as exc:
            ok = False
            note = f" ({type(exc).__name__}: {exc})"
        else:
            note = ""
        finally:
            with contextlib.suppress(Exception):
                c.close()
        if ok:
            typer.secho(f"  {n}: ok", fg=typer.colors.GREEN)
        else:
            typer.secho(f"  {n}: FAIL{note}", fg=typer.colors.RED)
            failures += 1
    if failures:
        raise typer.Exit(code=1)


def main() -> None:  # pragma: no cover - exercised via the anyduct entry point
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
