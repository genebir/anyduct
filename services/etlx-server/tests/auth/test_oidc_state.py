"""OidcStateSigner unit tests (Step 8.2b)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from etlx_server.auth.jwt_service import generate_rsa_keypair_pem
from etlx_server.auth.oidc_state import (
    InvalidStateError,
    OidcStateSigner,
)


@pytest.fixture(scope="module")
def keypair() -> tuple[bytes, bytes]:
    return generate_rsa_keypair_pem(bits=2048)


@pytest.fixture
def signer(keypair: tuple[bytes, bytes]) -> OidcStateSigner:
    private, public = keypair
    return OidcStateSigner(
        private_key_pem=private,
        public_key_pem=public,
        issuer="etlx-server",
        ttl_seconds=60,
    )


def test_sign_and_verify_roundtrip(signer: OidcStateSigner) -> None:
    token = signer.sign(provider="google", nonce="n0nce", return_to="/dashboard")
    state = signer.verify(token)
    assert state.provider == "google"
    assert state.nonce == "n0nce"
    assert state.return_to == "/dashboard"


def test_sign_and_verify_without_return_to(signer: OidcStateSigner) -> None:
    token = signer.sign(provider="azure", nonce="abc", return_to=None)
    state = signer.verify(token)
    assert state.return_to is None


def test_verify_rejects_garbage(signer: OidcStateSigner) -> None:
    with pytest.raises(InvalidStateError):
        signer.verify("not.a.real.jwt")


def test_verify_rejects_tampered_token(signer: OidcStateSigner) -> None:
    token = signer.sign(provider="google", nonce="x", return_to=None)
    # Replace the signature segment entirely with a clearly-bogus one.
    # Flipping a single trailing base64 char is unreliable — JWT
    # signatures are URL-safe base64 with no padding, and changing the
    # final character can leave the underlying signature bytes unchanged
    # if the flipped bits fall in the unused trailing region.
    head, payload, _ = token.split(".")
    bad = ".".join([head, payload, "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"])
    with pytest.raises(InvalidStateError):
        signer.verify(bad)


def test_verify_rejects_expired(signer: OidcStateSigner) -> None:
    # Travel back 1h before signing so the token's exp is already past by now.
    real_time = time.time
    with patch("etlx_server.auth.oidc_state.time.time", return_value=real_time() - 3600):
        token = signer.sign(provider="google", nonce="x", return_to=None)
    with pytest.raises(InvalidStateError):
        signer.verify(token)


def test_verify_rejects_access_token(signer: OidcStateSigner, keypair: tuple[bytes, bytes]) -> None:
    # An access token signed by the same key has a different token_type and
    # must not be accepted by the state verifier.
    from uuid import uuid4

    from etlx_server.auth.jwt_service import JwtService

    private, public = keypair
    jwt_svc = JwtService(
        private_key_pem=private,
        public_key_pem=public,
        issuer="etlx-server",
        audience="etlx",
        access_ttl_seconds=60,
        refresh_ttl_seconds=120,
    )
    access = jwt_svc.issue_access(uuid4())
    with pytest.raises(InvalidStateError):
        signer.verify(access)
