"""Connection CRUD + test endpoint end-to-end (Step 8.5c).

Tests inject a :class:`StaticSecretBackend` into ``app.state`` so the
write/delete paths actually run without standing up Vault. SQLite
(``:memory:``) is used for the POST /test happy path — no external
service required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from etlx_server.app_factory import create_app
from etlx_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from etlx_server.auth.password_service import PasswordService
from etlx_server.db.enums import AuthMethod, WorkspaceRole
from etlx_server.db.models import AuditLog, Connection, Membership, User, Workspace
from etlx_server.dependencies import get_session
from etlx_server.settings import Settings
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import EnvSecretBackend, StaticSecretBackend
from etl_plugins.core.exceptions import SecretError

pytestmark = pytest.mark.asyncio


# --- fixtures ---------------------------------------------------------------


def _build_app(
    session: AsyncSession,
    *,
    backend: StaticSecretBackend | EnvSecretBackend | None = None,
) -> tuple[FastAPI, StaticSecretBackend | EnvSecretBackend]:
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
    chosen_backend = backend if backend is not None else StaticSecretBackend()
    app.state.secret_backend = chosen_backend
    return app, chosen_backend


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


async def _seed_workspace_with_role(
    session: AsyncSession,
    *,
    slug: str,
    user: User,
    role: WorkspaceRole,
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


async def _audit_rows(session: AsyncSession, *, resource_id: UUID) -> list[AuditLog]:
    await session.commit()
    result = await session.execute(
        select(AuditLog)
        .where(AuditLog.resource_id == str(resource_id))
        .order_by(AuditLog.created_at)
    )
    return list(result.scalars().all())


# --- POST -------------------------------------------------------------------


async def test_post_creates_connection_with_secret(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="owner-cr@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-create", user=owner, role=WorkspaceRole.EDITOR
    )
    app, backend = _build_app(session)

    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/connections",
            json={
                "name": "prod-pg",
                "type": "postgres",
                "config": {
                    "host": "db.example.com",
                    "user": "etlx",
                    "password": {"$secret": "db_password"},
                    "port": 5432,
                },
                "secrets": {"db_password": "s3cret!"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "prod-pg"
    assert body["type"] == "postgres"
    # Placeholder is what landed in the row — never the plain value.
    placeholder = body["config_json"]["password"]
    assert placeholder.startswith("${SECRET:etlx/")
    assert "s3cret!" not in resp.text
    expected_path = f"etlx/{ws.id}/{body['id']}/db_password"
    assert body["secret_refs"] == [expected_path]

    # Backend got the value at the expected path.
    assert backend.get(expected_path) == "s3cret!"

    rows = await _audit_rows(session, resource_id=UUID(body["id"]))
    assert [r.action for r in rows] == ["connection.create"]
    assert rows[0].after_json["secret_refs"] == [expected_path]


async def test_post_orphan_marker_returns_422(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="orphan-marker@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-orphan-m", user=owner, role=WorkspaceRole.EDITOR
    )
    app, _ = _build_app(session)

    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/connections",
            json={
                "name": "x",
                "type": "postgres",
                "config": {"password": {"$secret": "missing_key"}},
                "secrets": {},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422
    assert "unknown secret key" in resp.json()["detail"]


async def test_post_orphan_secret_value_returns_422(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="orphan-val@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-orphan-v", user=owner, role=WorkspaceRole.EDITOR
    )
    app, _ = _build_app(session)

    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/connections",
            json={
                "name": "x",
                "type": "postgres",
                "config": {"host": "db"},
                "secrets": {"unused": "v"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422
    assert "orphan" in resp.json()["detail"]


async def test_post_readonly_backend_returns_503(session: AsyncSession) -> None:
    """EnvSecretBackend.set raises NotImplementedError → 503 with explicit detail."""
    owner = await _seed_user(session, email="ro-backend@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-ro", user=owner, role=WorkspaceRole.EDITOR
    )
    app, _ = _build_app(session, backend=EnvSecretBackend())

    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/connections",
            json={
                "name": "x",
                "type": "postgres",
                "config": {"password": {"$secret": "k"}},
                "secrets": {"k": "v"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 503
    assert "read-only" in resp.json()["detail"]


async def test_post_duplicate_name_returns_409(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="dup-conn@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-dup", user=owner, role=WorkspaceRole.EDITOR
    )
    session.add(
        Connection(
            workspace_id=ws.id,
            name="taken",
            type="postgres",
            config_json={"host": "x"},
            secret_refs=[],
        )
    )
    await session.flush()
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=owner.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/connections",
            json={"name": "taken", "type": "postgres", "config": {}, "secrets": {}},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409


async def test_post_viewer_forbidden(session: AsyncSession) -> None:
    user = await _seed_user(session, email="viewer-c@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-viewer", user=user, role=WorkspaceRole.VIEWER
    )
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/connections",
            json={"name": "x", "type": "postgres", "config": {}, "secrets": {}},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


# --- GET --------------------------------------------------------------------


async def test_list_returns_workspace_connections(session: AsyncSession) -> None:
    user = await _seed_user(session, email="list-c@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-list", user=user, role=WorkspaceRole.VIEWER
    )
    session.add_all(
        [
            Connection(
                workspace_id=ws.id, name="a", type="postgres", config_json={}, secret_refs=[]
            ),
            Connection(workspace_id=ws.id, name="b", type="sqlite", config_json={}, secret_refs=[]),
        ]
    )
    await session.flush()
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/connections",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert [c["name"] for c in resp.json()] == ["a", "b"]


async def test_get_single_404_when_in_other_workspace(session: AsyncSession) -> None:
    user = await _seed_user(session, email="cross-ws@example.com")
    ws_mine = await _seed_workspace_with_role(
        session, slug="conn-mine", user=user, role=WorkspaceRole.VIEWER
    )
    other = Workspace(name="Other", slug="conn-other-ws", color_hex="#000000")
    session.add(other)
    await session.flush()
    other_conn = Connection(
        workspace_id=other.id, name="x", type="postgres", config_json={}, secret_refs=[]
    )
    session.add(other_conn)
    await session.flush()

    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws_mine.id}/connections/{other_conn.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


# --- PATCH ------------------------------------------------------------------


async def test_patch_rename_only(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pat-rename@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-rename", user=user, role=WorkspaceRole.EDITOR
    )
    conn = Connection(
        workspace_id=ws.id,
        name="old",
        type="postgres",
        config_json={"host": "x"},
        secret_refs=[],
    )
    session.add(conn)
    await session.flush()
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.patch(
            f"/workspaces/{ws.id}/connections/{conn.id}",
            json={"name": "new"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "new"


async def test_patch_replaces_secrets(session: AsyncSession) -> None:
    """A new config that drops a secret triggers backend.delete for the old key."""
    user = await _seed_user(session, email="pat-sec@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-pat-sec", user=user, role=WorkspaceRole.EDITOR
    )
    app, backend = _build_app(session)

    async with _client(app) as client:
        token = await _login(client, email=user.email)
        # Create with one secret.
        first = await client.post(
            f"/workspaces/{ws.id}/connections",
            json={
                "name": "rotating",
                "type": "postgres",
                "config": {"password": {"$secret": "old_key"}},
                "secrets": {"old_key": "v1"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert first.status_code == 201
        conn_id = first.json()["id"]
        old_path = first.json()["secret_refs"][0]
        assert backend.get(old_path) == "v1"

        # PATCH with a totally new secret key — old one should be deleted.
        second = await client.patch(
            f"/workspaces/{ws.id}/connections/{conn_id}",
            json={
                "config": {"token": {"$secret": "new_key"}},
                "secrets": {"new_key": "v2"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    assert second.status_code == 200, second.text
    new_path = second.json()["secret_refs"][0]
    assert new_path != old_path
    assert backend.get(new_path) == "v2"
    with pytest.raises(SecretError):
        backend.get(old_path)


async def test_patch_empty_body_returns_400(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pat-empty@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-pat-empty", user=user, role=WorkspaceRole.EDITOR
    )
    conn = Connection(workspace_id=ws.id, name="x", type="postgres", config_json={}, secret_refs=[])
    session.add(conn)
    await session.flush()
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.patch(
            f"/workspaces/{ws.id}/connections/{conn.id}",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 400


async def test_patch_secrets_without_config_returns_400(session: AsyncSession) -> None:
    user = await _seed_user(session, email="pat-sec-only@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-pat-secs", user=user, role=WorkspaceRole.EDITOR
    )
    conn = Connection(workspace_id=ws.id, name="x", type="postgres", config_json={}, secret_refs=[])
    session.add(conn)
    await session.flush()
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.patch(
            f"/workspaces/{ws.id}/connections/{conn.id}",
            json={"secrets": {"k": "v"}},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 400


# --- DELETE -----------------------------------------------------------------


async def test_delete_removes_row_and_backend_secrets(session: AsyncSession) -> None:
    user = await _seed_user(session, email="del-c@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-del", user=user, role=WorkspaceRole.EDITOR
    )
    app, backend = _build_app(session)

    async with _client(app) as client:
        token = await _login(client, email=user.email)
        created = await client.post(
            f"/workspaces/{ws.id}/connections",
            json={
                "name": "purge-me",
                "type": "postgres",
                "config": {"password": {"$secret": "k"}},
                "secrets": {"k": "v"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        conn_id = created.json()["id"]
        path = created.json()["secret_refs"][0]
        assert backend.get(path) == "v"

        resp = await client.delete(
            f"/workspaces/{ws.id}/connections/{conn_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 204
    with pytest.raises(SecretError):
        backend.get(path)
    rows = await _audit_rows(session, resource_id=UUID(conn_id))
    # create + delete recorded.
    assert [r.action for r in rows] == ["connection.create", "connection.delete"]


async def test_delete_unknown_returns_404(session: AsyncSession) -> None:
    user = await _seed_user(session, email="del-404@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-del-404", user=user, role=WorkspaceRole.EDITOR
    )
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.delete(
            f"/workspaces/{ws.id}/connections/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404


# --- POST /test -------------------------------------------------------------


async def test_test_endpoint_sqlite_memory_returns_ok(session: AsyncSession) -> None:
    user = await _seed_user(session, email="tst-ok@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-tst-ok", user=user, role=WorkspaceRole.RUNNER
    )
    conn = Connection(
        workspace_id=ws.id,
        name="mem",
        type="sqlite",
        config_json={"database": ":memory:"},
        secret_refs=[],
    )
    session.add(conn)
    await session.flush()
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/connections/{conn.id}/test",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "error": None}


async def test_test_endpoint_unknown_connector_returns_error(
    session: AsyncSession,
) -> None:
    user = await _seed_user(session, email="tst-bad-type@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-tst-bad", user=user, role=WorkspaceRole.RUNNER
    )
    conn = Connection(
        workspace_id=ws.id,
        name="bogus",
        type="not-a-real-connector",
        config_json={},
        secret_refs=[],
    )
    session.add(conn)
    await session.flush()
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/connections/{conn.id}/test",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "not registered" in body["error"]


async def test_test_endpoint_resolves_secret(session: AsyncSession) -> None:
    """Confirm the tester reads the secret backend before constructing the connector."""
    user = await _seed_user(session, email="tst-secret@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-tst-secret", user=user, role=WorkspaceRole.RUNNER
    )
    # Stash a fake :memory: behind a secret to prove the resolver runs.
    conn_id = uuid4()
    secret_path = f"etlx/{ws.id}/{conn_id}/db"
    conn = Connection(
        id=conn_id,
        workspace_id=ws.id,
        name="resolved",
        type="sqlite",
        config_json={"database": "${SECRET:" + secret_path + "}"},
        secret_refs=[secret_path],
    )
    session.add(conn)
    await session.flush()
    backend = StaticSecretBackend({secret_path: ":memory:"})
    app, _ = _build_app(session, backend=backend)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/connections/{conn_id}/test",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


async def test_test_endpoint_viewer_forbidden(session: AsyncSession) -> None:
    user = await _seed_user(session, email="tst-viewer@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-tst-viewer", user=user, role=WorkspaceRole.VIEWER
    )
    conn = Connection(
        workspace_id=ws.id,
        name="mem",
        type="sqlite",
        config_json={"database": ":memory:"},
        secret_refs=[],
    )
    session.add(conn)
    await session.flush()
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.post(
            f"/workspaces/{ws.id}/connections/{conn.id}/test",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


# --- GET /tables + /columns (introspection, ADR-0033) -----------------------


async def test_introspection_tables_and_columns_sqlite(
    session: AsyncSession, tmp_path: Path
) -> None:
    import sqlite3

    db = tmp_path / "introspect.db"
    with sqlite3.connect(db) as raw:
        raw.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount REAL)")
        raw.execute("CREATE TABLE customers (id INTEGER, name TEXT)")
        raw.commit()

    user = await _seed_user(session, email="introspect-ok@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-introspect", user=user, role=WorkspaceRole.RUNNER
    )
    conn = Connection(
        workspace_id=ws.id,
        name="filedb",
        type="sqlite",
        config_json={"database": str(db)},
        secret_refs=[],
    )
    session.add(conn)
    await session.flush()
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        headers = {"Authorization": f"Bearer {token}"}
        tables_resp = await client.get(
            f"/workspaces/{ws.id}/connections/{conn.id}/tables", headers=headers
        )
        cols_resp = await client.get(
            f"/workspaces/{ws.id}/connections/{conn.id}/columns",
            params={"table": "orders"},
            headers=headers,
        )
    assert tables_resp.status_code == 200, tables_resp.text
    assert set(tables_resp.json()["tables"]) == {"orders", "customers"}
    assert cols_resp.status_code == 200, cols_resp.text
    body = cols_resp.json()
    assert body["table"] == "orders"
    assert [c["name"] for c in body["columns"]] == ["id", "amount"]


async def test_introspection_unsupported_connector_returns_422(session: AsyncSession) -> None:
    user = await _seed_user(session, email="introspect-unsup@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-introspect-u", user=user, role=WorkspaceRole.RUNNER
    )
    # kafka connector has no SchemaInspector capability.
    conn = Connection(
        workspace_id=ws.id,
        name="stream",
        type="kafka",
        config_json={},
        secret_refs=[],
    )
    session.add(conn)
    await session.flush()
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/connections/{conn.id}/tables",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 422, resp.text


async def test_introspection_viewer_forbidden(session: AsyncSession) -> None:
    user = await _seed_user(session, email="introspect-viewer@example.com")
    ws = await _seed_workspace_with_role(
        session, slug="conn-introspect-v", user=user, role=WorkspaceRole.VIEWER
    )
    conn = Connection(
        workspace_id=ws.id,
        name="mem",
        type="sqlite",
        config_json={"database": ":memory:"},
        secret_refs=[],
    )
    session.add(conn)
    await session.flush()
    app, _ = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/connections/{conn.id}/tables",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403
