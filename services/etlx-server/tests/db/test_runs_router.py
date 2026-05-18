"""Runs (read-only) end-to-end (Step 8.5e).

Runs are normally written by the worker engine (Step 9); these tests
seed rows directly via the ORM, then exercise the HTTP surface to
verify filtering, pagination, the workspace boundary, and the absence
of any mutation endpoint.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from etlx_server.app_factory import create_app
from etlx_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from etlx_server.auth.password_service import PasswordService
from etlx_server.db.enums import AuthMethod, LogLevel, RunStatus, WorkspaceRole
from etlx_server.db.models import (
    Membership,
    Pipeline,
    PipelineVersion,
    Run,
    RunLog,
    RunMetric,
    User,
    Workspace,
)
from etlx_server.dependencies import get_session
from etlx_server.settings import Settings
from fastapi import FastAPI
from httpx import ASGITransport
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


async def _seed_pipeline_with_version(
    session: AsyncSession, *, workspace_id: UUID, name: str
) -> tuple[Pipeline, PipelineVersion]:
    p = Pipeline(workspace_id=workspace_id, name=name)
    session.add(p)
    await session.flush()
    pv = PipelineVersion(pipeline_id=p.id, version=1, config_json={"name": name}, is_current=True)
    session.add(pv)
    await session.flush()
    return p, pv


async def _seed_run(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    pipeline_id: UUID,
    pipeline_version_id: UUID,
    status: RunStatus = RunStatus.SUCCEEDED,
    records_read: int = 10,
    records_written: int = 10,
    error_class: str | None = None,
    error_message: str | None = None,
) -> Run:
    r = Run(
        workspace_id=workspace_id,
        pipeline_id=pipeline_id,
        pipeline_version_id=pipeline_version_id,
        status=status,
        records_read=records_read,
        records_written=records_written,
        error_class=error_class,
        error_message=error_message,
        result_json={"core_run_id": str(uuid4())},
    )
    session.add(r)
    await session.flush()
    return r


async def _login(client: httpx.AsyncClient, *, email: str) -> str:
    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": "hunter2"},  # pragma: allowlist secret
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


# --- LIST -------------------------------------------------------------------


async def test_list_returns_runs_in_workspace(session: AsyncSession) -> None:
    user = await _seed_user(session, email="rn-list@example.com")
    ws = await _seed_workspace(session, slug="rn-list", user=user, role=WorkspaceRole.VIEWER)
    pipeline, pv = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="p")
    await _seed_run(
        session,
        workspace_id=ws.id,
        pipeline_id=pipeline.id,
        pipeline_version_id=pv.id,
    )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/runs",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "succeeded"
    assert body[0]["records_read"] == 10


async def test_list_filters_by_status(session: AsyncSession) -> None:
    user = await _seed_user(session, email="rn-filter@example.com")
    ws = await _seed_workspace(session, slug="rn-filter", user=user, role=WorkspaceRole.VIEWER)
    pipeline, pv = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="p")
    await _seed_run(
        session,
        workspace_id=ws.id,
        pipeline_id=pipeline.id,
        pipeline_version_id=pv.id,
        status=RunStatus.SUCCEEDED,
    )
    await _seed_run(
        session,
        workspace_id=ws.id,
        pipeline_id=pipeline.id,
        pipeline_version_id=pv.id,
        status=RunStatus.FAILED,
        error_class="ValueError",
        error_message="bad row",
    )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/runs",
            params={"status": "failed"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "failed"
    assert body[0]["error_class"] == "ValueError"


async def test_list_filters_by_pipeline_id(session: AsyncSession) -> None:
    user = await _seed_user(session, email="rn-by-pl@example.com")
    ws = await _seed_workspace(session, slug="rn-by-pl", user=user, role=WorkspaceRole.VIEWER)
    pipeline_a, pv_a = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="a")
    pipeline_b, pv_b = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="b")
    await _seed_run(
        session, workspace_id=ws.id, pipeline_id=pipeline_a.id, pipeline_version_id=pv_a.id
    )
    await _seed_run(
        session, workspace_id=ws.id, pipeline_id=pipeline_b.id, pipeline_version_id=pv_b.id
    )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/runs",
            params={"pipeline_id": str(pipeline_a.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["pipeline_id"] == str(pipeline_a.id)


async def test_list_does_not_leak_across_workspaces(session: AsyncSession) -> None:
    user = await _seed_user(session, email="rn-leak@example.com")
    ws_a = await _seed_workspace(session, slug="rn-leak-a", user=user, role=WorkspaceRole.VIEWER)
    ws_b = Workspace(name="B", slug="rn-leak-b", color_hex="#FF3D8B")
    session.add(ws_b)
    await session.flush()
    pipeline_a, pv_a = await _seed_pipeline_with_version(session, workspace_id=ws_a.id, name="a")
    pipeline_b, pv_b = await _seed_pipeline_with_version(session, workspace_id=ws_b.id, name="b")
    # The "leak" run belongs to ws_b — user is NOT a member.
    leak_run = await _seed_run(
        session, workspace_id=ws_b.id, pipeline_id=pipeline_b.id, pipeline_version_id=pv_b.id
    )
    await _seed_run(
        session, workspace_id=ws_a.id, pipeline_id=pipeline_a.id, pipeline_version_id=pv_a.id
    )

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        listed = await client.get(
            f"/workspaces/{ws_a.id}/runs",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Asking ws_a yields only ws_a's runs.
        assert listed.status_code == 200
        assert all(row["workspace_id"] == str(ws_a.id) for row in listed.json())

        # Direct GET of the leak run under ws_a → 404 (workspace-scoped lookup).
        direct = await client.get(
            f"/workspaces/{ws_a.id}/runs/{leak_run.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert direct.status_code == 404


async def test_list_respects_limit_and_offset(session: AsyncSession) -> None:
    user = await _seed_user(session, email="rn-page@example.com")
    ws = await _seed_workspace(session, slug="rn-page", user=user, role=WorkspaceRole.VIEWER)
    pipeline, pv = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="p")
    for _ in range(5):
        await _seed_run(
            session, workspace_id=ws.id, pipeline_id=pipeline.id, pipeline_version_id=pv.id
        )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        page = await client.get(
            f"/workspaces/{ws.id}/runs",
            params={"limit": 2, "offset": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert page.status_code == 200
    assert len(page.json()) == 2


# --- GET single -------------------------------------------------------------


async def test_get_single_returns_full_detail(session: AsyncSession) -> None:
    user = await _seed_user(session, email="rn-one@example.com")
    ws = await _seed_workspace(session, slug="rn-one", user=user, role=WorkspaceRole.VIEWER)
    pipeline, pv = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="p")
    run = await _seed_run(
        session, workspace_id=ws.id, pipeline_id=pipeline.id, pipeline_version_id=pv.id
    )
    run.worker_id = "worker-7"
    run.heartbeat_at = datetime.now(UTC) - timedelta(seconds=10)
    await session.flush()
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/runs/{run.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["worker_id"] == "worker-7"
    assert body["heartbeat_at"] is not None
    assert body["result_json"]["core_run_id"]


async def test_get_single_404(session: AsyncSession) -> None:
    user = await _seed_user(session, email="rn-404@example.com")
    ws = await _seed_workspace(session, slug="rn-404", user=user, role=WorkspaceRole.VIEWER)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/runs/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


# --- LOGS / METRICS ---------------------------------------------------------


async def test_logs_endpoint_returns_chronological(session: AsyncSession) -> None:
    user = await _seed_user(session, email="rn-logs@example.com")
    ws = await _seed_workspace(session, slug="rn-logs", user=user, role=WorkspaceRole.VIEWER)
    pipeline, pv = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="p")
    run = await _seed_run(
        session, workspace_id=ws.id, pipeline_id=pipeline.id, pipeline_version_id=pv.id
    )
    base = datetime.now(UTC)
    for i, level in enumerate([LogLevel.INFO, LogLevel.WARNING, LogLevel.ERROR]):
        session.add(
            RunLog(
                run_id=run.id,
                ts=base + timedelta(seconds=i),
                level=level,
                message=f"step {i}",
                context_json={"i": i},
            )
        )
    await session.flush()
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/runs/{run.id}/logs",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert [row["level"] for row in body] == ["info", "warning", "error"]
    assert body[0]["context_json"] == {"i": 0}


async def test_metrics_endpoint_returns_rows(session: AsyncSession) -> None:
    user = await _seed_user(session, email="rn-metrics@example.com")
    ws = await _seed_workspace(session, slug="rn-metrics", user=user, role=WorkspaceRole.VIEWER)
    pipeline, pv = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="p")
    run = await _seed_run(
        session, workspace_id=ws.id, pipeline_id=pipeline.id, pipeline_version_id=pv.id
    )
    session.add(
        RunMetric(run_id=run.id, name="records_read_total", value=42.0, attrs_json={"source": "pg"})
    )
    session.add(RunMetric(run_id=run.id, name="duration_seconds", value=1.5, attrs_json={}))
    await session.flush()
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/runs/{run.id}/metrics",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    names = {row["name"] for row in resp.json()}
    assert names == {"records_read_total", "duration_seconds"}


async def test_logs_endpoint_404_on_other_workspace(session: AsyncSession) -> None:
    """Logs lookup respects the workspace boundary too — not just the run row."""
    user = await _seed_user(session, email="rn-logs-leak@example.com")
    ws_a = await _seed_workspace(
        session, slug="rn-logs-leak-a", user=user, role=WorkspaceRole.VIEWER
    )
    ws_b = Workspace(name="B", slug="rn-logs-leak-b", color_hex="#FF3D8B")
    session.add(ws_b)
    await session.flush()
    pipeline_b, pv_b = await _seed_pipeline_with_version(session, workspace_id=ws_b.id, name="b")
    other_run = await _seed_run(
        session, workspace_id=ws_b.id, pipeline_id=pipeline_b.id, pipeline_version_id=pv_b.id
    )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws_a.id}/runs/{other_run.id}/logs",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


# --- no write surface -------------------------------------------------------


async def test_runs_has_no_mutation_endpoint(session: AsyncSession) -> None:
    """All run mutations belong to the worker engine; HTTP exposes 405 for POST/PATCH/DELETE."""
    user = await _seed_user(session, email="rn-nomut@example.com")
    ws = await _seed_workspace(session, slug="rn-nomut", user=user, role=WorkspaceRole.VIEWER)
    pipeline, pv = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="p")
    run = await _seed_run(
        session, workspace_id=ws.id, pipeline_id=pipeline.id, pipeline_version_id=pv.id
    )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        for method in ("post", "patch", "delete"):
            fn = getattr(client, method)
            resp = await fn(
                f"/workspaces/{ws.id}/runs/{run.id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            # 404 for POST (no such route), 405 for PATCH/DELETE on a GET-only path.
            assert resp.status_code in (404, 405), (method, resp.status_code)
