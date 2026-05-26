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
from typing import Any
from uuid import UUID

import typer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import get_secret_backend
from etl_plugins.observability.lineage import set_lineage_emitter
from etl_plugins.observability.logging import configure_logging
from etl_plugins.observability.openlineage import OpenLineageEmitter
from etlx_server.auth.password_service import PasswordService
from etlx_server.connections import (
    ConnectionNameTakenError,
    ConnectionRepository,
    ConnectionTester,
    SecretBackendReadOnlyError,
    SecretMarkerError,
    SecretWalker,
)
from etlx_server.db.enums import AuthMethod
from etlx_server.db.models import Connection, Workspace
from etlx_server.db.models.workspace import User
from etlx_server.db.session import make_engine, make_session_factory
from etlx_server.io.yaml_sync import export_workspace, import_yaml_dir
from etlx_server.scheduler import Scheduler
from etlx_server.sensors import SensorScheduler
from etlx_server.settings import get_settings
from etlx_server.worker import RunWorker, StreamWorker, ZombieReaper
from etlx_server.worker.recorder import log_processor

app = typer.Typer(help="etlx-server administration CLI (metadata DB sync, ops).")
worker_app = typer.Typer(help="Worker process commands.")
reaper_app = typer.Typer(help="Zombie-run reaper process commands.")
scheduler_app = typer.Typer(help="Cron scheduler process commands.")
sensor_scheduler_app = typer.Typer(help="Sensor scheduler process commands.")
stream_worker_app = typer.Typer(help="Stream worker process commands.")
admin_app = typer.Typer(help="One-shot admin / bootstrap commands.")
server_app = typer.Typer(help="Run the FastAPI server (uvicorn wrapper).")
db_app = typer.Typer(help="Metadata DB migrations (Alembic wrapper).")
connection_app = typer.Typer(help="Manage workspace connections (CRUD + test).")
app.add_typer(worker_app, name="worker")
app.add_typer(reaper_app, name="reaper")
app.add_typer(scheduler_app, name="scheduler")
app.add_typer(sensor_scheduler_app, name="sensor-scheduler")
app.add_typer(stream_worker_app, name="stream-worker")
app.add_typer(admin_app, name="admin")
app.add_typer(server_app, name="server")
app.add_typer(db_app, name="db")
app.add_typer(connection_app, name="connection")


def _install_openlineage_emitter() -> OpenLineageEmitter | None:
    """Read settings, install :class:`OpenLineageEmitter` if configured.

    Returns the emitter so callers can ``.close()`` it on shutdown. Returns
    ``None`` when ``openlineage_url`` isn't set — the default no-op emitter
    stays installed and OL export is silently disabled (ADR-0041 K5).
    """
    settings = get_settings()
    url = settings.openlineage_url
    if not url:
        return None
    emitter = OpenLineageEmitter(
        url,
        namespace=settings.openlineage_namespace,
        api_key=settings.openlineage_api_key,
    )
    set_lineage_emitter(emitter)
    return emitter


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


