"""OIDC service — handles the Authorization Code flow end-to-end (Step 8.2b).

ADR-0023. Generalized over Google / Azure AD / Okta / GitHub / arbitrary OIDC
providers; each is just a :class:`OidcProviderConfig` row.

Flow:

1. ``build_authorize_url(provider, return_to=...)`` returns the IdP URL the
   browser should hit and a signed ``state`` JWT. The state encodes
   ``provider`` + ``nonce`` + ``return_to`` so the callback is verifiable
   without server-side session storage.
2. The IdP redirects back to ``/auth/oidc/callback?provider=...&code=...&state=...``.
3. ``handle_callback(...)`` verifies the state, exchanges the code for an
   ID token + access token, verifies the ID token signature against the
   IdP's JWKS, checks the nonce, and returns an :class:`OidcUserInfo`.

The router takes that ``OidcUserInfo``, hands it to
:class:`UserRepository.provision_oidc_user`, and issues an ``etlx`` access
+ refresh token pair via :class:`JwtService`.

Dependencies are injectable for testing:

* ``http_client_factory`` — returns an ``httpx.AsyncClient``. Tests pass one
  backed by ``httpx.MockTransport`` so no network is touched.
* ``state_signer`` — :class:`OidcStateSigner`. Same RSA keypair as
  :class:`JwtService`.
* ``nonce_factory`` — defaults to :func:`secrets.token_urlsafe`. Tests can
  pin a deterministic value.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from etlx_server.auth.oidc_config import OidcProviderConfig
from etlx_server.auth.oidc_state import InvalidStateError, OidcStateSigner

HttpClientFactory = Callable[[], httpx.AsyncClient]


class UnknownProviderError(Exception):
    """Raised when a caller references a provider name that wasn't configured."""


class OidcDiscoveryError(Exception):
    """Raised when the IdP's discovery / JWKS document can't be loaded."""


class OidcExchangeError(Exception):
    """Raised when the token endpoint returns an error or a malformed payload."""


class IdTokenError(Exception):
    """Raised when ID token validation fails (signature, claims, or nonce)."""


@dataclass(frozen=True)
class OidcUserInfo:
    """Verified identity returned from a successful callback exchange."""

    provider: str
    """Provider name (e.g. ``google``)."""
    subject: str
    """IdP's stable user ID (``sub`` claim)."""
    email: str
    """Verified email address (lowercased)."""
    name: str
    """Display name (falls back to email-local part if IdP omits it)."""
    raw_id_token_claims: dict[str, Any]


@dataclass(frozen=True)
class OidcCallbackResult:
    """Bundle returned by :meth:`OidcService.handle_callback`."""

    user_info: OidcUserInfo
    provider: OidcProviderConfig
    return_to: str | None


def _default_http_client_factory(timeout_seconds: float) -> HttpClientFactory:
    def _factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout_seconds)

    return _factory


