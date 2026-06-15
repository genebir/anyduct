"""Workspace-scoped request context + RBAC dependency factories.

ADR-0023 dependency chain::

    Depends(get_current_user)
      → Depends(get_current_workspace)
        → Depends(require_workspace_role(WorkspaceRole.EDITOR))

Each protected endpoint declares the role it needs; the factory returns a
fresh callable per minimum role so the dependency identity carries the
policy. SuperAdmin (``users.is_superadmin``) bypasses the membership check
unconditionally — they can act in *any* workspace even without a row in
``memberships``. In that case ``WorkspaceContext.role`` is ``None``, which
endpoint code can use to distinguish "platform operator override" from
"actual workspace member."
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.auth.current_user import get_current_user
from anyduct_server.auth.membership_repository import MembershipRepository
from anyduct_server.auth.rbac import has_at_least
from anyduct_server.auth.schemas import CurrentUser
from anyduct_server.auth.workspace_repository import WorkspaceRepository
from anyduct_server.db.enums import WorkspaceRole
from anyduct_server.db.models import Workspace
from anyduct_server.dependencies import get_session


@dataclass(frozen=True)
class WorkspaceContext:
    """Resolved request context: who is acting, in which workspace, with what role.

    ``role`` is ``None`` only when the user is a SuperAdmin acting in a
    workspace they're not a member of — for ordinary members it always
    holds their actual role.
    """

    user: CurrentUser
    workspace: Workspace
    role: WorkspaceRole | None


async def get_current_workspace(
    workspace_id: UUID,
    user: CurrentUser = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WorkspaceContext:
    """Load the workspace named by the path param + the caller's role in it.

    * Workspace not found → 404.
    * Caller has no membership and is not SuperAdmin → 403.
    * Otherwise yields a populated :class:`WorkspaceContext`.

    Role membership is checked here (not in
    :func:`require_workspace_role`) so endpoints that only need access to
    *the workspace object* — without a minimum role — can declare
    ``Depends(get_current_workspace)`` directly and still get the
    membership guard for free.
    """
    workspace = await WorkspaceRepository(session).get_by_id(workspace_id)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"workspace {workspace_id} not found",
        )
    role = await MembershipRepository(session).get_role(workspace_id=workspace_id, user_id=user.id)
    if role is None and not user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not a member of this workspace",
        )
    return WorkspaceContext(user=user, workspace=workspace, role=role)


def require_workspace_role(
    min_role: WorkspaceRole,
) -> Callable[[WorkspaceContext], Awaitable[WorkspaceContext]]:
    """Build a Depends-callable enforcing ``role ≥ min_role`` (Superadmin bypass).

    Usage::

        @router.post("/workspaces/{workspace_id}/connections",
                     dependencies=[Depends(require_workspace_role(WorkspaceRole.EDITOR))])
        async def create_connection(...): ...

    Or as a value-returning Depends to access the resolved context::

        async def endpoint(
            ctx: WorkspaceContext = Depends(require_workspace_role(WorkspaceRole.VIEWER)),
        ): ...
    """

    async def _checker(
        ctx: WorkspaceContext = Depends(get_current_workspace),  # noqa: B008
    ) -> WorkspaceContext:
        if ctx.user.is_superadmin:
            return ctx
        # role is non-None here — get_current_workspace already rejected
        # non-superadmin non-members with 403.
        assert ctx.role is not None  # invariant guarded above
        if not has_at_least(ctx.role, min_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"requires role {min_role.value!r} or higher (you have {ctx.role.value!r})"
                ),
            )
        return ctx

    return _checker


__all__ = [
    "WorkspaceContext",
    "get_current_workspace",
    "require_workspace_role",
]
