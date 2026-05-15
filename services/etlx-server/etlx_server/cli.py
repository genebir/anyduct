"""``etlx-server`` admin CLI.

Operates on the metadata DB directly. The core ``etlx`` CLI in
:mod:`etl_plugins.cli` stays DB-agnostic (ADR-0017 unidirectional dependency).

Subcommands:

* ``etlx-server import-yaml <yaml_dir> --workspace <slug>``
* ``etlx-server export-yaml --workspace <slug> --to <dir>``

The slug is resolved via the ``workspaces.slug`` UNIQUE column, so callers can
script the CLI without juggling UUIDs.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import typer
from sqlalchemy import select

from etlx_server.db.models import Workspace
from etlx_server.db.session import make_engine, make_session_factory
from etlx_server.io.yaml_sync import export_workspace, import_yaml_dir

app = typer.Typer(help="etlx-server administration CLI (metadata DB sync, ops).")


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        typer.echo("error: DATABASE_URL env var is not set", err=True)
        raise typer.Exit(2)
    return url


async def _resolve_workspace_id(factory: object, slug: str) -> str:
    # ``factory`` is an ``async_sessionmaker``; typed as ``object`` here to keep
    # the CLI free of SQLAlchemy generics noise.
    async with factory() as session:  # type: ignore[operator]
        row = (
            await session.execute(select(Workspace).where(Workspace.slug == slug))
        ).scalar_one_or_none()
        if row is None:
            typer.echo(f"error: no workspace with slug={slug!r}", err=True)
            raise typer.Exit(1)
        return str(row.id)


@app.command("import-yaml")
def import_yaml_cmd(
    yaml_dir: Path = typer.Argument(  # noqa: B008
        ..., exists=True, file_okay=False, dir_okay=True, readable=True
    ),
    workspace: str = typer.Option(..., "--workspace", "-w", help="Target workspace slug."),
) -> None:
    """Import ``connections.yaml`` and ``pipelines/*.yaml`` into a workspace."""

    async def _run() -> None:
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        try:
            ws_id = await _resolve_workspace_id(factory, workspace)
            async with factory() as session:
                from uuid import UUID

                result = await import_yaml_dir(session, UUID(ws_id), yaml_dir)
                await session.commit()
            typer.echo(
                f"imported: connections={result.connections_upserted} "
                f"pipelines={result.pipelines_upserted} "
                f"new_versions={result.pipeline_versions_created} "
                f"unchanged={result.pipeline_versions_unchanged} "
                f"schedules={result.schedules_upserted}"
            )
        finally:
            await engine.dispose()

    asyncio.run(_run())


@app.command("export-yaml")
def export_yaml_cmd(
    workspace: str = typer.Option(..., "--workspace", "-w", help="Source workspace slug."),
    to: Path = typer.Option(  # noqa: B008
        ..., "--to", help="Output directory (created if missing)."
    ),
) -> None:
    """Export a workspace's metadata back to YAML files."""

    async def _run() -> None:
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        try:
            ws_id = await _resolve_workspace_id(factory, workspace)
            async with factory() as session:
                from uuid import UUID

                result = await export_workspace(session, UUID(ws_id), to)
            typer.echo(
                f"exported: connections={result.connections_written} "
                f"pipelines={result.pipelines_written} -> {to}"
            )
        finally:
            await engine.dispose()

    asyncio.run(_run())


def main() -> None:  # pragma: no cover — exercised via console script
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
    sys.exit(0)
