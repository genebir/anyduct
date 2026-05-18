"""DI helpers for the auth layer (Step 8.2a).

* :func:`get_jwt_service`, :func:`get_password_service` — return the
  singletons attached to ``app.state`` in :mod:`etlx_server.app_factory`.
* :func:`get_current_user` — protected-endpoint dependency: validates
  ``Authorization: Bearer <token>``, loads the user row, returns a
  :class:`CurrentUser`. Raises 401 on any failure with a uniform body.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.auth.jwt_service import InvalidTokenError, JwtService, TokenType
from etlx_server.auth.oidc_service import OidcService
from etlx_server.auth.password_service import PasswordService
from etlx_server.auth.schemas import CurrentUser
from etlx_server.auth.user_repository import UserRepository
from etlx_server.dependencies import get_session

_bearer = HTTPBearer(auto_error=False)


def get_jwt_service(request: Request) -> JwtService:
    return request.app.state.jwt_service  # type: ignore[no-any-return]


def get_password_service(request: Request) -> PasswordService:
    return request.app.state.password_service  # type: ignore[no-any-return]


def get_oidc_service(request: Request) -> OidcService:
    """Return the singleton OidcService attached at startup. Raises 503 if
    OIDC was not configured."""
    oidc = getattr(request.app.state, "oidc_service", None)
    if oidc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC is not configured",
        )
    return oidc  # type: ignore[no-any-return]


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),  # noqa: B008
    jwt_service: JwtService = Depends(get_jwt_service),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = jwt_service.verify(credentials.credentials, expected_type=TokenType.ACCESS)
    except InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    user = await UserRepository(session).get_by_id(claims.subject)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return CurrentUser(
        id=user.id,
        email=user.email,
        name=user.name,
        is_superadmin=user.is_superadmin,
    )


__all__ = [
    "get_current_user",
    "get_jwt_service",
    "get_oidc_service",
    "get_password_service",
]
