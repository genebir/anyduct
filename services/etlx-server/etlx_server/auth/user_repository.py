"""User table access — read + auth-provisioning queries.

Read paths (``get_by_id`` / ``get_by_email``) feed the login + ``Depends``
flow. ``provision_oidc_user`` is the OIDC-callback upsert: it creates a row
on first SSO login and refreshes the display name on re-login, while
refusing email collisions that could enable account takeover.

General CRUD (admin-driven user / membership management) lives in
Step 8.5 and will go through its own repository.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.db.enums import AuthMethod
from etlx_server.db.models import User


class OidcEmailCollisionError(Exception):
    """Raised when an OIDC callback email matches an existing user with an
    incompatible ``auth_method``.

    Two cases:

    * existing user is ``LOCAL`` — refusing protects against account takeover
      (an attacker who registers ``victim@example.com`` at the IdP could
      otherwise hijack the local account).
    * existing user is OIDC from a *different* provider — same email at two
      different IdPs is almost always a misconfiguration; manual operator
      action is safer than silent re-binding.
    """


class UserRepository:
    """Async data access for ``users``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        # Email uniqueness is enforced at the DB level (workspace.py:43).
        result = await self._session.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def provision_oidc_user(self, *, email: str, name: str, auth_method: AuthMethod) -> User:
        """Upsert an OIDC-authenticated user.

        * Create on first login.
        * On re-login from the same provider, refresh ``name``.
        * Reject if the email already belongs to a LOCAL account or to a
          different OIDC provider — see :class:`OidcEmailCollisionError`.
        """
        if auth_method is AuthMethod.LOCAL:
            raise ValueError("provision_oidc_user called with auth_method=LOCAL")

        normalized_email = email.lower()
        existing = await self.get_by_email(normalized_email)
        if existing is not None:
            if existing.auth_method is not auth_method:
                raise OidcEmailCollisionError(
                    f"email {normalized_email!r} is already registered with "
                    f"auth_method={existing.auth_method.value!r}; cannot "
                    f"re-bind to {auth_method.value!r}"
                )
            if existing.name != name:
                existing.name = name
                await self._session.flush()
            return existing

        user = User(
            email=normalized_email,
            name=name,
            auth_method=auth_method,
            password_hash=None,
        )
        self._session.add(user)
        await self._session.flush()
        return user


__all__ = ["OidcEmailCollisionError", "UserRepository"]
