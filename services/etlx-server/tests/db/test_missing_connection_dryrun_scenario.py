"""Missing-connection dry-run scenario (Phase AEC, 2026-06-04).

The web builder/list surfaces grew a *broken-reference safety net* this
session — pipelines & migrations that reference a connection no longer in
the workspace are flagged (ADC/ADD), the migration detail banners it
(ADL), the dashboard counts it (ADS), and **Run now / Trigger are
disabled** (ADV/ADW) because "the run would just fail to build".

This module dogfoods the *premise* those UI affordances rest on: a
pipeline that names a connection which doesn't exist must fail dry-run
with a clear, connection-naming error — through the real REST surface,
not a unit stub.

It also pins the web↔server agreement on *which* connections a config
references. The web walker (``lib/connection-usage.ts``
``extractConnectionNames``) and the server
(``etlx_server.pipelines.runtime.referenced_connection_names``) must
agree, or the UI would warn about a connection the runtime doesn't
actually need (or miss one it does). Scenario AEC2 specifically proves
the **DLQ** connection counts as a reference — the gap the ACL/ACM
follow-up fixed on the web side.

Scenarios:
* **AEC1** — a sink naming a non-existent connection → dry-run ``ok=false``
  + the missing name surfaced verbatim.
* **AEC2** — every source/sink connection exists but the **dlq** names a
  missing one → dry-run still fails, naming the dlq connection (proves
  dlq is a tracked reference, matching the web walker).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from etlx_server.app_factory import create_app
from etlx_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from etlx_server.auth.password_service import PasswordService
from etlx_server.db.enums import AuthMethod
from etlx_server.db.models import User
from etlx_server.dependencies import get_session
from etlx_server.settings import Settings
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend

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


async def _login(session: AsyncSession, client: httpx.AsyncClient, *, email: str) -> dict[str, str]:
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
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _make_ws(client: httpx.AsyncClient, h: dict[str, str], *, slug: str) -> str:
    resp = await client.post("/workspaces", headers=h, json={"name": "Eng Ws", "slug": slug})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _make_conn(
    client: httpx.AsyncClient, h: dict[str, str], ws_id: str, *, name: str, db: str
) -> None:
    resp = await client.post(
        f"/workspaces/{ws_id}/connections",
        headers=h,
        json={"name": name, "type": "sqlite", "config": {"database": db}, "secrets": {}},
    )
    assert resp.status_code == 201, resp.text


async def test_aec1_sink_missing_connection_fails_dry_run(session: AsyncSession, tmp_path) -> None:
    """A sink that names a connection which doesn't exist → dry-run fails
    and names the missing connection. This is the runtime fact the web's
    'Trigger disabled — missing connection' (ADW) relies on."""
    app = _build_app(session)
    async with _client(app) as client:
        h = await _login(session, client, email="aec1@example.com")
        ws_id = await _make_ws(client, h, slug="aec1-ws")
        # Only the source connection exists; the sink names a ghost.
        await _make_conn(client, h, ws_id, name="src", db=str(tmp_path / "src.db"))

        pipe = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines",
                headers=h,
                json={
                    "name": "broken_sink",
                    "config": {
                        "name": "broken_sink",
                        "mode": "batch",
                        "source": {"connection": "src", "query": "SELECT 1 AS n"},
                        "sink": {"connection": "ghost_sink", "table": "out"},
                    },
                },
            )
        ).json()
        pipe_id = pipe["id"]

        # Creating the pipeline succeeds — connection existence is NOT a
        # create-time check (a connection can be deleted after the
        # pipeline is built; that's exactly the ADC/ADL scenario).
        dry = await client.post(f"/workspaces/{ws_id}/pipelines/{pipe_id}/dry-run", headers=h)
        assert dry.status_code == 200, dry.text
        body = dry.json()
        assert body["ok"] is False, body
        # The missing connection name is surfaced verbatim so the operator
        # knows exactly which one to fix.
        joined = " ".join(body["errors"])
        assert "ghost_sink" in joined, body
        assert "src" not in joined, body  # the existing one is not flagged


async def test_aec2_missing_dlq_connection_fails_dry_run(session: AsyncSession, tmp_path) -> None:
    """Source + sink connections both exist, but the DLQ names a missing
    one → dry-run still fails, naming the dlq connection. Proves the DLQ
    connection is a *tracked reference* on the server, matching the web
    walker's ACL/ACM follow-up (which added dlq.connection)."""
    app = _build_app(session)
    async with _client(app) as client:
        h = await _login(session, client, email="aec2@example.com")
        ws_id = await _make_ws(client, h, slug="aec2-ws")
        await _make_conn(client, h, ws_id, name="src", db=str(tmp_path / "src.db"))
        await _make_conn(client, h, ws_id, name="dst", db=str(tmp_path / "dst.db"))

        pipe = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines",
                headers=h,
                json={
                    "name": "broken_dlq",
                    "config": {
                        "name": "broken_dlq",
                        "mode": "batch",
                        "source": {"connection": "src", "query": "SELECT 1 AS n"},
                        "sink": {"connection": "dst", "table": "out"},
                        "dlq": {"connection": "ghost_dlq", "mode": "table", "table": "dlq"},
                    },
                },
            )
        ).json()
        pipe_id = pipe["id"]

        dry = await client.post(f"/workspaces/{ws_id}/pipelines/{pipe_id}/dry-run", headers=h)
        assert dry.status_code == 200, dry.text
        body = dry.json()
        assert body["ok"] is False, body
        joined = " ".join(body["errors"])
        assert "ghost_dlq" in joined, body


