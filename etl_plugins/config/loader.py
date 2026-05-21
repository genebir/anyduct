"""YAML config loader: ``${VAR}`` substitution + ``!secret <path>`` resolution.

Load order:
    1. ``yaml.load`` with custom loader → ``!secret`` tags become :class:`SecretRef`
    2. recursively substitute ``${VAR}`` strings against ``env`` (default: ``os.environ``)
    3. if a :class:`~etl_plugins.config.secrets.SecretBackend` is provided, resolve
       all :class:`SecretRef` instances into plain strings
    4. (optional) validate against a Pydantic model
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from etl_plugins.config.models import ConnectionsConfig, PipelineConfig
from etl_plugins.config.secrets import SecretBackend
from etl_plugins.config.variables import resolve_config_variables
from etl_plugins.core.exceptions import ConfigError

VAR_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")
"""Matches ``${VAR_NAME}`` — uppercase + digits + underscores, must start non-digit."""


@dataclass(frozen=True)
class SecretRef:
    """Placeholder for a ``!secret <path>`` YAML scalar.

    Resolved to a plaintext string by :func:`resolve_secrets` using a SecretBackend.
    """

    path: str


class ETLYAMLLoader(yaml.SafeLoader):
    """SafeLoader subclass that recognises the ``!secret`` tag."""


def _construct_secret(loader: yaml.SafeLoader, node: yaml.Node) -> SecretRef:
    if not isinstance(node, yaml.ScalarNode):
        raise ConfigError(
            f"!secret expects a scalar value (e.g. '!secret path/to/key'), "
            f"got {type(node).__name__}"
        )
    return SecretRef(path=str(loader.construct_scalar(node)))


ETLYAMLLoader.add_constructor("!secret", _construct_secret)


# --- env var substitution ----------------------------------------------------


def _substitute(value: str, env: Mapping[str, str]) -> str:
    def replace(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in env:
            raise ConfigError(f"env var '{name}' not set (referenced as ${{{name}}})")
        return env[name]

    return VAR_PATTERN.sub(replace, value)


def expand_env(obj: Any, env: Mapping[str, str] | None = None) -> Any:
    """Recursively substitute ``${VAR}`` in all string values.

    SecretRef.path is also substituted so secret paths can depend on env.
    """
    e: Mapping[str, str] = env if env is not None else os.environ
    if isinstance(obj, str):
        return _substitute(obj, e)
    if isinstance(obj, SecretRef):
        return SecretRef(path=_substitute(obj.path, e))
    if isinstance(obj, list):
        return [expand_env(x, e) for x in obj]
    if isinstance(obj, dict):
        return {k: expand_env(v, e) for k, v in obj.items()}
    return obj


# --- secret resolution -------------------------------------------------------


def resolve_secrets(obj: Any, backend: SecretBackend) -> Any:
    """Walk ``obj`` and replace each :class:`SecretRef` with ``backend.get(ref.path)``."""
    if isinstance(obj, SecretRef):
        return backend.get(obj.path)
    if isinstance(obj, list):
        return [resolve_secrets(x, backend) for x in obj]
    if isinstance(obj, dict):
        return {k: resolve_secrets(v, backend) for k, v in obj.items()}
    return obj


def has_unresolved_secrets(obj: Any) -> bool:
    """True if any :class:`SecretRef` remains in the tree."""
    if isinstance(obj, SecretRef):
        return True
    if isinstance(obj, list):
        return any(has_unresolved_secrets(x) for x in obj)
    if isinstance(obj, dict):
        return any(has_unresolved_secrets(v) for v in obj.values())
    return False


# --- public API --------------------------------------------------------------


def load_yaml(path: str | Path) -> Any:
    """Parse YAML with !secret tag support. Returns the raw structure (dicts/lists/scalars)."""
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    with p.open(encoding="utf-8") as f:
        return yaml.load(f, Loader=ETLYAMLLoader)


def load_config(
    path: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    secret_backend: SecretBackend | None = None,
    require_secrets_resolved: bool = True,
) -> Any:
    """Load → expand ``${VAR}`` → resolve ``!secret`` (if backend given) → return dict.

    Raises :class:`ConfigError` if ``require_secrets_resolved=True`` and any
    :class:`SecretRef` is left in the tree.
    """
    data: Any = load_yaml(path)
    data = expand_env(data, env)
    if secret_backend is not None:
        data = resolve_secrets(data, secret_backend)
    if require_secrets_resolved and has_unresolved_secrets(data):
        raise ConfigError(
            f"unresolved !secret references in {path} — "
            f"pass a secret_backend to load_config / load_connections / load_pipeline"
        )
    return data


def load_connections(
    path: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    secret_backend: SecretBackend | None = None,
) -> ConnectionsConfig:
    """Load and validate ``configs/connections.yaml``."""
    data = load_config(path, env=env, secret_backend=secret_backend)
    return ConnectionsConfig.model_validate(data)


def load_pipeline(
    path: str | Path,
    *,
    env: Mapping[str, str] | None = None,
    secret_backend: SecretBackend | None = None,
) -> PipelineConfig:
    """Load and validate ``configs/pipelines/<x>.yaml``.

    Resolution order: ``${VAR}`` env → ``!secret`` → ``${var.name}`` pipeline
    variables (ADR-0041), so a variable's value may itself contain an env ref.
    """
    data = load_config(path, env=env, secret_backend=secret_backend)
    if isinstance(data, dict):
        data = resolve_config_variables(data)
    return PipelineConfig.model_validate(data)


# --- .env helper -------------------------------------------------------------


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> None:
    """Load a ``.env`` file into ``os.environ`` (no-op if file missing).

    Thin wrapper over ``python-dotenv`` — kept here so callers don't need to
    import a separate package directly.
    """
    from dotenv import load_dotenv as _load

    if path is None:
        _load(override=override)
    else:
        _load(dotenv_path=str(path), override=override)
