"""Secret backend abstraction. SPEC.md §5.1, §9.3.

Backends resolve a ``path`` to a plaintext secret. Step 7.4 added write
methods (``set`` / ``delete``) and concrete implementations for HashiCorp
Vault (KV v2), AWS Secrets Manager, GCP Secret Manager, and a local-only
file-backed store for development.

The backend is selected via the ``SECRET_BACKEND`` env var (or explicit
factory call):

- ``env`` (default) — ``path`` is treated as an environment variable name.
  Read-only.
- ``static`` — in-memory ``dict[str, str]``; useful for tests.
- ``file`` — JSON file at a fixed path. Read-write. Dev only — never commit
  the file. Path-prefix ``${FILE_SECRET_PATH}`` or constructor arg.
- ``vault`` — HashiCorp Vault KV v2 engine via ``hvac``.
- ``aws_sm`` — AWS Secrets Manager via ``boto3``.
- ``gcp_sm`` — Google Secret Manager via ``google-cloud-secret-manager``.

The cloud backends require optional extras (``etl-plugins[vault|aws-sm|gcp-sm]``).
The client libraries are imported lazily — importing this module does not
require them.
"""

from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from etl_plugins.core.exceptions import ConfigError, SecretError


class SecretBackend(ABC):
    """Resolves and (optionally) writes a secret at ``path``.

    Read is mandatory (``get``). Write methods default to raising
    :class:`NotImplementedError` — backends that support persistence override
    them.
    """

    @abstractmethod
    def get(self, path: str) -> str: ...

    def set(self, path: str, value: str) -> None:
        """Persist ``value`` at ``path``. Read-only backends raise."""
        raise NotImplementedError(f"{type(self).__name__} is read-only — set() is not supported")

    def delete(self, path: str) -> None:
        """Remove the secret at ``path``. Read-only backends raise."""
        raise NotImplementedError(f"{type(self).__name__} is read-only — delete() is not supported")


class EnvSecretBackend(SecretBackend):
    """Treats ``path`` as an environment variable name. Read-only."""

    def get(self, path: str) -> str:
        value = os.environ.get(path)
        if value is None:
            raise SecretError(f"env var '{path}' not set")
        return value


class StaticSecretBackend(SecretBackend):
    """In-memory map of ``path -> value``. Use for tests or short scripts.

    Do **not** ship a StaticSecretBackend with hard-coded production secrets.
    """

    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets: dict[str, str] = dict(secrets or {})

    def set(self, path: str, value: str) -> None:
        self._secrets[path] = value

    def delete(self, path: str) -> None:
        if path not in self._secrets:
            raise SecretError(f"secret '{path}' not in StaticSecretBackend")
        del self._secrets[path]

    def get(self, path: str) -> str:
        if path not in self._secrets:
            raise SecretError(f"secret '{path}' not in StaticSecretBackend")
        return self._secrets[path]


class FileSecretBackend(SecretBackend):
    """JSON file at ``file_path``. Read-write. **Dev only.**

    The file holds a flat ``{path: value}`` JSON object. Atomic writes via
    temp-file + ``os.replace`` so an aborted process cannot leave a partial
    file. ``chmod 0600`` is applied so the secrets aren't world-readable.

    Why exist: lets developers iterate without standing up Vault locally,
    while keeping production code paths identical. The file MUST be
    git-ignored — never commit it.
    """

    def __init__(self, file_path: str | Path | None = None) -> None:
        resolved = file_path or os.environ.get("FILE_SECRET_PATH")
        if not resolved:
            raise ConfigError(
                "FileSecretBackend requires file_path arg or FILE_SECRET_PATH env var"
            )
        self._path = Path(resolved)
        self._lock = threading.Lock()
        # Materialize the parent dir; do not touch the file itself (a missing
        # file is treated as an empty map on read).
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, str]:
        if not self._path.is_file():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SecretError(f"FileSecretBackend: {self._path} is not valid JSON ({e})") from e
        if not isinstance(data, dict):
            raise SecretError(f"FileSecretBackend: {self._path} must be a JSON object")
        return {str(k): str(v) for k, v in data.items()}

    def _atomic_write(self, payload: dict[str, str]) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        # 0600 — owner read/write only.
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)

    def get(self, path: str) -> str:
        with self._lock:
            store = self._load()
        if path not in store:
            raise SecretError(f"secret '{path}' not in FileSecretBackend({self._path})")
        return store[path]

    def set(self, path: str, value: str) -> None:
        with self._lock:
            store = self._load()
            store[path] = value
            self._atomic_write(store)

    def delete(self, path: str) -> None:
        with self._lock:
            store = self._load()
            if path not in store:
                raise SecretError(f"secret '{path}' not in FileSecretBackend({self._path})")
            del store[path]
            self._atomic_write(store)


