"""Role hierarchy unit tests (Step 8.3)."""

from __future__ import annotations

import pytest
from etlx_server.auth.rbac import has_at_least, role_rank
from etlx_server.db.enums import WorkspaceRole

ALL_ROLES = [
    WorkspaceRole.VIEWER,
    WorkspaceRole.RUNNER,
    WorkspaceRole.EDITOR,
    WorkspaceRole.OWNER,
]


def test_rank_is_strictly_increasing() -> None:
    ranks = [role_rank(r) for r in ALL_ROLES]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)


@pytest.mark.parametrize("role", ALL_ROLES)
def test_role_satisfies_itself(role: WorkspaceRole) -> None:
    assert has_at_least(role, role)


def test_owner_satisfies_everything() -> None:
    for required in ALL_ROLES:
        assert has_at_least(WorkspaceRole.OWNER, required)


def test_viewer_only_satisfies_viewer() -> None:
    assert has_at_least(WorkspaceRole.VIEWER, WorkspaceRole.VIEWER)
    for required in (WorkspaceRole.RUNNER, WorkspaceRole.EDITOR, WorkspaceRole.OWNER):
        assert not has_at_least(WorkspaceRole.VIEWER, required)


def test_runner_satisfies_viewer_only() -> None:
    assert has_at_least(WorkspaceRole.RUNNER, WorkspaceRole.VIEWER)
    assert has_at_least(WorkspaceRole.RUNNER, WorkspaceRole.RUNNER)
    assert not has_at_least(WorkspaceRole.RUNNER, WorkspaceRole.EDITOR)
    assert not has_at_least(WorkspaceRole.RUNNER, WorkspaceRole.OWNER)


def test_editor_satisfies_through_editor() -> None:
    assert has_at_least(WorkspaceRole.EDITOR, WorkspaceRole.VIEWER)
    assert has_at_least(WorkspaceRole.EDITOR, WorkspaceRole.RUNNER)
    assert has_at_least(WorkspaceRole.EDITOR, WorkspaceRole.EDITOR)
    assert not has_at_least(WorkspaceRole.EDITOR, WorkspaceRole.OWNER)