async def test_aee_config_json_roundtrips_for_web_walkers(session: AsyncSession, tmp_path) -> None:
    """The web usage/broken-reference features (ACL/ACR/ADC/ADS/ADU) all
    read ``pipelinesApi.list().current_config_json`` and walk it
    client-side. This pins that the GET response preserves — through the
    server's canonical ``PipelineConfig`` round-trip — exactly the bits
    those walkers parse: source/sink/dlq connection names, workspace
    ``variables``, and ``${var.x}`` reference tokens.
    """
    import json

    app = _build_app(session)
    async with _client(app) as client:
        h = await _login(session, client, email="aee@example.com")
        ws_id = await _make_ws(client, h, slug="aee-ws")

        await client.post(
            f"/workspaces/{ws_id}/pipelines",
            headers=h,
            json={
                "name": "roundtrip",
                "config": {
                    "name": "roundtrip",
                    "mode": "batch",
                    "variables": {"region": "us"},
                    "source": {
                        "connection": "src",
                        "query": "SELECT * FROM orders WHERE region = '${var.region}'",
                    },
                    "sink": {"connection": "dst", "table": "out"},
                    "dlq": {"connection": "errors_conn", "mode": "table", "table": "dlq"},
                },
            },
        )

        # GET via the same list endpoint the web reads.
        listing = await client.get(f"/workspaces/{ws_id}/pipelines", headers=h)
        assert listing.status_code == 200, listing.text
        row = next(p for p in listing.json() if p["name"] == "roundtrip")
        cfg = row["current_config_json"]
        assert cfg is not None

        # extractConnectionNames sees these three (incl. dlq — ACL/ACM f/u).
        assert cfg["source"]["connection"] == "src"
        assert cfg["sink"]["connection"] == "dst"
        assert cfg["dlq"]["connection"] == "errors_conn"
        # referencedVariableNames sees ${var.region}; variables block survives.
        assert cfg["variables"]["region"] == "us"
        assert "${var.region}" in json.dumps(cfg)


async def test_aef_graph_sql_exec_node_missing_connection_fails_dry_run(
    session: AsyncSession, tmp_path
) -> None:
    """A graph-mode pipeline whose only node is a ``sql_exec`` ("Run SQL")
    referencing a missing connection → dry-run fails naming it. Proves the
    graph sql_exec node's connection is a tracked reference on the server,
    matching the web walker which includes ``type === "sql_exec"`` graph
    nodes (ACL/ACM follow-up). Closes the last shape of the broken-ref
    web↔server agreement (linear + dlq covered by AEC, graph sql_exec here).
    """
    app = _build_app(session)
    async with _client(app) as client:
        h = await _login(session, client, email="aef@example.com")
        ws_id = await _make_ws(client, h, slug="aef-ws")

        pipe = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines",
                headers=h,
                json={
                    "name": "graph_run_sql",
                    "config": {
                        "name": "graph_run_sql",
                        "mode": "batch",
                        "graph": {
                            "nodes": [
                                {
                                    "id": "n1",
                                    "type": "sql_exec",
                                    "connection": "ghost_wh",
                                    "statement": "DELETE FROM staging.tmp",
                                }
                            ],
                            "edges": [],
                        },
                    },
                },
            )
        ).json()
        pipe_id = pipe["id"]

        dry = await client.post(f"/workspaces/{ws_id}/pipelines/{pipe_id}/dry-run", headers=h)
        assert dry.status_code == 200, dry.text
        body = dry.json()
        assert body["ok"] is False, body
        assert "ghost_wh" in " ".join(body["errors"]), body


