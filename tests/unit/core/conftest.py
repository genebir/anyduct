"""Core 테스트용 픽스처.

InMemory 커넥터 구현체는 `tests/fixtures/connectors.py`로 이동됨 —
contract test suite와 공유. 기존 코드 호환을 위해 여기서 re-export.
"""

from __future__ import annotations

import pytest

from tests.fixtures.connectors import InMemoryBatchSink, InMemoryBatchSource

__all__ = ["InMemoryBatchSink", "InMemoryBatchSource"]


@pytest.fixture
def in_memory_source(sample_records):  # type: ignore[no-untyped-def]
    return InMemoryBatchSource(sample_records)


@pytest.fixture
def in_memory_sink() -> InMemoryBatchSink:
    return InMemoryBatchSink()
