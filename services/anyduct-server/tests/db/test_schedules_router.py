"""Schedule CRUD end-to-end (Step 8.5e).

Covers the cron-validation matrix (batch/stream x valid/invalid/null),
the pipeline-workspace cross-check, RBAC, the toggle endpoint, and
audit pairing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
from anyduct_server.app_factory import create_app
from anyduct_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from anyduct_server.auth.password_service import PasswordService
from anyduct_server.db.enums import AuthMethod, PipelineMode, WorkspaceRole
from anyduct_server.db.models import AuditLog, Membership, Pipeline, User, Workspace
from anyduct_server.dependencies import get_session
from anyduct_server.settings import Settings
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


def _build_app(session: AsyncSession) -> FastAPI:
    private, public = generate_rsa_keypair_pem(bits=2048)
    settings = Settings(
        database_url="postgresql+asyncpg://stub:stub@stub:5432/stub",  # pragma: allowlist secret
        auth_jwt_private_key_pem=private.decode("utf-8"),
        auth_jwt_public_key_pem=public.decode("utf-8"),
        auth_jwt_access_ttl_seconds=60,
        auth_jwt_refresh_ttl_seconds=120,
    )
    app = create_app(settings=settings)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override_session
    app.state.password_service = PasswordService(rounds=4)
    app.state.jwt_service = JwtService(
        private_key_pem=private,
        public_key_pem=public,
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_audience,
        access_ttl_seconds=settings.auth_jwt_access_ttl_seconds,
        refresh_ttl_seconds=settings.auth_jwt_refresh_ttl_seconds,
    )
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        email=email.lower(),
        name="U",
        auth_method=AuthMethod.LOCAL,
        password_hash=PasswordService(rounds=4).hash("hunter2"),
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_workspace(
    session: AsyncSession, *, slug: str, user: User, role: WorkspaceRole
) -> Workspace:
    ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=user.id, role=role))
    await session.flush()
    return ws


async def _seed_pipeline(session: AsyncSession, *, workspace_id: UUID, name: str) -> Pipeline:
    p = Pipeline(workspace_id=workspace_id, name=name)
    session.add(p)
    await session.flush()
    return p


async def _login(client: httpx.AsyncClient, *, email: str) -> str:
    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": "hunter2"},  # pragma: allowlist secret
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


async def _audit_rows(session: AsyncSession, *, resource_id: UUID) -> list[AuditLog]:
    # Phase AAV follow-up (2026-06-01) — secondary ``id`` sort so two
    # audit rows written within the same microsecond don't reorder.
    # ``AuditLogRepository.query`` already does this; the test helper
    # should mirror it so create/delete flips don't flake. UUIDv7 IDs
    # are time-ordered, so an ascending ID secondary preserves
    # insertion order.
    await session.commit()
    result = await session.execute(
        select(AuditLog)
        .where(AuditLog.resource_id == str(resource_id))
        .order_by(AuditLog.created_at, AuditLog.id)
    )
    return list(result.scalars().all())


# --- POST -------------------------------------------------------------------


async def test_post_batch_with_valid_cron_creates_schedule(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session, email="sc-create@example.com")
    ws = await _seed_workspace(session, slug="sc-create", user=user, role=WorkspaceRole.EDITOR)
    pipeline = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules",
            json={
                "name": "nightly",
                "mode": "batch",
                "cron_expr": "0 3 * * *",
                "config_overrides": {"cursor_from": "2026-01-01"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "nightly"
    assert body["cron_expr"] == "0 3 * * *"
    assert body["mode"] == "batch"
    assert body["is_active"] is True
    rows = await _audit_rows(session, resource_id=UUID(body["id"]))
    assert [r.action for r in rows] == ["schedule.create"]
    assert rows[0].after_json["cron_expr"] == "0 3 * * *"


async def test_post_stream_allows_null_cron(session: AsyncSession) -> None:
    user = await _seed_user(session, email="sc-stream@example.com")
    ws = await _seed_workspace(session, slug="sc-stream", user=user, role=WorkspaceRole.EDITOR)
    pipeline = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules",
            json={"name": "always-on", "mode": "stream"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["cron_expr"] is None
    assert body["mode"] == "stream"


async def test_post_batch_missing_cron_returns_422(session: AsyncSession) -> None:
    user = await _seed_user(session, email="sc-batch-nocron@example.com")
    ws = await _seed_workspace(
        session, slug="sc-batch-nocron", user=user, role=WorkspaceRole.EDITOR
    )
    pipeline = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules",
            json={"name": "incomplete", "mode": "batch"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422
    assert "batch" in resp.json()["detail"].lower()


async def test_post_invalid_cron_returns_422(session: AsyncSession) -> None:
    user = await _seed_user(session, email="sc-badcron@example.com")
    ws = await _seed_workspace(session, slug="sc-badcron", user=user, role=WorkspaceRole.EDITOR)
    pipeline = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules",
            json={"name": "broken", "mode": "batch", "cron_expr": "this is not cron"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422
    assert "invalid cron" in resp.json()["detail"].lower()


async def test_post_viewer_forbidden(session: AsyncSession) -> None:
    user = await _seed_user(session, email="sc-viewer@example.com")
    ws = await _seed_workspace(session, slug="sc-viewer", user=user, role=WorkspaceRole.VIEWER)
    pipeline = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules",
            json={"name": "x", "mode": "batch", "cron_expr": "* * * * *"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


async def test_post_pipeline_in_other_workspace_returns_404(
    session: AsyncSession,
) -> None:
    """Cross-workspace pipeline access leaks no info — collapse to 404."""
    user = await _seed_user(session, email="sc-xws@example.com")
    ws = await _seed_workspace(session, slug="sc-xws", user=user, role=WorkspaceRole.EDITOR)
    # Pipeline lives in a DIFFERENT workspace the user isn't even a member of.
    other_ws = Workspace(name="Other", slug="sc-xws-other", color_hex="#FF3D8B")
    session.add(other_ws)
    await session.flush()
    other_pipeline = await _seed_pipeline(session, workspace_id=other_ws.id, name="p")

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{other_pipeline.id}/schedules",
            json={"name": "x", "mode": "batch", "cron_expr": "* * * * *"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


# --- GET --------------------------------------------------------------------


async def test_list_returns_schedules_sorted_by_name(session: AsyncSession) -> None:
    user = await _seed_user(session, email="sc-list@example.com")
    ws = await _seed_workspace(session, slug="sc-list", user=user, role=WorkspaceRole.EDITOR)
    pipeline = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        for name in ("b-second", "a-first"):
            await client.post(
                f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules",
                json={"name": name, "mode": "batch", "cron_expr": "* * * * *"},
                headers={"Authorization": f"Bearer {token}"},
            )
        resp = await client.get(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    names = [r["name"] for r in resp.json()]
    assert names == ["a-first", "b-second"]


async def test_get_single_404(session: AsyncSession) -> None:
    user = await _seed_user(session, email="sc-404@example.com")
    ws = await _seed_workspace(session, slug="sc-404", user=user, role=WorkspaceRole.VIEWER)
    pipeline = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


# --- PATCH ------------------------------------------------------------------


async def test_patch_updates_cron_and_records_audit(session: AsyncSession) -> None:
    user = await _seed_user(session, email="sc-patch@example.com")
    ws = await _seed_workspace(session, slug="sc-patch", user=user, role=WorkspaceRole.EDITOR)
    pipeline = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        created = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules",
            json={"name": "s", "mode": "batch", "cron_expr": "0 1 * * *"},
            headers={"Authorization": f"Bearer {token}"},
        )
        sid = UUID(created.json()["id"])
        resp = await client.patch(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules/{sid}",
            json={"cron_expr": "0 2 * * *"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["cron_expr"] == "0 2 * * *"
    rows = await _audit_rows(session, resource_id=sid)
    assert [r.action for r in rows] == ["schedule.create", "schedule.update"]
    assert rows[1].before_json["cron_expr"] == "0 1 * * *"
    assert rows[1].after_json["cron_expr"] == "0 2 * * *"


async def test_patch_invalid_cron_rejected(session: AsyncSession) -> None:
    user = await _seed_user(session, email="sc-patch-bad@example.com")
    ws = await _seed_workspace(session, slug="sc-patch-bad", user=user, role=WorkspaceRole.EDITOR)
    pipeline = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        created = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules",
            json={"name": "s", "mode": "batch", "cron_expr": "0 1 * * *"},
            headers={"Authorization": f"Bearer {token}"},
        )
        sid = created.json()["id"]
        resp = await client.patch(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules/{sid}",
            json={"cron_expr": "nope"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422


async def test_patch_empty_body_returns_400(session: AsyncSession) -> None:
    user = await _seed_user(session, email="sc-patch-empty@example.com")
    ws = await _seed_workspace(session, slug="sc-patch-empty", user=user, role=WorkspaceRole.EDITOR)
    pipeline = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        created = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules",
            json={"name": "s", "mode": "stream"},
            headers={"Authorization": f"Bearer {token}"},
        )
        sid = created.json()["id"]
        resp = await client.patch(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules/{sid}",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 400


# --- DELETE -----------------------------------------------------------------


async def test_delete_removes_row_and_records_audit(session: AsyncSession) -> None:
    user = await _seed_user(session, email="sc-del@example.com")
    ws = await _seed_workspace(session, slug="sc-del", user=user, role=WorkspaceRole.EDITOR)
    pipeline = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        created = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules",
            json={"name": "doomed", "mode": "batch", "cron_expr": "* * * * *"},
            headers={"Authorization": f"Bearer {token}"},
        )
        sid = UUID(created.json()["id"])
        resp = await client.delete(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules/{sid}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 204
    rows = await _audit_rows(session, resource_id=sid)
    assert [r.action for r in rows] == ["schedule.create", "schedule.delete"]
    assert rows[1].before_json["name"] == "doomed"


# --- TOGGLE -----------------------------------------------------------------


async def test_toggle_flips_is_active(session: AsyncSession) -> None:
    user = await _seed_user(session, email="sc-toggle@example.com")
    ws = await _seed_workspace(session, slug="sc-toggle", user=user, role=WorkspaceRole.EDITOR)
    pipeline = await _seed_pipeline(session, workspace_id=ws.id, name="p")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        created = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules",
            json={"name": "s", "mode": "batch", "cron_expr": "* * * * *"},
            headers={"Authorization": f"Bearer {token}"},
        )
        sid = UUID(created.json()["id"])
        assert created.json()["is_active"] is True

        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules/{sid}/toggle",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

        resp2 = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/schedules/{sid}/toggle",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["is_active"] is True

    rows = await _audit_rows(session, resource_id=sid)
    actions = [r.action for r in rows]
    assert actions == ["schedule.create", "schedule.toggle", "schedule.toggle"]
    assert rows[1].before_json["is_active"] is True
    assert rows[1].after_json["is_active"] is False


# --- repository-only checks reachable through HTTP --------------------------


async def test_update_unknown_field_via_repository_rejected(
    session: AsyncSession,
) -> None:
    """``ScheduleRepository.update`` whitelists allowed fields — direct kwarg drop."""
    from anyduct_server.schedules.repository import ScheduleRepository

    pipeline = await _seed_pipeline_helper(session)
    repo = ScheduleRepository(session)
    sched = await repo.add(
        pipeline_id=pipeline.id,
        name="s",
        mode=PipelineMode.BATCH,
        cron_expr="* * * * *",
        is_active=True,
        config_overrides={},
        created_by_user_id=None,
    )
    with pytest.raises(ValueError, match="unknown schedule fields"):
        await repo.update(sched, mode=PipelineMode.STREAM)  # type: ignore[arg-type]


async def _seed_pipeline_helper(session: AsyncSession) -> Pipeline:
    """Small helper for the repository-only test — workspace + pipeline, no user."""
    ws = Workspace(name="W", slug=f"sc-repo-{uuid4().hex[:8]}", color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    return await _seed_pipeline(session, workspace_id=ws.id, name="p")


# --- defensive: PATCH on schedule belonging to other pipeline returns 404 ---


async def test_patch_schedule_under_wrong_pipeline_returns_404(
    session: AsyncSession,
) -> None:
    """A schedule belongs to one pipeline; lookup under another's id must 404."""
    user = await _seed_user(session, email="sc-wrong-pl@example.com")
    ws = await _seed_workspace(session, slug="sc-wrong-pl", user=user, role=WorkspaceRole.EDITOR)
    pipeline_a = await _seed_pipeline(session, workspace_id=ws.id, name="a")
    pipeline_b = await _seed_pipeline(session, workspace_id=ws.id, name="b")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        created = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline_a.id}/schedules",
            json={"name": "s", "mode": "batch", "cron_expr": "* * * * *"},
            headers={"Authorization": f"Bearer {token}"},
        )
        sid = created.json()["id"]
        # Same workspace but the wrong pipeline.
        resp = await client.patch(
            f"/workspaces/{ws.id}/pipelines/{pipeline_b.id}/schedules/{sid}",
            json={"cron_expr": "0 2 * * *"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404
