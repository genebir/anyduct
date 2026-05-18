"""Secret-marker walker for the connections API (Step 8.5c).

The wire protocol carries secrets in two places:

* inside ``config``, anywhere a string would normally go, the client may use
  ``{"$secret": "<logical_key>"}`` as a sentinel marker;
* alongside ``config``, ``secrets: {logical_key: plain_value, ...}`` carries
  the actual values.

On the server we:

1. walk ``config`` replacing every ``{"$secret": k}`` with the placeholder
   string ``${SECRET:<backend_path>}`` (compatible with Step 7.3's
   ``yaml_sync`` round-trip and the core's secret resolver);
2. write each value to the configured :class:`SecretBackend` under its
   computed path;
3. return the sanitized config + the list of paths so the repository can
   persist them in ``connections.secret_refs``.

Orphan markers (key referenced in ``config`` but absent from ``secrets``)
and orphan values (key present in ``secrets`` but never referenced) both
raise :class:`SecretMarkerError` — the request was almost certainly a
mistake.

Path scheme: ``etlx/{workspace_id}/{connection_id}/{logical_key}``. The
``connection_id`` is the row's UUID, minted *before* the backend writes so
the path is stable across the entire connection's lifetime (slug/name
changes don't relocate secrets).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from etl_plugins.config.secrets import SecretBackend
from etl_plugins.core.exceptions import SecretError

# Format compatible with Step 7.3 yaml_sync.
_PLACEHOLDER_FORMAT = "${{SECRET:{path}}}"
_MARKER_KEY = "$secret"


class SecretMarkerError(Exception):
    """Orphan ``$secret`` marker or extra entry in ``secrets``."""


class SecretBackendReadOnlyError(Exception):
    """The configured backend doesn't support write — but the request supplied secrets."""


def _path_for(*, workspace_id: UUID, connection_id: UUID, logical_key: str) -> str:
    return f"etlx/{workspace_id}/{connection_id}/{logical_key}"


def _is_marker(obj: Any) -> bool:
    return (
        isinstance(obj, dict)
        and len(obj) == 1
        and _MARKER_KEY in obj
        and isinstance(obj[_MARKER_KEY], str)
        and obj[_MARKER_KEY]
    )


class SecretWalker:
    """Translate the marker pattern into placeholder strings + backend writes.

    Stateless — the methods take all dependencies as arguments. Lifted into
    a class so router/test wiring can rely on a single dependency type.
    """

    def sanitize(
        self,
        *,
        config: dict[str, Any],
        secrets: dict[str, str],
        workspace_id: UUID,
        connection_id: UUID,
    ) -> tuple[dict[str, Any], list[str]]:
        """Walk ``config``, replace markers with placeholders, return refs.

        Does **not** touch the backend. Caller is responsible for writing
        values via :meth:`write_secrets` once it has both halves.
        """
        used_keys: list[str] = []
        sanitized = self._walk(config, secrets, workspace_id, connection_id, used_keys)
        missing = set(secrets) - set(used_keys)
        if missing:
            raise SecretMarkerError(
                f"orphan secret values (not referenced in config): {sorted(missing)}"
            )
        # Build refs in traversal order; dedupe while preserving first occurrence.
        seen: set[str] = set()
        refs: list[str] = []
        for k in used_keys:
            path = _path_for(workspace_id=workspace_id, connection_id=connection_id, logical_key=k)
            if path not in seen:
                seen.add(path)
                refs.append(path)
        return sanitized, refs

    def _walk(
        self,
        obj: Any,
        secrets: dict[str, str],
        workspace_id: UUID,
        connection_id: UUID,
        used_keys: list[str],
    ) -> Any:
        if _is_marker(obj):
            key = obj[_MARKER_KEY]
            if key not in secrets:
                raise SecretMarkerError(f"config references unknown secret key {key!r}")
            used_keys.append(key)
            return _PLACEHOLDER_FORMAT.format(
                path=_path_for(
                    workspace_id=workspace_id,
                    connection_id=connection_id,
                    logical_key=key,
                )
            )
        if isinstance(obj, dict):
            return {
                k: self._walk(v, secrets, workspace_id, connection_id, used_keys)
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [self._walk(x, secrets, workspace_id, connection_id, used_keys) for x in obj]
        return obj

    @staticmethod
    def write_secrets(
        backend: SecretBackend,
        *,
        secrets: dict[str, str],
        workspace_id: UUID,
        connection_id: UUID,
    ) -> None:
        """Write each ``(logical_key, value)`` to the backend under the computed path.

        Raises :class:`SecretBackendReadOnlyError` if the backend declines
        writes (e.g. :class:`EnvSecretBackend`).
        """
        for key, value in secrets.items():
            path = _path_for(
                workspace_id=workspace_id, connection_id=connection_id, logical_key=key
            )
            try:
                backend.set(path, value)
            except NotImplementedError as e:
                raise SecretBackendReadOnlyError(str(e)) from e

    @staticmethod
    def delete_paths(backend: SecretBackend, paths: list[str]) -> None:
        """Best-effort delete of every path. Missing entries are tolerated."""
        for path in paths:
            try:
                backend.delete(path)
            except (SecretError, NotImplementedError, KeyError):
                # Read-only backend or missing secret — log on real systems,
                # but don't fail the user-facing delete.
                continue


__all__ = [
    "SecretBackendReadOnlyError",
    "SecretMarkerError",
    "SecretWalker",
]
