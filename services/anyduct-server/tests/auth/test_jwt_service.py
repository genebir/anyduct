"""JwtService unit tests (Step 8.2a)."""

from __future__ import annotations

import time
from uuid import uuid4

import pytest
from anyduct_server.auth.jwt_service import (
    InvalidTokenError,
    JwtService,
    TokenType,
    generate_rsa_keypair_pem,
)


@pytest.fixture(scope="module")
def keypair() -> tuple[bytes, bytes]:
    # 2048 is the default — fine for tests.
    return generate_rsa_keypair_pem(bits=2048)


@pytest.fixture
def svc(keypair: tuple[bytes, bytes]) -> JwtService:
    private, public = keypair
    return JwtService(
        private_key_pem=private,
        public_key_pem=public,
        issuer="anyduct-server",
        audience="anyduct",
        access_ttl_seconds=60,
        refresh_ttl_seconds=120,
    )


def test_issue_and_verify_access_token(svc: JwtService) -> None:
    user_id = uuid4()
    token = svc.issue_access(user_id)
    claims = svc.verify(token, expected_type=TokenType.ACCESS)
    assert claims.subject == user_id
    assert claims.token_type is TokenType.ACCESS
    assert claims.expires_at > claims.issued_at
    assert claims.jti  # populated


def test_issue_and_verify_refresh_token(svc: JwtService) -> None:
    user_id = uuid4()
    token = svc.issue_refresh(user_id)
    claims = svc.verify(token, expected_type=TokenType.REFRESH)
    assert claims.subject == user_id
    assert claims.token_type is TokenType.REFRESH


def test_access_token_cannot_be_used_as_refresh(svc: JwtService) -> None:
    token = svc.issue_access(uuid4())
    with pytest.raises(InvalidTokenError, match="wrong token type"):
        svc.verify(token, expected_type=TokenType.REFRESH)


def test_refresh_token_cannot_be_used_as_access(svc: JwtService) -> None:
    token = svc.issue_refresh(uuid4())
    with pytest.raises(InvalidTokenError, match="wrong token type"):
        svc.verify(token, expected_type=TokenType.ACCESS)


def test_invalid_signature_rejected(keypair: tuple[bytes, bytes]) -> None:
    private, _ = keypair
    other_private, other_public = generate_rsa_keypair_pem(bits=2048)
    issuer = JwtService(
        private_key_pem=private,
        issuer="anyduct-server",
        audience="anyduct",
        access_ttl_seconds=60,
        refresh_ttl_seconds=120,
    )
    verifier = JwtService(
        private_key_pem=None,
        public_key_pem=other_public,
        issuer="anyduct-server",
        audience="anyduct",
        access_ttl_seconds=60,
        refresh_ttl_seconds=120,
    )
    token = issuer.issue_access(uuid4())
    with pytest.raises(InvalidTokenError):
        verifier.verify(token, expected_type=TokenType.ACCESS)
    del other_private


def test_wrong_audience_rejected(keypair: tuple[bytes, bytes]) -> None:
    private, public = keypair
    issuer = JwtService(
        private_key_pem=private,
        public_key_pem=public,
        issuer="anyduct-server",
        audience="anyduct",
        access_ttl_seconds=60,
        refresh_ttl_seconds=120,
    )
    verifier = JwtService(
        private_key_pem=None,
        public_key_pem=public,
        issuer="anyduct-server",
        audience="other-audience",
        access_ttl_seconds=60,
        refresh_ttl_seconds=120,
    )
    token = issuer.issue_access(uuid4())
    with pytest.raises(InvalidTokenError):
        verifier.verify(token, expected_type=TokenType.ACCESS)


def test_expired_token_rejected(keypair: tuple[bytes, bytes]) -> None:
    private, public = keypair
    svc = JwtService(
        private_key_pem=private,
        public_key_pem=public,
        issuer="anyduct-server",
        audience="anyduct",
        access_ttl_seconds=-1,  # already expired
        refresh_ttl_seconds=-1,
    )
    token = svc.issue_access(uuid4())
    with pytest.raises(InvalidTokenError):
        svc.verify(token, expected_type=TokenType.ACCESS)


def test_verify_only_instance_cannot_issue(keypair: tuple[bytes, bytes]) -> None:
    _, public = keypair
    verifier = JwtService(
        private_key_pem=None,
        public_key_pem=public,
        issuer="anyduct-server",
        audience="anyduct",
        access_ttl_seconds=60,
        refresh_ttl_seconds=120,
    )
    with pytest.raises(RuntimeError, match="verify-only"):
        verifier.issue_access(uuid4())


def test_public_key_derived_from_private_if_omitted(keypair: tuple[bytes, bytes]) -> None:
    private, _ = keypair
    svc = JwtService(
        private_key_pem=private,
        public_key_pem=None,
        issuer="anyduct-server",
        audience="anyduct",
        access_ttl_seconds=60,
        refresh_ttl_seconds=120,
    )
    token = svc.issue_access(uuid4())
    assert svc.verify(token, expected_type=TokenType.ACCESS).subject is not None


def test_construction_requires_at_least_one_key() -> None:
    with pytest.raises(ValueError, match="at least one of"):
        JwtService(
            private_key_pem=None,
            public_key_pem=None,
            issuer="x",
            audience="y",
            access_ttl_seconds=60,
            refresh_ttl_seconds=120,
        )


def test_extra_claims_cannot_override_reserved(svc: JwtService) -> None:
    user_id = uuid4()
    token = svc.issue_access(user_id, extra_claims={"sub": "spoofed", "name": "alice"})
    claims = svc.verify(token, expected_type=TokenType.ACCESS)
    assert claims.subject == user_id  # 'sub' was not overridden
    assert claims.raw["name"] == "alice"


def test_issued_at_is_recent(svc: JwtService) -> None:
    before = int(time.time())
    token = svc.issue_access(uuid4())
    claims = svc.verify(token, expected_type=TokenType.ACCESS)
    after = int(time.time())
    assert before <= claims.issued_at <= after
