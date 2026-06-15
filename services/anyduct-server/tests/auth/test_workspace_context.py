"""require_workspace_role / WorkspaceContext unit tests (Step 8.3).

Exercises the checker function in isolation — no app, no DB — so the
role-precedence decision logic is covered independent of the FastAPI
wiring. End-to-end coverage with a real DB lives in
``tests/db/test_workspaces_router.py``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from anyduct_server.auth.schemas import CurrentUser
from anyduct_server.auth.workspace_context import (
    WorkspaceContext,
    require_workspace_role,
)
from anyduct_server.db.enums import WorkspaceRole
from anyduct_server.db.models import Workspace
from fastapi import HTTPException


def _user(*, is_superadmin: bool = False) -> CurrentUser:
    return CurrentUser(id=uuid4(), email="x@example.com", name="X", is_superadmin=is_superadmin)


def _workspace() -> Workspace:
    ws = Workspace()
    ws.id = uuid4()
    ws.name = "Demo"
    ws.slug = "demo"
    ws.color_hex = "#FF3D8B"
    return ws


def _ctx(role: WorkspaceRole | None, *, is_superadmin: bool = False) -> WorkspaceContext:
    return WorkspaceContext(
        user=_user(is_superadmin=is_superadmin), workspace=_workspace(), role=role
    )


@pytest.mark.asyncio
async def test_superadmin_bypass_with_no_membership() -> None:
    checker = require_workspace_role(WorkspaceRole.OWNER)
    ctx = _ctx(role=None, is_superadmin=True)
    assert await checker(ctx) is ctx


@pytest.mark.asyncio
async def test_superadmin_bypass_with_lower_role() -> None:
    """A SuperAdmin who happens to be a Viewer still bypasses Owner gates."""
    checker = require_workspace_role(WorkspaceRole.OWNER)
    ctx = _ctx(role=WorkspaceRole.VIEWER, is_superadmin=True)
    assert await checker(ctx) is ctx


@pytest.mark.asyncio
async def test_exact_role_allowed() -> None:
    checker = require_workspace_role(WorkspaceRole.EDITOR)
    ctx = _ctx(role=WorkspaceRole.EDITOR)
    assert (await checker(ctx)).role is WorkspaceRole.EDITOR


@pytest.mark.asyncio
async def test_higher_role_allowed() -> None:
    checker = require_workspace_role(WorkspaceRole.RUNNER)
    ctx = _ctx(role=WorkspaceRole.OWNER)
    assert await checker(ctx) is ctx


@pytest.mark.asyncio
async def test_lower_role_forbidden() -> None:
    checker = require_workspace_role(WorkspaceRole.EDITOR)
    ctx = _ctx(role=WorkspaceRole.RUNNER)
    with pytest.raises(HTTPException) as exc:
        await checker(ctx)
    assert exc.value.status_code == 403
    assert "'editor'" in exc.value.detail
    assert "'runner'" in exc.value.detail


@pytest.mark.asyncio
async def test_viewer_passes_viewer_gate() -> None:
    checker = require_workspace_role(WorkspaceRole.VIEWER)
    ctx = _ctx(role=WorkspaceRole.VIEWER)
    assert await checker(ctx) is ctx
