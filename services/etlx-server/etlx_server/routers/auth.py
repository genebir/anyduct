"""Authentication endpoints — ``/auth/login``, ``/auth/refresh``, ``/auth/logout``.

Step 8.2a (local + JWT). OIDC endpoints arrive in Step 8.2b.

Notes:

* Logout is *stateless* — the server returns 204 and the client discards both
  tokens. Access tokens are short-lived (default 15 min) so a silently-leaked
  token has a small window. Refresh-rotation denylists (jti recorded on
  issue, revoked on logout) ride along with audit-log work in Step 8.4.
* Login responds with generic 401 for both "no such email" and "wrong
  password" — and runs a dummy bcrypt verify on the missing-user path so
  response time doesn't leak which case occurred.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.auth.current_user import (
    get_current_user,
    get_jwt_service,
    get_password_service,
)
from etlx_server.auth.jwt_service import InvalidTokenError, JwtService, TokenType
from etlx_server.auth.password_service import PasswordService
from etlx_server.auth.schemas import CurrentUser, LoginRequest, RefreshRequest, TokenPair
from etlx_server.auth.user_repository import UserRepository
from etlx_server.db.enums import AuthMethod
from etlx_server.dependencies import get_session, get_settings
from etlx_server.settings import Settings

# A well-formed bcrypt hash whose plaintext nobody knows. We feed this to
# password_service.verify() when the user lookup fails so that
# missing-user/wrong-password paths take roughly the same wall time.
_DUMMY_BCRYPT_HASH = "$2b$12$" + "A" * 53


router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_pair(jwt_service: JwtService, user_id: UUID, settings: Settings) -> TokenPair:
    return TokenPair(
        access_token=jwt_service.issue_access(user_id),
        refresh_token=jwt_service.issue_refresh(user_id),
        expires_in=settings.auth_jwt_access_ttl_seconds,
    )


@router.post("/login", response_model=TokenPair)
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    jwt_service: JwtService = Depends(get_jwt_service),  # noqa: B008
    password_service: PasswordService = Depends(get_password_service),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> TokenPair:
    """Local email + password login."""
    if not settings.auth_local_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="local authentication is disabled",
        )

    user = await UserRepository(session).get_by_email(body.email)
    invalid_creds = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid email or password",
    )

    if user is None or user.auth_method is not AuthMethod.LOCAL or not user.password_hash:
        password_service.verify(body.password, _DUMMY_BCRYPT_HASH)
        raise invalid_creds
    if not password_service.verify(body.password, user.password_hash):
        raise invalid_creds

    return _issue_pair(jwt_service, user.id, settings)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    body: RefreshRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    jwt_service: JwtService = Depends(get_jwt_service),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> TokenPair:
    """Trade a valid refresh token for a fresh access/refresh pair."""
    try:
        claims = jwt_service.verify(body.refresh_token, expected_type=TokenType.REFRESH)
    except InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid refresh token: {e}",
        ) from e
    user = await UserRepository(session).get_by_id(claims.subject)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user not found",
        )
    return _issue_pair(jwt_service, user.id, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(_: CurrentUser = Depends(get_current_user)) -> None:  # noqa: B008
    """Stateless logout — client discards tokens. Returns 204."""
    return None


@router.get("/me", response_model=CurrentUser)
async def me(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:  # noqa: B008
    """Return the authenticated user identity — handy for FE bootstrap."""
    return user
