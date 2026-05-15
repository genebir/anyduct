"""Reusable FastAPI dependency injection helpers.

Step 8.1. Resources (settings, engine, session factory) are stored on
``app.state`` by the lifespan handler. These helpers expose them via
``Depends(...)`` so endpoints stay decoupled from how the app is wired.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from etlx_server.settings import Settings


def get_settings(request: Request) -> Settings:
    """Return the process-wide Settings instance attached to ``app.state``."""
    return request.app.state.settings  # type: ignore[no-any-return]


def get_engine(request: Request) -> AsyncEngine:
    return request.app.state.engine  # type: ignore[no-any-return]


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory  # type: ignore[no-any-return]


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a fresh async session per request. Auto-rolled back on exception."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


__all__ = ["get_engine", "get_session", "get_session_factory", "get_settings"]