@admin_app.command("gen-jwt-keys")
def gen_jwt_keys_cmd(
    out_dir: Path = typer.Option(  # noqa: B008
        Path(".run"),
        "--out-dir",
        help="Directory to write `jwt_private.pem` + `jwt_public.pem`.",
    ),
    bits: int = typer.Option(2048, "--bits", help="RSA modulus size."),
    force: bool = typer.Option(False, "--force", help="Overwrite existing keys (default: refuse)."),
) -> None:
    """Generate an RSA keypair for JWT RS256.

    Local-dev convenience: run once, then ``etlx server run`` (via the
    `etlx` wrapper) exports the PEMs as ``AUTH_JWT_PRIVATE_KEY_PEM`` /
    ``AUTH_JWT_PUBLIC_KEY_PEM`` so the FastAPI app boots its
    ``JwtService``. The default output dir is ``.run/`` which is already
    in ``.gitignore``."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    out_dir.mkdir(parents=True, exist_ok=True)
    priv_path = out_dir / "jwt_private.pem"
    pub_path = out_dir / "jwt_public.pem"

    if (priv_path.exists() or pub_path.exists()) and not force:
        typer.echo(
            f"refusing to overwrite existing keys at {out_dir}/ (use --force to replace)",
            err=True,
        )
        raise typer.Exit(1)

    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    priv_path.write_bytes(priv_pem)
    priv_path.chmod(0o600)
    pub_path.write_bytes(pub_pem)
    pub_path.chmod(0o644)

    typer.echo(f"wrote: {priv_path} (mode 0600)")
    typer.echo(f"wrote: {pub_path}")


# ---------- server (FastAPI) -----------------------------------------------


@server_app.command("run")
def server_run_cmd(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind address."),
    port: int = typer.Option(8000, "--port", help="TCP port."),
    reload: bool = typer.Option(False, "--reload", help="Hot-reload on source changes (dev only)."),
    workers: int = typer.Option(1, "--workers", help="Worker processes (ignored when --reload)."),
    log_level: str = typer.Option("info", "--log-level", help="uvicorn log level."),
) -> None:
    """Run the FastAPI app via uvicorn. Production should typically use a
    process manager directly (gunicorn + uvicorn workers); this command
    is for local dev and quick deploys."""
    import uvicorn

    uvicorn.run(
        "etlx_server.main:app",
        host=host,
        port=port,
        reload=reload,
        workers=workers if not reload else 1,
        log_level=log_level,
    )


# ---------- db (Alembic) ---------------------------------------------------


def _alembic_cfg() -> object:
    """Build the Alembic Config pointing at the bundled ``alembic.ini``.

    Lazy-import alembic so the CLI startup stays fast for non-db commands."""
    from pathlib import Path as _Path

    from alembic.config import Config

    server_dir = _Path(__file__).resolve().parent.parent
    ini = server_dir / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(server_dir / "alembic"))
    # env.py reads $DATABASE_URL itself; nothing else to wire here.
    return cfg


@db_app.command("upgrade")
def db_upgrade_cmd(
    revision: str = typer.Argument("head", help="Target revision (default: head)."),
) -> None:
    """Run ``alembic upgrade <revision>`` against ``$DATABASE_URL``."""
    from alembic import command

    _ = _database_url()  # surface env-var-missing error early
    command.upgrade(_alembic_cfg(), revision)  # type: ignore[arg-type]


@db_app.command("downgrade")
def db_downgrade_cmd(
    revision: str = typer.Argument(..., help="Target revision (e.g. -1, base, <rev>)."),
) -> None:
    """Run ``alembic downgrade <revision>`` against ``$DATABASE_URL``."""
    from alembic import command

    _ = _database_url()
    command.downgrade(_alembic_cfg(), revision)  # type: ignore[arg-type]


@db_app.command("current")
def db_current_cmd() -> None:
    """Show the current Alembic revision applied to ``$DATABASE_URL``."""
    from alembic import command

    _ = _database_url()
    command.current(_alembic_cfg())  # type: ignore[arg-type]


@db_app.command("history")
def db_history_cmd() -> None:
    """List all known Alembic revisions."""
    from alembic import command

    command.history(_alembic_cfg())  # type: ignore[arg-type]


# ---------- connection (CRUD + test) ---------------------------------------


def _load_connection_doc(path: Path) -> dict[str, Any]:
    """Load a connection spec from JSON or YAML. Auto-detect by suffix."""
    import json

    import yaml

    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(raw)
    elif path.suffix.lower() == ".json":
        data = json.loads(raw)
    else:
        # Best effort: try YAML first (superset of JSON).
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError:
            data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level must be a mapping")
    return data


def _parse_secret_flags(secret_flags: list[str]) -> dict[str, str]:
    """Parse repeated ``--secret KEY=VALUE`` into a dict."""
    out: dict[str, str] = {}
    for entry in secret_flags:
        if "=" not in entry:
            raise typer.BadParameter(f"--secret expects KEY=VALUE, got {entry!r}")
        key, _, value = entry.partition("=")
        if not key:
            raise typer.BadParameter(f"--secret key cannot be empty in {entry!r}")
        out[key] = value
    return out


async def _resolve_connection_by_name(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    name: str,
) -> Connection:
    row = (
        await session.execute(
            select(Connection).where(
                Connection.workspace_id == workspace_id,
                Connection.name == name,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        typer.echo(
            f"error: no connection named {name!r} in this workspace",
            err=True,
        )
        raise typer.Exit(1)
    return row


def _print_summary(connection: Connection) -> None:
    import json

    typer.echo(f"id           : {connection.id}")
    typer.echo(f"name         : {connection.name}")
    typer.echo(f"type         : {connection.type}")
    typer.echo(f"secret refs  : {len(connection.secret_refs)}")
    for ref in connection.secret_refs:
        typer.echo(f"  - {ref}")
    typer.echo("config (sanitized — secrets shown as ${SECRET:…} placeholders):")
    typer.echo(json.dumps(connection.config_json, indent=2, ensure_ascii=False))


@connection_app.command("list")
def connection_list_cmd(
    workspace: str = typer.Option(..., "--workspace", "-w", help="Workspace slug."),
) -> None:
    """List every connection in a workspace (name, type, secret-ref count)."""

    async def _run() -> None:
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        try:
            ws_id = UUID(await _resolve_workspace_id(factory, workspace))
            async with factory() as session:
                rows = await ConnectionRepository(session).list_for_workspace(workspace_id=ws_id)
            if not rows:
                typer.echo("(no connections)")
                return
            typer.echo(f"{'NAME':<32} {'TYPE':<12} {'SECRETS':>7}  CREATED")
            for r in rows:
                typer.echo(
                    f"{r.name:<32} {r.type:<12} {len(r.secret_refs):>7}  {r.created_at.isoformat()}"
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


@connection_app.command("get")
def connection_get_cmd(
    name: str = typer.Argument(..., help="Connection name."),
    workspace: str = typer.Option(..., "--workspace", "-w", help="Workspace slug."),
) -> None:
    """Show one connection's sanitized config + secret refs."""

    async def _run() -> None:
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        try:
            ws_id = UUID(await _resolve_workspace_id(factory, workspace))
            async with factory() as session:
                row = await _resolve_connection_by_name(session, workspace_id=ws_id, name=name)
                _print_summary(row)
        finally:
            await engine.dispose()

    asyncio.run(_run())


