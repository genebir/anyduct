"""Signed OIDC ``state`` token (Step 8.2b).

The state value passed to the IdP and echoed back to ``/auth/oidc/callback``
carries three pieces of data the callback handler needs:

* the **provider name** — so we can validate the callback's ``?provider=``
  query matches what the user originally clicked,
* the **nonce** — the value we expect to see inside the ID token, and
* an optional **return_to** URL — where the FE wants to land after a
  successful exchange.

We encode all three into a short-lived RS256 JWT signed with the same RSA
keypair :class:`anyduct_server.auth.jwt_service.JwtService` uses for access
tokens. Stateless — no DB, no cookies — but tamper-evident and replay-able
only within the configured TTL (default 10 min).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import jwt

_STATE_TOKEN_TYPE = "oidc_state"


@dataclass(frozen=True)
class OidcState:
    """Decoded state token contents."""

    provider: str
    nonce: str
    return_to: str | None


class InvalidStateError(Exception):
    """Raised when state verification fails (expired, tampered, wrong type)."""


class OidcStateSigner:
    """Sign and verify OIDC state tokens.

    Reuses the JWT signing keypair to avoid a second key-management chore;
    the ``token_type`` claim discriminates state tokens from access /
    refresh tokens so they cannot be swapped.
    """

    def __init__(
        self,
        *,
        private_key_pem: bytes,
        public_key_pem: bytes,
        issuer: str,
        ttl_seconds: int = 600,
    ) -> None:
        self._private_pem = private_key_pem
        self._public_pem = public_key_pem
        self._issuer = issuer
        self._ttl_seconds = ttl_seconds

    def sign(self, *, provider: str, nonce: str, return_to: str | None) -> str:
        now = int(time.time())
        payload = {
            "iss": self._issuer,
            "iat": now,
            "nbf": now,
            "exp": now + self._ttl_seconds,
            "jti": uuid.uuid4().hex,
            "token_type": _STATE_TOKEN_TYPE,
            "provider": provider,
            "nonce": nonce,
            "return_to": return_to,
        }
        return jwt.encode(payload, self._private_pem, algorithm="RS256")

    def verify(self, token: str) -> OidcState:
        try:
            raw = jwt.decode(
                token,
                self._public_pem,
                algorithms=["RS256"],
                issuer=self._issuer,
                # No audience — state tokens are self-issued and self-consumed.
                options={"require": ["exp", "iat", "iss", "token_type"]},
            )
        except jwt.PyJWTError as e:
            raise InvalidStateError(f"state token invalid: {e}") from e
        if raw.get("token_type") != _STATE_TOKEN_TYPE:
            raise InvalidStateError(f"unexpected token_type: {raw.get('token_type')!r}")
        provider = raw.get("provider")
        nonce = raw.get("nonce")
        if not isinstance(provider, str) or not isinstance(nonce, str):
            raise InvalidStateError("state token missing provider/nonce")
        return_to = raw.get("return_to")
        if return_to is not None and not isinstance(return_to, str):
            raise InvalidStateError("state token has malformed return_to")
        return OidcState(provider=provider, nonce=nonce, return_to=return_to)


__all__ = ["InvalidStateError", "OidcState", "OidcStateSigner"]
