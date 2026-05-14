"""Verify the contract suite by running it against the InMemory reference connectors.

If these tests pass, the contract suite is well-formed — and any new connector
that passes these mixins gives the same guarantees.
"""

from __future__ import annotations

import pytest

from etl_plugins.core.connector import BatchSink, BatchSource
from etl_plugins.core.record import Record
from tests.contracts.batch import (
    _BatchRoundTripContract,
    _BatchSinkContract,
    _BatchSourceContract,
)
from tests.fixtures.connectors import (
    InMemoryBatchSink,
    InMemoryBatchSource,
    InMemoryBatchSourceSink,
)
from tests.fixtures.records import sample_records as _sample


class TestInMemoryBatchSource(_BatchSourceContract):
    @pytest.fixture
    def source(self) -> BatchSource:
        return InMemoryBatchSource(_sample())

    @pytest.fixture
    def seeded_records(self) -> list[Record]:
        return _sample()


class TestInMemoryBatchSink(_BatchSinkContract):
    @pytest.fixture
    def sink(self) -> BatchSink:
        return InMemoryBatchSink()


class TestInMemoryRoundTrip(_BatchRoundTripContract):
    @pytest.fixture
    def round_trip_connector(self) -> BatchSource:
        return InMemoryBatchSourceSink()
