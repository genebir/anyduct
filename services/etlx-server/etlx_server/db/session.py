"""Async engine + session factory (ADR-0020).

Use::

    from etlx_server.db.session import make_engine, make_session_factory

    engine = make_engine("postgresql+asyncpg://etlx:etlx@localhost:5433/etlx")
    SessionLocal = make_session_factory(engine)

    async with SessionLocal() as session:
        ...
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_ENV_VAR = "DATABASE_URL"
DEFAULT_URL = "postgresql+asyncpg://etlx:etlx@localhost:5433/etlx"


def make_engine(url: str | None = None, *, echo: bool = False) -> AsyncEngine:
    """Create an async engine. ``url`` defaults to ``$DATABASE_URL`` then to a
    sensible local default (matches ``services/docker-compose.services.yml``)."""
    resolved = url or os.environ.get(DEFAULT_ENV_VAR, DEFAULT_URL)
    return create_async_engine(resolved, echo=echo, future=True)


def make_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """FastAPI ``Depends(get_session)`` 헬퍼 (Step 8 부트스트랩 시 wire)."""
    async with factory() as session:
        yield session
