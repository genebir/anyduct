"""Pipeline variable substitution — ``${var.name}`` (ADR-0041, V1).

Variables are config-driven, named values a pipeline can reference in any string
field (queries, options, transform expressions). They are a distinct namespace
from env vars (``${UPPER_CASE}``, see :mod:`etl_plugins.config.loader`) and
secrets (``!secret`` / ``${SECRET:...}``), so the three compose without clashing.

Two reference forms:

* **whole-string** ``"${var.name}"`` → replaced with the variable's *typed*
  value (int / bool / list / …), so ``chunk_size: "${var.cs}"`` stays an int.
* **embedded** ``"... ${var.name} ..."`` → the value's ``str()`` is interpolated,
  e.g. ``query: "SELECT * FROM t WHERE id > ${var.min_id}"``.

Local (pipeline) variables live in ``PipelineConfig.variables``. Workspace-wide
globals merge underneath them (locals win) at the service layer (V2); the core
resolver just takes the merged mapping. Variables can't reference other
variables in V1.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from etl_plugins.core.exceptions import ConfigError

_NAME = r"[a-zA-Z_][a-zA-Z0-9_]*"
VAR_REF = re.compile(rf"\$\{{var\.({_NAME})\}}")
WHOLE_VAR_REF = re.compile(rf"^\$\{{var\.({_NAME})\}}$")

__all__ = ["VAR_REF", "resolve_config_variables", "resolve_variables"]


def _lookup(name: str, variables: Mapping[str, Any]) -> Any:
    if name not in variables:
        raise ConfigError(f"variable 'var.{name}' is not defined")
    return variables[name]


def _substitute(value: str, variables: Mapping[str, Any]) -> Any:
    whole = WHOLE_VAR_REF.match(value)
    if whole is not None:
        return _lookup(whole.group(1), variables)  # preserve type
    return VAR_REF.sub(lambda m: str(_lookup(m.group(1), variables)), value)


def resolve_variables(obj: Any, variables: Mapping[str, Any]) -> Any:
    """Recursively replace ``${var.name}`` in every string under ``obj``."""
    if isinstance(obj, str):
        return _substitute(obj, variables)
    if isinstance(obj, dict):
        return {k: resolve_variables(v, variables) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_variables(x, variables) for x in obj]
    return obj


def resolve_config_variables(
    config: Mapping[str, Any], *, extra: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Resolve ``${var.name}`` across a pipeline-config dict.

    Variables = ``extra`` (e.g. workspace globals) overlaid by the config's own
    ``variables`` block (locals win). The ``variables`` block itself is left
    untouched (no inter-variable references in V1) and kept for round-tripping.
    """
    local = config.get("variables") or {}
    if not isinstance(local, dict):
        raise ConfigError("'variables' must be a mapping of name → value")
    merged: dict[str, Any] = {**(extra or {}), **local}
    return {
        key: (value if key == "variables" else resolve_variables(value, merged))
        for key, value in config.items()
    }
