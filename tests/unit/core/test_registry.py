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
    names = ConnectorRegistry.list_connectors()
    # list_connectors는 entry-point도 함께 로드하므로 다른 등록된 커넥터가 더 있을 수 있음
    assert "aaa" in names and "zzz" in names
    assert names == sorted(names)


def test_decorator_returns_class() -> None:
    decorated = ConnectorRegistry.register("d")(_DummySource)
    assert decorated is _DummySource


# ---------- Phase AAQ post-mortem (2026-05-29): built-in fallback ----------
#
# Symptom the user hit: dev server kept running against stale
# ``entry_points`` metadata after ``pyproject.toml`` added Vertica +
# MSSQL — registry lookup raised ``Connector 'vertica' not
# registered`` even though the source module existed. The fix is a
# built-in module-path fallback: if entry_points don't list the
# name but it's a known built-in, import the module so its
# top-level decorator registers it.


def _simulate_stale_metadata(*module_paths: str) -> None:
    """Reset the registry AND drop the relevant modules from
    ``sys.modules`` so the next ``importlib.import_module`` re-runs
    the top-level ``@register`` decorator. Mirrors the real user
    scenario where the dev server started before the new built-in
    module was ever imported."""
    import sys

    ConnectorRegistry.clear()
    ConnectorRegistry._entry_points_loaded = True
    for path in module_paths:
        sys.modules.pop(path, None)


def test_get_falls_back_to_builtin_module_when_entry_points_stale() -> None:
    """Simulate a stale-metadata install: pretend entry_points have
    been consulted and yielded nothing. Built-in connectors must
    still resolve via the module-path fallback."""
    _simulate_stale_metadata("etl_plugins.connectors.rdbms.sqlite")
    klass = ConnectorRegistry.get("sqlite")
    assert klass.__name__ == "SQLiteConnector"


def test_get_falls_back_to_builtin_for_new_connectors() -> None:
    """The same fallback path covers the connectors added in Phase
    AAQ (the trigger for this defence-in-depth)."""
    _simulate_stale_metadata("etl_plugins.connectors.rdbms.vertica")
    assert ConnectorRegistry.get("vertica").__name__ == "VerticaConnector"
    _simulate_stale_metadata("etl_plugins.connectors.rdbms.mssql")
    assert ConnectorRegistry.get("mssql").__name__ == "MSSQLConnector"


def test_get_unknown_still_raises_after_fallback_attempt() -> None:
    """An unknown name that *isn't* a built-in module still raises a
    clean RegistryError — the fallback doesn't paper over real typos."""
    ConnectorRegistry.clear()
    ConnectorRegistry._entry_points_loaded = True
    with pytest.raises(RegistryError, match="not registered"):
        ConnectorRegistry.get("ghost-driver-xyz")


def test_list_connectors_includes_builtins_on_stale_metadata() -> None:
    """``etlx list-connectors`` should be exhaustive even when the
    installed metadata doesn't list every built-in (e.g. the operator
    edited pyproject but hasn't reinstalled yet)."""
    _simulate_stale_metadata(
        "etl_plugins.connectors.rdbms.sqlite",
        "etl_plugins.connectors.rdbms.postgres",
        "etl_plugins.connectors.rdbms.mysql",
        "etl_plugins.connectors.rdbms.vertica",
        "etl_plugins.connectors.rdbms.mssql",
    )
    names = ConnectorRegistry.list_connectors()
    for required in ("sqlite", "postgres", "mysql", "vertica", "mssql"):
        assert required in names, f"built-in {required} missing from list"