class VaultSecretBackend(SecretBackend):
    """HashiCorp Vault KV v2 engine via ``hvac``.

    Each ``path`` maps to a single string under the key ``"value"`` in a KV v2
    secret. This is the simplest schema and matches our internal contract that
    "a secret is just one opaque string".

    Required pip extras: ``etl-plugins[vault]`` (installs ``hvac``).

    Config (constructor args or matching ``VAULT_*`` env vars):

    * ``url``       — ``VAULT_ADDR``       (e.g. ``http://127.0.0.1:8200``)
    * ``token``     — ``VAULT_TOKEN``      (root or AppRole-derived token)
    * ``mount_point`` — ``VAULT_KV_MOUNT`` (default ``"secret"``)
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        mount_point: str | None = None,
    ) -> None:
        try:
            import hvac  # noqa: F401  imported lazily for the extras hint below
        except ImportError as e:  # pragma: no cover — exercised via extras gating
            raise ConfigError(
                "VaultSecretBackend requires the 'vault' extra — pip install etl-plugins[vault]"
            ) from e
        self._url = url or os.environ.get("VAULT_ADDR")
        self._token = token or os.environ.get("VAULT_TOKEN")
        self._mount = mount_point or os.environ.get("VAULT_KV_MOUNT") or "secret"
        if not self._url or not self._token:
            raise ConfigError(
                "VaultSecretBackend requires url+token (or VAULT_ADDR+VAULT_TOKEN env vars)"
            )
        self._client = self._build_client()

    def _build_client(self) -> Any:
        import hvac

        client = hvac.Client(url=self._url, token=self._token)
        if not client.is_authenticated():
            raise SecretError("VaultSecretBackend: authentication failed (check VAULT_TOKEN)")
        return client

    def get(self, path: str) -> str:
        import hvac.exceptions

        try:
            resp = self._client.secrets.kv.v2.read_secret_version(
                path=path, mount_point=self._mount, raise_on_deleted_version=True
            )
        except hvac.exceptions.InvalidPath as e:
            raise SecretError(f"secret '{path}' not in Vault (mount={self._mount})") from e
        try:
            return str(resp["data"]["data"]["value"])
        except (KeyError, TypeError) as e:
            raise SecretError(
                f"Vault secret '{path}' is missing the 'value' key (mount={self._mount})"
            ) from e

    def set(self, path: str, value: str) -> None:
        self._client.secrets.kv.v2.create_or_update_secret(
            path=path, secret={"value": value}, mount_point=self._mount
        )

    def delete(self, path: str) -> None:
        # Permanent delete: remove all versions + metadata.
        self._client.secrets.kv.v2.delete_metadata_and_all_versions(
            path=path, mount_point=self._mount
        )


class AwsSmSecretBackend(SecretBackend):
    """AWS Secrets Manager via ``boto3``.

    Each ``path`` is the AWS secret name (a slash-segmented string). Values
    are stored as the ``SecretString`` (we do not use ``SecretBinary``).

    Required pip extras: ``etl-plugins[aws-sm]``.

    Config (constructor args or AWS-standard env vars):

    * ``region_name``  — ``AWS_REGION`` / ``AWS_DEFAULT_REGION``
    * ``endpoint_url`` — ``AWS_ENDPOINT_URL_SECRETSMANAGER`` (LocalStack / VPC endpoints)
    * Credentials follow the standard boto3 credential chain.

    ``delete`` uses ``ForceDeleteWithoutRecovery=True`` so the secret is gone
    immediately (no 7-day recovery window). Suitable for our model where the
    DB row is the SSOT — we never expect to "undelete".
    """

    def __init__(
        self,
        region_name: str | None = None,
        endpoint_url: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
    ) -> None:
        try:
            import boto3  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise ConfigError(
                "AwsSmSecretBackend requires the 'aws-sm' extra — pip install etl-plugins[aws-sm]"
            ) from e
        self._region = (
            region_name or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        )
        self._endpoint_url = endpoint_url or os.environ.get("AWS_ENDPOINT_URL_SECRETSMANAGER")
        self._access_key = aws_access_key_id
        self._secret_key = aws_secret_access_key
        self._client = self._build_client()

    def _build_client(self) -> Any:
        import boto3

        kwargs: dict[str, Any] = {"service_name": "secretsmanager"}
        if self._region:
            kwargs["region_name"] = self._region
        if self._endpoint_url:
            kwargs["endpoint_url"] = self._endpoint_url
        if self._access_key and self._secret_key:
            kwargs["aws_access_key_id"] = self._access_key
            kwargs["aws_secret_access_key"] = self._secret_key
        return boto3.client(**kwargs)

    def get(self, path: str) -> str:
        from botocore.exceptions import ClientError

        try:
            resp = self._client.get_secret_value(SecretId=path)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "ResourceNotFoundException":
                raise SecretError(f"secret '{path}' not in AWS Secrets Manager") from e
            raise
        if "SecretString" not in resp:
            raise SecretError(
                f"AWS secret '{path}' has no SecretString (binary secrets not supported)"
            )
        return str(resp["SecretString"])

    def set(self, path: str, value: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self._client.create_secret(Name=path, SecretString=value)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code != "ResourceExistsException":
                raise
            self._client.put_secret_value(SecretId=path, SecretString=value)

    def delete(self, path: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self._client.delete_secret(SecretId=path, ForceDeleteWithoutRecovery=True)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "ResourceNotFoundException":
                raise SecretError(f"secret '{path}' not in AWS Secrets Manager") from e
            raise


class GcpSmSecretBackend(SecretBackend):
    """Google Secret Manager via ``google-cloud-secret-manager``.

    Each ``path`` is the secret ID within ``project_id``. Always reads the
    ``latest`` version on ``get``. Writes call ``create_secret`` (idempotent on
    ``AlreadyExists`` -> falls through to ``add_secret_version``).

    Required pip extras: ``etl-plugins[gcp-sm]``.

    Config (constructor args or env vars):

    * ``project_id`` — ``GCP_PROJECT`` / ``GOOGLE_CLOUD_PROJECT``
    * Authentication uses Application Default Credentials (ADC).

    Note: there is no production-grade local emulator for Secret Manager.
    Integration tests are run against mocks (see
    ``tests/unit/config/test_secrets_gcp_sm.py``).
    """

    def __init__(self, project_id: str | None = None) -> None:
        try:
            from google.cloud import secretmanager
        except ImportError as e:  # pragma: no cover
            raise ConfigError(
                "GcpSmSecretBackend requires the 'gcp-sm' extra — pip install etl-plugins[gcp-sm]"
            ) from e
        self._project_id = (
            project_id or os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        )
        if not self._project_id:
            raise ConfigError("GcpSmSecretBackend requires project_id (or GCP_PROJECT env var)")
        self._client: Any = secretmanager.SecretManagerServiceClient()

    def _parent(self) -> str:
        return f"projects/{self._project_id}"

    def _secret_name(self, path: str) -> str:
        return f"projects/{self._project_id}/secrets/{path}"

    def get(self, path: str) -> str:
        from google.api_core import exceptions as gax_exceptions

        version_name = f"{self._secret_name(path)}/versions/latest"
        try:
            response = self._client.access_secret_version(request={"name": version_name})
        except gax_exceptions.NotFound as e:
            raise SecretError(f"secret '{path}' not in GCP Secret Manager") from e
        decoded: str = response.payload.data.decode("utf-8")
        return decoded

    def set(self, path: str, value: str) -> None:
        import contextlib

        from google.api_core import exceptions as gax_exceptions

        # AlreadyExists is expected when adding a new version to an existing
        # secret — fall through to add_secret_version below.
        with contextlib.suppress(gax_exceptions.AlreadyExists):
            self._client.create_secret(
                request={
                    "parent": self._parent(),
                    "secret_id": path,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
        self._client.add_secret_version(
            request={
                "parent": self._secret_name(path),
                "payload": {"data": value.encode("utf-8")},
            }
        )

    def delete(self, path: str) -> None:
        from google.api_core import exceptions as gax_exceptions

        try:
            self._client.delete_secret(request={"name": self._secret_name(path)})
        except gax_exceptions.NotFound as e:
            raise SecretError(f"secret '{path}' not in GCP Secret Manager") from e


def get_secret_backend(name: str | None = None, **opts: Any) -> SecretBackend:
    """Build a SecretBackend by name.

    Resolution order for ``name``:
        1. explicit argument
        2. ``SECRET_BACKEND`` environment variable
        3. ``"env"`` (default)

    ``opts`` are forwarded to the chosen backend's constructor.
    """
    resolved = (name or os.environ.get("SECRET_BACKEND") or "env").lower()
    if resolved == "env":
        return EnvSecretBackend()
    if resolved == "static":
        secrets = opts.get("secrets")
        return StaticSecretBackend(secrets if isinstance(secrets, dict) else None)
    if resolved == "file":
        return FileSecretBackend(file_path=opts.get("file_path"))
    if resolved == "vault":
        return VaultSecretBackend(
            url=opts.get("url"),
            token=opts.get("token"),
            mount_point=opts.get("mount_point"),
        )
    if resolved == "aws_sm":
        return AwsSmSecretBackend(
            region_name=opts.get("region_name"),
            endpoint_url=opts.get("endpoint_url"),
            aws_access_key_id=opts.get("aws_access_key_id"),
            aws_secret_access_key=opts.get("aws_secret_access_key"),
        )
    if resolved == "gcp_sm":
        return GcpSmSecretBackend(project_id=opts.get("project_id"))
    raise ConfigError(f"unknown secret backend: '{resolved}'")


__all__ = [
    "AwsSmSecretBackend",
    "EnvSecretBackend",
    "FileSecretBackend",
    "GcpSmSecretBackend",
    "SecretBackend",
    "StaticSecretBackend",
    "VaultSecretBackend",
    "get_secret_backend",
]
