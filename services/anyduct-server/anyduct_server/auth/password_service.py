"""bcrypt password hashing service.

Thin wrapper over the ``bcrypt`` package. We use ``bcrypt`` directly rather
than ``passlib`` because passlib is in maintenance mode and is incompatible
with bcrypt 4.x out of the box (it can't read the new version metadata and
trips on the strict 72-byte input limit).

Inputs longer than bcrypt's 72-byte ceiling are pre-hashed with SHA-256 so we
can accept arbitrary-length passwords. The pre-hash is base64-encoded to keep
the bytes within the ASCII range bcrypt expects.
"""

from __future__ import annotations

import base64
import hashlib

import bcrypt

# bcrypt's hard maximum input length. Anything beyond this is truncated by the
# underlying C library — silently in older versions, with an exception in 4.x.
_BCRYPT_MAX_BYTES = 72


def _prehash_if_long(password: str) -> bytes:
    encoded = password.encode("utf-8")
    if len(encoded) <= _BCRYPT_MAX_BYTES:
        return encoded
    digest = hashlib.sha256(encoded).digest()
    return base64.b64encode(digest)  # 44 bytes — always under the limit


class PasswordService:
    """Hash and verify passwords with bcrypt.

    ``rounds`` is the bcrypt cost factor — each additional round doubles the
    hash time. 12 is the rule-of-thumb for modern hardware; raise after
    benchmarking the production host.
    """

    def __init__(self, *, rounds: int = 12) -> None:
        self._rounds = rounds

    def hash(self, password: str) -> str:
        salt = bcrypt.gensalt(rounds=self._rounds)
        hashed = bcrypt.hashpw(_prehash_if_long(password), salt)
        return hashed.decode("ascii")

    def verify(self, password: str, hashed: str) -> bool:
        """Constant-time verify. Returns ``False`` on any malformed hash."""
        try:
            return bcrypt.checkpw(_prehash_if_long(password), hashed.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            return False


__all__ = ["PasswordService"]
