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
