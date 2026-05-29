"""Static lint rules for a parsed :class:`PipelineConfig` (Phase DD, 2026-05-29).

The previous Phases (X / Z / AA / CC) layered four mechanisms for keeping
the catalog's column lineage accurate:

* **Phase X** — sqlglot full-AST analysis of SQL source queries.
* **Phase Z** — SchemaInspector inject for ``SELECT *``.
* **Phase AA** — schema-passthrough fallback for opaque transforms.
* **Phase CC** — user-declared ``column_mapping`` on the transform itself.

Phase CC is the most accurate but is *opt-in*: a user only benefits from
it if they know to add the declaration. This module gives the dry-run +
the (future) builder a way to *nudge* the user toward it. We surface
advisory warnings — they don't make ``dry-run`` fail, but they show up
beside the connector health checks so the user is reminded to add a
``column_mapping`` declaration on each opaque transform.

The lint is intentionally *coarse*: there's no false-positive cost (the
worst case is a redundant "consider adding column_mapping" note for a
trivial transform). False *negatives* — silently missing an opaque
transform — would defeat the purpose, so the walker mirrors
:func:`derive_column_lineage` and inspects every shape (linear / task-DAG
/ graph).
"""

from __future__ import annotations

from dataclasses import dataclass

from etl_plugins.config.models import PipelineConfig, TransformConfig

#: Transform types whose body the static column-lineage walker can't read.
#: These are the targets of the ``column_mapping_recommended`` rule —
#: declaring a ``column_mapping`` on one of these unlocks accurate per-
#: column lineage in the catalog (ADR-0047).
_OPAQUE_TRANSFORM_TYPES = frozenset({"python", "custom_python", "sql_exec"})


@dataclass(frozen=True)
class LintWarning:
    """A single advisory warning emitted by :func:`lint_pipeline`.

    Attributes:
        code: Machine-readable identifier. Stable across versions — the
            UI / IDE keys filters and dismissals off this.
        message: User-facing English. The web client localises in the
            renderer, not here.
        location: Path-like address of the offending element, e.g.
            ``"tasks.0.transforms.2"`` or ``"graph.nodes.transform_1"``.
            Lets a builder jump straight to it.
    """

    code: str
    message: str
    location: str | None = None


def lint_pipeline(cfg: PipelineConfig) -> list[LintWarning]:
    """Run every Phase DD lint rule over a parsed pipeline config.

    Warnings are **advisory**: callers (dry-run, builder pre-save) decide
    whether to fail the operation or just surface them. The default
    posture is to *show* but not *block* — accuracy hints shouldn't
    prevent the user from running a pipeline that's otherwise valid.
    """
    warnings: list[LintWarning] = []

    if cfg.graph is not None:
        for node in cfg.graph.nodes:
            if node.type == "transform" and node.transform is not None:
                warnings.extend(_lint_transform(node.transform, f"graph.nodes.{node.id}"))
    elif cfg.tasks:
        for task_idx, task in enumerate(cfg.tasks):
            for tc_idx, tc in enumerate(task.transforms):
                warnings.extend(_lint_transform(tc, f"tasks.{task_idx}.transforms.{tc_idx}"))
    else:
        # Single-task (legacy) shape — transforms live at the top level.
        for tc_idx, tc in enumerate(cfg.transforms):
            warnings.extend(_lint_transform(tc, f"transforms.{tc_idx}"))

    return warnings


def _lint_transform(tc: TransformConfig, location: str) -> list[LintWarning]:
    """Per-transform rule fan-out. Add new rules as additional helpers."""
    out: list[LintWarning] = []
    out.extend(_rule_column_mapping_recommended(tc, location))
    return out


def _rule_column_mapping_recommended(tc: TransformConfig, location: str) -> list[LintWarning]:
    """Opaque transform (``python`` / ``custom_python`` / ``sql_exec``) without
    a ``column_mapping`` → recommend adding one for accurate lineage.

    The schema-passthrough fallback (ADR-0046) often catches the column-
    name-preserving case, but it can't see column renames or a-to-b
    moves. The catalog ends up with empty-upstream rows for those — not
    wrong, just less informative than it could be. A user-declared
    mapping closes that gap.
    """
    if tc.type not in _OPAQUE_TRANSFORM_TYPES:
        return []
    data = tc.model_dump()
    if data.get("column_mapping") is not None:
        return []
    return [
        LintWarning(
            code="column_mapping_recommended",
            message=(
                f"transform of type '{tc.type}' has no column_mapping. "
                "The catalog will try a schema-passthrough fallback "
                "(matching columns by name) when persistence runs, but "
                "renames and a→b moves inside the transform body won't "
                "be traced. Add a column_mapping declaration to make "
                "per-column lineage accurate."
            ),
            location=location,
        )
    ]


__all__ = ["LintWarning", "lint_pipeline"]
