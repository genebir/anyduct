"""Dry-run service for pipelines (Step 8.6).

Builds a pipeline from its current :class:`PipelineVersion` *without*
executing it: resolves every referenced connection from the workspace,
substitutes ``${SECRET:<path>}`` placeholders through the configured
:class:`SecretBackend`, instantiates connectors via the core registry,
and (optionally) runs :meth:`Connector.health_check` on each.

The point is to give the UI a single "is this pipeline ready to run?"
button — surface config errors, missing connection references, bad
secret paths, and connector health failures all at once, before
anything is enqueued.

Scope intentionally excludes record sampling. Pulling the first N
records from a source means actually opening a transaction / consuming
from a stream / etc., which has retry/timeout/back-pressure concerns
better handled by the worker engine (Step 9). If a "preview" feature
shows up in the UI later, it should ride on top of a worker-side
``run_pipeline_yaml(stop_after_records=N)`` path, not be reimplemented
here.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.models import ConnectionConfig, PipelineConfig
from etl_plugins.config.secrets import SecretBackend
from etl_plugins.config.variables import resolve_config_variables
from etl_plugins.core.exceptions import ConfigError, RegistryError, SecretError
from etl_plugins.runtime.builder import build_connector, build_pipeline
from etlx_server.db.models import Pipeline, PipelineVersion
from etlx_server.pipelines.runtime import (
    load_connections_by_name,
    referenced_connection_names,
    resolve_placeholders,
)
from etlx_server.variables.repository import WorkspaceVariableRepository


@dataclass(frozen=True)
class ConnectorCheck:
    """Outcome of resolving + health-checking one referenced connection."""

    name: str
    type: str
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class DryRunResult:
    """Bundled output. ``ok`` is true iff every check passed."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    connectors: list[ConnectorCheck] = field(default_factory=list)


def _blocking_health_check(connector: Any) -> tuple[bool, str | None]:
    """Pure-sync health probe on an already-built connector.

    The connector was instantiated up front to validate its options; here
    we just open/close it. Mirrors :mod:`etlx_server.connections.tester`
    so the dry-run answer agrees with what ``POST /connections/{id}/test``
    would say in isolation.
    """
    try:
        connector.connect()
        ok = connector.health_check()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        with contextlib.suppress(Exception):
            connector.close()
    return ok, None if ok else "health_check returned False"


class DryRunService:
    """Validate that a stored pipeline could run end-to-end right now."""

    def __init__(self, session: AsyncSession, backend: SecretBackend) -> None:
        self._session = session
        self._backend = backend

    async def run(
        self,
        pipeline: Pipeline,
        version: PipelineVersion,
        *,
        check_health: bool = True,
    ) -> DryRunResult:
        # 1) Resolve ${var.name} (workspace globals under pipeline locals, locals
        # win — ADR-0041 V2), then re-validate the config against the core schema.
        global_vars = await WorkspaceVariableRepository(self._session).as_dict(
            workspace_id=pipeline.workspace_id
        )
        try:
            cfg_dict = resolve_config_variables(version.config_json, extra=global_vars)
            pipeline_cfg = PipelineConfig.model_validate(cfg_dict)
        except ConfigError as e:
            return DryRunResult(ok=False, errors=[f"variable resolution failed: {e}"])
        except ValidationError as e:
            return DryRunResult(ok=False, errors=[f"invalid pipeline config: {e.errors()}"])

        # 2) Resolve every referenced connection name from this workspace.
        names = referenced_connection_names(pipeline_cfg)
        rows = await load_connections_by_name(
            self._session, workspace_id=pipeline.workspace_id, names=names
        )
        missing = [n for n in names if n not in rows]
        if missing:
            return DryRunResult(
                ok=False,
                errors=[f"connection(s) not found in workspace: {sorted(missing)}"],
            )

        # 3) For each connection: resolve secrets + try to instantiate.
        connectors: dict[str, Any] = {}
        checks: list[ConnectorCheck] = []
        errors: list[str] = []
        for name in names:
            row = rows[name]
            try:
                resolved = resolve_placeholders(row.config_json, self._backend)
            except SecretError as e:
                checks.append(
                    ConnectorCheck(name=name, type=row.type, ok=False, error=f"SecretError: {e}")
                )
                continue
            if not isinstance(resolved, dict):
                checks.append(
                    ConnectorCheck(
                        name=name,
                        type=row.type,
                        ok=False,
                        error="resolved config is not a JSON object",
                    )
                )
                continue
            try:
                conn_cfg = ConnectionConfig.model_validate({"type": row.type, **resolved})
            except ValidationError as e:
                checks.append(
                    ConnectorCheck(
                        name=name,
                        type=row.type,
                        ok=False,
                        error=f"invalid connection config: {e.errors()}",
                    )
                )
                continue
            try:
                connectors[name] = build_connector(name, conn_cfg)
            except (ConfigError, RegistryError) as e:
                checks.append(
                    ConnectorCheck(
                        name=name,
                        type=row.type,
                        ok=False,
                        error=f"{type(e).__name__}: {e}",
                    )
                )
                continue
            checks.append(ConnectorCheck(name=name, type=row.type, ok=True))

        # If any connector failed to instantiate we can't even try to
        # build the pipeline (it would just complain about missing keys).
        if any(not c.ok for c in checks):
            return DryRunResult(
                ok=False,
                errors=["one or more connections failed to build"],
                connectors=checks,
            )

        # 4) Try to build the Pipeline itself — verifies source/sink/DLQ
        # references line up + transforms parse correctly.
        try:
            build_pipeline(pipeline_cfg, connectors=connectors)
        except ConfigError as e:
            errors.append(f"pipeline build failed: {e}")
            self._close_all(connectors)
            return DryRunResult(ok=False, errors=errors, connectors=checks)

        # 5) Optional connector health checks — what the user really
        # wants to know is "would the credentials work right now?".
        # Done in parallel to keep wall time low. ``_blocking_health_check``
        # closes each connector itself, so no explicit cleanup below.
        if check_health:
            health_outcomes = await asyncio.gather(
                *(asyncio.to_thread(_blocking_health_check, connectors[c.name]) for c in checks)
            )
            checks = [
                ConnectorCheck(name=c.name, type=c.type, ok=ok, error=err)
                for c, (ok, err) in zip(checks, health_outcomes, strict=True)
            ]
            if any(not c.ok for c in checks):
                return DryRunResult(
                    ok=False,
                    errors=["one or more connectors failed health_check"],
                    connectors=checks,
                )
        else:
            self._close_all(connectors)

        return DryRunResult(ok=True, errors=[], connectors=checks)

    @staticmethod
    def _close_all(connectors: dict[str, Any]) -> None:
        for c in connectors.values():
            with contextlib.suppress(Exception):
                c.close()


__all__ = [
    "ConnectorCheck",
    "DryRunResult",
    "DryRunService",
]
