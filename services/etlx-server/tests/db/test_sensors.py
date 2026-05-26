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

import etlx_server.sensors.builtins  # noqa: F401 — service-side (asset_freshness)
import httpx
import pytest
import pytest_asyncio
from etlx_server.app_factory import create_app
from etlx_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from etlx_server.auth.password_service import PasswordService
from etlx_server.db.enums import AuthMethod, RunStatus, WorkspaceRole
from etlx_server.db.models import (
    Asset,
    Connection,
    Membership,
    Pipeline,
    PipelineVersion,
    Run,
    Sensor,
    User,
    Workspace,
)
from etlx_server.dependencies import get_secret_backend_dep, get_session, get_session_factory
from etlx_server.sensors import SensorRepository, SensorScheduler
from etlx_server.sensors.builtins.asset_freshness import AssetFreshnessSensor
from etlx_server.sensors.builtins.file_landed import FileLandedSensor
from etlx_server.sensors.builtins.lineage_arrival import LineageArrivalSensor
from etlx_server.sensors.context import use_sensor_context
from etlx_server.settings import Settings
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

# Built-ins must be registered before build_sensor dispatches.
import etl_plugins.sensors  # noqa: F401 — pure-core (http)
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

    # The /check endpoint depends on get_session_factory so async sensors
    # (asset_freshness) can open their own session via the ContextVar
    # protocol. The real factory wraps a fresh connection per call which
    # wouldn't see this test's outer transaction. Wrap the per-test
    # session in a single-instance "factory" so `factory()` returns the
    # same session — sync sensors (the existing /check happy-path test)
    # never call it; service-side ones do but see the same transaction.
    class _SingleSessionFactory:
        def __init__(self, s: AsyncSession) -> None:
            self._s = s

        def __call__(self) -> _SingleSessionFactory:
            return self

        async def __aenter__(self) -> AsyncSession:
            return self._s

        async def __aexit__(self, *_exc: object) -> None:
            return None

    _test_factory = _SingleSessionFactory(session)
    app.dependency_overrides[get_session_factory] = lambda: _test_factory

    # /check now also depends on the secret backend (file_landed needs
    # it to resolve a Connection's ``${SECRET:..}`` placeholders). The
    # other REST tests use synthetic sensor types that never touch
    # secrets, so a static-config no-op backend is enough.
    from etl_plugins.config.secrets import get_secret_backend

    _test_backend = get_secret_backend("static")
    app.dependency_overrides[get_secret_backend_dep] = lambda: _test_backend
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


# ---- AssetFreshnessSensor builtin -----------------------------------------
#
# Service-side sensor that reads ``assets.last_materialized_at`` and
# fires when the asset is stale OR has never been materialised. Four
# scenarios exercise builder validation + the three check branches +
# end-to-end scheduler firing.


async def test_asset_freshness_builder_rejects_bad_config() -> None:
    """Bad config bounces at build time, not at the first check, so a typo
    in the UI surfaces as a clear 4xx on POST /sensors."""
    from etl_plugins.core.exceptions import ConfigError
    from etl_plugins.core.sensor import build_sensor

    with pytest.raises(ConfigError):
        build_sensor("asset_freshness", {})  # missing both keys
    with pytest.raises(ConfigError):
        build_sensor("asset_freshness", {"asset_key": "k"})  # missing max_age
    with pytest.raises(ConfigError):
        build_sensor("asset_freshness", {"asset_key": "k", "max_age_minutes": 0})
    with pytest.raises(ConfigError):
        build_sensor("asset_freshness", {"asset_key": "k", "max_age_minutes": True})  # bool
    with pytest.raises(ConfigError):
        build_sensor("asset_freshness", {"asset_key": "", "max_age_minutes": 5})


