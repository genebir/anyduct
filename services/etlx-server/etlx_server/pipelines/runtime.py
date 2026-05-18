"""Shared helpers for materializing a stored Pipeline at runtime.

Both :class:`etlx_server.pipelines.dry_run.DryRunService` (HTTP dry-run)
and :class:`etlx_server.worker.executor.RunExecutor` (worker) need to:

1. Walk a stored ``config_json`` for ``${SECRET:<path>}`` placeholders
   and resolve them through the configured :class:`SecretBackend`.
2. Enumerate the connection names a :class:`PipelineConfig` references
   (source / sink / optional DLQ).
3. Load the matching :class:`Connection` rows by name in the
   workspace.

Centralizing those three concerns here keeps "what counts as a
buildable pipeline" identical between dry-run and the worker — a
dry-run that says ``ok`` and a worker run that fails because it
disagreed about a connection name would be a bad bug.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.models import PipelineConfig
from etl_plugins.config.secrets import SecretBackend
from etlx_server.db.models import Connection

# Same shape as the placeholder ``yaml_sync`` (Step 7.3) writes, the
# connections walker (Step 8.5c) produces, and the connection tester
# (Step 8.5c) resolves.
PLACEHOLDER_RE = re.compile(r"^\$\{SECRET:(?P<path>[^}]+)\}$")


def resolve_placeholders(obj: Any, backend: SecretBackend) -> Any:
    """Walk ``obj``, replacing each ``${SECRET:<path>}`` string with its value.

    Non-string / non-collection leaves pass through untouched. Backend
    errors propagate to the caller — neither dry-run nor the worker
    should swallow a missing-secret crash silently.
    """
    if isinstance(obj, str):
        m = PLACEHOLDER_RE.match(obj)
        if m is None:
            return obj
        return backend.get(m.group("path"))
    if isinstance(obj, dict):
        return {k: resolve_placeholders(v, backend) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_placeholders(x, backend) for x in obj]
    return obj


def referenced_connection_names(cfg: PipelineConfig) -> list[str]:
    """Source + sink + (optional) DLQ, deduped, traversal order preserved."""
    seen: set[str] = set()
    names: list[str] = []
    for n in (cfg.source.connection, cfg.sink.connection):
        if n not in seen:
            seen.add(n)
            names.append(n)
    if cfg.dlq is not None and cfg.dlq.connection not in seen:
        seen.add(cfg.dlq.connection)
        names.append(cfg.dlq.connection)
    return names


async def load_connections_by_name(
    session: AsyncSession, *, workspace_id: UUID, names: list[str]
) -> dict[str, Connection]:
    """Bulk-fetch Connection rows by name within a workspace."""
    if not names:
        return {}
    result = await session.execute(
        select(Connection).where(
            Connection.workspace_id == workspace_id,
            Connection.name.in_(names),
        )
    )
    rows = result.scalars().all()
    return {r.name: r for r in rows}


__all__ = [
    "PLACEHOLDER_RE",
    "load_connections_by_name",
    "referenced_connection_names",
    "resolve_placeholders",
]
