"""Connection CRUD + test endpoint (Step 8.5c).

| Method | Path                                            | Auth     |
|--------|-------------------------------------------------|----------|
| GET    | ``/workspaces/{ws}/connections``                | Viewer+  |
| POST   | ``/workspaces/{ws}/connections``                | Editor+  |
| GET    | ``/workspaces/{ws}/connections/{id}``           | Viewer+  |
| PATCH  | ``/workspaces/{ws}/connections/{id}``           | Editor+  |
| DELETE | ``/workspaces/{ws}/connections/{id}``           | Editor+  |
| POST   | ``/workspaces/{ws}/connections/{id}/test``      | Runner+  |

Secret handling honours ADR-0017 §6: plain values arrive in the request
body, the server immediately writes them to the configured
:class:`SecretBackend`, and the DB row stores only ``${SECRET:<path>}``
placeholders + the list of paths in ``secret_refs``. A read-only backend
(e.g. ``EnvSecretBackend``) combined with secrets in the body produces a
503 — the request is correct, the deployment is misconfigured.

Audit rows pair every mutation. Pre-mint the connection UUID before
writing to the backend so paths stay stable for the row's full lifetime
(slug/name changes don't relocate secrets).
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import SecretBackend
from etlx_server.audit.dependencies import get_audit_service
from etlx_server.audit.service import AuditService
from etlx_server.auth.schemas import (
    ColumnEntry,
    ConnectionColumnsResult,
    ConnectionCreateRequest,
    ConnectionSummary,
    ConnectionTablesResult,
    ConnectionTestResult,
    ConnectionUpdateRequest,
)
from etlx_server.auth.workspace_context import (
    WorkspaceContext,
    require_workspace_role,
)
from etlx_server.connections.inspect import (
    ConnectionInspector,
    InspectionUnsupportedError,
    SecretResolutionError,
)
from etlx_server.connections.repository import (
    ConnectionNameTakenError,
    ConnectionRepository,
)
from etlx_server.connections.secrets import (
    SecretBackendReadOnlyError,
    SecretMarkerError,
    SecretWalker,
)
from etlx_server.connections.tester import ConnectionTester
from etlx_server.db.enums import WorkspaceRole
from etlx_server.db.models import Connection
from etlx_server.dependencies import get_secret_backend_dep, get_session

router = APIRouter(prefix="/workspaces/{workspace_id}/connections", tags=["connections"])

_require_viewer = Depends(require_workspace_role(WorkspaceRole.VIEWER))
_require_runner = Depends(require_workspace_role(WorkspaceRole.RUNNER))
_require_editor = Depends(require_workspace_role(WorkspaceRole.EDITOR))


def _to_summary(connection: Connection) -> ConnectionSummary:
    return ConnectionSummary(
        id=connection.id,
        workspace_id=connection.workspace_id,
        name=connection.name,
        type=connection.type,
        config_json=connection.config_json,
        secret_refs=list(connection.secret_refs),
    )


def _sanitize_or_400(
    walker: SecretWalker,
    *,
    config: dict[str, Any],
    secrets: dict[str, str],
    workspace_id: UUID,
    connection_id: UUID,
) -> tuple[dict[str, Any], list[str]]:
    try:
        return walker.sanitize(
            config=config,
            secrets=secrets,
            workspace_id=workspace_id,
            connection_id=connection_id,
        )
    except SecretMarkerError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e


def _write_secrets_or_503(
    backend: SecretBackend,
    *,
    secrets: dict[str, str],
    workspace_id: UUID,
    connection_id: UUID,
) -> None:
    try:
        SecretWalker.write_secrets(
            backend,
            secrets=secrets,
            workspace_id=workspace_id,
            connection_id=connection_id,
        )
    except SecretBackendReadOnlyError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"secret backend is read-only: {e}",
        ) from e


@router.get("", response_model=list[ConnectionSummary])
async def list_connections(
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[ConnectionSummary]:
    rows = await ConnectionRepository(session).list_for_workspace(workspace_id=ctx.workspace.id)
    return [_to_summary(r) for r in rows]


@router.post("", response_model=ConnectionSummary, status_code=status.HTTP_201_CREATED)
async def create_connection(
    body: ConnectionCreateRequest,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    backend: SecretBackend = Depends(get_secret_backend_dep),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> ConnectionSummary:
    walker = SecretWalker()
    connection_id = uuid.uuid4()
    sanitized, refs = _sanitize_or_400(
        walker,
        config=body.config,
        secrets=body.secrets,
        workspace_id=ctx.workspace.id,
        connection_id=connection_id,
    )
    _write_secrets_or_503(
        backend,
        secrets=body.secrets,
        workspace_id=ctx.workspace.id,
        connection_id=connection_id,
    )

    repo = ConnectionRepository(session)
    try:
        connection = await repo.add(
            connection_id=connection_id,
            workspace_id=ctx.workspace.id,
            name=body.name,
            type=body.type,
            config_json=sanitized,
            secret_refs=refs,
            created_by_user_id=ctx.user.id,
        )
    except ConnectionNameTakenError as e:
        # DB rejected — clean up the backend entries we just wrote.
        SecretWalker.delete_paths(backend, refs)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="connection.create",
        resource_type="connection",
        resource_id=str(connection.id),
        before=None,
        after=ConnectionRepository.snapshot(connection),
    )
    await session.commit()
    return _to_summary(connection)


@router.get("/{connection_id}", response_model=ConnectionSummary)
async def get_connection(
    connection_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ConnectionSummary:
    connection = await ConnectionRepository(session).get(
        workspace_id=ctx.workspace.id, connection_id=connection_id
    )
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="connection not found")
    return _to_summary(connection)


@router.patch("/{connection_id}", response_model=ConnectionSummary)
async def update_connection(
    connection_id: UUID,
    body: ConnectionUpdateRequest,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    backend: SecretBackend = Depends(get_secret_backend_dep),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> ConnectionSummary:
    repo = ConnectionRepository(session)
    connection = await repo.get(workspace_id=ctx.workspace.id, connection_id=connection_id)
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="connection not found")

    if body.name is None and body.config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one of 'name' or 'config' is required",
        )
    if body.config is None and body.secrets is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'secrets' is only valid alongside a new 'config'",
        )

    before = ConnectionRepository.snapshot(connection)
    update_fields: dict[str, Any] = {}

    if body.name is not None:
        update_fields["name"] = body.name

    refs_added: list[str] = []
    refs_removed: list[str] = []
    if body.config is not None:
        walker = SecretWalker()
        new_secrets = body.secrets or {}
        sanitized, new_refs = _sanitize_or_400(
            walker,
            config=body.config,
            secrets=new_secrets,
            workspace_id=ctx.workspace.id,
            connection_id=connection.id,
        )
        old_refs = list(connection.secret_refs)
        refs_added = [r for r in new_refs if r not in old_refs]
        refs_removed = [r for r in old_refs if r not in new_refs]
        _write_secrets_or_503(
            backend,
            secrets=new_secrets,
            workspace_id=ctx.workspace.id,
            connection_id=connection.id,
        )
        update_fields["config_json"] = sanitized
        update_fields["secret_refs"] = new_refs

    try:
        updated = await repo.update(connection, **update_fields)
    except ConnectionNameTakenError as e:
        # Best-effort cleanup of any secrets we just wrote.
        if refs_added:
            SecretWalker.delete_paths(backend, refs_added)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    after = ConnectionRepository.snapshot(updated)

    # Purge backend entries that aren't referenced anymore. Best effort — we
    # don't want a backend hiccup to block a metadata update.
    if refs_removed:
        SecretWalker.delete_paths(backend, refs_removed)

    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="connection.update",
        resource_type="connection",
        resource_id=str(updated.id),
        before=before,
        after=after,
    )
    await session.commit()
    return _to_summary(updated)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: UUID,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    backend: SecretBackend = Depends(get_secret_backend_dep),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> None:
    repo = ConnectionRepository(session)
    connection = await repo.get(workspace_id=ctx.workspace.id, connection_id=connection_id)
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="connection not found")
    before = ConnectionRepository.snapshot(connection)
    paths_to_purge = list(connection.secret_refs)
    connection_uuid = connection.id

    await repo.delete(connection)
    # Backend cleanup is best-effort and happens *after* the DB delete so a
    # backend outage can't strand a connection row with secrets in limbo —
    # the next operator pass can rerun secret_backend cleanup separately.
    SecretWalker.delete_paths(backend, paths_to_purge)

    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="connection.delete",
        resource_type="connection",
        resource_id=str(connection_uuid),
        before=before,
        after=None,
    )
    await session.commit()


@router.post("/{connection_id}/test", response_model=ConnectionTestResult)
async def test_connection(
    connection_id: UUID,
    ctx: WorkspaceContext = _require_runner,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    backend: SecretBackend = Depends(get_secret_backend_dep),  # noqa: B008
) -> ConnectionTestResult:
    """Resolve secrets, build the connector, run its health check."""
    connection = await ConnectionRepository(session).get(
        workspace_id=ctx.workspace.id, connection_id=connection_id
    )
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="connection not found")
    outcome = await ConnectionTester(backend).run(connection)
    return ConnectionTestResult(ok=outcome.ok, error=outcome.error)


async def _resolve_connection_or_404(
    session: AsyncSession, *, workspace_id: UUID, connection_id: UUID
) -> Connection:
    connection = await ConnectionRepository(session).get(
        workspace_id=workspace_id, connection_id=connection_id
    )
    if connection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="connection not found")
    return connection


@router.get("/{connection_id}/tables", response_model=ConnectionTablesResult)
async def list_connection_tables(
    connection_id: UUID,
    ctx: WorkspaceContext = _require_runner,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    backend: SecretBackend = Depends(get_secret_backend_dep),  # noqa: B008
) -> ConnectionTablesResult:
    """Introspect the connection's tables for the builder's picker (ADR-0033)."""
    connection = await _resolve_connection_or_404(
        session, workspace_id=ctx.workspace.id, connection_id=connection_id
    )
    try:
        tables = await ConnectionInspector(backend).list_tables(connection)
    except InspectionUnsupportedError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    except SecretResolutionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"introspection failed: {type(e).__name__}: {e}",
        ) from e
    return ConnectionTablesResult(tables=tables)


@router.get("/{connection_id}/columns", response_model=ConnectionColumnsResult)
async def list_connection_columns(
    connection_id: UUID,
    table: str = Query(min_length=1),
    ctx: WorkspaceContext = _require_runner,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    backend: SecretBackend = Depends(get_secret_backend_dep),  # noqa: B008
) -> ConnectionColumnsResult:
    """Introspect a table's columns so downstream transforms can "click" them (ADR-0033)."""
    connection = await _resolve_connection_or_404(
        session, workspace_id=ctx.workspace.id, connection_id=connection_id
    )
    try:
        columns = await ConnectionInspector(backend).list_columns(connection, table)
    except InspectionUnsupportedError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
    except SecretResolutionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"introspection failed: {type(e).__name__}: {e}",
        ) from e
    return ConnectionColumnsResult(
        table=table,
        columns=[ColumnEntry(name=c.name, type=c.type) for c in columns],
    )
