"""POST /connections/{id}/test backbone (Step 8.5c).

Materializes a stored ``Connection`` row into a live core ``Connector`` —
resolving every ``${SECRET:<path>}`` placeholder through the configured
backend, then constructing the connector via
:class:`etl_plugins.core.registry.ConnectorRegistry`. Runs
``connect()`` + ``health_check()`` + ``close()`` and bundles the outcome
in :class:`ConnectionTestOutcome`.

The core's connector API is synchronous (database drivers, S3, etc.), so
:meth:`ConnectionTester.run` offloads the blocking calls to a worker
thread via :func:`asyncio.to_thread`. That keeps the FastAPI event loop
unblocked while still letting us reuse the same connectors the pipelines
do — no parallel "test only" code paths.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from dataclasses import dataclass
from typing import Any

from etl_plugins.config.secrets import SecretBackend
from etl_plugins.core.exceptions import ConfigError, RegistryError, SecretError
from etl_plugins.core.registry import ConnectorRegistry
from etlx_server.db.models import Connection

_PLACEHOLDER_RE = re.compile(r"^\$\{SECRET:(?P<path>[^}]+)\}$")


class SecretResolutionError(Exception):
    """Raised when a stored placeholder can't be resolved through the backend."""


@dataclass(frozen=True)
class ConnectionTestOutcome:
    """Result of a single ``POST /connections/{id}/test`` call."""

    ok: bool
    error: str | None = None
    """``None`` on success; otherwise a short, secret-free error string."""


def _resolve(obj: Any, backend: SecretBackend) -> Any:
    if isinstance(obj, str):
        m = _PLACEHOLDER_RE.match(obj)
        if m is None:
            return obj
        try:
            return backend.get(m.group("path"))
        except SecretError as e:
            raise SecretResolutionError(
                f"failed to resolve secret at {m.group('path')!r}: {e}"
            ) from e
    if isinstance(obj, dict):
        return {k: _resolve(v, backend) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve(x, backend) for x in obj]
    return obj


def _run_blocking_health_check(klass: type, options: dict[str, Any]) -> tuple[bool, str | None]:
    """Pure-sync inner worker. Returns ``(ok, error_or_none)``."""
    try:
        connector = klass(**options)
    except TypeError as e:
        # Misconfigured options — same conversion the runtime builder does.
        return False, f"ConfigError: {e}"
    try:
        connector.connect()
        ok = connector.health_check()
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        with contextlib.suppress(Exception):
            connector.close()
    return ok, None if ok else "health_check returned False"


class ConnectionTester:
    """Resolve secrets, build a connector, run its health check."""

    def __init__(self, backend: SecretBackend) -> None:
        self._backend = backend

    async def run(self, connection: Connection) -> ConnectionTestOutcome:
        try:
            options = _resolve(connection.config_json, self._backend)
        except SecretResolutionError as e:
            return ConnectionTestOutcome(ok=False, error=str(e))
        if not isinstance(options, dict):
            return ConnectionTestOutcome(ok=False, error="resolved config is not a JSON object")
        try:
            klass = ConnectorRegistry.get(connection.type)
        except (ConfigError, RegistryError) as e:
            return ConnectionTestOutcome(ok=False, error=f"{type(e).__name__}: {e}")
        ok, err = await asyncio.to_thread(_run_blocking_health_check, klass, options)
        return ConnectionTestOutcome(ok=ok, error=err)


__all__ = [
    "ConnectionTestOutcome",
    "ConnectionTester",
    "SecretResolutionError",
]
