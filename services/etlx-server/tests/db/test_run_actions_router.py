"""Run action + SSE log streaming end-to-end (Step 8.6).

Two surfaces:

* ``POST /runs/{rid}/retry`` — only failed/cancelled runs may be
  retried; the new row clones ``pipeline_version_id`` / ``schedule_id``
  and links back via ``result_json.retry_of``.
* ``GET /runs/{rid}/logs/stream`` — Server-Sent Events. A run that is
  already terminal at subscription time should drain its existing
  logs and close within the post-terminal idle window.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from etlx_server.app_factory import create_app
from etlx_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from etlx_server.auth.password_service import PasswordService
from etlx_server.db.enums import AuthMethod, LogLevel, RunStatus, WorkspaceRole
from etlx_server.db.models import (
    AuditLog,
    Membership,
    Pipeline,
    PipelineVersion,
    Run,
    RunLog,
    User,
    Workspace,
)
from etlx_server.dependencies import get_session
from etlx_server.settings import Settings
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


# --- fixtures (mirror the rest of the db/ suite) ---------------------------


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
    # Stub factory so the SSE route's Depends(get_session_factory) resolves
    # even on the 404 fast-path. The streaming test overrides it explicitly.
    app.state.session_factory = _NullSessionFactory()
    return app


class _NullSessionFactory:
    """Placeholder for ``app.state.session_factory`` — only the SSE happy
    path actually uses it (tests override it then)."""

    def __call__(self) -> _NullSessionFactory:
        return self

    async def __aenter__(self) -> AsyncSession:  # pragma: no cover — should never run
        raise RuntimeError("test must override get_session_factory before streaming")

    async def __aexit__(self, *_: object) -> None:  # pragma: no cover
        return None


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_user(session: AsyncSession, *, email: str) -> User:
    user = User(
        email=email.lower(),
        name="U",
        auth_method=AuthMethod.LOCAL,
        password_hash=PasswordService(rounds=4).hash("hunter2"),  # pragma: allowlist secret
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
    session: AsyncSession,
    *,
    workspace_id: UUID,
    name: str,
    config: dict[str, Any] | None = None,
) -> tuple[Pipeline, PipelineVersion]:
    p = Pipeline(workspace_id=workspace_id, name=name)
    session.add(p)
    await session.flush()
    pv = PipelineVersion(
        pipeline_id=p.id, version=1, config_json=config or {"name": name}, is_current=True
    )
    session.add(pv)
    await session.flush()
    return p, pv


async def _seed_run(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    pipeline_id: UUID,
    pipeline_version_id: UUID,
    status: RunStatus = RunStatus.FAILED,
    error_class: str | None = "ValueError",
    error_message: str | None = "boom",
    result_json: dict[str, Any] | None = None,
) -> Run:
    r = Run(
        workspace_id=workspace_id,
        pipeline_id=pipeline_id,
        pipeline_version_id=pipeline_version_id,
        status=status,
        error_class=error_class,
        error_message=error_message,
        result_json=result_json or {"core_run_id": str(uuid4())},
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


async def _audit_rows(session: AsyncSession, *, resource_id: UUID) -> list[AuditLog]:
    await session.commit()
    result = await session.execute(
        select(AuditLog)
        .where(AuditLog.resource_id == str(resource_id))
        .order_by(AuditLog.created_at)
    )
    return list(result.scalars().all())


# --- POST /retry ----------------------------------------------------------


async def test_retry_clones_failed_run(session: AsyncSession) -> None:
    user = await _seed_user(session, email="ra-retry@example.com")
    ws = await _seed_workspace(session, slug="ra-retry", user=user, role=WorkspaceRole.RUNNER)
    pipeline, pv = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="p")
    original = await _seed_run(
        session,
        workspace_id=ws.id,
        pipeline_id=pipeline.id,
        pipeline_version_id=pv.id,
        status=RunStatus.FAILED,
    )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/runs/{original.id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    new_run_id = UUID(body["id"])
    assert new_run_id != original.id
    assert body["pipeline_version_id"] == str(pv.id)
    assert body["status"] == "pending"

    # Original untouched.
    await session.commit()
    refreshed_original = (
        await session.execute(select(Run).where(Run.id == original.id))
    ).scalar_one()
    assert refreshed_original.status == RunStatus.FAILED

    # New row carries retry lineage in result_json.
    new_row = (await session.execute(select(Run).where(Run.id == new_run_id))).scalar_one()
    assert new_row.result_json == {"retry_of": str(original.id)}

    rows = await _audit_rows(session, resource_id=new_run_id)
    assert [r.action for r in rows] == ["run.retry"]
    assert rows[0].after_json["retry_of"] == str(original.id)


async def test_retry_409_for_succeeded_run(session: AsyncSession) -> None:
    """succeeded runs cannot be 'retried' — a fresh manual trigger is the right path."""
    user = await _seed_user(session, email="ra-retry-ok@example.com")
    ws = await _seed_workspace(session, slug="ra-retry-ok", user=user, role=WorkspaceRole.RUNNER)
    pipeline, pv = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="p")
    original = await _seed_run(
        session,
        workspace_id=ws.id,
        pipeline_id=pipeline.id,
        pipeline_version_id=pv.id,
        status=RunStatus.SUCCEEDED,
        error_class=None,
        error_message=None,
    )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/runs/{original.id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409


async def test_retry_409_for_running_run(session: AsyncSession) -> None:
    user = await _seed_user(session, email="ra-retry-run@example.com")
    ws = await _seed_workspace(session, slug="ra-retry-run", user=user, role=WorkspaceRole.RUNNER)
    pipeline, pv = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="p")
    original = await _seed_run(
        session,
        workspace_id=ws.id,
        pipeline_id=pipeline.id,
        pipeline_version_id=pv.id,
        status=RunStatus.RUNNING,
        error_class=None,
        error_message=None,
    )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/runs/{original.id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409


async def test_retry_works_for_cancelled_run(session: AsyncSession) -> None:
    user = await _seed_user(session, email="ra-retry-canc@example.com")
    ws = await _seed_workspace(session, slug="ra-retry-canc", user=user, role=WorkspaceRole.RUNNER)
    pipeline, pv = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="p")
    original = await _seed_run(
        session,
        workspace_id=ws.id,
        pipeline_id=pipeline.id,
        pipeline_version_id=pv.id,
        status=RunStatus.CANCELLED,
    )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/runs/{original.id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 202


async def test_retry_viewer_forbidden(session: AsyncSession) -> None:
    user = await _seed_user(session, email="ra-retry-viewer@example.com")
    ws = await _seed_workspace(
        session, slug="ra-retry-viewer", user=user, role=WorkspaceRole.VIEWER
    )
    pipeline, pv = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="p")
    original = await _seed_run(
        session,
        workspace_id=ws.id,
        pipeline_id=pipeline.id,
        pipeline_version_id=pv.id,
        status=RunStatus.FAILED,
    )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/runs/{original.id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


async def test_retry_404_for_other_workspace_run(session: AsyncSession) -> None:
    user = await _seed_user(session, email="ra-retry-xws@example.com")
    ws_a = await _seed_workspace(
        session, slug="ra-retry-xws-a", user=user, role=WorkspaceRole.RUNNER
    )
    ws_b = Workspace(name="B", slug="ra-retry-xws-b", color_hex="#FF3D8B")
    session.add(ws_b)
    await session.flush()
    pipeline_b, pv_b = await _seed_pipeline_with_version(session, workspace_id=ws_b.id, name="b")
    original = await _seed_run(
        session,
        workspace_id=ws_b.id,
        pipeline_id=pipeline_b.id,
        pipeline_version_id=pv_b.id,
        status=RunStatus.FAILED,
    )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws_a.id}/runs/{original.id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


# --- GET /logs/stream -----------------------------------------------------


async def test_logs_stream_drains_existing_and_closes_on_terminal(
    session: AsyncSession,
) -> None:
    """A run that is already terminal at subscription time should stream
    its existing log rows, then close after the idle window.

    Note: we use a small idle window via monkey-patch is overkill — the
    default 2s window is fine in CI. We just assert the stream
    terminates and yields the seeded events.
    """
    user = await _seed_user(session, email="ra-sse@example.com")
    ws = await _seed_workspace(session, slug="ra-sse", user=user, role=WorkspaceRole.VIEWER)
    pipeline, pv = await _seed_pipeline_with_version(session, workspace_id=ws.id, name="p")
    run = await _seed_run(
        session,
        workspace_id=ws.id,
        pipeline_id=pipeline.id,
        pipeline_version_id=pv.id,
        status=RunStatus.SUCCEEDED,
        error_class=None,
        error_message=None,
    )
    base = datetime.now(UTC) - timedelta(minutes=1)
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
    await session.commit()  # so the streaming sessions see the rows

    app = _build_app(session)
    # The stream uses a separate session per poll (via app.state.session_factory)
    # — for tests we'll override that to reuse the same session.
    from etlx_server.dependencies import get_session_factory

    class _StubFactory:
        """Yield the existing test session inside an ``async with`` block."""

        def __call__(self) -> Any:
            return self

        async def __aenter__(self) -> AsyncSession:
            return session

        async def __aexit__(self, *_: object) -> None:
            return None

    app.dependency_overrides[get_session_factory] = lambda: _StubFactory()

    async with _client(app) as client:
        token = await _login(client, email=user.email)
        # Bound the test with a wall-clock timeout — the stream should
        # close itself in ~2s once terminal + drained.
        events: list[str] = []
        async with client.stream(
            "GET",
            f"/workspaces/{ws.id}/runs/{run.id}/logs/stream",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

            async def _collect() -> None:
                async for chunk in resp.aiter_text():
                    events.append(chunk)

            await asyncio.wait_for(_collect(), timeout=8.0)

    body = "".join(events)
    # Three frames, one per seeded log row.
    assert body.count("event: log") == 3
    assert "step 0" in body
    assert "step 2" in body


async def test_logs_stream_404_for_unknown_run(session: AsyncSession) -> None:
    user = await _seed_user(session, email="ra-sse-404@example.com")
    ws = await _seed_workspace(session, slug="ra-sse-404", user=user, role=WorkspaceRole.VIEWER)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/runs/{uuid4()}/logs/stream",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


# --- POST /backfill (ADR-0039) ----------------------------------------------


async def test_backfill_enqueues_run_with_cursor_range(session: AsyncSession) -> None:
    user = await _seed_user(session, email="ra-bf@example.com")
    ws = await _seed_workspace(session, slug="ra-bf", user=user, role=WorkspaceRole.RUNNER)
    pipeline, pv = await _seed_pipeline_with_version(
        session,
        workspace_id=ws.id,
        name="p",
        config={
            "name": "p",
            "source": {"connection": "s", "query": "SELECT id FROM seed", "cursor_column": "id"},
            "sink": {"connection": "k", "table": "out", "mode": "append"},
        },
    )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/backfill",
            json={"cursor_from": "2026-01-01", "cursor_to": "2026-02-01"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["pipeline_version_id"] == str(pv.id)

    await session.commit()
    run = (await session.execute(select(Run).where(Run.id == UUID(body["id"])))).scalar_one()
    assert run.result_json["backfill"] == {
        "cursor_from": "2026-01-01",
        "cursor_to": "2026-02-01",
    }
    assert run.schedule_id is None


async def test_backfill_without_cursor_column_is_400(session: AsyncSession) -> None:
    user = await _seed_user(session, email="ra-bf-no@example.com")
    ws = await _seed_workspace(session, slug="ra-bf-no", user=user, role=WorkspaceRole.RUNNER)
    pipeline, _ = await _seed_pipeline_with_version(
        session,
        workspace_id=ws.id,
        name="p",
        config={
            "name": "p",
            "source": {"connection": "s", "query": "SELECT id FROM seed"},
            "sink": {"connection": "k", "table": "out", "mode": "append"},
        },
    )
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/backfill",
            json={"cursor_from": 1, "cursor_to": 10},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 400, resp.text
