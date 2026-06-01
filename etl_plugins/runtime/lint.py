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
from etl_plugins.runtime.column_lineage import (
    _apply_transform as _column_lineage_apply_transform,
)
from etl_plugins.runtime.column_lineage import (
    _initial_mapping as _column_lineage_initial_mapping,
)

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
    """Run every Phase DD/FF lint rule over a parsed pipeline config.

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

    # Phase FF: chain-aware lint that needs the upstream column mapping at
    # each transform position. Single-task / Task-DAG shapes only — graph
    # shape is handled by a future slice once we settle on how a multi-
    # source chain reports its "upstream mapping" at a transform node.
    warnings.extend(_lint_column_mapping_consistency(cfg))

    # Phase AAK (2026-05-29): surface every sink that the runtime will
    # try to auto-create on first run. Dry-run is the natural place for
    # this — the operator clicks "Dry Run" to predict what will happen,
    # and "I will create table X" deserves to land there alongside the
    # connector health checks.
    warnings.extend(_lint_auto_create_table_planned(cfg))

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


def _lint_column_mapping_consistency(cfg: PipelineConfig) -> list[LintWarning]:
    """Phase FF (ADR-0050): per-task chain walk that flags a
    ``column_mapping`` declaration whose ``source_col`` doesn't actually
    exist in the upstream mapping at that point in the transform chain.

    This catches the most common ``column_mapping`` mistake: a typo in
    the source column name, or a stale declaration that references a
    column an earlier transform already renamed away. Without this lint
    the catalog silently emits an empty-upstream row for the output
    column — technically correct but probably not what the user wanted.

    Replays the same walk that :func:`derive_column_lineage` does at
    persist time, but instead of building edges it inspects each
    transform's declaration before applying it.
    """
    if cfg.graph is not None:
        # Graph shape: punt to a future slice (see lint_pipeline docstring).
        return []

    warnings: list[LintWarning] = []
    has_explicit_tasks = bool(cfg.tasks)
    for task_idx, task in enumerate(cfg.effective_tasks()):
        source = task.source
        transforms = list(task.transforms)
        mapping = _column_lineage_initial_mapping(source.connection, source.query)
        if mapping is None:
            # If the source query isn't parseable as SQL, we have no
            # upstream mapping to consult — skip the consistency check.
            continue
        for tc_idx, tc in enumerate(transforms):
            data = tc.model_dump()
            declaration = data.get("column_mapping")
            if isinstance(declaration, dict):
                location = (
                    f"tasks.{task_idx}.transforms.{tc_idx}"
                    if has_explicit_tasks
                    else f"transforms.{tc_idx}"
                )
                for out_col, source_cols in declaration.items():
                    if not isinstance(source_cols, list):
                        continue
                    if not isinstance(out_col, str):
                        continue
                    for src_col in source_cols:
                        if not isinstance(src_col, str):
                            continue
                        if src_col not in mapping:
                            warnings.append(
                                LintWarning(
                                    code="column_mapping_unknown_source_column",
                                    message=(
                                        f"column_mapping for output '{out_col}' "
                                        f"references source column '{src_col}', "
                                        "but that name isn't in the upstream "
                                        "mapping at this point in the chain. "
                                        "Check the spelling, or whether an "
                                        "earlier transform already renamed it."
                                    ),
                                    location=location,
                                )
                            )
            next_mapping = _column_lineage_apply_transform(mapping, tc)
            if next_mapping is None:
                # The chain went opaque at this transform — every later
                # column_mapping necessarily references columns we
                # can't validate, so we stop the walk for this task.
                break
            mapping = next_mapping

    return warnings


def _lint_auto_create_table_planned(cfg: PipelineConfig) -> list[LintWarning]:
    """Phase AAK (2026-05-29): emit one info-style warning per sink
    that has ``auto_create_table=True``. Helps the operator see, from
    the dry-run, that the runtime will create a table on first run —
    no surprises on Trigger.

    Walks every shape so a graph-mode sink shows up the same as a
    linear sink. ``auto_create_if_exists`` is included in the message
    so the operator confirms which collision mode they picked
    (skip / drop / error).
    """

    def _message(table: str | None, if_exists: str) -> str:
        target = f"'{table}'" if table else "<unset table>"
        if if_exists == "drop":
            return (
                f"sink will rebuild table {target} on every run "
                "(auto_create_table=true, auto_create_if_exists='drop')"
            )
        if if_exists == "error":
            return (
                f"sink will create table {target} on first run, but "
                "fail the next run if the table already exists "
                "(auto_create_table=true, auto_create_if_exists='error')"
            )
        return (
            f"sink will create table {target} on first run if it's "
            "missing (auto_create_table=true)"
        )

    warnings: list[LintWarning] = []
    if cfg.graph is not None:
        for node in cfg.graph.nodes:
            if node.type != "sink":
                continue
            extras = node.model_dump()
            if not extras.get("auto_create_table"):
                continue
            warnings.append(
                LintWarning(
                    code="auto_create_table_planned",
                    message=_message(
                        node.table,
                        str(extras.get("auto_create_if_exists") or "skip"),
                    ),
                    location=f"graph.nodes.{node.id}",
                )
            )
        return warnings

    # Linear / task-DAG shape.
    has_explicit_tasks = bool(cfg.tasks)
    for task_idx, task in enumerate(cfg.effective_tasks()):
        for sink_idx, sink in enumerate(task.effective_sinks()):
            if not sink.auto_create_table:
                continue
            if has_explicit_tasks:
                if task.sinks:
                    location = f"tasks.{task_idx}.sinks.{sink_idx}"
                else:
                    location = f"tasks.{task_idx}.sink"
            else:
                # Single-task legacy shape — the sink lives at the top
                # level. Match the locator the rest of the file uses.
                location = "sinks." + str(sink_idx) if cfg.sinks else "sink"
            warnings.append(
                LintWarning(
                    code="auto_create_table_planned",
                    message=_message(sink.table, sink.auto_create_if_exists),
                    location=location,
                )
            )
    return warnings


__all__ = ["LintWarning", "lint_pipeline"]
