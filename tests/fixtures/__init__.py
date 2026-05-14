"""Shared test fixtures + sample data + in-memory connector reference impls.

Importable by any test module:

    from tests.fixtures.records import sample_records, large_records
    from tests.fixtures.connectors import InMemoryBatchSourceSink

Pytest fixtures (``@pytest.fixture``) live in ``tests/conftest.py`` and
``tests/<subdir>/conftest.py``. This package contains plain factories so they
can be reused outside the pytest fixture system.
"""
