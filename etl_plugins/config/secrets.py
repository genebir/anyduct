"""Secret backend abstraction. SPEC.md §5.1, §9.3.

The backend is selected via the ``SECRET_BACKEND`` env var (or explicit factory call):

- ``env`` (default) — ``path`` is treated as an environment variable name
- ``static`` — in-memory ``dict[str, str]``; useful for tests / declarative setups
- ``vault`` / ``aws_sm`` / ``gcp_sm`` — stubbed in Step 1.5; real impls in a later step
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

from etl_plugins.core.exceptions import ConfigError, SecretError


class SecretBackend(ABC):
    """Resolves a secret ``path`` to its plaintext value."""

    @abstractmethod
    def get(self, path: str) -> str: ...


class EnvSecretBackend(SecretBackend):
    """Treats ``path`` as an environment variable name."""

    def get(self, path: str) -> str:
        value = os.environ.get(path)
        if value is None:
            raise SecretError(f"env var '{path}' not set")
        return value


class StaticSecretBackend(SecretBackend):
    """In-memory map of ``path → value``. Use for tests or short scripts.

    Do **not** ship a StaticSecretBackend with hard-coded production secrets.
    """

    def __init__(self, secrets: dict[str, str] | None = None) -> None:
        self._secrets: dict[str, str] = dict(secrets or {})

    def set(self, path: str, value: str) -> None:
        self._secrets[path] = value

    def get(self, path: str) -> str:
        if path not in self._secrets:
            raise SecretError(f"secret '{path}' not in StaticSecretBackend")
        return self._secrets[path]


class _NotImplementedBackend(SecretBackend):
    """Placeholder for backends that aren't wired up yet."""

    def __init__(self, name: str) -> None:
        self._name = name

    def get(self, path: str) -> str:
        raise NotImplementedError(
            f"secret backend '{self._name}' is not implemented yet — "
            f"track this in ROADMAP.md / Step 1.5+"
        )


def get_secret_backend(name: str | None = None, **opts: Any) -> SecretBackend:
    """Build a SecretBackend by name.

    Resolution order for ``name``:
        1. explicit argument
        2. ``SECRET_BACKEND`` environment variable
        3. ``"env"`` (default)
    """
    resolved = (name or os.environ.get("SECRET_BACKEND") or "env").lower()
    if resolved == "env":
        return EnvSecretBackend()
    if resolved == "static":
        secrets = opts.get("secrets")
        return StaticSecretBackend(secrets if isinstance(secrets, dict) else None)
    if resolved in {"vault", "aws_sm", "gcp_sm"}:
        return _NotImplementedBackend(resolved)
    raise ConfigError(f"unknown secret backend: '{resolved}'")
