"""Cross-DB engineer onboarding journey (Phase WW, 2026-05-29).

User persona: a data engineer who knows SQL but doesn't want to
hand-write the destination DDL. They sign up, register a source +
destination connection, declare a single pipeline with
``auto_create_table: true`` on the sink, and confirm both that the
sink table appears with the right shape *and* that the catalog
reflects the new asset.

This is the *user-facing* dogfood for ADR-0066: the previous slice
proved the core mechanism works (Phase VV unit + cross-DB integration
tests); this one proves the REST surface area exposes it cleanly.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from anyduct_server.app_factory import create_app
from anyduct_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from anyduct_server.auth.password_service import PasswordService
from anyduct_server.db.enums import AuthMethod, RunStatus
from anyduct_server.db.models import User
from anyduct_server.dependencies import get_session
from anyduct_server.settings import Settings
from anyduct_server.worker.claim import claim_pending_run
from anyduct_server.worker.executor import RunExecutor
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend
from tests.db.test_worker_lifecycle import _SessionFactoryAdapter

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
    app.state.secret_backend = StaticSecretBackend()
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_user_login(session: AsyncSession, client: httpx.AsyncClient, *, email: str) -> str:
    user = User(
        email=email.lower(),
        name="Engineer",
        auth_method=AuthMethod.LOCAL,
        password_hash=PasswordService(rounds=4).hash("hunter2"),  # pragma: allowlist secret
    )
    session.add(user)
    await session.flush()
    await session.commit()
    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": "hunter2"},  # pragma: allowlist secret
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


async def _drain(session: AsyncSession, worker_id: str) -> int:
    executor = RunExecutor(
        _SessionFactoryAdapter(session), StaticSecretBackend(), worker_id=worker_id
    )
    executed = 0
    while True:
        claimed = await claim_pending_run(session, worker_id=worker_id)
        if claimed is None:
            break
        await session.commit()
        await executor.execute(claimed.id)
        executed += 1
    return executed


# ===== WW1: Engineer ships a cross-DB pipeline through REST =================


async def test_ww1_engineer_ships_cross_db_pipeline_via_rest_with_auto_create(
    session: AsyncSession, tmp_path: Path
) -> None:
    """End-to-end engineer workflow:

    1. Seed a source sqlite with mixed vendor types.
    2. Sign up + workspace + src/dst connections (two sqlite files).
    3. Create a pipeline with sink ``auto_create_table: true``.
    4. dry-run: must be ok, warnings exposed (lint nudge unrelated
       to auto_create_table — verifying the response stays clean).
    5. trigger → drain → run succeeded.
    6. Sink file now has the table with the right type-affinity shape.
    7. Catalog shows ``dst/orders_copy``.
    """
    # ---- Source warehouse with deliberately mixed vendor types ----
    src_path = tmp_path / "src.db"
    dst_path = tmp_path / "dst.db"
    raw = sqlite3.connect(str(src_path))
    try:
        raw.execute(
            "CREATE TABLE orders ("
            "id BIGINT, amount NUMERIC(10,2), created_at TIMESTAMPTZ, "
            "payload JSONB, customer VARCHAR(64))"
        )
        raw.executemany(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?)",
            [
                (1, 100.50, "2026-05-01T00:00:00Z", '{"a": 1}', "alice"),
                (2, 250.75, "2026-05-02T00:00:00Z", '{"b": 2}', "bob"),
            ],
        )
        raw.commit()
    finally:
        raw.close()
    # Sink db file exists but has no ``orders_copy`` table yet.
    sqlite3.connect(str(dst_path)).close()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_login(session, client, email="eng@example.com")
        h = {"Authorization": f"Bearer {token}"}

        # ---- workspace + 2 connections ----
        ws = (
            await client.post(
                "/workspaces",
                headers=h,
                json={"name": "Eng Ws", "slug": "eng-ws-vv"},
            )
        ).json()
        ws_id = ws["id"]
        for name, db in (("src", src_path), ("dst", dst_path)):
            r = await client.post(
                f"/workspaces/{ws_id}/connections",
                headers=h,
                json={
                    "name": name,
                    "type": "sqlite",
                    "config": {"database": str(db)},
                    "secrets": {},
                },
            )
            assert r.status_code == 201, r.text

        # ---- pipeline with auto_create_table ----
        pipe_resp = await client.post(
            f"/workspaces/{ws_id}/pipelines",
            headers=h,
            json={
                "name": "copy_orders",
                "config": {
                    "source": {
                        "connection": "src",
                        "query": ("SELECT id, amount, created_at, payload, customer FROM orders"),
                    },
                    "sink": {
                        "connection": "dst",
                        "table": "orders_copy",
                        "mode": "append",
                        "auto_create_table": True,
                    },
                },
            },
        )
        assert pipe_resp.status_code == 201, pipe_resp.text
        pipe_id = pipe_resp.json()["id"]

        # ---- dry-run sanity-check ----
        dry_resp = await client.post(f"/workspaces/{ws_id}/pipelines/{pipe_id}/dry-run", headers=h)
        assert dry_resp.status_code == 200
        dry = dry_resp.json()
        assert dry["ok"] is True
        # Warnings field always present even if empty.
        assert "warnings" in dry
        # Phase AAK (2026-05-29): the engineer's sink has
        # ``auto_create_table=true``, so the AAK lint rule must surface
        # an "auto_create_table_planned" warning — that's the operator's
        # confirmation before they hit Trigger.
        codes = {w["code"] for w in dry["warnings"]}
        assert "auto_create_table_planned" in codes
        # Both connections green.
        assert all(c["ok"] for c in dry["connectors"])

        # ---- trigger ----
        trig = await client.post(
            f"/workspaces/{ws_id}/pipelines/{pipe_id}/trigger",
            headers=h,
            json={},
        )
        assert trig.status_code == 202
        run_id = trig.json()["id"]

        # ---- drain + confirm run state ----
        assert await _drain(session, "ww1") == 1
        run_resp = await client.get(f"/workspaces/{ws_id}/runs/{run_id}", headers=h)
        assert run_resp.status_code == 200
        assert run_resp.json()["status"] == RunStatus.SUCCEEDED.value
        assert run_resp.json()["records_written"] == 2

        # ---- Verify the sink table was auto-created with the right shape ----
        out = sqlite3.connect(str(dst_path))
        try:
            info = out.execute('PRAGMA table_info("orders_copy")').fetchall()
            col_types = {row[1]: row[2] for row in info}
            assert col_types == {
                "id": "INTEGER",
                "amount": "NUMERIC(10,2)",
                "created_at": "TEXT",
                "payload": "TEXT",
                "customer": "TEXT",
            }
            rows = sorted(out.execute("SELECT id, customer FROM orders_copy").fetchall())
        finally:
            out.close()
        assert rows == [(1, "alice"), (2, "bob")]

        # ---- Catalog reflects the new sink asset ----
        assets_resp = await client.get(f"/workspaces/{ws_id}/assets", headers=h)
        keys = {a["asset_key"] for a in assets_resp.json()}
        assert "src/orders" in keys
        assert "dst/orders_copy" in keys