class OidcService:
    """Provider registry + Authorization Code flow driver."""

    def __init__(
        self,
        *,
        providers: Sequence[OidcProviderConfig],
        state_signer: OidcStateSigner,
        http_client_factory: HttpClientFactory | None = None,
        nonce_factory: Callable[[], str] | None = None,
        http_timeout_seconds: float = 10.0,
    ) -> None:
        self._providers: dict[str, OidcProviderConfig] = {p.name: p for p in providers}
        if len(self._providers) != len(providers):
            raise ValueError("OIDC provider names must be unique")
        self._state_signer = state_signer
        self._http_client_factory = http_client_factory or _default_http_client_factory(
            http_timeout_seconds
        )
        self._nonce_factory = nonce_factory or (lambda: secrets.token_urlsafe(32))
        # Cached discovery + JWKS docs — we trust the IdP to publish stable
        # endpoints; cache lives for the process lifetime. Operators can
        # restart the service to refresh.
        self._metadata_cache: dict[str, dict[str, Any]] = {}
        self._jwks_cache: dict[str, dict[str, Any]] = {}

    # ---------------------------------------------------------------- registry

    def list_providers(self) -> list[OidcProviderConfig]:
        return list(self._providers.values())

    def get_provider(self, name: str) -> OidcProviderConfig:
        try:
            return self._providers[name]
        except KeyError as e:
            raise UnknownProviderError(f"unknown OIDC provider: {name!r}") from e

    @property
    def is_configured(self) -> bool:
        return bool(self._providers)

    # ----------------------------------------------------------------- step 1

    async def build_authorize_url(
        self, provider_name: str, *, return_to: str | None = None
    ) -> tuple[str, str]:
        """Return ``(authorize_url, state_token)`` for redirecting the browser."""
        provider = self.get_provider(provider_name)
        metadata = await self._get_metadata(provider)
        nonce = self._nonce_factory()
        state = self._state_signer.sign(provider=provider.name, nonce=nonce, return_to=return_to)
        params = {
            "response_type": "code",
            "client_id": provider.client_id,
            "redirect_uri": provider.redirect_uri,
            "scope": " ".join(provider.scopes),
            "state": state,
            "nonce": nonce,
        }
        authorize_endpoint = self._require_endpoint(metadata, "authorization_endpoint", provider)
        return f"{authorize_endpoint}?{urlencode(params)}", state

    # ----------------------------------------------------------------- step 2

    async def handle_callback(
        self, *, provider_name: str, code: str, state_token: str
    ) -> OidcCallbackResult:
        """Verify state, exchange ``code``, validate ID token, return user info."""
        provider = self.get_provider(provider_name)
        try:
            state = self._state_signer.verify(state_token)
        except InvalidStateError as e:
            raise IdTokenError(f"state verification failed: {e}") from e
        if state.provider != provider.name:
            raise IdTokenError(
                f"state/provider mismatch: state={state.provider!r}, callback={provider.name!r}"
            )

        metadata = await self._get_metadata(provider)
        token_endpoint = self._require_endpoint(metadata, "token_endpoint", provider)

        token_payload = await self._exchange_code(
            token_endpoint=token_endpoint, provider=provider, code=code
        )
        id_token = token_payload.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise OidcExchangeError("token response missing id_token")

        claims = await self._verify_id_token(
            id_token=id_token, provider=provider, metadata=metadata, expected_nonce=state.nonce
        )
        user_info = self._extract_user_info(provider=provider, claims=claims)
        return OidcCallbackResult(user_info=user_info, provider=provider, return_to=state.return_to)

    # ---------------------------------------------------------------- internals

    async def _get_metadata(self, provider: OidcProviderConfig) -> dict[str, Any]:
        cached = self._metadata_cache.get(provider.name)
        if cached is not None:
            return cached
        async with self._http_client_factory() as client:
            try:
                resp = await client.get(provider.discovery_url)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                raise OidcDiscoveryError(
                    f"failed to load discovery doc for {provider.name!r}: {e}"
                ) from e
            metadata = resp.json()
        if not isinstance(metadata, dict):
            raise OidcDiscoveryError(f"discovery doc for {provider.name!r} is not a JSON object")
        self._metadata_cache[provider.name] = metadata
        return metadata

    async def _get_jwks(self, provider: OidcProviderConfig, jwks_uri: str) -> dict[str, Any]:
        cached = self._jwks_cache.get(provider.name)
        if cached is not None:
            return cached
        async with self._http_client_factory() as client:
            try:
                resp = await client.get(jwks_uri)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                raise OidcDiscoveryError(f"failed to load JWKS for {provider.name!r}: {e}") from e
            jwks = resp.json()
        if not isinstance(jwks, dict) or "keys" not in jwks:
            raise OidcDiscoveryError(f"JWKS for {provider.name!r} is malformed")
        self._jwks_cache[provider.name] = jwks
        return jwks

    async def _exchange_code(
        self, *, token_endpoint: str, provider: OidcProviderConfig, code: str
    ) -> dict[str, Any]:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": provider.redirect_uri,
            "client_id": provider.client_id,
            "client_secret": provider.client_secret.get_secret_value(),
        }
        async with self._http_client_factory() as client:
            try:
                resp = await client.post(token_endpoint, data=data)
            except httpx.HTTPError as e:
                raise OidcExchangeError(
                    f"token endpoint for {provider.name!r} unreachable: {e}"
                ) from e
        if resp.status_code >= 400:
            raise OidcExchangeError(
                f"token endpoint for {provider.name!r} returned "
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            )
        payload = resp.json()
        if not isinstance(payload, dict):
            raise OidcExchangeError("token endpoint returned non-object body")
        return payload

    async def _verify_id_token(
        self,
        *,
        id_token: str,
        provider: OidcProviderConfig,
        metadata: dict[str, Any],
        expected_nonce: str,
    ) -> dict[str, Any]:
        jwks_uri = self._require_endpoint(metadata, "jwks_uri", provider)
        jwks = await self._get_jwks(provider, jwks_uri)

        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as e:
            raise IdTokenError(f"id_token header invalid: {e}") from e
        kid = header.get("kid")
        alg = header.get("alg")
        if not isinstance(alg, str):
            raise IdTokenError("id_token missing 'alg' header")

        key = self._find_jwk(jwks, kid=kid, alg=alg)
        public_key = RSAAlgorithm.from_jwk(key)
        expected_issuer = _normalize_issuer(metadata.get("issuer"))
        try:
            claims = jwt.decode(
                id_token,
                public_key,  # type: ignore[arg-type]
                algorithms=[alg],
                audience=provider.client_id,
                issuer=expected_issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as e:
            raise IdTokenError(f"id_token validation failed: {e}") from e
        nonce = claims.get("nonce")
        if nonce != expected_nonce:
            raise IdTokenError("id_token nonce mismatch")
        return claims

    @staticmethod
    def _find_jwk(jwks: dict[str, Any], *, kid: str | None, alg: str) -> dict[str, Any]:
        keys: Iterable[dict[str, Any]] = jwks.get("keys") or ()
        candidates = [k for k in keys if isinstance(k, dict)]
        if kid is not None:
            for k in candidates:
                if k.get("kid") == kid:
                    return k
            raise IdTokenError(f"no JWK with kid={kid!r} in JWKS")
        # No kid in header — fall back to first key matching alg.
        for k in candidates:
            if k.get("alg") in (alg, None):
                return k
        raise IdTokenError(f"no JWK in JWKS matches alg={alg!r}")

    @staticmethod
    def _require_endpoint(metadata: dict[str, Any], key: str, provider: OidcProviderConfig) -> str:
        value = metadata.get(key)
        if not isinstance(value, str) or not value:
            raise OidcDiscoveryError(f"discovery doc for {provider.name!r} missing {key!r}")
        return value

    @staticmethod
    def _extract_user_info(*, provider: OidcProviderConfig, claims: dict[str, Any]) -> OidcUserInfo:
        email_raw = claims.get("email")
        if not isinstance(email_raw, str) or not email_raw:
            raise IdTokenError("id_token missing 'email' claim")
        email = email_raw.lower()
        # ``email_verified`` is optional in the spec but many IdPs include it.
        # When present, require it — refusing unverified emails prevents
        # account takeover via a malicious IdP that lets users self-attest.
        if claims.get("email_verified") is False:
            raise IdTokenError("id_token email is not verified by provider")
        name = claims.get("name")
        if not isinstance(name, str) or not name:
            name = email.split("@", 1)[0]
        sub = str(claims["sub"])
        return OidcUserInfo(
            provider=provider.name,
            subject=sub,
            email=email,
            name=name,
            raw_id_token_claims=claims,
        )


def _normalize_issuer(issuer: Any) -> str:
    if not isinstance(issuer, str) or not issuer:
        raise OidcDiscoveryError("discovery doc missing 'issuer'")
    return issuer


__all__ = [
    "HttpClientFactory",
    "IdTokenError",
    "OidcCallbackResult",
    "OidcDiscoveryError",
    "OidcExchangeError",
    "OidcService",
    "OidcUserInfo",
    "UnknownProviderError",
]
