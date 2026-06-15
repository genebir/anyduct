"""Pipeline action endpoints end-to-end (Step 8.6).

Covers POST /dry-run and POST /trigger:

* dry-run resolves connection references in the workspace, builds the
  pipeline, optionally health-checks each connector (we use the real
  sqlite :memory: connector for the happy path so the answer means
  something);
* trigger creates a pending Run row from the current PipelineVersion
  and pairs it with a ``run.trigger`` audit entry.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from anyduct_server.app_factory import create_app
from anyduct_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from anyduct_server.auth.password_service import PasswordService
from anyduct_server.db.enums import AuthMethod, RunStatus, WorkspaceRole
from anyduct_server.db.models import (
    AuditLog,
    Connection,
    Membership,
    Pipeline,
    PipelineVersion,
    Run,
    User,
    Workspace,
)
from anyduct_server.dependencies import get_session
from anyduct_server.settings import Settings
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend

pytestmark = pytest.mark.asyncio


# --- fixtures ---------------------------------------------------------------


def _build_app(
    session: AsyncSession,
    *,
    backend: StaticSecretBackend | None = None,
) -> tuple[FastAPI, StaticSecretBackend]:
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
    chosen = backend if backend is not None else StaticSecretBackend()
    app.state.secret_backend = chosen
    return app, chosen


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


async def _seed_connection(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    name: str,
    type: str = "sqlite",
    config: dict[str, Any] | None = None,
) -> Connection:
    conn = Connection(
        workspace_id=workspace_id,
        name=name,
        type=type,
        config_json=config or {"database": ":memory:"},
        secret_refs=[],
    )
    session.add(conn)
    await session.flush()
    return conn


def _sample_pipeline_config(
    name: str,
    *,
    source_conn: str,
    sink_conn: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "source": {"connection": source_conn, "query": "select 1"},
        "sink": {"connection": sink_conn, "table": "out", "mode": "append"},
    }


async def _seed_pipeline_with_version(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    name: str,
    config: dict[str, Any],
) -> tuple[Pipeline, PipelineVersion]:
    p = Pipeline(workspace_id=workspace_id, name=name)
    session.add(p)
    await session.flush()
    pv = PipelineVersion(pipeline_id=p.id, version=1, config_json=config, is_current=True)
    session.add(pv)
    await session.flush()
    return p, pv


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


# --- dry-run ---------------------------------------------------------------


async def test_dry_run_happy_path_sqlite(session: AsyncSession) -> None:
    """sqlite :memory: source + sink → all connectors instantiate + pass health_check."""
    user = await _seed_user(session, email="pa-dr-ok@example.com")
    ws = await _seed_workspace(session, slug="pa-dr-ok", user=user, role=WorkspaceRole.RUNNER)
    await _seed_connection(session, workspace_id=ws.id, name="src")
    await _seed_connection(session, workspace_id=ws.id, name="dst")
    pipeline, _ = await _seed_pipeline_with_version(
        session,
        workspace_id=ws.id,
        name="p",
        config=_sample_pipeline_config("p", source_conn="src", sink_conn="dst"),
    )
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/dry-run",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["errors"] == []
    names = {c["name"]: c for c in body["connectors"]}
    assert set(names) == {"src", "dst"}
    assert all(c["ok"] for c in body["connectors"])
    # Phase DD (2026-05-29): warnings field always present; empty when
    # there's nothing to nudge the user about (declarative-only pipeline).
    assert body["warnings"] == []


async def test_dry_run_surfaces_column_mapping_lint_for_opaque_transform(
    session: AsyncSession,
) -> None:
    """Phase DD (2026-05-29, ADR-0048): a pipeline with a python transform
    that doesn't declare ``column_mapping`` should still pass dry-run
    (ok=True) but include an advisory ``column_mapping_recommended``
    warning, so the UI can surface "add a column_mapping for accurate
    catalog lineage" beside the otherwise-green checks.
    """
    user = await _seed_user(session, email="pa-dr-lint@example.com")
    ws = await _seed_workspace(session, slug="pa-dr-lint", user=user, role=WorkspaceRole.RUNNER)
    await _seed_connection(session, workspace_id=ws.id, name="src")
    await _seed_connection(session, workspace_id=ws.id, name="dst")
    cfg = _sample_pipeline_config("p", source_conn="src", sink_conn="dst")
    cfg["transforms"] = [
        {
            "type": "custom_python",
            "code": "def transform(record):\n    return record\n",
            # Intentionally no column_mapping — exactly what the lint flags.
        }
    ]
    pipeline, _ = await _seed_pipeline_with_version(
        session, workspace_id=ws.id, name="p", config=cfg
    )
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/dry-run",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Warnings are advisory — ok stays True.
    assert body["ok"] is True
    assert body["errors"] == []
    codes = [w["code"] for w in body["warnings"]]
    assert "column_mapping_recommended" in codes
    matching = next(w for w in body["warnings"] if w["code"] == "column_mapping_recommended")
    assert matching["location"] == "transforms.0"
    assert "column_mapping" in matching["message"]


async def test_dry_run_no_lint_warning_when_column_mapping_declared(
    session: AsyncSession,
) -> None:
    """Same pipeline shape with ``column_mapping`` declared → no warning.
    Confirms the lint actually responds to the user's escape hatch."""
    user = await _seed_user(session, email="pa-dr-nolint@example.com")
    ws = await _seed_workspace(session, slug="pa-dr-nolint", user=user, role=WorkspaceRole.RUNNER)
    await _seed_connection(session, workspace_id=ws.id, name="src")
    await _seed_connection(session, workspace_id=ws.id, name="dst")
    cfg = _sample_pipeline_config("p", source_conn="src", sink_conn="dst")
    # Source query exposes ``id`` so the column_mapping's source column
    # actually exists upstream (the Phase FF consistency lint would
    # otherwise flag it as ``column_mapping_unknown_source_column``).
    cfg["source"]["query"] = "select 1 as id"
    cfg["transforms"] = [
        {
            "type": "custom_python",
            "code": "def transform(record):\n    return record\n",
            "column_mapping": {"id": ["id"]},
        }
    ]
    pipeline, _ = await _seed_pipeline_with_version(
        session, workspace_id=ws.id, name="p", config=cfg
    )
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/dry-run",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    # Narrowed: the Phase DD lint specifically should not fire when the
    # user declared a column_mapping. (Other Phase FF+ lint rules can
    # still emit advice as new ones land.)
    assert not any(w["code"] == "column_mapping_recommended" for w in body["warnings"])
    assert not any(w["code"] == "column_mapping_unknown_source_column" for w in body["warnings"])


