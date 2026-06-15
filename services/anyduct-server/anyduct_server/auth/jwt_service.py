"""JWT issue + verify service (ADR-0023, RS256).

* Asymmetric RS256: private key signs, public key verifies. Service can be
  deployed in two modes — full key pair (issues + verifies) or public-key
  only (verifies tokens minted elsewhere). The latter is what a downstream
  validator (e.g. a worker) needs.
* Token kinds: ``access`` (short-lived, ~15min) and ``refresh`` (longer,
  ~7d). Both share the same signing key but carry a ``token_type`` claim;
  ``verify`` enforces the expected type so an access token can't be used as
  a refresh token, and vice versa.
* All times are seconds since epoch (POSIX). ``iat``/``exp``/``nbf`` are
  emitted; ``iss``/``aud`` are validated against the configured values.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

import jwt
from cryptography.hazmat.primitives import serialization


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class InvalidTokenError(Exception):
    """Raised when verify() fails (expired, wrong signature, wrong type, ...)."""


@dataclass(frozen=True)
class Claims:
    """Parsed JWT claims relevant to the application."""

    subject: UUID
    """``sub`` — the user id."""
    token_type: TokenType
    """``token_type`` — ``"access"`` or ``"refresh"``."""
    jti: str
    """Unique token id — used for refresh-rotation denylists (future)."""
    issued_at: int
    expires_at: int
    raw: dict[str, Any]
    """Full claims dict — for endpoint code that needs extra fields."""


def _load_public_key_from_private(pem: bytes) -> bytes:
    """Derive a public key PEM from a private key PEM."""
    private = serialization.load_pem_private_key(pem, password=None)
    return private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


class JwtService:
    """Issue and verify RS256 JWTs.

    Construct with both keys for an issuer node; with public-key only for a
    pure verifier. ``issue_access``/``issue_refresh`` raise ``RuntimeError``
    when called on a verifier-only instance.
    """

    def __init__(
        self,
        *,
        private_key_pem: str | bytes | None,
        public_key_pem: str | bytes | None = None,
        issuer: str,
        audience: str,
        access_ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> None:
        if private_key_pem is None and public_key_pem is None:
            raise ValueError(
                "JwtService requires at least one of private_key_pem or public_key_pem"
            )
        self._private_pem = self._as_bytes(private_key_pem) if private_key_pem else None
        if public_key_pem is not None:
            self._public_pem = self._as_bytes(public_key_pem)
        else:
            assert self._private_pem is not None  # narrowing for mypy
            self._public_pem = _load_public_key_from_private(self._private_pem)
        self._issuer = issuer
        self._audience = audience
        self._access_ttl = access_ttl_seconds
        self._refresh_ttl = refresh_ttl_seconds

    @staticmethod
    def _as_bytes(value: str | bytes) -> bytes:
        return value.encode("utf-8") if isinstance(value, str) else value

    # ------------------------------------------------------------------ issue

    def issue_access(self, user_id: UUID, *, extra_claims: dict[str, Any] | None = None) -> str:
        return self._issue(user_id, TokenType.ACCESS, self._access_ttl, extra_claims)

    def issue_refresh(self, user_id: UUID) -> str:
        return self._issue(user_id, TokenType.REFRESH, self._refresh_ttl, None)

    def _issue(
        self,
        user_id: UUID,
        token_type: TokenType,
        ttl_seconds: int,
        extra_claims: dict[str, Any] | None,
    ) -> str:
        if self._private_pem is None:
            raise RuntimeError("JwtService is verify-only (no private key configured)")
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(user_id),
            "iat": now,
            "nbf": now,
            "exp": now + ttl_seconds,
            "jti": uuid.uuid4().hex,
            "token_type": token_type.value,
        }
        if extra_claims:
            # Reserved claims always win over user-supplied ones.
            for key, value in extra_claims.items():
                payload.setdefault(key, value)
        return jwt.encode(payload, self._private_pem, algorithm="RS256")

    # ----------------------------------------------------------------- verify

    def verify(self, token: str, *, expected_type: TokenType) -> Claims:
        """Decode + validate. Raises :class:`InvalidTokenError` on any failure."""
        try:
            raw = jwt.decode(
                token,
                self._public_pem,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.PyJWTError as e:
            raise InvalidTokenError(str(e)) from e
        actual_type = raw.get("token_type")
        if actual_type != expected_type.value:
            raise InvalidTokenError(
                f"wrong token type: expected {expected_type.value!r}, got {actual_type!r}"
            )
        try:
            subject = UUID(str(raw["sub"]))
        except (KeyError, ValueError) as e:
            raise InvalidTokenError("token has invalid 'sub' claim") from e
        return Claims(
            subject=subject,
            token_type=TokenType(actual_type),
            jti=str(raw.get("jti", "")),
            issued_at=int(raw.get("iat", 0)),
            expires_at=int(raw.get("exp", 0)),
            raw=raw,
        )


def generate_rsa_keypair_pem(bits: int = 2048) -> tuple[bytes, bytes]:
    """Convenience for tests/scripts: generate a fresh RSA keypair (no password)."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


__all__ = [
    "Claims",
    "InvalidTokenError",
    "JwtService",
    "TokenType",
    "generate_rsa_keypair_pem",
]
