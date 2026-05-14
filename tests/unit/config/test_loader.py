"""YAML loader / ${VAR} 치환 / !secret 해석 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from etl_plugins.config.loader import (
    SecretRef,
    expand_env,
    has_unresolved_secrets,
    load_config,
    load_connections,
    load_dotenv,
    load_pipeline,
    load_yaml,
    resolve_secrets,
)
from etl_plugins.config.secrets import StaticSecretBackend
from etl_plugins.core.exceptions import ConfigError

# ---------- expand_env ----------


def test_expand_env_substitutes_in_string() -> None:
    assert expand_env("hello ${NAME}", {"NAME": "world"}) == "hello world"


def test_expand_env_recurses_dict() -> None:
    result = expand_env(
        {"a": "${X}", "b": {"c": "${Y}"}},
        {"X": "1", "Y": "2"},
    )
    assert result == {"a": "1", "b": {"c": "2"}}


def test_expand_env_recurses_list() -> None:
    assert expand_env(["${A}", "${B}"], {"A": "x", "B": "y"}) == ["x", "y"]


def test_expand_env_missing_raises() -> None:
    with pytest.raises(ConfigError, match="MISSING"):
        expand_env("${MISSING}", {})


def test_expand_env_leaves_non_strings_alone() -> None:
    assert expand_env(42, {}) == 42
    assert expand_env(True, {}) is True
    assert expand_env(None, {}) is None


def test_expand_env_descends_into_secret_ref() -> None:
    ref = SecretRef(path="api/${ENV}/key")
    result = expand_env(ref, {"ENV": "prod"})
    assert isinstance(result, SecretRef)
    assert result.path == "api/prod/key"


def test_expand_env_pattern_rejects_lowercase() -> None:
    # ${var} (소문자)는 매치되지 않으므로 그대로 통과
    assert expand_env("${var}", {}) == "${var}"


# ---------- resolve_secrets ----------


def test_resolve_secrets_replaces_refs_with_values() -> None:
    backend = StaticSecretBackend({"a/b": "secret-abc"})
    result = resolve_secrets({"k": SecretRef(path="a/b")}, backend)
    assert result == {"k": "secret-abc"}


def test_resolve_secrets_recurses() -> None:
    backend = StaticSecretBackend({"p1": "v1", "p2": "v2"})
    result = resolve_secrets(
        {"a": [SecretRef(path="p1"), {"nested": SecretRef(path="p2")}]},
        backend,
    )
    assert result == {"a": ["v1", {"nested": "v2"}]}


def test_has_unresolved_secrets() -> None:
    assert has_unresolved_secrets(SecretRef(path="x")) is True
    assert has_unresolved_secrets({"k": SecretRef(path="x")}) is True
    assert has_unresolved_secrets([1, 2, SecretRef(path="y")]) is True
    assert has_unresolved_secrets({"k": "v"}) is False
    assert has_unresolved_secrets([1, 2, 3]) is False


# ---------- load_yaml ----------


def test_load_yaml_parses_basic(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text("a: 1\nb: [x, y]\n")
    assert load_yaml(p) == {"a": 1, "b": ["x", "y"]}


def test_load_yaml_recognizes_secret_tag(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("password: !secret db/prod/password\n")
    data = load_yaml(p)
    assert data == {"password": SecretRef(path="db/prod/password")}


def test_load_yaml_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_yaml(tmp_path / "does-not-exist.yaml")


def test_load_yaml_secret_non_scalar_raises(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("password: !secret [a, b]\n")
    with pytest.raises(ConfigError, match="scalar"):
        load_yaml(p)


# ---------- load_config ----------


def test_load_config_expands_env_and_resolves_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        """\
host: ${PG_HOST}
password: !secret pg/prod/password
"""
    )
    monkeypatch.setenv("PG_HOST", "db.internal")
    backend = StaticSecretBackend({"pg/prod/password": "topsecret"})
    data = load_config(p, secret_backend=backend)
    assert data == {"host": "db.internal", "password": "topsecret"}


def test_load_config_unresolved_secret_raises(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("password: !secret pg/prod\n")
    with pytest.raises(ConfigError, match="unresolved !secret"):
        load_config(p)  # secret_backend 미지정


def test_load_config_unresolved_secret_allowed_when_explicit(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("password: !secret pg/prod\n")
    data = load_config(p, require_secrets_resolved=False)
    assert isinstance(data["password"], SecretRef)


def test_load_config_missing_env_var_raises(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("host: ${NOT_SET}\n")
    with pytest.raises(ConfigError, match="NOT_SET"):
        load_config(p, env={})


def test_load_config_custom_env(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("h: ${HOST}\n")
    data = load_config(p, env={"HOST": "explicit"})
    assert data == {"h": "explicit"}


# ---------- load_connections / load_pipeline ----------


def test_load_connections_full_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "connections.yaml"
    p.write_text(
        """\
connections:
  pg_prod:
    type: postgres
    host: ${PG_HOST}
    port: 5432
    password: !secret pg/prod/password
    options:
      pool_size: 10
"""
    )
    cc = load_connections(
        p,
        env={"PG_HOST": "db.internal"},
        secret_backend=StaticSecretBackend({"pg/prod/password": "s3cret"}),
    )
    pg = cc.connections["pg_prod"]
    assert pg.type == "postgres"
    opts = pg.options()
    assert opts["host"] == "db.internal"
    assert opts["password"] == "s3cret"
    assert opts["options"] == {"pool_size": 10}


def test_load_pipeline_full(tmp_path: Path) -> None:
    p = tmp_path / "pipeline.yaml"
    p.write_text(
        """\
name: orders_to_dw
mode: batch
source:
  connection: pg_prod
  query: SELECT *
  chunk_size: 1000
transforms:
  - type: rename
    mapping: {a: b}
sink:
  connection: snowflake_dw
  table: ANALYTICS.ORDERS
  mode: upsert
  key_columns: [id]
retry:
  max_attempts: 5
"""
    )
    pc = load_pipeline(p)
    assert pc.name == "orders_to_dw"
    assert pc.source.chunk_size == 1000
    assert pc.sink.mode == "upsert"
    assert pc.retry is not None
    assert pc.retry.max_attempts == 5


# ---------- load_dotenv ----------


def test_load_dotenv_loads_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("FOO_FROM_DOTENV=bar\n")
    monkeypatch.delenv("FOO_FROM_DOTENV", raising=False)

    load_dotenv(env_path)
    import os

    assert os.environ.get("FOO_FROM_DOTENV") == "bar"


def test_load_dotenv_missing_file_is_noop(tmp_path: Path) -> None:
    # 존재하지 않아도 에러 없이 통과해야 한다
    load_dotenv(tmp_path / "nonexistent.env")
