"""Runtime templating — ``{{ ... }}`` substitution against a per-run context.

This is the *dynamic, per-run* layer, distinct from the three existing
*static, load-time* substitution namespaces so they compose without
clashing:

| Syntax           | When        | Source                                   |
|------------------|-------------|------------------------------------------|
| ``${ENV}``       | load        | environment variables (config.loader)    |
| ``!secret`` / ``${SECRET:...}`` | resolve | secret backend          |
| ``${var.name}``  | load        | pipeline/workspace variables (variables) |
| **``{{ expr }}``** | **run**   | **runtime context (this module)**        |

Why a separate ``{{ }}`` syntax (Airflow-style) instead of extending
``${...}``: runtime values (the run's logical date, its id, trigger
params) only exist *per execution*, after the static config is already
resolved. Keeping the syntaxes distinct lets a pipeline mix a static
variable and a per-run param in the same string without ambiguity:

    query: "SELECT * FROM ${var.table} WHERE day = '{{ ds }}'"

**Security**: unlike Airflow we do NOT run Jinja2 (arbitrary code in a
template is an injection vector, and a heavy dependency). This renderer
only resolves *dotted attribute/key paths* into the context mapping —
no function calls, no expressions, no builtins. This matches the
project's sandboxed-expression posture (filter/branch predicates).

Available context keys (see :class:`RuntimeContext.as_mapping`):

* ``run_id`` — the run's identifier (string)
* ``ds`` — logical date ``YYYY-MM-DD``
* ``ds_nodash`` — logical date ``YYYYMMDD``
* ``ts`` — logical timestamp, ISO-8601
* ``logical_date`` — alias of ``ts``
* ``pipeline_name`` — the pipeline's name (may be empty)
* ``params.<key>`` — trigger-time / declared parameters (nested ok:
  ``params.window.start``)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from etl_plugins.core.exceptions import ConfigError

# A dotted path: ``ds`` / ``params.foo`` / ``params.win.start``. Leading
# segment is an identifier; following segments are identifiers too. We
# deliberately do NOT support indexing/calls — paths only.
_PATH = r"[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*"
TEMPLATE_REF = re.compile(r"\{\{\s*(" + _PATH + r")\s*\}\}")
WHOLE_TEMPLATE_REF = re.compile(r"^\{\{\s*(" + _PATH + r")\s*\}\}$")

__all__ = [
    "TEMPLATE_REF",
    "RuntimeContext",
    "has_template",
    "render_config_templates",
    "render_templates",
]


@dataclass(frozen=True)
class RuntimeContext:
    """The per-run values a pipeline can template against.

    Construct one per execution. ``logical_date`` is the run's
    business/execution time — for a scheduled run it's the scheduled
    tick; for a manual run it's the trigger time. ``params`` is the
    merged parameter mapping (declared defaults overridden by
    trigger-time values).
    """

    run_id: str
    logical_date: datetime
    params: dict[str, Any] = field(default_factory=dict)
    pipeline_name: str = ""

    def as_mapping(self) -> dict[str, Any]:
        """Flat context mapping consumed by the renderer."""
        ds = self.logical_date.date().isoformat()
        return {
            "run_id": self.run_id,
            "ds": ds,
            "ds_nodash": ds.replace("-", ""),
            "ts": self.logical_date.isoformat(),
            "logical_date": self.logical_date.isoformat(),
            "pipeline_name": self.pipeline_name,
            "params": dict(self.params),
        }


def has_template(obj: Any) -> bool:
    """True if any string under ``obj`` contains a ``{{ ... }}`` reference."""
    if isinstance(obj, str):
        return bool(TEMPLATE_REF.search(obj))
    if isinstance(obj, dict):
        return any(has_template(v) for v in obj.values())
    if isinstance(obj, list):
        return any(has_template(x) for x in obj)
    return False


def _resolve_path(path: str, context: dict[str, Any]) -> Any:
    cur: Any = context
    walked: list[str] = []
    for seg in path.split("."):
        walked.append(seg)
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        else:
            joined = ".".join(walked)
            raise ConfigError(
                f"template reference '{{{{ {path} }}}}' is undefined "
                f"(no '{joined}' in run context). Available top-level keys: "
                f"{sorted(context)}; declare it under the pipeline's 'params' "
                f"or pass it at trigger time."
            )
    return cur


def _substitute(value: str, context: dict[str, Any]) -> Any:
    whole = WHOLE_TEMPLATE_REF.match(value)
    if whole is not None:
        # Whole-string reference → preserve the value's native type
        # (so ``chunk_size: "{{ params.cs }}"`` stays an int).
        return _resolve_path(whole.group(1), context)
    # Embedded → string interpolation.
    return TEMPLATE_REF.sub(lambda m: str(_resolve_path(m.group(1), context)), value)


def render_templates(obj: Any, context: dict[str, Any]) -> Any:
    """Recursively render ``{{ ... }}`` in every string under ``obj``.

    Non-string scalars pass through. Raises :class:`ConfigError` on an
    undefined reference (typos surface immediately rather than silently
    producing a literal ``{{ ... }}`` in a query).
    """
    if isinstance(obj, str):
        return _substitute(obj, context)
    if isinstance(obj, dict):
        return {k: render_templates(v, context) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render_templates(x, context) for x in obj]
    return obj


def render_config_templates(
    config: dict[str, Any],
    context: RuntimeContext,
) -> dict[str, Any]:
    """Render a whole pipeline-config dict against a :class:`RuntimeContext`.

    Mirrors :func:`etl_plugins.config.variables.resolve_config_variables`
    but for the runtime ``{{ }}`` layer. Call this *after* the static
    ``${var}`` / secret / env resolution and *before* ``build_pipeline``,
    so the built pipeline already carries concrete, per-run values.
    Returns the input unchanged (no copy) when there are no templates.
    """
    if not has_template(config):
        return config
    rendered = render_templates(config, context.as_mapping())
    assert isinstance(rendered, dict)
    return rendered
