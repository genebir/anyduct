"""Test fixtures for anyduct-server.

Two layers:

* **Unit** (no Docker) — ``TestClient`` against the FastAPI placeholder
  (e.g. ``tests/test_health.py``). No DB.
* **Integration** (Docker required) — testcontainers Postgres + Alembic
  ``upgrade head`` + async session. ``pytestmark = pytest.mark.it`` in each
  integration test module.

The integration fixtures spin a single PG container per session, run
``alembic upgrade head`` once, and hand each test a fresh async session
inside an outer transaction that rolls back on teardown (test isolation
without re-creating the schema).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_DIR = REPO_ROOT / "services" / "anyduct-server"


@pytest.fixture(scope="session")
def metadata_db_container() -> Iterator[PostgresContainer]:
    """A long-lived postgres:16-alpine container for metadata schema tests."""
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def metadata_db_url(metadata_db_container: PostgresContainer) -> str:
    """``postgresql+asyncpg://...`` URL for SQLAlchemy + Alembic."""
    host = metadata_db_container.get_container_host_ip()
    port = metadata_db_container.get_exposed_port(5432)
    user = metadata_db_container.username
    password = metadata_db_container.password
    db = metadata_db_container.dbname
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


@pytest.fixture(scope="session")
def _alembic_upgrade(metadata_db_url: str) -> None:
    """Run ``alembic upgrade head`` against the container once per session.

    Uses subprocess so alembic config + env.py are exercised as production
    would. ``DATABASE_URL`` env var drives the URL (env.py reads it).
    """
    env = {**os.environ, "DATABASE_URL": metadata_db_url}
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=str(SERVER_DIR),
        env=env,
        check=True,
    )


@pytest_asyncio.fixture(scope="session")
async def metadata_engine(
    metadata_db_url: str, _alembic_upgrade: None
) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(metadata_db_url, future=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session(metadata_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Per-test async session with rollback isolation.

    매 테스트마다 새 connection + 외부 트랜잭션을 시작하고, 테스트 종료 시
    무조건 rollback해서 다음 테스트에 상태가 새지 않게 한다.
    """
    connection = await metadata_engine.connect()
    trans = await connection.begin()
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        autoflush=False,
    )
    async with factory() as s:
        try:
            yield s
        finally:
            await s.close()
    await trans.rollback()
    await connection.close()


# 비-async 테스트 (예: tests/test_health.py)에서도 conftest가 수집되는데,
# 위의 fixtures가 모두 testcontainers를 import 한다. tests 파일이 사용하지
# 않으면 fixture 자체가 인스턴스화되지 않으므로 안전.


def pytest_collection_modifyitems(items: list[Any]) -> None:
    """services/anyduct-server/tests/db/* 는 모두 통합 테스트로 마킹."""
    for item in items:
        if "/tests/db/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.it)
