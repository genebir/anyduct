"""Settings (Step 8.1) — env-driven, validated, cached."""

from __future__ import annotations

import pytest
from etlx_server.settings import Environment, Settings, get_settings


def test_settings_reads_database_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/db")
    s = Settings()
    assert s.database_url == "postgresql+asyncpg://u:p@h:5432/db"


def test_settings_defaults_to_development() -> None:
    s = Settings(database_url="postgresql+asyncpg://stub:stub@stub:5432/stub")
    assert s.environment is Environment.DEVELOPMENT
    assert s.docs_enabled is True


def test_settings_production_hides_docs() -> None:
    s = Settings(
        database_url="postgresql+asyncpg://stub:stub@stub:5432/stub",
        environment=Environment.PRODUCTION,
    )
    assert s.docs_enabled is False


def test_settings_rejects_unknown_environment() -> None:
    with pytest.raises(ValueError):
        Settings(
            database_url="postgresql+asyncpg://stub:stub@stub:5432/stub",
            environment="bogus",  # type: ignore[arg-type]
        )


def test_get_settings_is_cached() -> None:
    a = get_settings()
    b = get_settings()
    assert a is b


def test_settings_cors_origins_empty_by_default() -> None:
    s = Settings(database_url="postgresql+asyncpg://stub:stub@stub:5432/stub")
    assert s.cors_origins == []


# --- OpenLineage (ADR-0041 K5) --------------------------------------------


def test_openlineage_disabled_by_default() -> None:
    s = Settings(database_url="postgresql+asyncpg://stub:stub@stub:5432/stub")
    assert s.openlineage_url is None
    assert s.openlineage_namespace == "etl-plugins"
    assert s.openlineage_api_key is None


def test_openlineage_url_reads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLINEAGE_URL", "http://marquez:5000")
    monkeypatch.setenv("OPENLINEAGE_NAMESPACE", "prod-warehouse")
    monkeypatch.setenv("OPENLINEAGE_API_KEY", "sk-test")  # pragma: allowlist secret
    s = Settings(database_url="postgresql+asyncpg://stub:stub@stub:5432/stub")
    assert s.openlineage_url == "http://marquez:5000"
    assert s.openlineage_namespace == "prod-warehouse"
    assert s.openlineage_api_key == "sk-test"  # pragma: allowlist secret


def test_install_openlineage_returns_none_when_url_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_install_openlineage_emitter`` is a no-op when the URL isn't
    configured — the default no-op emitter stays installed."""
    from etlx_server.cli import _install_openlineage_emitter
    from etlx_server.settings import get_settings as _get_settings

    monkeypatch.delenv("OPENLINEAGE_URL", raising=False)
    _get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        result = _install_openlineage_emitter()
        assert result is None
    finally:
        _get_settings.cache_clear()  # type: ignore[attr-defined]


def test_install_openlineage_installs_emitter_when_url_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``OPENLINEAGE_URL`` is configured the helper builds an
    :class:`OpenLineageEmitter` and installs it as the process-wide
    lineage emitter."""
    from etlx_server.cli import _install_openlineage_emitter
    from etlx_server.settings import get_settings as _get_settings

    from etl_plugins.observability.lineage import (
        NoOpLineageEmitter,
        get_lineage_emitter,
        set_lineage_emitter,
    )
    from etl_plugins.observability.openlineage import OpenLineageEmitter

    monkeypatch.setenv("OPENLINEAGE_URL", "http://marquez:5000")
    monkeypatch.setenv("OPENLINEAGE_NAMESPACE", "test-ns")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://stub:stub@stub:5432/stub")
    _get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        emitter = _install_openlineage_emitter()
        assert isinstance(emitter, OpenLineageEmitter)
        assert get_lineage_emitter() is emitter
    finally:
        # Restore the global emitter so we don't leak into other tests.
        set_lineage_emitter(NoOpLineageEmitter())
        if emitter is not None:
            emitter.close()
        _get_settings.cache_clear()  # type: ignore[attr-defined]
