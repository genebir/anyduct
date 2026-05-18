"""Workspace routes (Step 8.3 — read-only sliver; full CRUD lands in Step 8.5).

Single endpoint for now: ``GET /workspaces/{workspace_id}``. It exists to:

1. exercise the RBAC dependency stack end-to-end (``get_current_user`` →
   ``get_current_workspace`` → ``require_workspace_role``), so the wiring
   is covered by integration tests before Step 8.5 piles on more handlers,
2. give the FE something to fetch in case Step 10 prototyping starts
   before 8.5.

Step 8.5 will add ``POST /``, ``GET /`` (list), ``PATCH``, ``DELETE``, plus
membership management — all built on the same dependency primitives.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from etlx_server.auth.schemas import WorkspaceSummary
from etlx_server.auth.workspace_context import (
    WorkspaceContext,
    require_workspace_role,
)
from etlx_server.db.enums import WorkspaceRole

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

# Module-level Depends instance — required by ruff B008 (no function calls in
# default args). The factory result is reused for the workspace read endpoint;
# Step 8.5 endpoints with stricter roles will build their own.
_require_viewer = Depends(require_workspace_role(WorkspaceRole.VIEWER))


@router.get("/{workspace_id}", response_model=WorkspaceSummary)
async def get_workspace(
    ctx: WorkspaceContext = _require_viewer,
) -> WorkspaceSummary:
    return WorkspaceSummary(
        id=ctx.workspace.id,
        name=ctx.workspace.name,
        slug=ctx.workspace.slug,
        color_hex=ctx.workspace.color_hex,
        role=ctx.role.value if ctx.role is not None else None,
    )
