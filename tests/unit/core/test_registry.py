"""ConnectorRegistry 테스트."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from etl_plugins.core.connector import BatchSource
from etl_plugins.core.exceptions import RegistryError
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry


class _DummySource(BatchSource):
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def health_check(self) -> bool:
        return True

    def read(  # type: ignore[override]
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
    ) -> Iterator[Record]:
        return iter([])


class _AltSource(BatchSource):
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def health_check(self) -> bool:
        return True

    def read(  # type: ignore[override]
        self,
        query: str | None = None,
        *,
        chunk_size: int = 10_000,
    ) -> Iterator[Record]:
        return iter([])


@pytest.fixture(autouse=True)
def _clear() -> Iterator[None]:
    ConnectorRegistry.clear()
    yield
    ConnectorRegistry.clear()


def test_register_and_get() -> None:
    ConnectorRegistry.register("dummy")(_DummySource)
    assert ConnectorRegistry.get("dummy") is _DummySource


def test_register_sets_name_attribute() -> None:
    ConnectorRegistry.register("dummy")(_DummySource)
    assert _DummySource.name == "dummy"


def test_get_unknown_raises() -> None:
    with pytest.raises(RegistryError, match="not registered"):
        ConnectorRegistry.get("ghost")


def test_duplicate_registration_raises() -> None:
    ConnectorRegistry.register("dummy")(_DummySource)
    with pytest.raises(RegistryError, match="already registered"):
        ConnectorRegistry.register("dummy")(_AltSource)


def test_duplicate_registration_with_replace() -> None:
    ConnectorRegistry.register("dummy")(_DummySource)
    ConnectorRegistry.register("dummy", replace=True)(_AltSource)
    assert ConnectorRegistry.get("dummy") is _AltSource


def test_list_connectors_sorted() -> None:
    ConnectorRegistry.register("zzz")(_DummySource)
    ConnectorRegistry.register("aaa")(_AltSource)
    assert ConnectorRegistry.list_connectors() == ["aaa", "zzz"]


def test_decorator_returns_class() -> None:
    decorated = ConnectorRegistry.register("d")(_DummySource)
    assert decorated is _DummySource