async def test_asset_freshness_triggers_when_never_materialised(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An asset_key not present in the catalog → triggered=True (so the
    sensor can bootstrap a fresh workspace)."""
    slug = f"sn-af-never-{uuid4().hex[:8]}"
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        await s.commit()
        ws_id = ws.id
    try:
        sensor = AssetFreshnessSensor(asset_key="postgres://x/y", max_age_minutes=30)
        async with use_sensor_context(session_factory=real_factory, workspace_id=ws_id):
            result = await sensor.check_async()
        assert result.triggered is True
        assert "never" in (result.message or "")
        assert result.metadata["reason"] == "never_materialised"
        assert result.metadata["asset_key"] == "postgres://x/y"
    finally:
        async with real_factory() as s:
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_asset_freshness_fresh_does_not_trigger(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An asset materialised inside the budget → triggered=False."""
    slug = f"sn-af-fresh-{uuid4().hex[:8]}"
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        s.add(
            Asset(
                workspace_id=ws.id,
                asset_key="postgres://x/y",
                last_materialized_at=datetime.now(UTC) - timedelta(minutes=2),
            )
        )
        await s.commit()
        ws_id = ws.id
    try:
        sensor = AssetFreshnessSensor(asset_key="postgres://x/y", max_age_minutes=30)
        async with use_sensor_context(session_factory=real_factory, workspace_id=ws_id):
            result = await sensor.check_async()
        assert result.triggered is False
        assert result.metadata["reason"] == "fresh"
        assert result.metadata["age_minutes"] >= 1
    finally:
        async with real_factory() as s:
            await s.execute(delete(Asset).where(Asset.workspace_id == ws_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_asset_freshness_stale_triggers_via_scheduler(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """End-to-end via the scheduler: stale asset → tick fires → PENDING
    Run on the target pipeline + last_triggered_at stamped."""
    slug = f"sn-af-stale-{uuid4().hex[:8]}"
    ws_id: UUID
    p_id: UUID
    s_id: UUID
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        p, _pv = await _seed_pipeline_with_current(s, workspace_id=ws.id, name="consumer")
        s.add(
            Asset(
                workspace_id=ws.id,
                asset_key="postgres://x/orders",
                # Stale by 2h, budget is 30m → trigger.
                last_materialized_at=datetime.now(UTC) - timedelta(hours=2),
            )
        )
        sensor = await SensorRepository(s).create(
            workspace_id=ws.id,
            name="orders-freshness",
            sensor_type="asset_freshness",
            config_json={"asset_key": "postgres://x/orders", "max_age_minutes": 30},
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
        assert sensor_after.last_triggered_at is not None
        assert sensor_after.last_result_json["triggered"] is True
        assert sensor_after.last_result_json["metadata"]["reason"] == "stale"
        assert len(runs) == 1
        assert runs[0].status == RunStatus.PENDING
        # Sensor result piggybacks onto the run's result_json so the
        # consumer pipeline can see the freshness diagnosis.
        sensor_payload = runs[0].result_json["sensor_result"]
        assert sensor_payload["metadata"]["asset_key"] == "postgres://x/orders"
    finally:
        async with real_factory() as s:
            await s.execute(delete(Run).where(Run.pipeline_id == p_id))
            await s.execute(delete(Sensor).where(Sensor.id == s_id))
            await s.execute(delete(Asset).where(Asset.workspace_id == ws_id))
            await s.execute(delete(PipelineVersion).where(PipelineVersion.pipeline_id == p_id))
            await s.execute(delete(Pipeline).where(Pipeline.id == p_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_asset_freshness_missing_context_soft_fails() -> None:
    """If someone calls check_async without ``use_sensor_context``
    (e.g. forgot to wire it in a test) we surface a precise soft-fail
    rather than crash with a weird AttributeError."""
    sensor = AssetFreshnessSensor(asset_key="k", max_age_minutes=10)
    result = await sensor.check_async()
    assert result.triggered is False
    assert "server context" in (result.message or "")
    assert result.metadata["error"] == "missing_context"


# ---- LineageArrivalSensor builtin -----------------------------------------
#
# Reads ``assets.last_materialized_at`` for a set of upstream keys; fires
# when (require_all=true) every upstream OR (require_all=false) any
# upstream has a fresh materialisation. Uses the sensor's stored
# ``last_triggered_at`` for de-dupe so a single materialisation event
# can't fire the sensor on every subsequent tick.


async def test_lineage_arrival_builder_rejects_bad_config() -> None:
    from etl_plugins.core.exceptions import ConfigError
    from etl_plugins.core.sensor import build_sensor

    # Missing keys
    with pytest.raises(ConfigError):
        build_sensor("lineage_arrival", {})
    # Empty list
    with pytest.raises(ConfigError):
        build_sensor(
            "lineage_arrival",
            {"upstream_asset_keys": [], "window_minutes": 60},
        )
    # Non-string element
    with pytest.raises(ConfigError):
        build_sensor(
            "lineage_arrival",
            {"upstream_asset_keys": ["a", 42], "window_minutes": 60},
        )
    # Negative window
    with pytest.raises(ConfigError):
        build_sensor(
            "lineage_arrival",
            {"upstream_asset_keys": ["a"], "window_minutes": -1},
        )
    # Bool window (isinstance(True, int) is True trap)
    with pytest.raises(ConfigError):
        build_sensor(
            "lineage_arrival",
            {"upstream_asset_keys": ["a"], "window_minutes": True},
        )
    # Non-bool require_all
    with pytest.raises(ConfigError):
        build_sensor(
            "lineage_arrival",
            {"upstream_asset_keys": ["a"], "window_minutes": 60, "require_all": "yes"},
        )


async def test_lineage_arrival_fires_when_all_upstreams_fresh_require_all(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """require_all=True: every upstream materialised within window → trigger."""
    slug = f"sn-la-all-{uuid4().hex[:8]}"
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        now = datetime.now(UTC)
        s.add(Asset(workspace_id=ws.id, asset_key="postgres://x/a", last_materialized_at=now))
        s.add(Asset(workspace_id=ws.id, asset_key="postgres://x/b", last_materialized_at=now))
        await s.commit()
        ws_id = ws.id
    try:
        sensor = LineageArrivalSensor(
            upstream_asset_keys=["postgres://x/a", "postgres://x/b"],
            window_minutes=60,
            require_all=True,
        )
        async with use_sensor_context(session_factory=real_factory, workspace_id=ws_id):
            result = await sensor.check_async()
        assert result.triggered is True
        assert "2 upstream(s) arrived" in (result.message or "")
        assert result.metadata["require_all"] is True
        assert {a["asset_key"] for a in result.metadata["arrived"]} == {
            "postgres://x/a",
            "postgres://x/b",
        }
        assert result.metadata["stale"] == []
        assert result.metadata["missing"] == []
    finally:
        async with real_factory() as s:
            await s.execute(delete(Asset).where(Asset.workspace_id == ws_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_lineage_arrival_does_not_fire_when_partial_require_all(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """require_all=True with one stale upstream: no trigger, descriptive
    message + ``stale`` metadata says which key held it up."""
    slug = f"sn-la-partial-{uuid4().hex[:8]}"
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        now = datetime.now(UTC)
        s.add(Asset(workspace_id=ws.id, asset_key="postgres://x/a", last_materialized_at=now))
        s.add(
            Asset(
                workspace_id=ws.id,
                asset_key="postgres://x/b",
                last_materialized_at=now - timedelta(hours=2),  # outside window
            )
        )
        await s.commit()
        ws_id = ws.id
    try:
        sensor = LineageArrivalSensor(
            upstream_asset_keys=["postgres://x/a", "postgres://x/b"],
            window_minutes=60,
            require_all=True,
        )
        async with use_sensor_context(session_factory=real_factory, workspace_id=ws_id):
            result = await sensor.check_async()
        assert result.triggered is False
        assert "1/2" in (result.message or "")
        assert {a["asset_key"] for a in result.metadata["arrived"]} == {"postgres://x/a"}
        assert result.metadata["stale"] == ["postgres://x/b"]
    finally:
        async with real_factory() as s:
            await s.execute(delete(Asset).where(Asset.workspace_id == ws_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_lineage_arrival_fires_on_any_when_require_all_false(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """require_all=False with one fresh upstream + one stale: trigger."""
    slug = f"sn-la-any-{uuid4().hex[:8]}"
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        now = datetime.now(UTC)
        s.add(Asset(workspace_id=ws.id, asset_key="postgres://x/a", last_materialized_at=now))
        s.add(
            Asset(
                workspace_id=ws.id,
                asset_key="postgres://x/b",
                last_materialized_at=now - timedelta(hours=2),
            )
        )
        await s.commit()
        ws_id = ws.id
    try:
        sensor = LineageArrivalSensor(
            upstream_asset_keys=["postgres://x/a", "postgres://x/b"],
            window_minutes=60,
            require_all=False,
        )
        async with use_sensor_context(session_factory=real_factory, workspace_id=ws_id):
            result = await sensor.check_async()
        assert result.triggered is True
        assert result.metadata["require_all"] is False
        assert {a["asset_key"] for a in result.metadata["arrived"]} == {"postgres://x/a"}
        assert result.metadata["stale"] == ["postgres://x/b"]
    finally:
        async with real_factory() as s:
            await s.execute(delete(Asset).where(Asset.workspace_id == ws_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_lineage_arrival_dedupes_via_last_triggered_at(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Same materialisation event must not refire after the sensor has
    already fired on it: ``last_triggered_at`` raises the cutoff above
    the materialisation timestamp, so the next check is quiet."""
    slug = f"sn-la-dedup-{uuid4().hex[:8]}"
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        materialised_at = datetime.now(UTC) - timedelta(minutes=5)
        s.add(
            Asset(
                workspace_id=ws.id,
                asset_key="postgres://x/a",
                last_materialized_at=materialised_at,
            )
        )
        await s.commit()
        ws_id = ws.id
    try:
        sensor = LineageArrivalSensor(
            upstream_asset_keys=["postgres://x/a"],
            window_minutes=60,
            require_all=True,
        )
        # First check (no prior fire) → triggered.
        async with use_sensor_context(
            session_factory=real_factory, workspace_id=ws_id, last_triggered_at=None
        ):
            first = await sensor.check_async()
        assert first.triggered is True
        # Pretend the scheduler stamped last_triggered_at = now.
        # The materialisation timestamp is older than this stamp → quiet.
        async with use_sensor_context(
            session_factory=real_factory,
            workspace_id=ws_id,
            last_triggered_at=datetime.now(UTC),
        ):
            second = await sensor.check_async()
        assert second.triggered is False
        assert second.metadata["stale"] == ["postgres://x/a"]
    finally:
        async with real_factory() as s:
            await s.execute(delete(Asset).where(Asset.workspace_id == ws_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_lineage_arrival_marks_uncatalogued_keys_as_missing(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An asset_key absent from the catalog at all is reported as
    ``missing``, distinct from ``stale`` (which means in-catalog but old).
    With ``require_all=True`` this prevents trigger; consumers see WHICH
    key isn't even being produced."""
    slug = f"sn-la-miss-{uuid4().hex[:8]}"
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        s.add(
            Asset(
                workspace_id=ws.id,
                asset_key="postgres://x/a",
                last_materialized_at=datetime.now(UTC),
            )
        )
        await s.commit()
        ws_id = ws.id
    try:
        sensor = LineageArrivalSensor(
            upstream_asset_keys=["postgres://x/a", "postgres://x/never-existed"],
            window_minutes=60,
            require_all=True,
        )
        async with use_sensor_context(session_factory=real_factory, workspace_id=ws_id):
            result = await sensor.check_async()
        assert result.triggered is False
        assert result.metadata["missing"] == ["postgres://x/never-existed"]
        assert {a["asset_key"] for a in result.metadata["arrived"]} == {"postgres://x/a"}
    finally:
        async with real_factory() as s:
            await s.execute(delete(Asset).where(Asset.workspace_id == ws_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


# ---- FileLandedSensor builtin ---------------------------------------------
#
# Polls a workspace S3 connection's bucket+prefix; fires when a matching
# object lands. Tests cover builder validation + the soft-fail paths
# (missing connection, wrong type, secret resolution OK with no bucket)
# + end-to-end with a stubbed S3 client (monkey-patched to avoid spinning
# up LocalStack just for one sensor).


def _seed_s3_connection(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    bucket: str,
    name: str = "s3-data-lake",
    type_: str = "s3",
) -> Connection:
    conn = Connection(
        workspace_id=workspace_id,
        type=type_,
        name=name,
        # No secret placeholders in the test fixture — the resolver is
        # exercised by a dedicated secret-walker test; we cover the
        # connection-lookup + S3-client path here. Embedding plaintext
        # creds in a TEST is fine (no real bucket); production code is
        # what the !secret hard rule applies to.
        config_json={"bucket": bucket, "region": "us-east-1"},
        secret_refs=[],
    )
    session.add(conn)
    return conn


class _NoopBackend:
    """SecretBackend stub — file_landed doesn't actually walk secrets in
    these tests (config has no ``${SECRET:..}`` markers) but the sensor
    refuses to run without ``backend is not None`` in the ContextVar."""

    def get(self, path: str) -> str:  # pragma: no cover — never reached
        raise AssertionError(f"unexpected secret backend lookup: {path!r}")

    def set(self, path: str, value: str) -> None:  # pragma: no cover
        raise AssertionError("read-only")

    def delete(self, path: str) -> None:  # pragma: no cover
        raise AssertionError("read-only")


async def test_file_landed_builder_rejects_bad_config() -> None:
    from etl_plugins.core.exceptions import ConfigError
    from etl_plugins.core.sensor import build_sensor

    # Missing connection_id
    with pytest.raises(ConfigError):
        build_sensor("file_landed", {"prefix": "x/"})
    # connection_id not a UUID
    with pytest.raises(ConfigError):
        build_sensor("file_landed", {"connection_id": "not-a-uuid", "prefix": "x/"})
    # Missing prefix
    with pytest.raises(ConfigError):
        build_sensor("file_landed", {"connection_id": str(uuid4())})
    # Negative min_size_bytes
    with pytest.raises(ConfigError):
        build_sensor(
            "file_landed",
            {"connection_id": str(uuid4()), "prefix": "x/", "min_size_bytes": -1},
        )
    # bool min_size_bytes (isinstance(True, int) trap)
    with pytest.raises(ConfigError):
        build_sensor(
            "file_landed",
            {"connection_id": str(uuid4()), "prefix": "x/", "min_size_bytes": True},
        )


async def test_file_landed_missing_context_soft_fails() -> None:
    sensor = FileLandedSensor(
        connection_id=uuid4(), prefix="x/", pattern="*.parquet", min_size_bytes=0
    )
    result = await sensor.check_async()
    assert result.triggered is False
    assert result.metadata["error"] == "missing_context"


async def test_file_landed_unknown_connection_soft_fails(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """connection_id pointing at a non-existent row: triggered=False with
    a clear ``connection_not_found`` reason. No S3 call attempted."""
    slug = f"sn-fl-noconn-{uuid4().hex[:8]}"
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        await s.commit()
        ws_id = ws.id
    try:
        sensor = FileLandedSensor(connection_id=uuid4(), prefix="x/")
        async with use_sensor_context(
            session_factory=real_factory,
            workspace_id=ws_id,
            secret_backend=_NoopBackend(),  # type: ignore[arg-type]
        ):
            result = await sensor.check_async()
        assert result.triggered is False
        assert result.metadata["error"] == "connection_not_found"
    finally:
        async with real_factory() as s:
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_file_landed_wrong_connection_type_soft_fails(
    real_factory: async_sessionmaker[AsyncSession],
) -> None:
    """If the user picked a postgres connection by mistake, we report
    'wrong_connection_type' rather than crashing on a missing 'bucket'."""
    slug = f"sn-fl-wrong-{uuid4().hex[:8]}"
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        conn = Connection(
            workspace_id=ws.id,
            type="postgres",
            name="oops-not-s3",
            config_json={"host": "x", "port": 5432, "database": "d"},
            secret_refs=[],
        )
        s.add(conn)
        await s.commit()
        ws_id, conn_id = ws.id, conn.id
    try:
        sensor = FileLandedSensor(connection_id=conn_id, prefix="x/")
        async with use_sensor_context(
            session_factory=real_factory,
            workspace_id=ws_id,
            secret_backend=_NoopBackend(),  # type: ignore[arg-type]
        ):
            result = await sensor.check_async()
        assert result.triggered is False
        assert result.metadata["error"] == "wrong_connection_type"
        assert result.metadata["connection_type"] == "postgres"
    finally:
        async with real_factory() as s:
            await s.execute(delete(Connection).where(Connection.workspace_id == ws_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_file_landed_fires_when_matching_object_lands(
    real_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end happy path with a stubbed S3 client: prefix has 2 keys,
    one matches the pattern + size threshold, one doesn't → triggered."""
    slug = f"sn-fl-fire-{uuid4().hex[:8]}"
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        conn = _seed_s3_connection(s, workspace_id=ws.id, bucket="data-lake")
        await s.commit()
        ws_id, conn_id = ws.id, conn.id

    class _StubPaginator:
        def paginate(self, *, Bucket: str, Prefix: str) -> list[dict[str, Any]]:  # noqa: N803 — mirrors boto3 list_objects_v2 kwargs
            assert Bucket == "data-lake"
            assert Prefix == "incoming/orders/"
            now = datetime.now(UTC)
            return [
                {
                    "Contents": [
                        {
                            "Key": "incoming/orders/2026-05-26.parquet",
                            "Size": 12_345,
                            "LastModified": now,
                        },
                        {
                            "Key": "incoming/orders/2026-05-26.crc",  # wrong pattern
                            "Size": 4,
                            "LastModified": now,
                        },
                        {
                            "Key": "incoming/orders/.placeholder.parquet",
                            "Size": 0,  # below min_size_bytes
                            "LastModified": now,
                        },
                    ]
                }
            ]

    class _StubS3Client:
        def get_paginator(self, name: str) -> _StubPaginator:
            assert name == "list_objects_v2"
            return _StubPaginator()

    monkeypatch.setattr(
        "etlx_server.sensors.builtins.file_landed._build_s3_client",
        lambda _cfg: _StubS3Client(),
    )

    try:
        sensor = FileLandedSensor(
            connection_id=conn_id,
            prefix="incoming/orders/",
            pattern="*.parquet",
            min_size_bytes=1,
        )
        async with use_sensor_context(
            session_factory=real_factory,
            workspace_id=ws_id,
            secret_backend=_NoopBackend(),  # type: ignore[arg-type]
        ):
            result = await sensor.check_async()
        assert result.triggered is True
        assert result.metadata["bucket"] == "data-lake"
        assert result.metadata["match_count"] == 1
        # The .crc (wrong pattern) and the 0-byte placeholder must NOT
        # leak into matched.
        assert [m["key"] for m in result.metadata["matched"]] == [
            "incoming/orders/2026-05-26.parquet"
        ]
    finally:
        async with real_factory() as s:
            await s.execute(delete(Connection).where(Connection.workspace_id == ws_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


async def test_file_landed_dedupes_via_last_triggered_at(
    real_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same object landing must not refire after the sensor has already
    fired on it: ``last_triggered_at`` raises the cutoff above the
    object's LastModified so the next check is quiet."""
    slug = f"sn-fl-dedup-{uuid4().hex[:8]}"
    async with real_factory() as s:
        ws = await _seed_workspace(s, slug=slug)
        conn = _seed_s3_connection(s, workspace_id=ws.id, bucket="bkt")
        await s.commit()
        ws_id, conn_id = ws.id, conn.id

    object_landed_at = datetime.now(UTC) - timedelta(minutes=10)

    class _StubPaginator:
        def paginate(self, *, Bucket: str, Prefix: str) -> list[dict[str, Any]]:  # noqa: N803 — mirrors boto3 list_objects_v2 kwargs
            return [
                {
                    "Contents": [
                        {
                            "Key": "x/file.json",
                            "Size": 100,
                            "LastModified": object_landed_at,
                        }
                    ]
                }
            ]

    class _StubS3Client:
        def get_paginator(self, _name: str) -> _StubPaginator:
            return _StubPaginator()

    monkeypatch.setattr(
        "etlx_server.sensors.builtins.file_landed._build_s3_client",
        lambda _cfg: _StubS3Client(),
    )

    try:
        sensor = FileLandedSensor(connection_id=conn_id, prefix="x/", pattern="*.json")
        # First check (no prior fire) → triggered.
        async with use_sensor_context(
            session_factory=real_factory,
            workspace_id=ws_id,
            last_triggered_at=None,
            secret_backend=_NoopBackend(),  # type: ignore[arg-type]
        ):
            first = await sensor.check_async()
        assert first.triggered is True
        # Pretend the scheduler stamped last_triggered_at = NOW (after
        # the object landed). Same file is still present in the listing
        # but its LastModified is older than the cutoff → quiet.
        async with use_sensor_context(
            session_factory=real_factory,
            workspace_id=ws_id,
            last_triggered_at=datetime.now(UTC),
            secret_backend=_NoopBackend(),  # type: ignore[arg-type]
        ):
            second = await sensor.check_async()
        assert second.triggered is False
        assert second.metadata["match_count"] == 0
    finally:
        async with real_factory() as s:
            await s.execute(delete(Connection).where(Connection.workspace_id == ws_id))
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()