async def test_dry_run_reports_missing_connection(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pa-dr-miss@example.com")
    ws = await _seed_workspace(session, slug="pa-dr-miss", user=user, role=WorkspaceRole.RUNNER)
    # Only ``src`` exists; ``dst`` is referenced but absent.
    await _seed_connection(session, workspace_id=ws.id, name="src")
    pipeline, _ = await _seed_pipeline_with_version(
        session,
        workspace_id=ws.id,
        name="p",
        config=_sample_pipeline_config("p", source_conn="src", sink_conn="missing"),
    )
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/dry-run",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert any("missing" in e or "not found" in e for e in body["errors"])


async def test_dry_run_reports_unknown_connector_type(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pa-dr-type@example.com")
    ws = await _seed_workspace(session, slug="pa-dr-type", user=user, role=WorkspaceRole.RUNNER)
    await _seed_connection(
        session,
        workspace_id=ws.id,
        name="src",
        type="not-a-real-connector",
        config={},
    )
    await _seed_connection(session, workspace_id=ws.id, name="dst")
    pipeline, _ = await _seed_pipeline_with_version(
        session,
        workspace_id=ws.id,
        name="p",
        config=_sample_pipeline_config("p", source_conn="src", sink_conn="dst"),
    )
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/dry-run",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    by_name = {c["name"]: c for c in body["connectors"]}
    assert by_name["src"]["ok"] is False
    assert "not registered" in (by_name["src"]["error"] or "")
    # dst was fine on its own — the failure mode is per-connector.
    assert by_name["dst"]["ok"] is True


async def test_dry_run_resolves_secret(session: AsyncSession) -> None:
    """A connection whose ``database`` is a ``${SECRET:...}`` placeholder still passes."""
    user = await _seed_user(session, email="pa-dr-secret@example.com")
    ws = await _seed_workspace(session, slug="pa-dr-secret", user=user, role=WorkspaceRole.RUNNER)
    conn_id = uuid4()
    secret_path = f"anyduct/{ws.id}/{conn_id}/db"
    src = Connection(
        id=conn_id,
        workspace_id=ws.id,
        name="src",
        type="sqlite",
        config_json={"database": "${SECRET:" + secret_path + "}"},
        secret_refs=[secret_path],
    )
    session.add(src)
    await _seed_connection(session, workspace_id=ws.id, name="dst")
    await session.flush()
    pipeline, _ = await _seed_pipeline_with_version(
        session,
        workspace_id=ws.id,
        name="p",
        config=_sample_pipeline_config("p", source_conn="src", sink_conn="dst"),
    )
    backend = StaticSecretBackend({secret_path: ":memory:"})
    app, _ = _build_app(session, backend=backend)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/dry-run",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


async def test_dry_run_viewer_forbidden(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pa-dr-viewer@example.com")
    ws = await _seed_workspace(session, slug="pa-dr-viewer", user=user, role=WorkspaceRole.VIEWER)
    await _seed_connection(session, workspace_id=ws.id, name="src")
    await _seed_connection(session, workspace_id=ws.id, name="dst")
    pipeline, _ = await _seed_pipeline_with_version(
        session,
        workspace_id=ws.id,
        name="p",
        config=_sample_pipeline_config("p", source_conn="src", sink_conn="dst"),
    )
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/dry-run",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


async def test_dry_run_404_for_unknown_pipeline(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pa-dr-404@example.com")
    ws = await _seed_workspace(session, slug="pa-dr-404", user=user, role=WorkspaceRole.RUNNER)
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{uuid4()}/dry-run",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


# --- trigger ---------------------------------------------------------------


async def test_trigger_creates_pending_run_and_audit(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pa-trg@example.com")
    ws = await _seed_workspace(session, slug="pa-trg", user=user, role=WorkspaceRole.RUNNER)
    await _seed_connection(session, workspace_id=ws.id, name="src")
    await _seed_connection(session, workspace_id=ws.id, name="dst")
    pipeline, pv = await _seed_pipeline_with_version(
        session,
        workspace_id=ws.id,
        name="p",
        config=_sample_pipeline_config("p", source_conn="src", sink_conn="dst"),
    )
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/trigger",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["schedule_id"] is None
    assert body["triggered_by_user_id"] == str(user.id)
    assert body["pipeline_version_id"] == str(pv.id)

    run_id = UUID(body["id"])
    # Cross-check via DB.
    await session.commit()
    row = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
    assert row.status == RunStatus.PENDING
    assert row.schedule_id is None
    assert row.pipeline_version_id == pv.id

    rows = await _audit_rows(session, resource_id=run_id)
    assert [r.action for r in rows] == ["run.trigger"]
    assert rows[0].after_json["source"] == "manual"
    assert rows[0].after_json["version"] == 1


async def test_trigger_viewer_forbidden(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pa-trg-viewer@example.com")
    ws = await _seed_workspace(session, slug="pa-trg-viewer", user=user, role=WorkspaceRole.VIEWER)
    pipeline, _ = await _seed_pipeline_with_version(
        session,
        workspace_id=ws.id,
        name="p",
        config=_sample_pipeline_config("p", source_conn="src", sink_conn="dst"),
    )
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/trigger",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


async def test_trigger_409_when_no_current_version(session: AsyncSession) -> None:
    """A row without any current PipelineVersion can't be triggered."""
    user = await _seed_user(session, email="pa-trg-novers@example.com")
    ws = await _seed_workspace(session, slug="pa-trg-novers", user=user, role=WorkspaceRole.RUNNER)
    # Pipeline with no version rows at all.
    p = Pipeline(workspace_id=ws.id, name="empty")
    session.add(p)
    await session.flush()
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{p.id}/trigger",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409


async def test_trigger_404_for_unknown_pipeline(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pa-trg-404@example.com")
    ws = await _seed_workspace(session, slug="pa-trg-404", user=user, role=WorkspaceRole.RUNNER)
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{uuid4()}/trigger",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


async def test_trigger_404_for_cross_workspace_pipeline(session: AsyncSession) -> None:
    """Same boundary check schedules use — leak no info about other workspaces."""
    user = await _seed_user(session, email="pa-trg-xws@example.com")
    ws = await _seed_workspace(session, slug="pa-trg-xws", user=user, role=WorkspaceRole.RUNNER)
    other_ws = Workspace(name="Other", slug="pa-trg-xws-other", color_hex="#FF3D8B")
    session.add(other_ws)
    await session.flush()
    other_pipeline, _ = await _seed_pipeline_with_version(
        session,
        workspace_id=other_ws.id,
        name="p",
        config=_sample_pipeline_config("p", source_conn="src", sink_conn="dst"),
    )
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines/{other_pipeline.id}/trigger",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404
