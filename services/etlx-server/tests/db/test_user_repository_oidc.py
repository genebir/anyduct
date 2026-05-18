"""UserRepository.provision_oidc_user — integration tests (Step 8.2b).

Exercises the upsert + email-collision logic against a real metadata DB,
because the conftest ``session`` fixture provides per-test rollback
isolation that a unit-level fake can't replicate.
"""

from __future__ import annotations

import pytest
from etlx_server.auth.password_service import PasswordService
from etlx_server.auth.user_repository import (
    OidcEmailCollisionError,
    UserRepository,
)
from etlx_server.db.enums import AuthMethod
from etlx_server.db.models import User
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_provision_creates_new_oidc_user(session: AsyncSession) -> None:
    repo = UserRepository(session)
    user = await repo.provision_oidc_user(
        email="Alice@Example.com",
        name="Alice",
        auth_method=AuthMethod.OIDC_GOOGLE,
    )
    assert user.email == "alice@example.com"  # normalized
    assert user.name == "Alice"
    assert user.auth_method is AuthMethod.OIDC_GOOGLE
    assert user.password_hash is None


async def test_provision_relogin_updates_name(session: AsyncSession) -> None:
    repo = UserRepository(session)
    await repo.provision_oidc_user(
        email="alice@example.com",
        name="Alice",
        auth_method=AuthMethod.OIDC_GOOGLE,
    )
    second = await repo.provision_oidc_user(
        email="alice@example.com",
        name="Alice Updated",
        auth_method=AuthMethod.OIDC_GOOGLE,
    )
    assert second.name == "Alice Updated"


async def test_provision_relogin_is_idempotent(session: AsyncSession) -> None:
    repo = UserRepository(session)
    first = await repo.provision_oidc_user(
        email="alice@example.com",
        name="Alice",
        auth_method=AuthMethod.OIDC_GOOGLE,
    )
    second = await repo.provision_oidc_user(
        email="alice@example.com",
        name="Alice",
        auth_method=AuthMethod.OIDC_GOOGLE,
    )
    assert first.id == second.id


async def test_provision_rejects_local_account_collision(session: AsyncSession) -> None:
    pw = PasswordService(rounds=4)
    session.add(
        User(
            email="alice@example.com",
            name="Alice (local)",
            auth_method=AuthMethod.LOCAL,
            password_hash=pw.hash("hunter2"),
        )
    )
    await session.flush()

    repo = UserRepository(session)
    with pytest.raises(OidcEmailCollisionError, match="local"):
        await repo.provision_oidc_user(
            email="alice@example.com",
            name="Alice (OIDC)",
            auth_method=AuthMethod.OIDC_GOOGLE,
        )


async def test_provision_rejects_different_oidc_provider_collision(
    session: AsyncSession,
) -> None:
    repo = UserRepository(session)
    await repo.provision_oidc_user(
        email="alice@example.com",
        name="Alice",
        auth_method=AuthMethod.OIDC_GOOGLE,
    )
    with pytest.raises(OidcEmailCollisionError, match="oidc:google"):
        await repo.provision_oidc_user(
            email="alice@example.com",
            name="Alice",
            auth_method=AuthMethod.OIDC_AZURE,
        )


async def test_provision_local_auth_method_raises_value_error(
    session: AsyncSession,
) -> None:
    repo = UserRepository(session)
    with pytest.raises(ValueError, match="LOCAL"):
        await repo.provision_oidc_user(
            email="x@example.com",
            name="X",
            auth_method=AuthMethod.LOCAL,
        )
