"""Alembic environment — async (ADR-0020).

DB URL resolution order:
  1. ``$DATABASE_URL`` 환경변수 (운영/CI/로컬 dev)
  2. ``alembic.ini``의 ``sqlalchemy.url`` (fallback)

``target_metadata``는 ``etlx_server.db.Base.metadata`` — 모든 모델은
``etlx_server.db.models`` 한 곳에서 import되어 자동 등록된다.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context

# 모든 모델을 import해서 Base.metadata에 등록.
from etlx_server.db.base import Base
from etlx_server.db.models import (  # noqa: F401 — side-effect import for metadata registration
    AuditLog,
    Membership,
    PersonalAccessToken,
    Pipeline,
    PipelineVersion,
    Run,
    RunLog,
    RunMetric,
    Schedule,
    User,
    Workspace,
)
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 환경변수 우선.
url_from_env = os.environ.get("DATABASE_URL")
if url_from_env:
    config.set_main_option("sqlalchemy.url", url_from_env)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without connecting (`alembic upgrade head --sql`)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Connect to the DB and apply migrations (default)."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
