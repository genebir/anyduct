"""UUID v7 generator (RFC 9562, time-ordered).

ADR-0020에서 UUID PK + 시간순 정렬을 위해 uuid7을 권장한다. PostgreSQL 18+
는 ``uuidv7()`` 네이티브 함수를 가지지만, 우리는 PG 16+를 타깃하므로
application-side 생성기로 처리한다. RFC 9562 §5.7을 따른다 — 48-bit unix
millisecond timestamp + 12-bit random + 62-bit random + 버전/variant 비트.

외부 dep 없이 stdlib만 사용. ~10 LOC.
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Return a new RFC 9562 UUID v7 (time-ordered)."""
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48 bits
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF  # 12 bits
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF  # 62 bits
    value = (ts_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0x2 << 62) | rand_b
    return uuid.UUID(int=value)
