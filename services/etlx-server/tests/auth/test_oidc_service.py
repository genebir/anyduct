"""OidcService unit tests (Step 8.2b).

Mocks the IdP via :class:`httpx.MockTransport` — discovery, JWKS, token, and
userinfo endpoints all respond from in-process handlers. ID tokens are real
JWTs signed with a separate RSA keypair representing the IdP, so the
signature-verification path runs end-to-end.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from etlx_server.auth.jwt_service import generate_rsa_keypair_pem
from etlx_server.auth.oidc_config import OidcProviderConfig
from etlx_server.auth.oidc_service import (
    IdTokenError,
    OidcDiscoveryError,
    OidcExchangeError,
    OidcService,
    UnknownProviderError,
)
from etlx_server.auth.oidc_state import OidcStateSigner
from etlx_server.db.enums import AuthMethod
from jwt.algorithms import RSAAlgorithm
from pydantic import SecretStr

ISSUER = "https://idp.example.com"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTH_URL = f"{ISSUER}/oauth2/auth"
TOKEN_URL = f"{ISSUER}/oauth2/token"
JWKS_URL = f"{ISSUER}/oauth2/jwks"
USERINFO_URL = f"{ISSUER}/oauth2/userinfo"
KID = "idp-key-1"


# --- IdP keypair + JWKS -----------------------------------------------------


@pytest.fixture(scope="module")
def idp_keys() -> tuple[bytes, bytes, dict[str, Any]]:
    """Generate a fresh RSA keypair representing the IdP, plus its JWKS doc."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = KID
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    jwks = {"keys": [jwk]}
    return private_pem, public_pem, jwks


def _mint_id_token(
    *,
    private_pem: bytes,
    audience: str,
    nonce: str,
    email: str = "alice@example.com",
    email_verified: bool | None = True,
    name: str | None = "Alice",
    sub: str = "idp-sub-001",
    exp_offset: int = 300,
    iss: str = ISSUER,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": iss,
        "aud": audience,
        "sub": sub,
        "iat": now,
        "exp": now + exp_offset,
        "nonce": nonce,
        "email": email,
    }
    if email_verified is not None:
        payload["email_verified"] = email_verified
    if name is not None:
        payload["name"] = name
    return jwt.encode(payload, private_pem, algorithm="RS256", headers={"kid": KID})


# --- Mock IdP transport -----------------------------------------------------


class MockIdP:
    """Stateful mock that records the last code-exchange and lets tests
    pre-stage what the next token endpoint call should return."""

    def __init__(self, *, jwks: dict[str, Any]) -> None:
        self._jwks = jwks
        self.next_id_token: str | None = None
        self.last_form: dict[str, str] = {}
        self.fail_token_endpoint = False
        self.fail_discovery = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == DISCOVERY_URL:
            if self.fail_discovery:
                return httpx.Response(500, text="discovery boom")
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": AUTH_URL,
                    "token_endpoint": TOKEN_URL,
                    "jwks_uri": JWKS_URL,
                    "userinfo_endpoint": USERINFO_URL,
                },
            )
        if url == JWKS_URL:
            return httpx.Response(200, json=self._jwks)
        if url == TOKEN_URL:
            if self.fail_token_endpoint:
                return httpx.Response(400, json={"error": "invalid_grant"})
            body = request.content.decode()
            self.last_form = {k: v[0] for k, v in parse_qs(body).items()}
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


# --- Service fixtures -------------------------------------------------------


@pytest.fixture(scope="module")
def server_keys() -> tuple[bytes, bytes]:
    return generate_rsa_keypair_pem(bits=2048)


@pytest.fixture
def state_signer(server_keys: tuple[bytes, bytes]) -> OidcStateSigner:
    private, public = server_keys
    return OidcStateSigner(
        private_key_pem=private,
        public_key_pem=public,
        issuer="etlx-server",
        ttl_seconds=60,
    )


def _make_provider(name: str = "google", *, client_id: str = "etlx-client") -> OidcProviderConfig:
    return OidcProviderConfig(
        name=name,
        display_name=name.title(),
        client_id=client_id,
        client_secret=SecretStr("s3cret"),
        discovery_url=DISCOVERY_URL,
        redirect_uri="https://app.example.com/auth/oidc/callback",
        scopes=["openid", "email", "profile"],
    )


@pytest.fixture
def mock_idp(idp_keys: tuple[bytes, bytes, dict[str, Any]]) -> MockIdP:
    return MockIdP(jwks=idp_keys[2])


