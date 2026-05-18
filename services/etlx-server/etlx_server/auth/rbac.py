"""Role-based access control primitives (Step 8.3, ADR-0023).

The four workspace roles form a strict hierarchy:

    Owner (4)  ⊇  Editor (3)  ⊇  Runner (2)  ⊇  Viewer (1)

Why hierarchical works for the ADR-0023 capability matrix:

* Owner = member management + workspace deletion + everything Editor can do.
* Editor = all resource CRUD (which naturally subsumes "trigger pipeline").
* Runner = trigger + run inspection + DLQ reprocess — a subset of Editor.
* Viewer = read-only.

So requiring "editor or higher" denies Runner and Viewer but allows Owner —
exactly the policy the table describes. A more granular permission system
(per-action grants) is a future expansion; the simple ranking covers the
ADR's four-role baseline.

Note the global ``users.is_superadmin`` flag is **not** modeled here — it is
checked in :func:`etlx_server.auth.workspace_context.require_workspace_role`
because it bypasses the membership table entirely.
"""

from __future__ import annotations

from etlx_server.db.enums import WorkspaceRole

_ROLE_RANK: dict[WorkspaceRole, int] = {
    WorkspaceRole.VIEWER: 1,
    WorkspaceRole.RUNNER: 2,
    WorkspaceRole.EDITOR: 3,
    WorkspaceRole.OWNER: 4,
}


def role_rank(role: WorkspaceRole) -> int:
    """Return the integer precedence of ``role`` (higher = more privileged)."""
    return _ROLE_RANK[role]


def has_at_least(actual: WorkspaceRole, required: WorkspaceRole) -> bool:
    """Does ``actual`` satisfy ``required`` under the role hierarchy?"""
    return _ROLE_RANK[actual] >= _ROLE_RANK[required]


__all__ = ["has_at_least", "role_rank"]
