"""Operator onboarding journey dogfooding scenario (Phase TT, 2026-05-29).

User persona: a brand-new operator on day 1. They want to take a sqlite
file they already have, point a pipeline at it, run it once, and confirm
that the catalog + audit trail records what just happened. This is the
canonical "first 15 minutes with the product" flow — every REST hop
along the way must respond clearly enough that the operator never
wonders "did that work?" without checking the next page.

The scenario walks the REST API the same order a real operator would:

1. ``POST /workspaces`` — sign up / create their first workspace.
2. ``POST /workspaces/{ws}/connections`` x 2 — register src + dst.
3. ``POST /connections/{id}/test`` — verify creds before queueing a run.
4. ``POST /workspaces/{ws}/pipelines`` — describe the work.
5. ``POST /pipelines/{pid}/dry-run`` — sanity check + surface lint warnings
   the operator should know about.
6. ``POST /pipelines/{pid}/trigger`` — actually run it.
7. Drain the worker (production = a background process).
8. ``GET /runs/{rid}`` — confirm the run succeeded.
9. ``GET /workspaces/{ws}/assets`` — confirm the catalog filled in.
10. ``GET /audit?action=run.sql_read`` — confirm the source SELECT was
    audited for compliance.

Each step asserts not only the status code, but also the *shape* of
the response — the things a UI / CLI / human depends on to navigate.
Anything missing here is a UX dogfooding miss.
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


# ----- harness ---------------------------------------------------------------


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


async def _seed_user_and_login(
    session: AsyncSession, client: httpx.AsyncClient, *, email: str
) -> str:
    """Seed a local user + return their access token. The login response
    itself is part of the journey — we don't shortcut it via JWT inject."""
    user = User(
        email=email.lower(),
        name="Operator",
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
    body = resp.json()
    # Token + the new ``/auth/me`` shape the UI immediately fetches.
    assert "access_token" in body
    return str(body["access_token"])


async def _drain_pending_runs(session: AsyncSession, worker_id: str) -> int:
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


# ===== TT1: Operator onboarding from sign-in to first catalog ===============


async def test_tt1_operator_onboarding_full_journey_to_first_catalog(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Walk the canonical first-time-operator flow over REST. Every hop
    asserts both status code and a load-bearing response field — the UI
    needs these to navigate, so missing values are UX bugs, not test
    smells. The journey ends by confirming the catalog + audit feed
    reflect the work the operator just did."""

    # ---- Seed the on-disk sqlite warehouse the operator already has ----
    db_path = tmp_path / "onboarding.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw_orders (id INTEGER, amount INTEGER)")
        conn.executemany(
            "INSERT INTO raw_orders VALUES (?, ?)",
            [(1, 100), (2, 250), (3, 75)],
        )
        conn.execute("CREATE TABLE clean_orders (id INTEGER, amount INTEGER)")
        conn.commit()
    finally:
        conn.close()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="op@example.com")
        h = {"Authorization": f"Bearer {token}"}

        # ---- Step 1: create the workspace (auto-Owner membership) -------
        ws_resp = await client.post(
            "/workspaces",
            headers=h,
            json={"name": "First Workspace", "slug": "first-ws", "color_hex": "#33AAFF"},
        )
        assert ws_resp.status_code == 201, ws_resp.text
        ws = ws_resp.json()
        # The UI needs id + slug + name to render the sidebar entry right away.
        assert "id" in ws and "slug" in ws and "name" in ws
        ws_id = ws["id"]

        # ---- Step 2: register two connections (src + dst pointing at same db) --
        for name in ("src", "dst"):
            c_resp = await client.post(
                f"/workspaces/{ws_id}/connections",
                headers=h,
                json={
                    "name": name,
                    "type": "sqlite",
                    "config": {"database": str(db_path)},
                    "secrets": {},
                },
            )
            assert c_resp.status_code == 201, c_resp.text
            c_body = c_resp.json()
            assert c_body["name"] == name
            assert c_body["type"] == "sqlite"

        # List back to grab the ids the UI needs for the "Test" button.
        list_resp = await client.get(f"/workspaces/{ws_id}/connections", headers=h)
        assert list_resp.status_code == 200
        by_name = {c["name"]: c for c in list_resp.json()}
        assert {"src", "dst"} <= set(by_name)
        src_id = by_name["src"]["id"]

        # ---- Step 3: test the connection (the operator clicks "Test") --
        test_resp = await client.post(f"/workspaces/{ws_id}/connections/{src_id}/test", headers=h)
        assert test_resp.status_code == 200, test_resp.text
        test_body = test_resp.json()
        # The shape the UI shows in the "Test result" toast.
        assert test_body["ok"] is True

        # ---- Step 4: create the pipeline ---------------------------------
        pipe_resp = await client.post(
            f"/workspaces/{ws_id}/pipelines",
            headers=h,
            json={
                "name": "clean_orders",
                "description": "Filter raw orders into clean ones",
                "config": {
                    "source": {
                        "connection": "src",
                        "query": "SELECT id, amount FROM raw_orders",
                    },
                    "sink": {
                        "connection": "dst",
                        "table": "clean_orders",
                        "mode": "append",
                    },
                },
            },
        )
        assert pipe_resp.status_code == 201, pipe_resp.text
        pipe = pipe_resp.json()
        assert pipe["name"] == "clean_orders"
        pipe_id = pipe["id"]

        # ---- Step 5: dry-run before triggering (UX hygiene) -------------
        dry_resp = await client.post(f"/workspaces/{ws_id}/pipelines/{pipe_id}/dry-run", headers=h)
        assert dry_resp.status_code == 200, dry_resp.text
        dry = dry_resp.json()
        assert dry["ok"] is True
        # The connectors list lets the UI render "src ✓ dst ✓" badges.
        conn_results = {c["name"]: c for c in dry["connectors"]}
        assert {"src", "dst"} <= set(conn_results)
        assert all(c["ok"] for c in dry["connectors"])
        # Warnings field is always present even when empty (the UI
        # always renders the panel container, just empty here).
        assert "warnings" in dry

        # ---- Step 6: actually trigger the run ----------------------------
        trig_resp = await client.post(
            f"/workspaces/{ws_id}/pipelines/{pipe_id}/trigger",
            headers=h,
            json={},
        )
        assert trig_resp.status_code == 202, trig_resp.text
        trig = trig_resp.json()
        assert "id" in trig
        run_id = trig["id"]

        # ---- Step 7: the worker drains (production = bg daemon) ---------
        executed = await _drain_pending_runs(session, "tt1")
        assert executed == 1

        # ---- Step 8: confirm the run summary the UI shows ---------------
        run_resp = await client.get(f"/workspaces/{ws_id}/runs/{run_id}", headers=h)
        assert run_resp.status_code == 200, run_resp.text
        run_body = run_resp.json()
        assert run_body["status"] == RunStatus.SUCCEEDED.value
        assert run_body.get("records_written") == 3
        # `pipeline_version_id` is the audit anchor — make sure it's
        # surfaced so the UI can render "this run used version N".
        assert run_body.get("pipeline_version_id") is not None

        # ---- Step 9: catalog reflects the new sink ---------------------
        assets_resp = await client.get(f"/workspaces/{ws_id}/assets", headers=h)
        assert assets_resp.status_code == 200
        keys = {a["asset_key"] for a in assets_resp.json()}
        assert "src/raw_orders" in keys
        assert "dst/clean_orders" in keys

        # ---- Step 10: data-plane audit row for the source SELECT --------
        audit_resp = await client.get(f"/audit?workspace_id={ws_id}&action=run.sql_read", headers=h)
        assert audit_resp.status_code == 200, audit_resp.text
        audit_rows = audit_resp.json()
        # Exactly one ``run.sql_read`` for this run — Phase QQ/W contract.
        matching = [r for r in audit_rows if r["resource_id"] == run_id]
        assert len(matching) == 1
        # The after_json payload shape the UI uses to render "SELECT id,
        # amount FROM raw_orders against src (sqlite)".
        after = matching[0]["after_json"]
        assert after is not None
        assert after.get("query") == "SELECT id, amount FROM raw_orders"
        assert after.get("connection") == "src"
        assert after.get("connection_type") == "sqlite"


# ===== TT2: Same journey but with custom_python — dry-run lint nudge =======


async def test_tt2_pipeline_with_python_surfaces_column_mapping_nudge(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The user persona for *this* test is a data engineer who reaches
    for ``custom_python`` early. The dry-run lint (Phase DD/FF) should
    surface a ``column_mapping_recommended`` advice without blocking
    the run — and the run itself should still go green. This catches
    UX regressions in the lint nudge presentation."""
    db_path = tmp_path / "py.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("CREATE TABLE raw (id INTEGER, name TEXT)")
        conn.executemany("INSERT INTO raw VALUES (?, ?)", [(1, "alice"), (2, "bob")])
        conn.execute("CREATE TABLE out (id INTEGER, name TEXT)")
        conn.commit()
    finally:
        conn.close()

    app = _build_app(session)
    async with _client(app) as client:
        token = await _seed_user_and_login(session, client, email="eng@example.com")
        h = {"Authorization": f"Bearer {token}"}

        ws_resp = await client.post(
            "/workspaces",
            headers=h,
            json={"name": "Eng", "slug": "eng-ws"},
        )
        ws_id = ws_resp.json()["id"]
        for name in ("src", "dst"):
            await client.post(
                f"/workspaces/{ws_id}/connections",
                headers=h,
                json={
                    "name": name,
                    "type": "sqlite",
                    "config": {"database": str(db_path)},
                    "secrets": {},
                },
            )

        # Pipeline with a python transform but no ``column_mapping`` —
        # the Phase DD lint should fire.
        pipe = await client.post(
            f"/workspaces/{ws_id}/pipelines",
            headers=h,
            json={
                "name": "py",
                "config": {
                    "source": {"connection": "src", "query": "SELECT id, name FROM raw"},
                    "transforms": [
                        {
                            "type": "custom_python",
                            "code": ("def transform(record):\n    return record\n"),
                        }
                    ],
                    "sink": {"connection": "dst", "table": "out", "mode": "append"},
                },
            },
        )
        pipe_id = pipe.json()["id"]

        dry_resp = await client.post(f"/workspaces/{ws_id}/pipelines/{pipe_id}/dry-run", headers=h)
        assert dry_resp.status_code == 200
        dry = dry_resp.json()
        # Run is still healthy — warnings don't flip ``ok`` (Phase DD).
        assert dry["ok"] is True
        codes = [w["code"] for w in dry["warnings"]]
        assert "column_mapping_recommended" in codes
        nudge = next(w for w in dry["warnings"] if w["code"] == "column_mapping_recommended")
        # The UI deep-links to the offending transform by ``location``.
        assert nudge["location"] == "transforms.0"
        # ``message`` is what the UI shows verbatim — confirm it mentions
        # the escape hatch so the operator knows how to act on the nudge.
        assert "column_mapping" in nudge["message"]
