"""``etlx-server admin create-user`` CLI integration tests.

The CLI's command body calls ``asyncio.run(...)`` itself, so these tests
stay synchronous (no ``@pytest.mark.asyncio``). They invoke via
``typer.testing.CliRunner`` and inspect the DB through a one-shot
``asyncio.run`` inside the assertion path — which is safe precisely
because the surrounding pytest function isn't running inside an event
loop."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest
from etlx_server.auth.password_service import PasswordService
from etlx_server.cli import app
from etlx_server.db.enums import AuthMethod
from etlx_server.db.models.workspace import User
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from typer.testing import CliRunner

pytestmark = pytest.mark.it

_runner = CliRunner()


@pytest.fixture
def env_db_url(metadata_db_url: str, _alembic_upgrade: None) -> Iterator[str]:
    """Point the CLI's DATABASE_URL env var at the testcontainer DB.

    The CLI makes its own engine + session, and assertions create
    yet-another fresh engine per ``asyncio.run`` call. asyncpg pools are
    bound to their creating event loop, so we cannot reuse the
    session-scoped ``metadata_engine`` from inside ``asyncio.run`` here.
    Building a one-shot engine + disposing inside each ``_go`` keeps
    every coroutine on a fresh loop with its own pool.

    Real rows commit through the CLI's path; we wipe ``users`` on
    teardown so other tests in the session aren't poisoned."""
    prior = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = metadata_db_url
    try:
        yield metadata_db_url
    finally:
        if prior is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = prior

        async def _cleanup() -> None:
            eng = create_async_engine(metadata_db_url, future=True)
            try:
                factory = async_sessionmaker(bind=eng, expire_on_commit=False)
                async with factory() as s:
                    await s.execute(delete(User))
                    await s.commit()
            finally:
                await eng.dispose()

        asyncio.run(_cleanup())


def _fetch_user(db_url: str, email: str) -> User | None:
    async def _go() -> User | None:
        eng = create_async_engine(db_url, future=True)
        try:
            factory = async_sessionmaker(bind=eng, expire_on_commit=False)
            async with factory() as s:
                return (
                    await s.execute(select(User).where(User.email == email))
                ).scalar_one_or_none()
        finally:
            await eng.dispose()

    return asyncio.run(_go())


def test_create_user_seeds_local_account(
    env_db_url: str,
) -> None:
    result = _runner.invoke(
        app,
        [
            "admin",
            "create-user",
            "--email",
            "alice@example.com",
            "--name",
            "Alice",
            "--password",
            "s3cret-pass",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "created: user" in result.output
    assert "alice@example.com" in result.output

    user = _fetch_user(env_db_url, "alice@example.com")
    assert user is not None
    assert user.name == "Alice"
    assert user.auth_method == AuthMethod.LOCAL
    assert user.is_superadmin is False
    assert user.password_hash is not None
    # bcrypt round-trip — the hash must verify the original password.
    assert PasswordService().verify("s3cret-pass", user.password_hash) is True


def test_create_user_superadmin_flag(
    env_db_url: str,
) -> None:
    result = _runner.invoke(
        app,
        [
            "admin",
            "create-user",
            "--email",
            "root@example.com",
            "--name",
            "Root",
            "--password",
            "super-pass",
            "--superadmin",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "(superadmin)" in result.output

    user = _fetch_user(env_db_url, "root@example.com")
    assert user is not None
    assert user.is_superadmin is True


def test_create_user_rejects_duplicate_email(
    env_db_url: str,
) -> None:
    first = _runner.invoke(
        app,
        [
            "admin",
            "create-user",
            "--email",
            "dup@example.com",
            "--name",
            "Dup",
            "--password",
            "p",
        ],
    )
    assert first.exit_code == 0, first.output

    second = _runner.invoke(
        app,
        [
            "admin",
            "create-user",
            "--email",
            "dup@example.com",
            "--name",
            "Other",
            "--password",
            "p",
        ],
    )
    assert second.exit_code == 1
    assert "already exists" in second.output
