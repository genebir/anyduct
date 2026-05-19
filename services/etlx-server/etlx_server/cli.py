"""``etlx-server`` admin CLI.

Operates on the metadata DB directly. The core ``etlx`` CLI in
:mod:`etl_plugins.cli` stays DB-agnostic (ADR-0017 unidirectional dependency).

Subcommands:

* ``etlx-server import-yaml <yaml_dir> --workspace <slug>``
* ``etlx-server export-yaml --workspace <slug> --to <dir>``
* ``etlx-server worker run`` — long-running worker process

The slug is resolved via the ``workspaces.slug`` UNIQUE column, so callers can
script the CLI without juggling UUIDs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import uuid
from pathlib import Path

import typer
from sqlalchemy import select

from etl_plugins.config.secrets import get_secret_backend
from etl_plugins.observability.logging import configure_logging
from etlx_server.auth.password_service import PasswordService
from etlx_server.db.enums import AuthMethod
from etlx_server.db.models import Workspace
from etlx_server.db.models.workspace import User
from etlx_server.db.session import make_engine, make_session_factory
from etlx_server.io.yaml_sync import export_workspace, import_yaml_dir
from etlx_server.scheduler import Scheduler
from etlx_server.worker import RunWorker, StreamWorker, ZombieReaper
from etlx_server.worker.recorder import log_processor

app = typer.Typer(help="etlx-server administration CLI (metadata DB sync, ops).")
worker_app = typer.Typer(help="Worker process commands.")
reaper_app = typer.Typer(help="Zombie-run reaper process commands.")
scheduler_app = typer.Typer(help="Cron scheduler process commands.")
stream_worker_app = typer.Typer(help="Stream worker process commands.")
admin_app = typer.Typer(help="One-shot admin / bootstrap commands.")
app.add_typer(worker_app, name="worker")
app.add_typer(reaper_app, name="reaper")
app.add_typer(scheduler_app, name="scheduler")
app.add_typer(stream_worker_app, name="stream-worker")
app.add_typer(admin_app, name="admin")


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


@admin_app.command("create-user")
def create_user_cmd(
    email: str = typer.Option(..., "--email", help="Login email (unique)."),
    name: str = typer.Option(..., "--name", help="Display name."),
    password: str = typer.Option(
        ...,
        "--password",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
        help="Login password (prompted if omitted).",
    ),
    superadmin: bool = typer.Option(
        False, "--superadmin", help="Grant cross-workspace SuperAdmin privileges."
    ),
) -> None:
    """Seed a local-auth user. Use this once at deploy time to bootstrap
    the first admin — subsequent users join via OIDC or invite flows."""

    async def _run() -> None:
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        try:
            async with factory() as session:
                existing = (
                    await session.execute(select(User).where(User.email == email))
                ).scalar_one_or_none()
                if existing is not None:
                    typer.echo(f"error: user with email={email!r} already exists", err=True)
                    raise typer.Exit(1)
                user = User(
                    email=email,
                    name=name,
                    auth_method=AuthMethod.LOCAL,
                    password_hash=PasswordService().hash(password),
                    is_superadmin=superadmin,
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
            badge = " (superadmin)" if superadmin else ""
            typer.echo(f"created: user id={user.id} email={email}{badge}")
        finally:
            await engine.dispose()

    asyncio.run(_run())


@worker_app.command("run")
def worker_run_cmd(
    poll_interval: float = typer.Option(
        1.0,
        "--poll-interval",
        help="Seconds to wait between empty-queue polls.",
        min=0.1,
        max=60.0,
    ),
    worker_id: str = typer.Option(
        "",
        "--worker-id",
        help="Identifier stamped on claimed rows (default: auto-generated UUID).",
    ),
    secret_backend: str = typer.Option(
        "env",
        "--secret-backend",
        help="One of: env, static, file, vault, aws_sm, gcp_sm.",
    ),
    secret_file_path: str = typer.Option(
        "",
        "--secret-file-path",
        help="Used only when --secret-backend=file.",
    ),
    log_flush_interval: float = typer.Option(
        2.0,
        "--log-flush-interval",
        help=(
            "Seconds between RunRecorder flushes to run_logs / run_metrics. "
            "Lower = more live UI but more transactions; 0 disables periodic "
            "flush (recorder writes once at run end)."
        ),
        min=0.0,
        max=60.0,
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Run the worker poll loop until SIGTERM/SIGINT.

    The worker claims pending Run rows via ``FOR UPDATE SKIP LOCKED``,
    materializes the stored pipeline, and writes back terminal status.
    Multiple instances can run in parallel — the queue is the table.
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Install the recorder's structlog processor so anything the pipeline
    # logs via ctx.logger / structlog.get_logger() lands in ``run_logs``
    # whenever a recorder is active for that run_id (Step 9.3c).
    configure_logging(level=log_level, json=True, extra_processors=[log_processor])
    wid = worker_id or f"worker-{uuid.uuid4().hex[:12]}"

    async def _run() -> None:
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        backend_opts: dict[str, str] = {}
        if secret_file_path:
            backend_opts["file_path"] = secret_file_path
        backend = get_secret_backend(secret_backend, **backend_opts)
        worker = RunWorker(
            factory,
            backend,
            worker_id=wid,
            poll_interval=poll_interval,
            log_flush_interval_seconds=(log_flush_interval if log_flush_interval > 0 else None),
        )

        loop = asyncio.get_running_loop()

        def _shutdown(_signame: str) -> None:
            typer.echo(f"received {_signame}, shutting down worker")
            worker.stop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _shutdown, sig.name)

        try:
            await worker.run()
        finally:
            await engine.dispose()

    asyncio.run(_run())


@reaper_app.command("run")
def reaper_run_cmd(
    heartbeat_timeout: float = typer.Option(
        60.0,
        "--heartbeat-timeout",
        help="Seconds since last heartbeat after which a running row is considered dead.",
        min=5.0,
        max=3600.0,
    ),
    scan_interval: float = typer.Option(
        30.0,
        "--scan-interval",
        help="Seconds to wait between scans.",
        min=1.0,
        max=600.0,
    ),
    batch_limit: int = typer.Option(
        100,
        "--batch-limit",
        help="Maximum rows to reap per scan.",
        min=1,
        max=10000,
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Run the zombie reaper poll loop until SIGTERM/SIGINT.

    Scans ``runs`` for rows in ``running`` whose ``heartbeat_at`` is
    older than ``--heartbeat-timeout`` and transitions them to
    ``failed`` with ``error_class='ZombieReaped'``. Auto-resubmit is
    intentionally NOT done — a poison row would otherwise take down
    every worker that touches it.
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    async def _run() -> None:
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        reaper = ZombieReaper(
            factory,
            heartbeat_timeout_seconds=heartbeat_timeout,
            scan_interval_seconds=scan_interval,
            batch_limit=batch_limit,
        )

        loop = asyncio.get_running_loop()

        def _shutdown(_signame: str) -> None:
            typer.echo(f"received {_signame}, shutting down reaper")
            reaper.stop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _shutdown, sig.name)

        try:
            await reaper.run()
        finally:
            await engine.dispose()

    asyncio.run(_run())


@scheduler_app.command("run")
def scheduler_run_cmd(
    tick_interval: float = typer.Option(
        10.0,
        "--tick-interval",
        help="Seconds between scans of active schedules.",
        min=1.0,
        max=600.0,
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Run the cron scheduler until SIGTERM/SIGINT.

    Inspects active batch ``schedules`` periodically and creates pending
    Run rows whose ``scheduled_at`` is the cron's next firing time. No
    catchup: if a firing was missed (process down), only the next future
    firing is enqueued — not the missed ones.
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    async def _run() -> None:
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        scheduler = Scheduler(factory, tick_interval_seconds=tick_interval)

        loop = asyncio.get_running_loop()

        def _shutdown(_signame: str) -> None:
            typer.echo(f"received {_signame}, shutting down scheduler")
            scheduler.stop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _shutdown, sig.name)

        try:
            await scheduler.run()
        finally:
            await engine.dispose()

    asyncio.run(_run())


@stream_worker_app.command("run")
def stream_worker_run_cmd(
    tick_interval: float = typer.Option(
        5.0,
        "--tick-interval",
        help="Seconds between scans of active stream schedules.",
        min=1.0,
        max=60.0,
    ),
    worker_id: str = typer.Option(
        "",
        "--worker-id",
        help="Identifier stamped on Run rows (default: auto-generated UUID).",
    ),
    secret_backend: str = typer.Option(
        "env",
        "--secret-backend",
        help="One of: env, static, file, vault, aws_sm, gcp_sm.",
    ),
    secret_file_path: str = typer.Option(
        "",
        "--secret-file-path",
        help="Used only when --secret-backend=file.",
    ),
    log_flush_interval: float = typer.Option(
        2.0,
        "--log-flush-interval",
        help="Seconds between RunRecorder flushes to run_logs / run_metrics.",
        min=0.0,
        max=60.0,
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Run stream pipelines for all active stream schedules.

    Scans :class:`Schedule` rows where ``mode='stream'`` and
    ``is_active=true`` and keeps an ``arun_stream`` task alive per
    schedule. On SIGTERM/SIGINT, every in-flight stream is cancelled
    and its Run row is stamped ``cancelled`` so the UI doesn't show
    ghost ``running`` rows after the operator restarts the process.

    **Single-replica today.** Running two instances would race —
    they'd each try to spawn the same schedule. Multi-replica is
    a Step 11 enhancement (``FOR UPDATE SKIP LOCKED`` on the
    schedule row before spawn).
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    configure_logging(level=log_level, json=True, extra_processors=[log_processor])
    wid = worker_id or f"stream-worker-{uuid.uuid4().hex[:12]}"

    async def _run() -> None:
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        backend_opts: dict[str, str] = {}
        if secret_file_path:
            backend_opts["file_path"] = secret_file_path
        backend = get_secret_backend(secret_backend, **backend_opts)
        worker = StreamWorker(
            factory,
            backend,
            worker_id=wid,
            tick_interval_seconds=tick_interval,
            log_flush_interval_seconds=(log_flush_interval if log_flush_interval > 0 else None),
        )

        loop = asyncio.get_running_loop()

        def _shutdown(_signame: str) -> None:
            typer.echo(f"received {_signame}, shutting down stream worker")
            worker.stop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, _shutdown, sig.name)

        try:
            await worker.run()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def main() -> None:  # pragma: no cover — exercised via console script
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
    sys.exit(0)
