"""End-to-end OIDC router test against a real metadata DB (Step 8.2b).

Drives the full ``/auth/oidc/{providers,login,callback}`` flow with the IdP
mocked at the ``httpx`` transport layer. Verifies:

* providers listing reflects ``settings.auth_oidc_enabled``,
* ``/login`` returns a usable authorize URL + signed state,
* ``/callback`` provisions a new user, returns a token pair, and that
  ``/auth/me`` works with the issued access token,
* re-login refreshes the user's display name,
* email collisions with a local account surface as 409.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from anyduct_server.app_factory import create_app
from anyduct_server.auth.jwt_service import JwtService, generate_rsa_keypair_pem
from anyduct_server.auth.oidc_config import OidcProviderConfig
from anyduct_server.auth.oidc_service import OidcService
from anyduct_server.auth.oidc_state import OidcStateSigner
from anyduct_server.auth.password_service import PasswordService
from anyduct_server.db.enums import AuthMethod
from anyduct_server.db.models import User
from anyduct_server.dependencies import get_session
from anyduct_server.settings import Settings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import ASGITransport
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

ISSUER = "https://idp.example.com"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTH_URL = f"{ISSUER}/oauth2/auth"
TOKEN_URL = f"{ISSUER}/oauth2/token"
JWKS_URL = f"{ISSUER}/oauth2/jwks"
KID = "idp-key-1"
CLIENT_ID = "anyduct-client"


# --- IdP keypair ------------------------------------------------------------


def _new_idp_keypair() -> tuple[bytes, dict[str, Any]]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = KID
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    return private_pem, {"keys": [jwk]}


def _mint_id_token(
    *,
    private_pem: bytes,
    nonce: str,
    email: str = "alice@example.com",
    name: str = "Alice",
    sub: str = "idp-sub-001",
) -> str:
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": sub,
        "iat": now,
        "exp": now + 300,
        "nonce": nonce,
        "email": email,
        "email_verified": True,
        "name": name,
    }
    return jwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": KID})


class MockIdP:
    def __init__(self, *, jwks: dict[str, Any]) -> None:
        self._jwks = jwks
        self.next_id_token: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == DISCOVERY_URL:
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": AUTH_URL,
                    "token_endpoint": TOKEN_URL,
                    "jwks_uri": JWKS_URL,
                },
            )
        if url == JWKS_URL:
            return httpx.Response(200, json=self._jwks)
        if url == TOKEN_URL:
            return httpx.Response(
                200,
                json={
                    "access_token": "fake-access",
                    "id_token": self.next_id_token or "",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        return httpx.Response(404, text=f"unexpected URL: {url}")


# --- App fixture wiring -----------------------------------------------------


def _make_provider() -> OidcProviderConfig:
    return OidcProviderConfig(
        name="google",
        display_name="Google",
        client_id=CLIENT_ID,
        client_secret=SecretStr("s3cret"),
        discovery_url=DISCOVERY_URL,
        redirect_uri="https://app.example.com/auth/oidc/callback",
        scopes=["openid", "email", "profile"],
    )


def _build_app(
    session: AsyncSession,
    *,
    mock_idp: MockIdP,
    oidc_enabled: bool = True,
) -> FastAPI:
    """App wired to the test session + a mock IdP for OIDC.

    Skips the lifespan (which would open a real DB engine we don't need)
    and attaches JwtService + PasswordService + OidcService manually.
    """
    server_private, server_public = generate_rsa_keypair_pem(bits=2048)
    settings = Settings(
        database_url="postgresql+asyncpg://stub:stub@stub:5432/stub",
        auth_jwt_private_key_pem=server_private.decode("utf-8"),
        auth_jwt_public_key_pem=server_public.decode("utf-8"),
        auth_jwt_access_ttl_seconds=60,
        auth_jwt_refresh_ttl_seconds=120,
        auth_oidc_enabled=oidc_enabled,
        auth_oidc_providers=[_make_provider()],
    )
    app = create_app(settings=settings)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override_session
    app.state.password_service = PasswordService(rounds=4)
    app.state.jwt_service = JwtService(
        private_key_pem=server_private,
        public_key_pem=server_public,
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_audience,
        access_ttl_seconds=settings.auth_jwt_access_ttl_seconds,
        refresh_ttl_seconds=settings.auth_jwt_refresh_ttl_seconds,
    )
    state_signer = OidcStateSigner(
        private_key_pem=server_private,
        public_key_pem=server_public,
        issuer=settings.auth_jwt_issuer,
        ttl_seconds=settings.auth_oidc_state_ttl_seconds,
    )
    transport = httpx.MockTransport(mock_idp.handler)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    app.state.oidc_service = OidcService(
        providers=settings.auth_oidc_providers,
        state_signer=state_signer,
        http_client_factory=factory,
        nonce_factory=lambda: "n-fixed",
    )
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- tests ------------------------------------------------------------------


async def test_providers_lists_configured(session: AsyncSession) -> None:
    _, jwks = _new_idp_keypair()
    app = _build_app(session, mock_idp=MockIdP(jwks=jwks))
    async with _client(app) as client:
        resp = await client.get("/auth/oidc/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert body == [{"name": "google", "display_name": "Google"}]


async def test_providers_empty_when_disabled(session: AsyncSession) -> None:
    _, jwks = _new_idp_keypair()
    app = _build_app(session, mock_idp=MockIdP(jwks=jwks), oidc_enabled=False)
    async with _client(app) as client:
        resp = await client.get("/auth/oidc/providers")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_login_disabled_returns_503(session: AsyncSession) -> None:
    _, jwks = _new_idp_keypair()
    app = _build_app(session, mock_idp=MockIdP(jwks=jwks), oidc_enabled=False)
    async with _client(app) as client:
        resp = await client.get("/auth/oidc/login", params={"provider": "google"})
    assert resp.status_code == 503


async def test_login_unknown_provider_returns_404(session: AsyncSession) -> None:
    _, jwks = _new_idp_keypair()
    app = _build_app(session, mock_idp=MockIdP(jwks=jwks))
    async with _client(app) as client:
        resp = await client.get("/auth/oidc/login", params={"provider": "nope"})
    assert resp.status_code == 404


async def test_login_returns_authorize_url(session: AsyncSession) -> None:
    _, jwks = _new_idp_keypair()
    app = _build_app(session, mock_idp=MockIdP(jwks=jwks))
    async with _client(app) as client:
        resp = await client.get(
            "/auth/oidc/login",
            params={"provider": "google", "return_to": "/dashboard"},
        )
    assert resp.status_code == 200
    body = resp.json()
    parsed = urlparse(body["authorize_url"])
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTH_URL
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert params["client_id"] == CLIENT_ID
    assert params["nonce"] == "n-fixed"
    assert params["state"] == body["state"]


async def test_callback_provisions_user_and_issues_tokens(
    session: AsyncSession,
) -> None:
    private_pem, jwks = _new_idp_keypair()
    idp = MockIdP(jwks=jwks)
    app = _build_app(session, mock_idp=idp)
    async with _client(app) as client:
        login = await client.get(
            "/auth/oidc/login",
            params={"provider": "google", "return_to": "/x"},
        )
        state = login.json()["state"]
        idp.next_id_token = _mint_id_token(private_pem=private_pem, nonce="n-fixed")

        resp = await client.get(
            "/auth/oidc/callback",
            params={"provider": "google", "code": "code-xyz", "state": state},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["return_to"] == "/x"

        me = await client.get(
            "/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
        )
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


async def test_callback_relogin_updates_existing_user(session: AsyncSession) -> None:
    private_pem, jwks = _new_idp_keypair()
    idp = MockIdP(jwks=jwks)
    app = _build_app(session, mock_idp=idp)

    async def _login_with_name(client: httpx.AsyncClient, name: str) -> dict[str, Any]:
        login = await client.get("/auth/oidc/login", params={"provider": "google"})
        state = login.json()["state"]
        idp.next_id_token = _mint_id_token(private_pem=private_pem, nonce="n-fixed", name=name)
        resp = await client.get(
            "/auth/oidc/callback",
            params={"provider": "google", "code": "c1", "state": state},
        )
        return resp.json()  # type: ignore[no-any-return]

    async with _client(app) as client:
        first = await _login_with_name(client, "Alice")
        me1 = await client.get(
            "/auth/me", headers={"Authorization": f"Bearer {first['access_token']}"}
        )
        await _login_with_name(client, "Alice Updated")
        # Second login should have refreshed the row in place.
        me_again = await client.get(
            "/auth/me", headers={"Authorization": f"Bearer {first['access_token']}"}
        )
    assert me1.status_code == 200
    assert me1.json()["name"] == "Alice"
    assert me_again.json()["name"] == "Alice Updated"


async def test_callback_rejects_local_account_collision(session: AsyncSession) -> None:
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

    private_pem, jwks = _new_idp_keypair()
    idp = MockIdP(jwks=jwks)
    app = _build_app(session, mock_idp=idp)
    async with _client(app) as client:
        login = await client.get("/auth/oidc/login", params={"provider": "google"})
        state = login.json()["state"]
        idp.next_id_token = _mint_id_token(private_pem=private_pem, nonce="n-fixed")
        resp = await client.get(
            "/auth/oidc/callback",
            params={"provider": "google", "code": "c", "state": state},
        )
    assert resp.status_code == 409
    assert "local" in resp.json()["detail"]


async def test_callback_rejects_tampered_state(session: AsyncSession) -> None:
    _, jwks = _new_idp_keypair()
    app = _build_app(session, mock_idp=MockIdP(jwks=jwks))
    async with _client(app) as client:
        resp = await client.get(
            "/auth/oidc/callback",
            params={"provider": "google", "code": "c", "state": "garbage"},
        )
    assert resp.status_code == 401


async def test_callback_rejects_state_provider_mismatch(session: AsyncSession) -> None:
    _, jwks = _new_idp_keypair()
    app = _build_app(session, mock_idp=MockIdP(jwks=jwks))
    async with _client(app) as client:
        login = await client.get("/auth/oidc/login", params={"provider": "google"})
        state = login.json()["state"]
        # State was bound to "google" but we hit the callback with a different
        # provider name — service rejects.
        resp = await client.get(
            "/auth/oidc/callback",
            params={"provider": "nope", "code": "c", "state": state},
        )
    assert resp.status_code == 404  # unknown provider trips first


async def test_callback_with_unconfigured_oidc_returns_503(session: AsyncSession) -> None:
    _, jwks = _new_idp_keypair()
    app = _build_app(session, mock_idp=MockIdP(jwks=jwks), oidc_enabled=False)
    async with _client(app) as client:
        resp = await client.get(
            "/auth/oidc/callback",
            params={"provider": "google", "code": "c", "state": "x"},
        )
    assert resp.status_code == 503
