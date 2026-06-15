"""OIDC SSO endpoints (Step 8.2b, ADR-0023).

Three endpoints under ``/auth/oidc``:

* ``GET /providers`` — public-safe list of configured providers (name +
  display_name). FE renders one button per provider.
* ``GET /login?provider=<name>&return_to=<url>`` — returns the IdP authorize
  URL plus the signed ``state`` token. FE either redirects via ``302`` or
  performs ``window.location = authorize_url`` — both work because state is
  embedded in the URL.
* ``GET /callback?provider=<name>&code=<code>&state=<state>`` — IdP returns
  here. We validate state + nonce, exchange the code, look up / provision
  the user, then mint an ``anyduct`` access + refresh token pair.

The auth-method on the resulting user comes from
:meth:`OidcProviderConfig.auth_method` — provider name → AuthMethod enum.

Email collisions with existing LOCAL accounts surface as 409 (account
takeover prevention — see :class:`UserRepository.OidcEmailCollisionError`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.auth.current_user import get_jwt_service, get_oidc_service
from anyduct_server.auth.jwt_service import JwtService
from anyduct_server.auth.oidc_service import (
    IdTokenError,
    OidcDiscoveryError,
    OidcExchangeError,
    OidcService,
    UnknownProviderError,
)
from anyduct_server.auth.schemas import (
    OidcAuthorizeResponse,
    OidcCallbackResponse,
    OidcProviderSummary,
)
from anyduct_server.auth.user_repository import OidcEmailCollisionError, UserRepository
from anyduct_server.dependencies import get_session, get_settings
from anyduct_server.settings import Settings

router = APIRouter(prefix="/auth/oidc", tags=["auth"])


def _require_enabled(settings: Settings) -> None:
    if not settings.auth_oidc_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC is disabled",
        )


@router.get("/providers", response_model=list[OidcProviderSummary])
async def list_providers(
    oidc: OidcService = Depends(get_oidc_service),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> list[OidcProviderSummary]:
    """Public summary of configured providers — secrets never leave the server."""
    if not settings.auth_oidc_enabled:
        return []
    return [
        OidcProviderSummary(name=p.name, display_name=p.display_name) for p in oidc.list_providers()
    ]


@router.get("/login", response_model=OidcAuthorizeResponse)
async def login(
    provider: str = Query(min_length=1, max_length=32),
    return_to: str | None = Query(default=None, max_length=2048),
    oidc: OidcService = Depends(get_oidc_service),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> OidcAuthorizeResponse:
    """Build the IdP authorize URL + signed state token."""
    _require_enabled(settings)
    try:
        authorize_url, state = await oidc.build_authorize_url(provider, return_to=return_to)
    except UnknownProviderError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except OidcDiscoveryError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return OidcAuthorizeResponse(authorize_url=authorize_url, state=state)


@router.get("/callback", response_model=OidcCallbackResponse)
async def callback(
    provider: str = Query(min_length=1, max_length=32),
    code: str = Query(min_length=1),
    state: str = Query(min_length=1),
    oidc: OidcService = Depends(get_oidc_service),  # noqa: B008
    jwt_service: JwtService = Depends(get_jwt_service),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> OidcCallbackResponse:
    """Exchange the authorization code and mint an ``anyduct`` token pair."""
    _require_enabled(settings)
    try:
        result = await oidc.handle_callback(provider_name=provider, code=code, state_token=state)
    except UnknownProviderError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except IdTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"OIDC callback rejected: {e}",
        ) from e
    except OidcExchangeError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"OIDC token exchange failed: {e}",
        ) from e
    except OidcDiscoveryError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"OIDC discovery failed: {e}",
        ) from e

    repo = UserRepository(session)
    try:
        user = await repo.provision_oidc_user(
            email=result.user_info.email,
            name=result.user_info.name,
            auth_method=result.provider.auth_method,
        )
    except OidcEmailCollisionError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    return OidcCallbackResponse(
        access_token=jwt_service.issue_access(user.id),
        refresh_token=jwt_service.issue_refresh(user.id),
        expires_in=settings.auth_jwt_access_ttl_seconds,
        return_to=result.return_to,
    )