def _make_service(
    *,
    providers: list[OidcProviderConfig],
    state_signer: OidcStateSigner,
    mock_idp: MockIdP,
    nonce: str = "n-fixed",
) -> OidcService:
    transport = httpx.MockTransport(mock_idp.handler)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    return OidcService(
        providers=providers,
        state_signer=state_signer,
        http_client_factory=factory,
        nonce_factory=lambda: nonce,
    )


# --- tests ------------------------------------------------------------------


def test_provider_registry_lookup(state_signer: OidcStateSigner, mock_idp: MockIdP) -> None:
    p = _make_provider("google")
    svc = _make_service(providers=[p], state_signer=state_signer, mock_idp=mock_idp)
    assert [x.name for x in svc.list_providers()] == ["google"]
    assert svc.get_provider("google") is p
    with pytest.raises(UnknownProviderError):
        svc.get_provider("unknown")


def test_duplicate_provider_names_rejected(state_signer: OidcStateSigner) -> None:
    p1 = _make_provider("google", client_id="a")
    p2 = _make_provider("google", client_id="b")
    with pytest.raises(ValueError, match="unique"):
        OidcService(providers=[p1, p2], state_signer=state_signer)


def test_provider_name_maps_to_auth_method() -> None:
    assert _make_provider("google").auth_method is AuthMethod.OIDC_GOOGLE
    assert _make_provider("azure").auth_method is AuthMethod.OIDC_AZURE
    assert _make_provider("okta").auth_method is AuthMethod.OIDC_OKTA
    assert _make_provider("github").auth_method is AuthMethod.OIDC_GITHUB
    assert _make_provider("custom").auth_method is AuthMethod.OIDC_GENERIC


