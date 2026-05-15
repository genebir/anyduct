"""VaultSecretBackend integration test (Step 7.4).

Spins a real ``hashicorp/vault:1.18`` dev-mode container via testcontainers,
exercises get/set/delete on the KV v2 engine, and rolls back when done.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from testcontainers.vault import VaultContainer

from etl_plugins.config.secrets import VaultSecretBackend
from etl_plugins.core.exceptions import SecretError

pytestmark = pytest.mark.it


@pytest.fixture(scope="module")
def vault_container() -> Iterator[VaultContainer]:
    """Vault dev-mode container with a fixed root token.

    KV v2 is mounted at ``secret/`` by default in dev mode, which matches the
    backend's default ``mount_point``.
    """
    with VaultContainer("hashicorp/vault:1.18", root_token="root") as container:
        yield container


@pytest.fixture
def vault_backend(vault_container: VaultContainer) -> VaultSecretBackend:
    return VaultSecretBackend(
        url=vault_container.get_connection_url(),
        token="root",
        mount_point="secret",
    )


def test_vault_round_trip(vault_backend: VaultSecretBackend) -> None:
    vault_backend.set("app/db/password", "s3cret")
    assert vault_backend.get("app/db/password") == "s3cret"


def test_vault_overwrite_creates_new_version(vault_backend: VaultSecretBackend) -> None:
    vault_backend.set("app/api/key", "v1")
    vault_backend.set("app/api/key", "v2")
    # get returns the latest version.
    assert vault_backend.get("app/api/key") == "v2"


def test_vault_get_missing_raises(vault_backend: VaultSecretBackend) -> None:
    with pytest.raises(SecretError, match="not in Vault"):
        vault_backend.get("does/not/exist")


def test_vault_delete(vault_backend: VaultSecretBackend) -> None:
    vault_backend.set("temp/key", "x")
    vault_backend.delete("temp/key")
    with pytest.raises(SecretError):
        vault_backend.get("temp/key")
