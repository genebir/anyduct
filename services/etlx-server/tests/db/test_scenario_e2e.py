"""End-to-end scenario tests (Step 8.7).

Exercises the full HTTP surface in the same order a real user would:

    login → create workspace → create connections → create pipeline
        → (optional) schedule → dry-run → trigger → list/get run
        → inspect audit trail

The point is to catch *composition* bugs that single-endpoint tests
can't see: a missing FK, an audit row that drops because of a session
detach, an RBAC dep that doesn't compose with another, a UUID that
serializes one way in one endpoint and another way in the next.

Direct user seeding is the only "back-door" we take — there is no
signup endpoint yet (auth.py exposes login/refresh/logout/me only),
so users get provisioned via the ORM and then *everything else*
flows through the public HTTP API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import httpx
import pytest
from etlx_server.app_factory import create_app
from etlx_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from etlx_server.auth.password_service import PasswordService
from etlx_server.db.enums import AuthMethod, RunStatus
from etlx_server.db.models import Run, User
from etlx_server.dependencies import get_session
from etlx_server.settings import Settings
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend

pytestmark = pytest.mark.asyncio


# --- fixtures ---------------------------------------------------------------


def _build_app(session: AsyncSession) -> tuple[FastAPI, StaticSecretBackend]:
    private, public = generate_rsa_keypair_pem(bits=2048)
    settings = Settings(
        database_url="postgresql+asyncpg://stub:stub@stub:5432/stub",  # pragma: allowlist secret
        auth_jwt_private_key_pem=private.decode("utf-8"),
        auth_jwt_public_key_pem=public.decode("utf-8"),
        auth_jwt_access_ttl_seconds=120,
        auth_jwt_refresh_ttl_seconds=240,
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
    backend = StaticSecretBackend()
    app.state.secret_backend = backend
    return app, backend


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_user(session: AsyncSession, *, email: str) -> User:
    """The only back-door — no signup endpoint exists yet."""
    user = User(
        email=email.lower(),
        name="Scenario User",
        auth_method=AuthMethod.LOCAL,
        password_hash=PasswordService(rounds=4).hash("hunter2"),  # pragma: allowlist secret
    )
    session.add(user)
    await session.flush()
    await session.commit()  # so the HTTP-side login can see them
    return user


async def _login(client: httpx.AsyncClient, *, email: str) -> str:
    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": "hunter2"},  # pragma: allowlist secret
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sample_pipeline_config(
    *,
    source_conn: str,
    sink_conn: str,
) -> dict[str, Any]:
    """Minimal valid PipelineConfig — server injects ``name``."""
    return {
        "source": {"connection": source_conn, "query": "select 1"},
        "sink": {"connection": sink_conn, "table": "out", "mode": "append"},
    }


# --- the main scenario -----------------------------------------------------


async def test_full_happy_path(session: AsyncSession) -> None:
    """Sign in → workspace → connections → pipeline → schedule → dry-run → trigger → run.

    Each step is a single HTTP call. We commit between steps where the
    router would normally do so itself (every mutation endpoint pairs
    its write with ``session.commit()``); the test fixture's outer
    transaction will still roll the whole tree back on teardown.
    """
    user = await _seed_user(session, email="scenario-happy@example.com")
    app, _backend = _build_app(session)

    async with _client(app) as client:
        token = await _login(client, email=user.email)
        headers = _auth(token)

        # 1) Create a workspace — the caller becomes its first Owner.
        ws_resp = await client.post(
            "/workspaces",
            json={"name": "Scenario", "slug": "scenario-ws", "color_hex": "#FF3D8B"},
            headers=headers,
        )
        assert ws_resp.status_code == 201, ws_resp.text
        ws_id = UUID(ws_resp.json()["id"])

        # 2) Add two connections (source + sink). sqlite :memory: keeps
        # the dry-run health check honest — they actually open + close.
        for name in ("src", "dst"):
            resp = await client.post(
                f"/workspaces/{ws_id}/connections",
                json={
                    "name": name,
                    "type": "sqlite",
                    "config": {"database": ":memory:"},
                    "secrets": {},
                },
                headers=headers,
            )
            assert resp.status_code == 201, (name, resp.text)

        # 3) Create the pipeline.
        pipeline_resp = await client.post(
            f"/workspaces/{ws_id}/pipelines",
            json={
                "name": "etl-scenario",
                "description": "scenario e2e",
                "config": _sample_pipeline_config(source_conn="src", sink_conn="dst"),
            },
            headers=headers,
        )
        assert pipeline_resp.status_code == 201, pipeline_resp.text
        pipeline_id = UUID(pipeline_resp.json()["id"])
        assert pipeline_resp.json()["current_version"] == 1

        # 4) Attach a schedule.
        sched_resp = await client.post(
            f"/workspaces/{ws_id}/pipelines/{pipeline_id}/schedules",
            json={
                "name": "nightly",
                "mode": "batch",
                "cron_expr": "0 3 * * *",
            },
            headers=headers,
        )
        assert sched_resp.status_code == 201, sched_resp.text

        # 5) Dry-run — proves config + connections + secrets all line up.
        dr_resp = await client.post(
            f"/workspaces/{ws_id}/pipelines/{pipeline_id}/dry-run",
            headers=headers,
        )
        assert dr_resp.status_code == 200, dr_resp.text
        body = dr_resp.json()
        assert body["ok"] is True
        assert body["errors"] == []
        assert {c["name"] for c in body["connectors"]} == {"src", "dst"}

        # 6) Trigger — enqueue a pending run for the current version.
        trig_resp = await client.post(
            f"/workspaces/{ws_id}/pipelines/{pipeline_id}/trigger",
            json={},
            headers=headers,
        )
        assert trig_resp.status_code == 202, trig_resp.text
        run_id = UUID(trig_resp.json()["id"])
        assert trig_resp.json()["status"] == "pending"
        assert trig_resp.json()["schedule_id"] is None
        assert trig_resp.json()["triggered_by_user_id"] == str(user.id)

        # 7) The run shows up in the workspace listing + drill-down works.
        listing = await client.get(f"/workspaces/{ws_id}/runs", headers=headers)
        assert listing.status_code == 200
        listed_ids = {row["id"] for row in listing.json()}
        assert str(run_id) in listed_ids

        single = await client.get(f"/workspaces/{ws_id}/runs/{run_id}", headers=headers)
        assert single.status_code == 200
        assert single.json()["status"] == "pending"
        # No worker has touched the row yet — heartbeat/worker_id remain null.
        assert single.json()["worker_id"] is None
        assert single.json()["heartbeat_at"] is None

        # 8) Audit trail — every state-changing call we made above should
        # appear, in order. There is no audit row for the dry-run (read-only)
        # or for any GET. Listing requires SuperAdmin for the global view,
        # so we scope it to this workspace where the caller is Owner.
        audit_resp = await client.get(
            "/audit",
            params={"workspace_id": str(ws_id)},
            headers=headers,
        )
        assert audit_resp.status_code == 200, audit_resp.text
        # Audit returns newest first; flip for chronological reading.
        actions = [row["action"] for row in reversed(audit_resp.json())]
        # Workspace + 2 connections + pipeline + schedule + run trigger.
        # Exact order is deterministic because we make them serially.
        assert actions == [
            "workspace.create",
            "connection.create",
            "connection.create",
            "pipeline.create",
            "schedule.create",
            "run.trigger",
        ]


# --- retry-after-failure variant -------------------------------------------


async def test_retry_after_simulated_worker_failure(session: AsyncSession) -> None:
    """Trigger a run, simulate the worker failing it, retry — verify lineage.

    The worker is the only legitimate writer for ``status`` transitions
    (Step 9), so to test retry without the worker existing we flip the
    Run row's status directly. The HTTP retry endpoint itself never
    touches an existing row — it just clones into a fresh pending one.
    """
    user = await _seed_user(session, email="scenario-retry@example.com")
    app, _ = _build_app(session)

    async with _client(app) as client:
        token = await _login(client, email=user.email)
        headers = _auth(token)

        # Same minimal setup — workspace, connections, pipeline, trigger.
        ws_id = UUID(
            (
                await client.post(
                    "/workspaces",
                    json={"name": "R", "slug": "r-retry", "color_hex": "#FF3D8B"},
                    headers=headers,
                )
            ).json()["id"]
        )
        for name in ("src", "dst"):
            r = await client.post(
                f"/workspaces/{ws_id}/connections",
                json={
                    "name": name,
                    "type": "sqlite",
                    "config": {"database": ":memory:"},
                    "secrets": {},
                },
                headers=headers,
            )
            assert r.status_code == 201
        pipeline_id = UUID(
            (
                await client.post(
                    f"/workspaces/{ws_id}/pipelines",
                    json={
                        "name": "p",
                        "config": _sample_pipeline_config(source_conn="src", sink_conn="dst"),
                    },
                    headers=headers,
                )
            ).json()["id"]
        )
        original_run_id = UUID(
            (
                await client.post(
                    f"/workspaces/{ws_id}/pipelines/{pipeline_id}/trigger",
                    json={},
                    headers=headers,
                )
            ).json()["id"]
        )

        # Simulate the worker failing the row.
        row = (await session.execute(select(Run).where(Run.id == original_run_id))).scalar_one()
        row.status = RunStatus.FAILED
        row.error_class = "ValueError"
        row.error_message = "simulated worker failure"
        await session.commit()

        # The retry endpoint clones it — original is unchanged.
        retry_resp = await client.post(
            f"/workspaces/{ws_id}/runs/{original_run_id}/retry",
            headers=headers,
        )
        assert retry_resp.status_code == 202, retry_resp.text
        new_run_id = UUID(retry_resp.json()["id"])
        assert new_run_id != original_run_id
        assert retry_resp.json()["status"] == "pending"

        # Verify lineage + that the original is untouched.
        await session.commit()
        new_row = (await session.execute(select(Run).where(Run.id == new_run_id))).scalar_one()
        assert new_row.result_json == {"retry_of": str(original_run_id)}
        assert new_row.pipeline_version_id == row.pipeline_version_id

        original_after = (
            await session.execute(select(Run).where(Run.id == original_run_id))
        ).scalar_one()
        assert original_after.status == RunStatus.FAILED
        assert original_after.error_message == "simulated worker failure"


# --- non-member cross-workspace boundary -----------------------------------


async def test_non_member_cannot_see_workspace_resources(session: AsyncSession) -> None:
    """Two users in two workspaces — neither can read the other's runs/pipelines."""
    alice = await _seed_user(session, email="alice-scenario@example.com")
    bob = await _seed_user(session, email="bob-scenario@example.com")
    app, _ = _build_app(session)

    async with _client(app) as client:
        alice_token = await _login(client, email=alice.email)
        bob_token = await _login(client, email=bob.email)

        # Alice builds her workspace with a pipeline + connection.
        ws_id = UUID(
            (
                await client.post(
                    "/workspaces",
                    json={"name": "A", "slug": "alice-ws", "color_hex": "#FF3D8B"},
                    headers=_auth(alice_token),
                )
            ).json()["id"]
        )
        for name in ("src", "dst"):
            await client.post(
                f"/workspaces/{ws_id}/connections",
                json={
                    "name": name,
                    "type": "sqlite",
                    "config": {"database": ":memory:"},
                    "secrets": {},
                },
                headers=_auth(alice_token),
            )
        pipeline_id = UUID(
            (
                await client.post(
                    f"/workspaces/{ws_id}/pipelines",
                    json={
                        "name": "p",
                        "config": _sample_pipeline_config(source_conn="src", sink_conn="dst"),
                    },
                    headers=_auth(alice_token),
                )
            ).json()["id"]
        )

        # Bob tries to read Alice's workspace — he's not a member.
        assert (
            await client.get(f"/workspaces/{ws_id}", headers=_auth(bob_token))
        ).status_code == 403
        # ...and can't list her pipelines (also 403 — get_current_workspace fires first).
        assert (
            await client.get(f"/workspaces/{ws_id}/pipelines", headers=_auth(bob_token))
        ).status_code == 403
        # ...nor dry-run them.
        assert (
            await client.post(
                f"/workspaces/{ws_id}/pipelines/{pipeline_id}/dry-run",
                headers=_auth(bob_token),
            )
        ).status_code == 403
        # ...nor trigger.
        assert (
            await client.post(
                f"/workspaces/{ws_id}/pipelines/{pipeline_id}/trigger",
                json={},
                headers=_auth(bob_token),
            )
        ).status_code == 403
