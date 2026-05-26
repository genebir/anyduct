"""Sensors (ADR-0041 K3b) — repository + scheduler + REST.

Three layers in one file because the surface is small and they share
the seed boilerplate; splitting would mean repeating ``_seed_workspace
+ _seed_pipeline_with_current`` three times.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from etlx_server.app_factory import create_app
from etlx_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from etlx_server.auth.password_service import PasswordService
from etlx_server.db.enums import AuthMethod, RunStatus, WorkspaceRole
from etlx_server.db.models import (
    Membership,
    Pipeline,
    PipelineVersion,
    Run,
    Sensor,
    User,
    Workspace,
)
from etlx_server.dependencies import get_session
from etlx_server.sensors import SensorRepository, SensorScheduler
from etlx_server.settings import Settings
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

# Built-ins must be registered before build_sensor dispatches.
import etl_plugins.sensors  # noqa: F401
from etl_plugins.core.sensor import (
    SensorBase,
    SensorResult,
    register_sensor,
)

pytestmark = pytest.mark.asyncio


# --- shared fixtures ---------------------------------------------------------


@pytest_asyncio.fixture
async def real_factory(
    metadata_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Sessionmaker on independent connections — the scheduler tests need to
    see each other's commits across replicas."""
    return async_sessionmaker(bind=metadata_engine, expire_on_commit=False)


async def _seed_workspace(session: AsyncSession, *, slug: str) -> Workspace:
    ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    return ws


async def _seed_pipeline_with_current(
    session: AsyncSession, *, workspace_id: UUID, name: str
) -> tuple[Pipeline, PipelineVersion]:
    p = Pipeline(workspace_id=workspace_id, name=name)
    session.add(p)
    await session.flush()
    pv = PipelineVersion(pipeline_id=p.id, version=1, config_json={"name": name}, is_current=True)
    session.add(pv)
    await session.flush()
    return p, pv


# A predictable test sensor we can register once per test process.
class _AlwaysFires(SensorBase):
    def __init__(self, message: str = "ok") -> None:
        self._message = message

    def check(self) -> SensorResult:
        return SensorResult(triggered=True, message=self._message, metadata={"src": "test"})


class _NeverFires(SensorBase):
    def check(self) -> SensorResult:
        return SensorResult(triggered=False, message="quiet")


for _name, _cls in (
    ("k3b-test-always", _AlwaysFires),
    ("k3b-test-never", _NeverFires),
):
    try:

        @register_sensor(_name)
        def _b(cfg: dict[str, Any], _factory: type = _cls) -> SensorBase:
            return _factory()

    except Exception:
        pass


# ---- repository ------------------------------------------------------------


async def test_create_get_list_round_trip(session: AsyncSession) -> None:
    ws = await _seed_workspace(session, slug="sn-crud")
    p, _ = await _seed_pipeline_with_current(session, workspace_id=ws.id, name="p1")
    repo = SensorRepository(session)
    created = await repo.create(
        workspace_id=ws.id,
        name="health-probe",
        sensor_type="http",
        config_json={"url": "http://example.test/x"},
        target_pipeline_id=p.id,
        poll_interval_seconds=30,
        is_active=True,
        created_by_user_id=None,
    )
    fetched = await repo.get(workspace_id=ws.id, sensor_id=created.id)
    assert fetched is not None and fetched.name == "health-probe"
    rows = await repo.list_for_workspace(workspace_id=ws.id)
    assert [r.name for r in rows] == ["health-probe"]


async def test_create_duplicate_name_raises(session: AsyncSession) -> None:
    from etlx_server.sensors.repository import SensorNameTakenError

    ws = await _seed_workspace(session, slug="sn-dup")
    repo = SensorRepository(session)
    await repo.create(
        workspace_id=ws.id,
        name="probe",
        sensor_type="http",
        config_json={"url": "http://x.test/"},
        target_pipeline_id=None,
        poll_interval_seconds=60,
        is_active=True,
        created_by_user_id=None,
    )
    with pytest.raises(SensorNameTakenError):
        await repo.create(
            workspace_id=ws.id,
            name="probe",
            sensor_type="http",
            config_json={"url": "http://y.test/"},
            target_pipeline_id=None,
            poll_interval_seconds=60,
            is_active=True,
            created_by_user_id=None,
        )