@pytest.mark.asyncio
async def test_build_authorize_url_shape(state_signer: OidcStateSigner, mock_idp: MockIdP) -> None:
    p = _make_provider()
    svc = _make_service(providers=[p], state_signer=state_signer, mock_idp=mock_idp)
    authorize_url, state_token = await svc.build_authorize_url("google", return_to="/dashboard")
    parsed = urlparse(authorize_url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTH_URL
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert params["response_type"] == "code"
    assert params["client_id"] == p.client_id
    assert params["redirect_uri"] == p.redirect_uri
    assert params["scope"] == "openid email profile"
    assert params["nonce"] == "n-fixed"
    assert params["state"] == state_token
    # State decodes to the expected fields.
    state = state_signer.verify(state_token)
    assert state.provider == "google"
    assert state.return_to == "/dashboard"


@pytest.mark.asyncio
async def test_build_authorize_url_unknown_provider(
    state_signer: OidcStateSigner, mock_idp: MockIdP
) -> None:
    svc = _make_service(providers=[_make_provider()], state_signer=state_signer, mock_idp=mock_idp)
    with pytest.raises(UnknownProviderError):
        await svc.build_authorize_url("nope")


@pytest.mark.asyncio
async def test_build_authorize_url_discovery_failure(
    state_signer: OidcStateSigner, mock_idp: MockIdP
) -> None:
    mock_idp.fail_discovery = True
    svc = _make_service(providers=[_make_provider()], state_signer=state_signer, mock_idp=mock_idp)
    with pytest.raises(OidcDiscoveryError):
        await svc.build_authorize_url("google")


@pytest.mark.asyncio
async def test_handle_callback_happy_path(
    state_signer: OidcStateSigner,
    mock_idp: MockIdP,
    idp_keys: tuple[bytes, bytes, dict[str, Any]],
) -> None:
    p = _make_provider()
    svc = _make_service(providers=[p], state_signer=state_signer, mock_idp=mock_idp)
    _, state = await svc.build_authorize_url("google", return_to="/x")
    mock_idp.next_id_token = _mint_id_token(
        private_pem=idp_keys[0], audience=p.client_id, nonce="n-fixed"
    )

    result = await svc.handle_callback(
        provider_name="google", code="auth-code-xyz", state_token=state
    )
    assert result.user_info.email == "alice@example.com"
    assert result.user_info.name == "Alice"
    assert result.user_info.subject == "idp-sub-001"
    assert result.return_to == "/x"
    # Confirm token exchange got the right form fields.
    assert mock_idp.last_form["code"] == "auth-code-xyz"
    assert mock_idp.last_form["client_id"] == p.client_id
    assert mock_idp.last_form["redirect_uri"] == p.redirect_uri
    assert mock_idp.last_form["grant_type"] == "authorization_code"


@pytest.mark.asyncio
async def test_handle_callback_state_provider_mismatch(
    state_signer: OidcStateSigner, mock_idp: MockIdP
) -> None:
    p = _make_provider("google")
    svc = _make_service(providers=[p], state_signer=state_signer, mock_idp=mock_idp)
    # Forge a state token bound to a different provider name.
    other_state = state_signer.sign(provider="azure", nonce="n", return_to=None)
    with pytest.raises(IdTokenError, match="state/provider mismatch"):
        await svc.handle_callback(provider_name="google", code="x", state_token=other_state)


@pytest.mark.asyncio
async def test_handle_callback_token_endpoint_error(
    state_signer: OidcStateSigner, mock_idp: MockIdP
) -> None:
    p = _make_provider()
    svc = _make_service(providers=[p], state_signer=state_signer, mock_idp=mock_idp)
    _, state = await svc.build_authorize_url("google")
    mock_idp.fail_token_endpoint = True
    with pytest.raises(OidcExchangeError):
        await svc.handle_callback(provider_name="google", code="x", state_token=state)


@pytest.mark.asyncio
async def test_handle_callback_nonce_mismatch(
    state_signer: OidcStateSigner,
    mock_idp: MockIdP,
    idp_keys: tuple[bytes, bytes, dict[str, Any]],
) -> None:
    p = _make_provider()
    svc = _make_service(providers=[p], state_signer=state_signer, mock_idp=mock_idp)
    _, state = await svc.build_authorize_url("google")
    # ID token carries a different nonce — must be rejected.
    mock_idp.next_id_token = _mint_id_token(
        private_pem=idp_keys[0], audience=p.client_id, nonce="DIFFERENT"
    )
    with pytest.raises(IdTokenError, match="nonce"):
        await svc.handle_callback(provider_name="google", code="x", state_token=state)


@pytest.mark.asyncio
async def test_handle_callback_rejects_unverified_email(
    state_signer: OidcStateSigner,
    mock_idp: MockIdP,
    idp_keys: tuple[bytes, bytes, dict[str, Any]],
) -> None:
    p = _make_provider()
    svc = _make_service(providers=[p], state_signer=state_signer, mock_idp=mock_idp)
    _, state = await svc.build_authorize_url("google")
    mock_idp.next_id_token = _mint_id_token(
        private_pem=idp_keys[0],
        audience=p.client_id,
        nonce="n-fixed",
        email_verified=False,
    )
    with pytest.raises(IdTokenError, match="not verified"):
        await svc.handle_callback(provider_name="google", code="x", state_token=state)


@pytest.mark.asyncio
async def test_handle_callback_audience_mismatch_rejected(
    state_signer: OidcStateSigner,
    mock_idp: MockIdP,
    idp_keys: tuple[bytes, bytes, dict[str, Any]],
) -> None:
    p = _make_provider(client_id="our-client")
    svc = _make_service(providers=[p], state_signer=state_signer, mock_idp=mock_idp)
    _, state = await svc.build_authorize_url("google")
    # ID token issued for a different audience.
    mock_idp.next_id_token = _mint_id_token(
        private_pem=idp_keys[0], audience="someone-else", nonce="n-fixed"
    )
    with pytest.raises(IdTokenError):
        await svc.handle_callback(provider_name="google", code="x", state_token=state)


@pytest.mark.asyncio
async def test_handle_callback_missing_id_token(
    state_signer: OidcStateSigner, mock_idp: MockIdP
) -> None:
    p = _make_provider()
    svc = _make_service(providers=[p], state_signer=state_signer, mock_idp=mock_idp)
    _, state = await svc.build_authorize_url("google")
    mock_idp.next_id_token = None  # token endpoint returns id_token=""
    with pytest.raises(OidcExchangeError, match="id_token"):
        await svc.handle_callback(provider_name="google", code="x", state_token=state)


@pytest.mark.asyncio
async def test_handle_callback_falls_back_to_email_local_when_name_missing(
    state_signer: OidcStateSigner,
    mock_idp: MockIdP,
    idp_keys: tuple[bytes, bytes, dict[str, Any]],
) -> None:
    p = _make_provider()
    svc = _make_service(providers=[p], state_signer=state_signer, mock_idp=mock_idp)
    _, state = await svc.build_authorize_url("google")
    mock_idp.next_id_token = _mint_id_token(
        private_pem=idp_keys[0],
        audience=p.client_id,
        nonce="n-fixed",
        email="bob@example.com",
        name=None,
    )
    result = await svc.handle_callback(provider_name="google", code="x", state_token=state)
    assert result.user_info.name == "bob"
