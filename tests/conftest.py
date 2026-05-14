"""Top-level pytest fixtures shared across all test modules."""

from __future__ import annotations

import pytest

from etl_plugins.core.record import Record
from tests.fixtures.records import sample_records as _sample_records


@pytest.fixture
def sample_records() -> list[Record]:
    """The standard 3-row dataset used by contract tests and many unit tests."""
    return _sample_records()