async def test_create_unknown_type_raises(session: AsyncSession) -> None:
    from etlx_server.sensors.repository import UnknownSensorTypeError

    ws = await _seed_workspace(session, slug="sn-unk")
    repo = SensorRepository(session)
    with pytest.raises(UnknownSensorTypeError):
        await repo.create(
            workspace_id=ws.id,
            name="probe",
            sensor_type="not-a-real-sensor",
            config_json={},
            target_pipeline_id=None,
            poll_interval_seconds=60,
            is_active=True,
            created_by_user_id=None,
        )


# ---- scheduler -------------------------------------------------------------


async def test_scheduler_tick_fires_and_enqueues_pending_run(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A sensor whose check() returns triggered=True should land a PENDING
    Run on its target pipeline + stamp ``last_triggered_at``."""
    slug = f"sn-fire-{uuid4().hex[:8]}"
    ws_id: UUID
    p_id: UUID
    s_id: UUID
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        p, _pv = await _seed_pipeline_with_current(s, workspace_id=ws.id, name="t")
        repo = SensorRepository(s)
        sensor = await repo.create(
            workspace_id=ws.id,
            name="fires",
            sensor_type="k3b-test-always",
            config_json={},
            target_pipeline_id=p.id,
            poll_interval_seconds=5,
            is_active=True,
            created_by_user_id=None,
        )
        await s.commit()
        ws_id, p_id, s_id = ws.id, p.id, sensor.id
    try:
        fired = await SensorScheduler(real_factory).tick_once()
        assert fired == 1
        async with real_factory() as s:
            sensor_after = (await s.execute(select(Sensor).where(Sensor.id == s_id))).scalar_one()
            runs = list(
                (await s.execute(select(Run).where(Run.pipeline_id == p_id))).scalars().all()
            )
        assert sensor_after.last_check_at is not None
        assert sensor_after.last_triggered_at is not None
        assert sensor_after.last_result_json is not None
        assert sensor_after.last_result_json["triggered"] is True
        assert len(runs) == 1
        assert runs[0].status == RunStatus.PENDING
        # Trigger context propagates onto the run's result_json so the UI
        # / downstream pipeline can see what fired it.
        assert runs[0].result_json["triggered_by"] == "sensor"
        assert runs[0].result_json["sensor_id"] == str(s_id)
    finally:
        async with real_factory() as s:
            await s.execute(delete(Run).where(Run.pipeline_id == p_id))
            await s.execute(delete(Sensor).where(Sensor.id == s_id))
            await s.execute(delete(PipelineVersion).where(PipelineVersion.pipeline_id == p_id))
            await s.execute(delete(Pipeline).where(Pipeline.id == p_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_scheduler_tick_does_not_enqueue_when_check_negative(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A sensor whose check() returns triggered=False stamps last_check_at
    but does NOT enqueue any run."""
    slug = f"sn-quiet-{uuid4().hex[:8]}"
    ws_id: UUID
    p_id: UUID
    s_id: UUID
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        p, _pv = await _seed_pipeline_with_current(s, workspace_id=ws.id, name="t")
        sensor = await SensorRepository(s).create(
            workspace_id=ws.id,
            name="quiet",
            sensor_type="k3b-test-never",
            config_json={},
            target_pipeline_id=p.id,
            poll_interval_seconds=5,
            is_active=True,
            created_by_user_id=None,
        )
        await s.commit()
        ws_id, p_id, s_id = ws.id, p.id, sensor.id
    try:
        fired = await SensorScheduler(real_factory).tick_once()
        assert fired == 0
        async with real_factory() as s:
            sensor_after = (await s.execute(select(Sensor).where(Sensor.id == s_id))).scalar_one()
            runs = list(
                (await s.execute(select(Run).where(Run.pipeline_id == p_id))).scalars().all()
            )
        assert sensor_after.last_check_at is not None
        assert sensor_after.last_triggered_at is None
        assert sensor_after.last_result_json["triggered"] is False
        assert runs == []
    finally:
        async with real_factory() as s:
            await s.execute(delete(Sensor).where(Sensor.id == s_id))
            await s.execute(delete(PipelineVersion).where(PipelineVersion.pipeline_id == p_id))
            await s.execute(delete(Pipeline).where(Pipeline.id == p_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_scheduler_skip_locked_partitions_concurrent_ticks(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """K2-pattern multi-replica safety: two SensorScheduler ticks running
    in parallel must NOT double-fire the same sensor. Exactly one trigger
    run lands."""
    slug = f"sn-mr-{uuid4().hex[:8]}"
    ws_id: UUID
    p_id: UUID
    s_id: UUID
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        p, _pv = await _seed_pipeline_with_current(s, workspace_id=ws.id, name="t")
        sensor = await SensorRepository(s).create(
            workspace_id=ws.id,
            name="fires-mr",
            sensor_type="k3b-test-always",
            config_json={},
            target_pipeline_id=p.id,
            poll_interval_seconds=5,
            is_active=True,
            created_by_user_id=None,
        )
        await s.commit()
        ws_id, p_id, s_id = ws.id, p.id, sensor.id
    try:
        a, b = await asyncio.gather(
            SensorScheduler(real_factory).tick_once(),
            SensorScheduler(real_factory).tick_once(),
        )
        # SKIP LOCKED partitions the sensor across the two replicas — exactly
        # one fires; the other gets nothing this tick.
        assert a + b == 1, f"expected single fire across replicas, got ({a}, {b})"
        async with real_factory() as s:
            runs = list(
                (await s.execute(select(Run).where(Run.pipeline_id == p_id))).scalars().all()
            )
        assert len(runs) == 1
    finally:
        async with real_factory() as s:
            await s.execute(delete(Run).where(Run.pipeline_id == p_id))
            await s.execute(delete(Sensor).where(Sensor.id == s_id))
            await s.execute(delete(PipelineVersion).where(PipelineVersion.pipeline_id == p_id))
            await s.execute(delete(Pipeline).where(Pipeline.id == p_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_scheduler_respects_poll_interval(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A sensor whose last_check_at is within poll_interval_seconds isn't
    due yet; the tick should skip it."""
    slug = f"sn-iv-{uuid4().hex[:8]}"
    ws_id: UUID
    p_id: UUID
    s_id: UUID
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        p, _pv = await _seed_pipeline_with_current(s, workspace_id=ws.id, name="t")
        # Long poll interval + recently checked → not due.
        sensor = await SensorRepository(s).create(
            workspace_id=ws.id,
            name="recent",
            sensor_type="k3b-test-always",
            config_json={},
            target_pipeline_id=p.id,
            poll_interval_seconds=3600,
            is_active=True,
            created_by_user_id=None,
        )
        sensor.last_check_at = datetime.now(UTC) - timedelta(seconds=10)
        await s.commit()
        ws_id, p_id, s_id = ws.id, p.id, sensor.id
    try:
        fired = await SensorScheduler(real_factory).tick_once()
        assert fired == 0
    finally:
        async with real_factory() as s:
            await s.execute(delete(Sensor).where(Sensor.id == s_id))
            await s.execute(delete(PipelineVersion).where(PipelineVersion.pipeline_id == p_id))
            await s.execute(delete(Pipeline).where(Pipeline.id == p_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


# ---- REST -------------------------------------------------------------------


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


async def _seed_ws_with_member(
    session: AsyncSession, *, slug: str, user: User, role: WorkspaceRole
) -> Workspace:
    ws = Workspace(name=slug.title(), slug=slug, color_hex="#FF3D8B")
    session.add(ws)
    await session.flush()
    session.add(Membership(workspace_id=ws.id, user_id=user.id, role=role))
    await session.flush()
    return ws


async def _login(client: httpx.AsyncClient, *, email: str) -> str:
    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": "hunter2"},  # pragma: allowlist secret
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


async def test_rest_crud_happy_path(session: AsyncSession) -> None:
    user = await _seed_user(session, email="sn-rest@example.com")
    ws = await _seed_ws_with_member(session, slug="rest-1", user=user, role=WorkspaceRole.EDITOR)
    p, _ = await _seed_pipeline_with_current(session, workspace_id=ws.id, name="t")
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        h = {"Authorization": f"Bearer {token}"}

        # Create
        body = {
            "name": "health-probe",
            "type": "http",
            "config_json": {"url": "http://example.test/healthz"},
            "target_pipeline_id": str(p.id),
            "poll_interval_seconds": 60,
            "is_active": True,
        }
        resp = await client.post(f"/workspaces/{ws.id}/sensors", json=body, headers=h)
        assert resp.status_code == 201, resp.text
        sid = resp.json()["id"]

        # List
        ls = await client.get(f"/workspaces/{ws.id}/sensors", headers=h)
        assert ls.status_code == 200
        assert [s["name"] for s in ls.json()] == ["health-probe"]

        # Get
        g = await client.get(f"/workspaces/{ws.id}/sensors/{sid}", headers=h)
        assert g.status_code == 200
        assert g.json()["poll_interval_seconds"] == 60

        # Update
        u = await client.patch(
            f"/workspaces/{ws.id}/sensors/{sid}",
            json={"poll_interval_seconds": 120, "is_active": False},
            headers=h,
        )
        assert u.status_code == 200
        assert u.json()["poll_interval_seconds"] == 120
        assert u.json()["is_active"] is False

        # Delete
        d = await client.delete(f"/workspaces/{ws.id}/sensors/{sid}", headers=h)
        assert d.status_code == 204
        ls2 = await client.get(f"/workspaces/{ws.id}/sensors", headers=h)
        assert ls2.json() == []


async def test_rest_create_rejects_unknown_type(session: AsyncSession) -> None:
    user = await _seed_user(session, email="sn-unk-rest@example.com")
    ws = await _seed_ws_with_member(session, slug="rest-unk", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        h = {"Authorization": f"Bearer {token}"}
        resp = await client.post(
            f"/workspaces/{ws.id}/sensors",
            json={"name": "x", "type": "not-real", "config_json": {}, "poll_interval_seconds": 60},
            headers=h,
        )
        assert resp.status_code == 422
        assert "unknown sensor type" in resp.json()["detail"]


async def test_rest_create_rejects_invalid_config(session: AsyncSession) -> None:
    """http sensor without 'url' should bounce at create time (build_sensor
    catches missing required keys, no row persisted)."""
    user = await _seed_user(session, email="sn-cfg@example.com")
    ws = await _seed_ws_with_member(session, slug="rest-cfg", user=user, role=WorkspaceRole.EDITOR)
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        h = {"Authorization": f"Bearer {token}"}
        resp = await client.post(
            f"/workspaces/{ws.id}/sensors",
            json={"name": "bad", "type": "http", "config_json": {}, "poll_interval_seconds": 60},
            headers=h,
        )
        assert resp.status_code == 422
        assert "invalid sensor config" in resp.json()["detail"]
        # And nothing landed in the DB.
        rows = (
            (await session.execute(select(Sensor).where(Sensor.workspace_id == ws.id)))
            .scalars()
            .all()
        )
        assert list(rows) == []


async def test_rest_non_member_forbidden(session: AsyncSession) -> None:
    owner = await _seed_user(session, email="sn-owner@example.com")
    outsider = await _seed_user(session, email="sn-out@example.com")
    ws = await _seed_ws_with_member(session, slug="rest-403", user=owner, role=WorkspaceRole.OWNER)
    app = _build_app(session)
    async with _client(app) as client:
        out_tok = await _login(client, email=outsider.email)
        resp = await client.get(
            f"/workspaces/{ws.id}/sensors",
            headers={"Authorization": f"Bearer {out_tok}"},
        )
    assert resp.status_code == 403


async def test_rest_manual_check_uses_configured_sensor(session: AsyncSession) -> None:
    """The /check endpoint runs the configured sensor once and returns its
    result. It must NOT enqueue a trigger run."""
    user = await _seed_user(session, email="sn-check@example.com")
    ws = await _seed_ws_with_member(
        session, slug="rest-check", user=user, role=WorkspaceRole.EDITOR
    )
    p, _ = await _seed_pipeline_with_current(session, workspace_id=ws.id, name="t")
    # Use the always-fires test sensor for a deterministic result.
    sensor = await SensorRepository(session).create(
        workspace_id=ws.id,
        name="manual",
        sensor_type="k3b-test-always",
        config_json={},
        target_pipeline_id=p.id,
        poll_interval_seconds=60,
        is_active=True,
        created_by_user_id=user.id,
    )
    await session.flush()
    app = _build_app(session)
    async with _client(app) as client:
        token = await _login(client, email=user.email)
        h = {"Authorization": f"Bearer {token}"}
        resp = await client.post(f"/workspaces/{ws.id}/sensors/{sensor.id}/check", headers=h)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["triggered"] is True
        assert body["message"] == "ok"
        # /check does NOT enqueue a trigger run.
        runs = (await session.execute(select(Run).where(Run.pipeline_id == p.id))).scalars().all()
        assert list(runs) == []