@connection_app.command("add")
def connection_add_cmd(
    workspace: str = typer.Option(..., "--workspace", "-w", help="Workspace slug."),
    name: str = typer.Option(..., "--name", help="Connection name (unique per workspace)."),
    type_: str = typer.Option(
        ..., "--type", help="Connector type (e.g. postgres / mysql / sqlite / s3)."
    ),
    config_file: Path | None = typer.Option(  # noqa: B008
        None,
        "--config-file",
        help="JSON or YAML file with {name?, type?, config, secrets?}. "
        "`config` may contain {$secret: KEY} markers. "
        "`secrets` may include the values; --secret CLI flags merge in.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    secret_flags: list[str] = typer.Option(  # noqa: B008
        [],
        "--secret",
        help="KEY=VALUE secret pair; repeatable. Merged into --config-file secrets.",
    ),
) -> None:
    """Create a new connection. Secrets go to the active SecretBackend; the
    metadata DB only stores `${SECRET:…}` placeholders + the ref list."""

    async def _run() -> None:
        if config_file is None:
            typer.echo("error: --config-file is required for `connection add`", err=True)
            raise typer.Exit(2)

        doc = _load_connection_doc(config_file)
        config = doc.get("config")
        if not isinstance(config, dict):
            typer.echo(
                "error: --config-file must define a `config:` mapping at the top level",
                err=True,
            )
            raise typer.Exit(2)
        secrets: dict[str, str] = {**doc.get("secrets", {}), **_parse_secret_flags(secret_flags)}

        backend = get_secret_backend()
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        try:
            ws_id = UUID(await _resolve_workspace_id(factory, workspace))
            connection_id = uuid.uuid4()
            walker = SecretWalker()
            try:
                sanitized, refs = walker.sanitize(
                    config=config,
                    secrets=secrets,
                    workspace_id=ws_id,
                    connection_id=connection_id,
                )
            except SecretMarkerError as e:
                typer.echo(f"error: {e}", err=True)
                raise typer.Exit(1) from e

            try:
                SecretWalker.write_secrets(
                    backend,
                    secrets=secrets,
                    workspace_id=ws_id,
                    connection_id=connection_id,
                )
            except SecretBackendReadOnlyError as e:
                typer.echo(
                    f"error: secret backend is read-only ({e}); "
                    "configure ETLX_SECRET_BACKEND to a writable backend.",
                    err=True,
                )
                raise typer.Exit(1) from e

            async with factory() as session:
                try:
                    connection = await ConnectionRepository(session).add(
                        connection_id=connection_id,
                        workspace_id=ws_id,
                        name=name,
                        type=type_,
                        config_json=sanitized,
                        secret_refs=refs,
                        created_by_user_id=None,
                    )
                    await session.commit()
                except ConnectionNameTakenError as e:
                    # Roll back the backend writes we just did.
                    SecretWalker.delete_paths(backend, refs)
                    typer.echo(f"error: {e}", err=True)
                    raise typer.Exit(1) from e
                typer.echo(f"created: connection id={connection.id} name={connection.name}")
        finally:
            await engine.dispose()

    asyncio.run(_run())