async def test_aep_dry_run_returns_lint_warnings(session: AsyncSession, tmp_path) -> None:
    """A custom_python transform without ``column_mapping`` makes the
    server emit an advisory lint warning (Phase DD). This pins that the
    dry-run REST *response* carries ``warnings`` — the data the web now
    renders in the builder DryRunPanel + migration card (AEN/AEO). Without
    this the UI surfaces would always be empty.
    """
    app = _build_app(session)
    async with _client(app) as client:
        h = await _login(session, client, email="aep@example.com")
        ws_id = await _make_ws(client, h, slug="aep-ws")
        # Connections must exist or dry-run early-returns before linting.
        await _make_conn(client, h, ws_id, name="src", db=str(tmp_path / "src.db"))
        await _make_conn(client, h, ws_id, name="dst", db=str(tmp_path / "dst.db"))

        pipe = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines",
                headers=h,
                json={
                    "name": "lint_me",
                    "config": {
                        "name": "lint_me",
                        "mode": "batch",
                        "source": {"connection": "src", "query": "SELECT 1 AS n"},
                        # custom_python transform with no column_mapping →
                        # DD's column_mapping_recommended advisory fires.
                        "transforms": [
                            {
                                "type": "custom_python",
                                "code": "def transform(record):\n    return record\n",
                            }
                        ],
                        "sink": {"connection": "dst", "table": "out"},
                    },
                },
            )
        ).json()
        pipe_id = pipe["id"]

        dry = await client.post(f"/workspaces/{ws_id}/pipelines/{pipe_id}/dry-run", headers=h)
        assert dry.status_code == 200, dry.text
        body = dry.json()
        # Warnings are advisory — present regardless of ok.
        assert "warnings" in body, body
        assert len(body["warnings"]) > 0, body
        joined = " ".join(w["message"] for w in body["warnings"]).lower()
        assert "column_mapping" in joined or "column mapping" in joined, body


async def test_dlq9_dry_run_returns_dlq_recommended_warning(
    session: AsyncSession, tmp_path
) -> None:
    """DLQ-8 (ADR-0076) over REST: a custom_python transform with no
    ``dlq`` makes the dry-run response carry a ``dlq_recommended`` advisory
    — the data the builder DryRunPanel surfaces so the operator knows one
    bad record would fail the whole run. Pins the new lint reaches the API.
    """
    app = _build_app(session)
    async with _client(app) as client:
        h = await _login(session, client, email="dlq9@example.com")
        ws_id = await _make_ws(client, h, slug="dlq9-ws")
        await _make_conn(client, h, ws_id, name="src", db=str(tmp_path / "src.db"))
        await _make_conn(client, h, ws_id, name="dst", db=str(tmp_path / "dst.db"))

        pipe = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines",
                headers=h,
                json={
                    "name": "no_dlq",
                    "config": {
                        "name": "no_dlq",
                        "mode": "batch",
                        "source": {"connection": "src", "query": "SELECT 1 AS n"},
                        "transforms": [
                            {
                                "type": "custom_python",
                                "code": "def transform(record):\n    return record\n",
                            }
                        ],
                        "sink": {"connection": "dst", "table": "out"},
                    },
                },
            )
        ).json()
        pipe_id = pipe["id"]

        dry = await client.post(f"/workspaces/{ws_id}/pipelines/{pipe_id}/dry-run", headers=h)
        assert dry.status_code == 200, dry.text
        body = dry.json()
        codes = [w["code"] for w in body["warnings"]]
        assert "dlq_recommended" in codes, body
        msg = next(w for w in body["warnings"] if w["code"] == "dlq_recommended")["message"]
        assert "dlq" in msg.lower()


async def test_dlq9_dry_run_no_dlq_warning_when_dlq_configured(
    session: AsyncSession, tmp_path
) -> None:
    """The mirror: once a ``dlq`` is configured, the advisory disappears."""
    app = _build_app(session)
    async with _client(app) as client:
        h = await _login(session, client, email="dlq9b@example.com")
        ws_id = await _make_ws(client, h, slug="dlq9b-ws")
        await _make_conn(client, h, ws_id, name="src", db=str(tmp_path / "src.db"))
        await _make_conn(client, h, ws_id, name="dst", db=str(tmp_path / "dst.db"))

        pipe = (
            await client.post(
                f"/workspaces/{ws_id}/pipelines",
                headers=h,
                json={
                    "name": "with_dlq",
                    "config": {
                        "name": "with_dlq",
                        "mode": "batch",
                        "source": {"connection": "src", "query": "SELECT 1 AS n"},
                        "transforms": [
                            {
                                "type": "custom_python",
                                "code": "def transform(record):\n    return record\n",
                            }
                        ],
                        "sink": {"connection": "dst", "table": "out"},
                        "dlq": {"connection": "dst", "table": "bad", "mode": "append"},
                    },
                },
            )
        ).json()
        pipe_id = pipe["id"]

        dry = await client.post(f"/workspaces/{ws_id}/pipelines/{pipe_id}/dry-run", headers=h)
        assert dry.status_code == 200, dry.text
        codes = [w["code"] for w in dry.json()["warnings"]]
        assert "dlq_recommended" not in codes
