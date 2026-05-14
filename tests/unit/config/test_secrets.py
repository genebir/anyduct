"""SecretBackend 테스트."""

from __future__ import annotations

import pytest

from etl_plugins.config.secrets import (
    EnvSecretBackend,
    SecretBackend,
    StaticSecretBackend,
    get_secret_backend,
)
from etl_plugins.core.exceptions import ConfigError, SecretError

# ---------- EnvSecretBackend ----------


def test_env_backend_returns_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "topsecret")
    assert EnvSecretBackend().get("MY_SECRET") == "topsecret"


def test_env_backend_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GHOST_VAR", raising=False)
    with pytest.raises(SecretError, match="GHOST_VAR"):
        EnvSecretBackend().get("GHOST_VAR")


# ---------- StaticSecretBackend ----------


def test_static_backend_returns_mapped_value() -> None:
    b = StaticSecretBackend({"a/b": "abc"})
    assert b.get("a/b") == "abc"


def test_static_backend_missing_raises() -> None:
    b = StaticSecretBackend({"x": "1"})
    with pytest.raises(SecretError, match="not in StaticSecretBackend"):
        b.get("missing")


def test_static_backend_set() -> None:
    b = StaticSecretBackend()
    b.set("new", "val")
    assert b.get("new") == "val"


# ---------- get_secret_backend factory ----------


def test_factory_defaults_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_BACKEND", raising=False)
    assert isinstance(get_secret_backend(), EnvSecretBackend)


def test_factory_uses_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_BACKEND", "static")
    assert isinstance(get_secret_backend(), StaticSecretBackend)


def test_factory_explicit_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_BACKEND", "static")
    assert isinstance(get_secret_backend("env"), EnvSecretBackend)


def test_factory_static_with_secrets() -> None:
    b = get_secret_backend("static", secrets={"k": "v"})
    assert b.get("k") == "v"


def test_factory_unknown_raises() -> None:
    with pytest.raises(ConfigError, match="unknown secret backend"):
        get_secret_backend("rotato")


def test_factory_not_implemented_stubs() -> None:
    for name in ["vault", "aws_sm", "gcp_sm"]:
        backend = get_secret_backend(name)
        assert isinstance(backend, SecretBackend)
        with pytest.raises(NotImplementedError, match=name):
            backend.get("anything")


def test_secret_backend_abstract() -> None:
    with pytest.raises(TypeError):
        SecretBackend()  # type: ignore[abstract]
