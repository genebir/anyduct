"""PasswordService unit tests (Step 8.2a)."""

from __future__ import annotations

from anyduct_server.auth.password_service import PasswordService


def test_hash_and_verify_round_trip() -> None:
    svc = PasswordService(rounds=4)  # low cost — fast tests
    hashed = svc.hash("hunter2")
    assert hashed != "hunter2"
    assert svc.verify("hunter2", hashed)


def test_wrong_password_does_not_verify() -> None:
    svc = PasswordService(rounds=4)
    hashed = svc.hash("hunter2")
    assert not svc.verify("hunter1", hashed)


def test_verify_returns_false_on_malformed_hash() -> None:
    svc = PasswordService(rounds=4)
    # passlib raises ValueError on a non-bcrypt string — service swallows it.
    assert svc.verify("anything", "not-a-valid-hash") is False


def test_hash_uses_bcrypt_marker() -> None:
    svc = PasswordService(rounds=4)
    hashed = svc.hash("x")
    assert hashed.startswith("$2")  # bcrypt identifier ($2a/$2b/$2y/etc.)
