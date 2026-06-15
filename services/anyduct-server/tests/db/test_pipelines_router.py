"""Pipeline CRUD + version-history end-to-end (Step 8.5d).

The most interesting branch is :meth:`PipelineRepository.ensure_version`'s
idempotency: a PATCH whose config matches the current row reuses that
row (no version bump), while a PATCH that changes anything in
``config_json`` creates a new immutable snapshot. Both branches are
covered alongside the standard CRUD audit pairing.
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
from anyduct_server.db.enums import AuthMethod, WorkspaceRole
from anyduct_server.db.models import (
    AuditLog,
    Membership,
    Pipeline,
    PipelineVersion,
    User,
    Workspace,
)
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
        database_url="postgresql+asyncpg://stub:stub@stub:5432/stub",
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


async def _login(client: httpx.AsyncClient, *, email: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": "hunter2"})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def _sample_config(*, source_conn: str = "src", sink_conn: str = "dst") -> dict[str, Any]:
    """Minimal valid PipelineConfig — server will inject ``name``."""
    return {
        "source": {"connection": source_conn, "query": "select 1"},
        "sink": {"connection": sink_conn, "table": "out", "mode": "append"},
    }


async def _audit_rows(session: AsyncSession, *, resource_id: UUID) -> list[AuditLog]:
    await session.commit()
    result = await session.execute(
        select(AuditLog)
        .where(AuditLog.resource_id == str(resource_id))
        .order_by(AuditLog.created_at)
    )
    return list(result.scalars().all())


async def _version_rows(session: AsyncSession, *, pipeline_id: UUID) -> list[PipelineVersion]:
    await session.commit()
    result = await session.execute(
        select(PipelineVersion)
        .where(PipelineVersion.pipeline_id == pipeline_id)
        .order_by(PipelineVersion.version)
    )
    return list(result.scalars().all())


# --- POST -------------------------------------------------------------------


async def test_post_creates_pipeline_with_version_1(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pp-create@example.com")
    ws = await _seed_workspace(session, slug="pp-create", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={
                "name": "etl-daily",
                "description": "nightly load",
                "config": _sample_config(),
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "etl-daily"
    assert body["current_version"] == 1
    assert body["current_config_json"]["name"] == "etl-daily"  # server-injected
    assert body["current_config_json"]["source"]["connection"] == "src"

    versions = await _version_rows(session, pipeline_id=UUID(body["id"]))
    assert [(v.version, v.is_current) for v in versions] == [(1, True)]

    rows = await _audit_rows(session, resource_id=UUID(body["id"]))
    assert [r.action for r in rows] == ["pipeline.create"]
    assert rows[0].after_json["current_version"] == 1


async def test_post_fanout_config_round_trips(session: AsyncSession) -> None:
    """Fan-out (multiple sinks, ADR-0026) survives validate → dump → store."""
    user = await _seed_user(session, email="pp-fanout@example.com")
    ws = await _seed_workspace(session, slug="pp-fanout", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={
                "name": "fan-out",
                "config": {
                    "source": {"connection": "src", "query": "select 1"},
                    "sinks": [
                        {"connection": "warehouse", "table": "t", "mode": "append"},
                        {"connection": "archive", "table": "t", "mode": "overwrite"},
                    ],
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201, resp.text
    cfg = resp.json()["current_config_json"]
    assert cfg["sink"] is None
    assert [s["connection"] for s in cfg["sinks"]] == ["warehouse", "archive"]


async def test_post_dag_config_round_trips(session: AsyncSession) -> None:
    """Multi-task DAG (ADR-0028) survives validate → dump → store."""
    user = await _seed_user(session, email="pp-dag@example.com")
    ws = await _seed_workspace(session, slug="pp-dag", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={
                "name": "etl-dag",
                "config": {
                    "tasks": [
                        {
                            "name": "stage",
                            "source": {"connection": "src", "query": "select 1"},
                            "sink": {"connection": "dst", "table": "staging"},
                        },
                        {
                            "name": "publish",
                            "source": {"connection": "dst", "query": "select * from staging"},
                            "sink": {"connection": "dst", "table": "final"},
                            "depends_on": ["stage"],
                        },
                    ]
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201, resp.text
    cfg = resp.json()["current_config_json"]
    assert cfg["source"] is None
    assert [t["name"] for t in cfg["tasks"]] == ["stage", "publish"]
    assert cfg["tasks"][1]["depends_on"] == ["stage"]


async def test_pipeline_triggers_set_and_get(session: AsyncSession) -> None:
    """PUT then GET downstream triggers (ADR-0029)."""
    user = await _seed_user(session, email="pp-trig@example.com")
    ws = await _seed_workspace(session, slug="pp-trig", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        headers = {"Authorization": f"Bearer {token}"}
        a = (
            await client.post(
                f"/workspaces/{ws.id}/pipelines",
                json={"name": "a", "config": _sample_config()},
                headers=headers,
            )
        ).json()
        b = (
            await client.post(
                f"/workspaces/{ws.id}/pipelines",
                json={"name": "b", "config": _sample_config()},
                headers=headers,
            )
        ).json()

        put = await client.put(
            f"/workspaces/{ws.id}/pipelines/{a['id']}/triggers",
            json={"target_pipeline_ids": [b["id"]]},
            headers=headers,
        )
        assert put.status_code == 200, put.text
        assert put.json()["target_pipeline_ids"] == [b["id"]]

        got = await client.get(f"/workspaces/{ws.id}/pipelines/{a['id']}/triggers", headers=headers)
        assert got.json()["target_pipeline_ids"] == [b["id"]]

    rows = await _audit_rows(session, resource_id=UUID(a["id"]))
    assert "pipeline.triggers_set" in [r.action for r in rows]


async def test_pipeline_triggers_reject_self_and_missing(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pp-trig2@example.com")
    ws = await _seed_workspace(session, slug="pp-trig2", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        headers = {"Authorization": f"Bearer {token}"}
        a = (
            await client.post(
                f"/workspaces/{ws.id}/pipelines",
                json={"name": "a", "config": _sample_config()},
                headers=headers,
            )
        ).json()
        # self-trigger → 400
        self_resp = await client.put(
            f"/workspaces/{ws.id}/pipelines/{a['id']}/triggers",
            json={"target_pipeline_ids": [a["id"]]},
            headers=headers,
        )
        assert self_resp.status_code == 400, self_resp.text
        # unknown target → 404
        missing = await client.put(
            f"/workspaces/{ws.id}/pipelines/{a['id']}/triggers",
            json={"target_pipeline_ids": [str(uuid4())]},
            headers=headers,
        )
        assert missing.status_code == 404, missing.text


async def test_post_graph_config_round_trips(session: AsyncSession) -> None:
    """Dataflow graph (ADR-0030) survives validate → dump → store."""
    user = await _seed_user(session, email="pp-graph@example.com")
    ws = await _seed_workspace(session, slug="pp-graph", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={
                "name": "graph",
                "config": {
                    "graph": {
                        "nodes": [
                            {"id": "s", "type": "source", "connection": "src", "query": "select 1"},
                            {"id": "ka", "type": "sink", "connection": "dst", "table": "a"},
                            {"id": "kb", "type": "sink", "connection": "dst", "table": "b"},
                        ],
                        "edges": [
                            {"from_node": "s", "to_node": "ka", "when": "data['t'] == 'a'"},
                            {"from_node": "s", "to_node": "kb", "when": "data['t'] == 'b'"},
                        ],
                    }
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201, resp.text
    cfg = resp.json()["current_config_json"]
    assert cfg["source"] is None
    assert [n["id"] for n in cfg["graph"]["nodes"]] == ["s", "ka", "kb"]
    assert cfg["graph"]["edges"][0]["when"] == "data['t'] == 'a'"


async def test_post_both_sink_and_sinks_returns_422(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pp-both@example.com")
    ws = await _seed_workspace(session, slug="pp-both", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={
                "name": "bad",
                "config": {
                    "source": {"connection": "src"},
                    "sink": {"connection": "a"},
                    "sinks": [{"connection": "b"}],
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422, resp.text


async def test_post_invalid_config_returns_422(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pp-bad@example.com")
    ws = await _seed_workspace(session, slug="pp-bad", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={"name": "no-source", "config": {"sink": {"connection": "d"}}},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422
    assert "source" in resp.json()["detail"]


async def test_post_duplicate_name_returns_409(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pp-dup@example.com")
    ws = await _seed_workspace(session, slug="pp-dup", user=user, role=WorkspaceRole.EDITOR)
    session.add(Pipeline(workspace_id=ws.id, name="taken-name"))
    await session.flush()
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={"name": "taken-name", "config": _sample_config()},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409


async def test_post_viewer_forbidden(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pp-viewer@example.com")
    ws = await _seed_workspace(session, slug="pp-viewer", user=user, role=WorkspaceRole.VIEWER)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={"name": "x", "config": _sample_config()},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


# --- GET --------------------------------------------------------------------


async def test_list_returns_pipelines_with_current_version(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session, email="pp-list@example.com")
    ws = await _seed_workspace(session, slug="pp-list", user=user, role=WorkspaceRole.VIEWER)
    app = _build_app(session)
    # Use repository directly to seed via the same path the router would use.
    from anyduct_server.pipelines.repository import PipelineRepository

    repo = PipelineRepository(session)
    await repo.add(
        workspace_id=ws.id,
        name="a",
        description=None,
        config_json={"name": "a", "source": {"connection": "s"}, "sink": {"connection": "d"}},
        created_by_user_id=user.id,
    )
    await session.flush()
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/pipelines",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["name"] for r in rows] == ["a"]
    assert rows[0]["current_version"] == 1


async def test_get_single_404(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pp-404@example.com")
    ws = await _seed_workspace(session, slug="pp-404", user=user, role=WorkspaceRole.VIEWER)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/pipelines/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


# --- PATCH ------------------------------------------------------------------


async def test_patch_metadata_only_does_not_bump_version(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session, email="pp-meta@example.com")
    ws = await _seed_workspace(session, slug="pp-meta", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        created = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={"name": "p", "config": _sample_config()},
            headers={"Authorization": f"Bearer {token}"},
        )
        pid = created.json()["id"]
        resp = await client.patch(
            f"/workspaces/{ws.id}/pipelines/{pid}",
            json={"description": "now described"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["description"] == "now described"
    assert resp.json()["current_version"] == 1

    versions = await _version_rows(session, pipeline_id=UUID(pid))
    assert len(versions) == 1


async def test_patch_config_diff_creates_new_version(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pp-diff@example.com")
    ws = await _seed_workspace(session, slug="pp-diff", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        created = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={"name": "p", "config": _sample_config()},
            headers={"Authorization": f"Bearer {token}"},
        )
        pid = created.json()["id"]
        resp = await client.patch(
            f"/workspaces/{ws.id}/pipelines/{pid}",
            json={"config": _sample_config(source_conn="src-v2")},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["current_version"] == 2

    versions = await _version_rows(session, pipeline_id=UUID(pid))
    assert [(v.version, v.is_current) for v in versions] == [(1, False), (2, True)]

    rows = await _audit_rows(session, resource_id=UUID(pid))
    # create + update
    assert [r.action for r in rows] == ["pipeline.create", "pipeline.update"]
    assert rows[1].after_json["version_created"] is True


async def test_patch_config_identical_is_no_op(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pp-idem@example.com")
    ws = await _seed_workspace(session, slug="pp-idem", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        created = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={"name": "p", "config": _sample_config()},
            headers={"Authorization": f"Bearer {token}"},
        )
        pid = created.json()["id"]
        resp = await client.patch(
            f"/workspaces/{ws.id}/pipelines/{pid}",
            json={"config": _sample_config()},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["current_version"] == 1  # no bump

    versions = await _version_rows(session, pipeline_id=UUID(pid))
    assert len(versions) == 1

    rows = await _audit_rows(session, resource_id=UUID(pid))
    assert rows[-1].after_json["version_created"] is False


async def test_patch_empty_body_returns_400(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pp-empty@example.com")
    ws = await _seed_workspace(session, slug="pp-empty", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        created = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={"name": "p", "config": _sample_config()},
            headers={"Authorization": f"Bearer {token}"},
        )
        pid = created.json()["id"]
        resp = await client.patch(
            f"/workspaces/{ws.id}/pipelines/{pid}",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 400


async def test_patch_name_collision_returns_409(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pp-coll@example.com")
    ws = await _seed_workspace(session, slug="pp-coll", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        a = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={"name": "a", "config": _sample_config()},
            headers={"Authorization": f"Bearer {token}"},
        )
        await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={"name": "b", "config": _sample_config()},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp = await client.patch(
            f"/workspaces/{ws.id}/pipelines/{a.json()['id']}",
            json={"name": "b"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409


# --- DELETE -----------------------------------------------------------------


async def test_delete_cascades_versions_and_records_audit(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session, email="pp-del@example.com")
    ws = await _seed_workspace(session, slug="pp-del", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        created = await client.post(
            f"/workspaces/{ws.id}/pipelines",
            json={"name": "doomed", "config": _sample_config()},
            headers={"Authorization": f"Bearer {token}"},
        )
        pid = UUID(created.json()["id"])
        resp = await client.delete(
            f"/workspaces/{ws.id}/pipelines/{pid}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 204
    versions = await _version_rows(session, pipeline_id=pid)
    assert versions == []
    rows = await _audit_rows(session, resource_id=pid)
    assert [r.action for r in rows] == ["pipeline.create", "pipeline.delete"]


# --- GET /versions ----------------------------------------------------------


async def test_versions_endpoint_returns_history(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pp-versions@example.com")
    ws = await _seed_workspace(session, slug="pp-versions", user=user, role=WorkspaceRole.VIEWER)
    # Use repo to seed multi-version history.
    from anyduct_server.pipelines.repository import PipelineRepository

    repo = PipelineRepository(session)
    pipeline, _ = await repo.add(
        workspace_id=ws.id,
        name="evolving",
        description=None,
        config_json={
            "name": "evolving",
            "source": {"connection": "s"},
            "sink": {"connection": "d"},
        },
        created_by_user_id=user.id,
    )
    await repo.ensure_version(
        pipeline,
        {
            "name": "evolving",
            "source": {"connection": "s2"},
            "sink": {"connection": "d"},
        },
        created_by_user_id=user.id,
    )

    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/pipelines/{pipeline.id}/versions",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert [(r["version"], r["is_current"]) for r in body] == [(1, False), (2, True)]
    assert body[1]["config_json"]["source"]["connection"] == "s2"


async def test_versions_endpoint_404_for_unknown_pipeline(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session, email="pp-v-404@example.com")
    ws = await _seed_workspace(session, slug="pp-v-404", user=user, role=WorkspaceRole.VIEWER)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/pipelines/{uuid4()}/versions",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404