@connection_app.command("update")
def connection_update_cmd(
    name: str = typer.Argument(..., help="Existing connection name."),
    workspace: str = typer.Option(..., "--workspace", "-w", help="Workspace slug."),
    new_name: str | None = typer.Option(None, "--rename", help="Change the connection name."),
    type_: str | None = typer.Option(None, "--type", help="Change the connector type."),
    config_file: Path | None = typer.Option(  # noqa: B008
        None,
        "--config-file",
        help="Replace config from JSON/YAML (same schema as `add`).",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    secret_flags: list[str] = typer.Option(  # noqa: B008
        [],
        "--secret",
        help="KEY=VALUE secret pair (merged into --config-file secrets if any).",
    ),
) -> None:
    """Update fields of an existing connection. Omit a flag to leave it
    unchanged. When --config-file is supplied, secrets in the backend are
    fully re-written: paths not used by the new config are deleted."""

    async def _run() -> None:
        backend = get_secret_backend()
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        try:
            ws_id = UUID(await _resolve_workspace_id(factory, workspace))
            async with factory() as session:
                row = await _resolve_connection_by_name(session, workspace_id=ws_id, name=name)

                updates: dict[str, Any] = {}
                if new_name is not None:
                    updates["name"] = new_name
                if type_ is not None:
                    updates["type"] = type_

                if config_file is not None:
                    doc = _load_connection_doc(config_file)
                    config = doc.get("config")
                    if not isinstance(config, dict):
                        typer.echo(
                            "error: --config-file must define a top-level `config:` mapping",
                            err=True,
                        )
                        raise typer.Exit(2)
                    secrets: dict[str, str] = {
                        **doc.get("secrets", {}),
                        **_parse_secret_flags(secret_flags),
                    }
                    walker = SecretWalker()
                    try:
                        sanitized, new_refs = walker.sanitize(
                            config=config,
                            secrets=secrets,
                            workspace_id=ws_id,
                            connection_id=row.id,
                        )
                    except SecretMarkerError as e:
                        typer.echo(f"error: {e}", err=True)
                        raise typer.Exit(1) from e

                    try:
                        SecretWalker.write_secrets(
                            backend,
                            secrets=secrets,
                            workspace_id=ws_id,
                            connection_id=row.id,
                        )
                    except SecretBackendReadOnlyError as e:
                        typer.echo(f"error: secret backend is read-only ({e})", err=True)
                        raise typer.Exit(1) from e

                    # Paths removed by the new config — best-effort delete.
                    removed = [p for p in row.secret_refs if p not in new_refs]
                    SecretWalker.delete_paths(backend, removed)

                    updates["config_json"] = sanitized
                    updates["secret_refs"] = new_refs

                elif secret_flags:
                    typer.echo(
                        "warning: --secret has no effect without --config-file (config "
                        "shape isn't being changed). skipping.",
                        err=True,
                    )

                if not updates:
                    typer.echo("no changes — supply --rename / --type / --config-file")
                    raise typer.Exit(2)

                try:
                    connection = await ConnectionRepository(session).update(row, **updates)
                except ConnectionNameTakenError as e:
                    typer.echo(f"error: {e}", err=True)
                    raise typer.Exit(1) from e
                await session.commit()
                typer.echo(f"updated: connection id={connection.id} name={connection.name}")
        finally:
            await engine.dispose()

    asyncio.run(_run())


@connection_app.command("delete")
def connection_delete_cmd(
    name: str = typer.Argument(..., help="Connection name to delete."),
    workspace: str = typer.Option(..., "--workspace", "-w", help="Workspace slug."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Delete a connection row + best-effort wipe of its backend secrets."""

    async def _run() -> None:
        backend = get_secret_backend()
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        try:
            ws_id = UUID(await _resolve_workspace_id(factory, workspace))
            async with factory() as session:
                row = await _resolve_connection_by_name(session, workspace_id=ws_id, name=name)
                if not yes:
                    typer.confirm(
                        f"delete connection {row.name!r} (id={row.id})? "
                        "secrets will also be removed from the backend.",
                        abort=True,
                    )
                refs = list(row.secret_refs)
                await ConnectionRepository(session).delete(row)
                await session.commit()
                SecretWalker.delete_paths(backend, refs)
                typer.echo(f"deleted: {name}")
        finally:
            await engine.dispose()

    asyncio.run(_run())


@connection_app.command("test")
def connection_test_cmd(
    name: str = typer.Argument(..., help="Connection name to test."),
    workspace: str = typer.Option(..., "--workspace", "-w", help="Workspace slug."),
) -> None:
    """Resolve secrets through the backend, build the connector via
    `ConnectorRegistry`, and run its health check. Exits 0 on success,
    1 with the error message on failure."""

    async def _run() -> None:
        backend = get_secret_backend()
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        try:
            ws_id = UUID(await _resolve_workspace_id(factory, workspace))
            async with factory() as session:
                row = await _resolve_connection_by_name(session, workspace_id=ws_id, name=name)
            outcome = await ConnectionTester(backend).run(row)
            if outcome.ok:
                typer.echo(f"OK   {name} ({row.type})")
            else:
                typer.echo(f"FAIL {name} ({row.type}): {outcome.error}", err=True)
                raise typer.Exit(1)
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
        envvar="SECRET_BACKEND",
        help=(
            "One of: env, static, file, vault, aws_sm, gcp_sm. "
            "Falls back to the SECRET_BACKEND env var so it matches the API "
            "server's configuration (avoid worker/server backend mismatch)."
        ),
    ),
    secret_file_path: str = typer.Option(
        "",
        "--secret-file-path",
        envvar="SECRET_BACKEND_FILE_PATH",
        help="Used only when --secret-backend=file. Falls back to SECRET_BACKEND_FILE_PATH.",
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
        # ADR-0041 K5: install OpenLineage emitter if configured. None when
        # ``openlineage_url`` is unset — runs simply don't get exported,
        # internal catalog stays unaffected.
        ol_emitter = _install_openlineage_emitter()
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
            if ol_emitter is not None:
                ol_emitter.close()
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


@sensor_scheduler_app.command("run")
def sensor_scheduler_run_cmd(
    tick_interval: float = typer.Option(
        5.0,
        "--tick-interval",
        help="Seconds between scans of active sensors.",
        min=1.0,
        max=600.0,
    ),
    secret_backend: str = typer.Option(
        "env",
        "--secret-backend",
        envvar="SECRET_BACKEND",
        help=(
            "One of: env, static, file, vault, aws_sm, gcp_sm. Required for "
            "sensors that reference a Connection with secret_refs "
            "(file_landed, future dataset_row_count). Falls back to the "
            "SECRET_BACKEND env var so it matches the API server's "
            "configuration."
        ),
    ),
    secret_file_path: str = typer.Option(
        "",
        "--secret-file-path",
        envvar="SECRET_BACKEND_FILE_PATH",
        help="Used only when --secret-backend=file. Falls back to SECRET_BACKEND_FILE_PATH.",
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Run the sensor scheduler until SIGTERM/SIGINT (ADR-0041 K3b).

    Periodically polls active ``sensors`` rows whose ``last_check_at +
    poll_interval_seconds`` has elapsed, calls each sensor's
    ``check()``, and on ``triggered=True`` enqueues a PENDING Run on
    the sensor's ``target_pipeline_id``.

    **Multi-replica safe** (K2 pattern): the due-sensor query holds
    ``FOR UPDATE SKIP LOCKED`` so two replicas partition the work
    instead of double-firing the same sensor.
    """
    # Make sure built-in sensors register their dispatchers before the
    # scheduler dispatches an event. Two packages:
    #   * etl_plugins.sensors          — pure-core builtins (http)
    #   * etlx_server.sensors.builtins — service-side builtins that need
    #     metadata-DB access (asset_freshness)
    import etl_plugins.sensors  # noqa: F401 — register side-effects
    import etlx_server.sensors.builtins  # noqa: F401 — register side-effects

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    async def _run() -> None:
        engine = make_engine(_database_url())
        factory = make_session_factory(engine)
        backend_opts: dict[str, str] = {}
        if secret_file_path:
            backend_opts["file_path"] = secret_file_path
        backend = get_secret_backend(secret_backend, **backend_opts)
        scheduler = SensorScheduler(
            factory, tick_interval_seconds=tick_interval, secret_backend=backend
        )

        loop = asyncio.get_running_loop()

        def _shutdown(_signame: str) -> None:
            typer.echo(f"received {_signame}, shutting down sensor scheduler")
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
        envvar="SECRET_BACKEND",
        help=(
            "One of: env, static, file, vault, aws_sm, gcp_sm. "
            "Falls back to the SECRET_BACKEND env var so it matches the API "
            "server's configuration (avoid worker/server backend mismatch)."
        ),
    ),
    secret_file_path: str = typer.Option(
        "",
        "--secret-file-path",
        envvar="SECRET_BACKEND_FILE_PATH",
        help="Used only when --secret-backend=file. Falls back to SECRET_BACKEND_FILE_PATH.",
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

    **Multi-replica safe** (ADR-0041 K2b): two stream-worker processes
    coordinate via the schedule row's ``FOR UPDATE SKIP LOCKED`` and a
    post-lock "RUNNING run already exists" re-check inside
    ``_start_schedule``. Failover: the ZombieReaper reaps stale RUNNING
    rows so a surviving replica takes over on its next tick.
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
        # ADR-0041 K5: install OpenLineage emitter if configured (see
        # worker_run_cmd for the same wiring rationale).
        ol_emitter = _install_openlineage_emitter()
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
            if ol_emitter is not None:
                ol_emitter.close()
            await engine.dispose()

    asyncio.run(_run())


def main() -> None:  # pragma: no cover — exercised via console script
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
    sys.exit(0)
