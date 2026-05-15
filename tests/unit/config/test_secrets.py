"""SecretBackend unit tests (Step 1.5 base + Step 7.4 new backends).

Integration tests for Vault and AWS Secrets Manager (real Docker containers
via testcontainers) live under ``tests/integration/test_secrets_vault.py`` and
``test_secrets_aws_sm.py``. GCP Secret Manager has no production-grade local
emulator, so it's covered here with mocks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from etl_plugins.config.secrets import (
    AwsSmSecretBackend,
    EnvSecretBackend,
    FileSecretBackend,
    GcpSmSecretBackend,
    SecretBackend,
    StaticSecretBackend,
    VaultSecretBackend,
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


def test_env_backend_is_read_only() -> None:
    b = EnvSecretBackend()
    with pytest.raises(NotImplementedError, match="read-only"):
        b.set("X", "v")
    with pytest.raises(NotImplementedError, match="read-only"):
        b.delete("X")


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


def test_static_backend_delete() -> None:
    b = StaticSecretBackend({"x": "1"})
    b.delete("x")
    with pytest.raises(SecretError):
        b.get("x")


def test_static_backend_delete_missing_raises() -> None:
    b = StaticSecretBackend()
    with pytest.raises(SecretError, match="not in StaticSecretBackend"):
        b.delete("ghost")


# ---------- FileSecretBackend ----------


def test_file_backend_round_trip(tmp_path: Path) -> None:
    f = tmp_path / "secrets.json"
    b = FileSecretBackend(file_path=f)
    b.set("pg/password", "s3cret")
    assert b.get("pg/password") == "s3cret"
    # Persisted on disk in JSON form.
    on_disk = json.loads(f.read_text())
    assert on_disk == {"pg/password": "s3cret"}


def test_file_backend_atomic_write_no_tmp_left(tmp_path: Path) -> None:
    f = tmp_path / "secrets.json"
    b = FileSecretBackend(file_path=f)
    b.set("k", "v")
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_file_backend_chmod_0600(tmp_path: Path) -> None:
    f = tmp_path / "secrets.json"
    b = FileSecretBackend(file_path=f)
    b.set("k", "v")
    mode = f.stat().st_mode & 0o777
    # Owner read+write, no group/other access.
    assert mode == 0o600


def test_file_backend_delete(tmp_path: Path) -> None:
    f = tmp_path / "secrets.json"
    b = FileSecretBackend(file_path=f)
    b.set("k", "v")
    b.delete("k")
    with pytest.raises(SecretError):
        b.get("k")


def test_file_backend_missing_get_raises(tmp_path: Path) -> None:
    b = FileSecretBackend(file_path=tmp_path / "nonexistent.json")
    with pytest.raises(SecretError, match="not in FileSecretBackend"):
        b.get("anything")


def test_file_backend_corrupt_json_raises(tmp_path: Path) -> None:
    f = tmp_path / "bad.json"
    f.write_text("not json {", encoding="utf-8")
    b = FileSecretBackend(file_path=f)
    with pytest.raises(SecretError, match="not valid JSON"):
        b.get("x")


def test_file_backend_non_object_raises(tmp_path: Path) -> None:
    f = tmp_path / "list.json"
    f.write_text("[]", encoding="utf-8")
    b = FileSecretBackend(file_path=f)
    with pytest.raises(SecretError, match="must be a JSON object"):
        b.get("x")


def test_file_backend_requires_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FILE_SECRET_PATH", raising=False)
    with pytest.raises(ConfigError, match="FILE_SECRET_PATH"):
        FileSecretBackend()


def test_file_backend_reads_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    f = tmp_path / "via_env.json"
    monkeypatch.setenv("FILE_SECRET_PATH", str(f))
    b = FileSecretBackend()
    b.set("k", "v")
    assert b.get("k") == "v"


# ---------- VaultSecretBackend (constructor / config validation only) ----------
# Real I/O is exercised in tests/integration/test_secrets_vault.py.


def test_vault_backend_requires_url_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VAULT_ADDR", raising=False)
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="VAULT_ADDR"):
        VaultSecretBackend()


# ---------- AwsSmSecretBackend (constructor) ----------


def test_aws_sm_backend_constructs_without_calling_aws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # boto3.client is created eagerly but it does not contact AWS until a call
    # is made — passing a fake endpoint is enough to keep the constructor
    # offline. We still patch to be safe.
    fake_client = MagicMock()
    with patch("boto3.client", return_value=fake_client) as mock_boto:
        b = AwsSmSecretBackend(
            region_name="us-east-1",
            endpoint_url="http://localhost:9999",
        )
    mock_boto.assert_called_once()
    assert b is not None


# ---------- GcpSmSecretBackend (full mock-based suite) ----------
# No local emulator → mocks are the production-realistic option.


@pytest.fixture
def gcp_mock_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    from google.cloud import secretmanager

    instance = MagicMock()
    monkeypatch.setattr(secretmanager, "SecretManagerServiceClient", lambda: instance)
    return instance


def test_gcp_sm_requires_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    with pytest.raises(ConfigError, match="project_id"):
        GcpSmSecretBackend()


def test_gcp_sm_get_decodes_payload(gcp_mock_client: Any) -> None:
    payload = MagicMock()
    payload.payload.data = b"top-secret"
    gcp_mock_client.access_secret_version.return_value = payload

    b = GcpSmSecretBackend(project_id="proj-123")
    assert b.get("api/key") == "top-secret"

    request = gcp_mock_client.access_secret_version.call_args.kwargs["request"]
    assert request["name"] == "projects/proj-123/secrets/api/key/versions/latest"


def test_gcp_sm_get_not_found_raises_secret_error(gcp_mock_client: Any) -> None:
    from google.api_core import exceptions as gax_exceptions

    gcp_mock_client.access_secret_version.side_effect = gax_exceptions.NotFound("no such secret")

    b = GcpSmSecretBackend(project_id="proj-123")
    with pytest.raises(SecretError, match="not in GCP Secret Manager"):
        b.get("missing")


def test_gcp_sm_set_creates_then_adds_version(gcp_mock_client: Any) -> None:
    b = GcpSmSecretBackend(project_id="proj-123")
    b.set("api/key", "v1")
    gcp_mock_client.create_secret.assert_called_once()
    gcp_mock_client.add_secret_version.assert_called_once()
    add_kwargs = gcp_mock_client.add_secret_version.call_args.kwargs["request"]
    assert add_kwargs["payload"]["data"] == b"v1"


def test_gcp_sm_set_handles_already_exists(gcp_mock_client: Any) -> None:
    from google.api_core import exceptions as gax_exceptions

    gcp_mock_client.create_secret.side_effect = gax_exceptions.AlreadyExists("exists")

    b = GcpSmSecretBackend(project_id="proj-123")
    b.set("api/key", "v2")
    # Falls through to add_secret_version on the existing secret.
    gcp_mock_client.add_secret_version.assert_called_once()


def test_gcp_sm_delete(gcp_mock_client: Any) -> None:
    b = GcpSmSecretBackend(project_id="proj-123")
    b.delete("api/key")
    gcp_mock_client.delete_secret.assert_called_once()
    req = gcp_mock_client.delete_secret.call_args.kwargs["request"]
    assert req["name"] == "projects/proj-123/secrets/api/key"


def test_gcp_sm_delete_not_found_raises(gcp_mock_client: Any) -> None:
    from google.api_core import exceptions as gax_exceptions

    gcp_mock_client.delete_secret.side_effect = gax_exceptions.NotFound("nope")
    b = GcpSmSecretBackend(project_id="proj-123")
    with pytest.raises(SecretError, match="not in GCP Secret Manager"):
        b.delete("ghost")


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


def test_factory_file(tmp_path: Path) -> None:
    b = get_secret_backend("file", file_path=tmp_path / "f.json")
    assert isinstance(b, FileSecretBackend)
    b.set("k", "v")
    assert b.get("k") == "v"


def test_factory_gcp_sm(gcp_mock_client: Any) -> None:
    b = get_secret_backend("gcp_sm", project_id="proj")
    assert isinstance(b, GcpSmSecretBackend)


def test_factory_aws_sm() -> None:
    with patch("boto3.client", return_value=MagicMock()):
        b = get_secret_backend("aws_sm", region_name="us-east-1")
    assert isinstance(b, AwsSmSecretBackend)


def test_factory_unknown_raises() -> None:
    with pytest.raises(ConfigError, match="unknown secret backend"):
        get_secret_backend("rotato")


def test_secret_backend_abstract() -> None:
    with pytest.raises(TypeError):
        SecretBackend()  # type: ignore[abstract]
