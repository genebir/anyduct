"""YAML <-> metadata DB bidirectional sync.

Direction 1 (import): ``configs/*.yaml`` -> DB upsert.
Direction 2 (export): DB -> ``configs/*.yaml``.

Reuses :mod:`etl_plugins.config.models` (single source of truth for the YAML
schema). Secrets never enter the DB in plaintext:

* ``!secret <path>`` in YAML is parsed into :class:`SecretRef` by the core
  loader.
* Before persisting, every :class:`SecretRef` is replaced with a
  ``${SECRET:<path>}`` string in ``config_json`` and its path is collected in
  ``connections.secret_refs``.
* On export, ``${SECRET:<path>}`` strings are re-rendered as ``!secret <path>``
  YAML tags.
* ``${VAR}`` env-var placeholders pass through verbatim (never expanded at
  import time — they remain unresolved in DB until run time).

Idempotency: a re-import of an unchanged YAML produces no new
``PipelineVersion``. A change creates a new version with ``is_current=True``
and demotes the previous one in the same transaction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.loader import SecretRef, load_yaml
from etl_plugins.config.models import ConnectionConfig, ConnectionsConfig, PipelineConfig
from etlx_server.db.enums import PipelineMode
from etlx_server.db.models import (
    Connection,
    Pipeline,
    PipelineVersion,
    Schedule,
)

# ``${SECRET:<path>}`` placeholder format used inside config_json.
_SECRET_PLACEHOLDER_RE = re.compile(r"^\$\{SECRET:(?P<path>[^}]+)\}$")


def _make_secret_placeholder(path: str) -> str:
    return f"${{SECRET:{path}}}"


# ---------------------------------------------------------------------------
# Secret extraction (YAML side -> DB side)
# ---------------------------------------------------------------------------


def _extract_secret_refs(obj: Any, *, _refs: list[str] | None = None) -> tuple[Any, list[str]]:
    """Walk ``obj`` replacing :class:`SecretRef` leaves with placeholder strings.

    Returns ``(new_tree, list_of_unique_paths_in_traversal_order)``.
    """
    refs: list[str] = _refs if _refs is not None else []
    if isinstance(obj, SecretRef):
        if obj.path not in refs:
            refs.append(obj.path)
        return _make_secret_placeholder(obj.path), refs
    if isinstance(obj, dict):
        return {k: _extract_secret_refs(v, _refs=refs)[0] for k, v in obj.items()}, refs
    if isinstance(obj, list):
        return [_extract_secret_refs(x, _refs=refs)[0] for x in obj], refs
    return obj, refs


# ---------------------------------------------------------------------------
# Placeholder rendering (DB side -> YAML side)
# ---------------------------------------------------------------------------


class _SecretTag:
    """Sentinel used during export so yaml.dump emits ``!secret <path>``."""

    def __init__(self, path: str) -> None:
        self.path = path


class _ETLYAMLDumper(yaml.SafeDumper):
    """SafeDumper that knows how to emit ``!secret`` tags."""


def _secret_tag_representer(dumper: _ETLYAMLDumper, data: _SecretTag) -> yaml.Node:
    return dumper.represent_scalar("!secret", data.path, style="plain")


_ETLYAMLDumper.add_representer(_SecretTag, _secret_tag_representer)


def _render_secret_placeholders(obj: Any) -> Any:
    """Inverse of :func:`_extract_secret_refs`."""
    if isinstance(obj, str):
        m = _SECRET_PLACEHOLDER_RE.match(obj)
        if m is not None:
            return _SecretTag(m.group("path"))
        return obj
    if isinstance(obj, dict):
        return {k: _render_secret_placeholders(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_render_secret_placeholders(x) for x in obj]
    return obj


# ---------------------------------------------------------------------------
# Import (YAML -> DB)
# ---------------------------------------------------------------------------


@dataclass
class ImportResult:
    """Summary of an import run — useful for CLI output and tests."""

    connections_upserted: int = 0
    pipelines_upserted: int = 0
    pipeline_versions_created: int = 0
    schedules_upserted: int = 0
    # Pipeline names that were re-imported without producing a new version.
    pipeline_versions_unchanged: int = 0


async def _upsert_connection(
    session: AsyncSession,
    workspace_id: UUID,
    name: str,
    cfg: ConnectionConfig,
    secret_refs: list[str],
) -> Connection:
    """Insert or update a ``connections`` row keyed by ``(workspace_id, name)``."""
    existing = (
        await session.execute(
            select(Connection).where(
                Connection.workspace_id == workspace_id, Connection.name == name
            )
        )
    ).scalar_one_or_none()
    config_json = cfg.options()
    if existing is None:
        row = Connection(
            workspace_id=workspace_id,
            name=name,
            type=cfg.type,
            config_json=config_json,
            secret_refs=secret_refs,
        )
        session.add(row)
        await session.flush()
        return row
    existing.type = cfg.type
    existing.config_json = config_json
    existing.secret_refs = secret_refs
    await session.flush()
    return existing


async def import_connections_yaml(
    session: AsyncSession,
    workspace_id: UUID,
    path: str | Path,
) -> int:
    """Import a ``connections.yaml`` file into the metadata DB.

    Returns the number of rows upserted.
    """
    raw = load_yaml(path)
    if raw is None or "connections" not in raw:
        raise ValueError(f"{path}: missing top-level 'connections' key")
    # Extract refs per connection (so each row knows its own ref list), then
    # validate the assembled document once (catches both extra top-level keys
    # and per-connection schema issues).
    per_connection: dict[str, tuple[dict[str, Any], list[str]]] = {}
    for name, conn_raw in raw["connections"].items():
        sanitized, refs = _extract_secret_refs(conn_raw)
        per_connection[name] = (sanitized, refs)
    parsed = ConnectionsConfig.model_validate(
        {"connections": {name: s for name, (s, _) in per_connection.items()}}
    )
    for name, cfg in parsed.connections.items():
        await _upsert_connection(session, workspace_id, name, cfg, per_connection[name][1])
    return len(parsed.connections)


async def _upsert_pipeline(session: AsyncSession, workspace_id: UUID, name: str) -> Pipeline:
    existing = (
        await session.execute(
            select(Pipeline).where(Pipeline.workspace_id == workspace_id, Pipeline.name == name)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = Pipeline(workspace_id=workspace_id, name=name)
    session.add(row)
    await session.flush()
    return row


async def _ensure_pipeline_version(
    session: AsyncSession,
    pipeline: Pipeline,
    config_json: dict[str, Any],
) -> tuple[PipelineVersion, bool]:
    """Return ``(version_row, was_created)``.

    If the current version's ``config_json`` equals ``config_json`` exactly, no
    new row is created.
    """
    current = (
        await session.execute(
            select(PipelineVersion)
            .where(
                PipelineVersion.pipeline_id == pipeline.id,
                PipelineVersion.is_current.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if current is not None and current.config_json == config_json:
        return current, False
    # Compute next version number.
    next_version_num = 1
    if current is not None:
        next_version_num = current.version + 1
        current.is_current = False
    new_row = PipelineVersion(
        pipeline_id=pipeline.id,
        version=next_version_num,
        config_json=config_json,
        is_current=True,
    )
    session.add(new_row)
    await session.flush()
    return new_row, True


async def _upsert_default_schedule(
    session: AsyncSession,
    pipeline: Pipeline,
    cfg: PipelineConfig,
) -> Schedule | None:
    """Maintain at most one Schedule row named ``"default"`` per pipeline.

    Created/updated when ``cfg.schedule`` is set; deleted when it's removed.
    """
    existing = (
        await session.execute(
            select(Schedule).where(Schedule.pipeline_id == pipeline.id, Schedule.name == "default")
        )
    ).scalar_one_or_none()
    mode = PipelineMode(cfg.mode)
    if cfg.schedule is None and mode is not PipelineMode.STREAM:
        if existing is not None:
            await session.delete(existing)
            await session.flush()
        return None
    if existing is None:
        row = Schedule(
            pipeline_id=pipeline.id,
            name="default",
            cron_expr=cfg.schedule,
            mode=mode,
            is_active=True,
        )
        session.add(row)
        await session.flush()
        return row
    existing.cron_expr = cfg.schedule
    existing.mode = mode
    await session.flush()
    return existing


async def import_pipeline_yaml(
    session: AsyncSession,
    workspace_id: UUID,
    path: str | Path,
) -> tuple[PipelineVersion, bool, Schedule | None]:
    """Import a single pipeline YAML.

    Returns ``(version_row, was_new_version, schedule_or_none)``.
    """
    raw = load_yaml(path)
    sanitized, _ = _extract_secret_refs(raw)
    cfg = PipelineConfig.model_validate(sanitized)
    pipeline = await _upsert_pipeline(session, workspace_id, cfg.name)
    config_json = cfg.model_dump(mode="json")
    version, created = await _ensure_pipeline_version(session, pipeline, config_json)
    schedule = await _upsert_default_schedule(session, pipeline, cfg)
    return version, created, schedule


async def import_yaml_dir(
    session: AsyncSession,
    workspace_id: UUID,
    yaml_dir: str | Path,
) -> ImportResult:
    """Walk ``yaml_dir`` importing ``connections.yaml`` + ``pipelines/*.yaml``.

    Both files are optional. Missing files are skipped. Returns an
    :class:`ImportResult` summarising what changed.
    """
    base = Path(yaml_dir)
    if not base.is_dir():
        raise ValueError(f"not a directory: {base}")
    result = ImportResult()

    connections_file = base / "connections.yaml"
    if connections_file.is_file():
        result.connections_upserted = await import_connections_yaml(
            session, workspace_id, connections_file
        )

    pipelines_dir = base / "pipelines"
    if pipelines_dir.is_dir():
        for pipeline_path in sorted(pipelines_dir.glob("*.yaml")):
            _, created, schedule = await import_pipeline_yaml(session, workspace_id, pipeline_path)
            result.pipelines_upserted += 1
            if created:
                result.pipeline_versions_created += 1
            else:
                result.pipeline_versions_unchanged += 1
            if schedule is not None:
                result.schedules_upserted += 1
    return result


# ---------------------------------------------------------------------------
# Export (DB -> YAML)
# ---------------------------------------------------------------------------


@dataclass
class ExportResult:
    connections_written: int = 0
    pipelines_written: int = 0


def _dump_yaml(data: Any) -> str:
    return yaml.dump(
        data,
        Dumper=_ETLYAMLDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


async def export_workspace(
    session: AsyncSession,
    workspace_id: UUID,
    out_dir: str | Path,
) -> ExportResult:
    """Render a workspace's connections + current pipeline versions to YAML.

    Layout produced:

    * ``out_dir/connections.yaml`` (only if the workspace has connections)
    * ``out_dir/pipelines/<pipeline-name>.yaml`` (one per current version)

    ``${SECRET:<path>}`` strings become ``!secret <path>`` tags. ``${VAR}`` and
    other strings pass through verbatim.
    """
    base = Path(out_dir)
    base.mkdir(parents=True, exist_ok=True)
    result = ExportResult()

    conns = (
        (
            await session.execute(
                select(Connection)
                .where(Connection.workspace_id == workspace_id)
                .order_by(Connection.name)
            )
        )
        .scalars()
        .all()
    )
    if conns:
        connections_doc: dict[str, dict[str, Any]] = {"connections": {}}
        for c in conns:
            cfg_dict: dict[str, Any] = {"type": c.type, **c.config_json}
            connections_doc["connections"][c.name] = _render_secret_placeholders(cfg_dict)
        (base / "connections.yaml").write_text(_dump_yaml(connections_doc), encoding="utf-8")
        result.connections_written = len(conns)

    pipelines = (
        (
            await session.execute(
                select(Pipeline)
                .where(Pipeline.workspace_id == workspace_id)
                .order_by(Pipeline.name)
            )
        )
        .scalars()
        .all()
    )
    if pipelines:
        pipelines_dir = base / "pipelines"
        pipelines_dir.mkdir(parents=True, exist_ok=True)
        for p in pipelines:
            current = (
                await session.execute(
                    select(PipelineVersion)
                    .where(
                        PipelineVersion.pipeline_id == p.id,
                        PipelineVersion.is_current.is_(True),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if current is None:
                continue
            rendered = _render_secret_placeholders(current.config_json)
            file_name = f"{p.name}.yaml"
            (pipelines_dir / file_name).write_text(_dump_yaml(rendered), encoding="utf-8")
            result.pipelines_written += 1

    return result


__all__ = [
    "ExportResult",
    "ImportResult",
    "export_workspace",
    "import_connections_yaml",
    "import_pipeline_yaml",
    "import_yaml_dir",
]
