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

import re
from dataclasses import dataclass

from etl_plugins.config.models import PipelineConfig, TaskConfig, TransformConfig
from etl_plugins.core.templating import template_paths
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
_OPAQUE_TRANSFORM_TYPES = frozenset({"python", "custom_python", "sql_exec", "sql"})

#: Transform types that run arbitrary code *per record* and can therefore
#: raise on a single bad row. Without a ``dlq:`` such a raise fails the
#: whole run — the target of the ``dlq_recommended`` rule (ADR-0076).
#: ``sql_exec`` is excluded: it's a one-shot statement, not per-record, so
#: a DLQ (record-level routing) wouldn't help it.
_PER_RECORD_CODE_TRANSFORM_TYPES = frozenset({"python", "custom_python"})


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

    # Phase DLQ-8 (2026-06-04, ADR-0076): a per-record code transform with
    # no DLQ fails the whole run on a single bad record. Nudge the operator
    # to configure a dead-letter queue so failures route aside instead.
    warnings.extend(_lint_dlq_recommended(cfg))

    # ADR-0094 (2026-06-11): a ``sql`` transform with ``pushdown: true``
    # silently runs locally (DuckDB) when the task shape doesn't qualify —
    # explain why at dry-run so "I asked the warehouse to do it" doesn't
    # quietly become "Python did it".
    warnings.extend(_lint_sql_pushdown_ineligible(cfg))

    # ADR-0097/0098 (2026-06-17): the deferred ``{{ map.* }}`` / ``{{ xcom.* }}``
    # namespaces are resolved per task at execution time. A reference that
    # names no ``expand`` key / no upstream task never gets substituted — the
    # literal token reaches the connector (or the per-task render raises
    # ConfigError). Surface it statically so the typo shows up at dry-run.
    warnings.extend(_lint_deferred_template_refs(cfg))

    # ADR-0099: a ``proc_call`` step is opaque — without a declared
    # ``reads``/``writes`` it contributes nothing to the catalog. Nudge the
    # user to annotate it so the lineage graph isn't silently missing the
    # tables a stored procedure touches.
    warnings.extend(_lint_proc_call_lineage(cfg))

    return warnings


def _lint_proc_call_lineage(cfg: PipelineConfig) -> list[LintWarning]:
    """Advisory: a ``proc_call`` task with neither ``reads`` nor ``writes``
    declared is invisible to the catalog (the procedure body is opaque)."""
    out: list[LintWarning] = []
    if not cfg.tasks:
        return out
    for idx, task in enumerate(cfg.tasks):
        if task.kind == "proc_call" and not task.reads and not task.writes:
            out.append(
                LintWarning(
                    code="proc_call_lineage_recommended",
                    message=(
                        f"proc_call step {task.name!r} declares no reads/writes, so "
                        "the procedure's tables won't appear in the catalog. Add "
                        "'reads'/'writes' (the tables it touches) for lineage."
                    ),
                    location=f"tasks.{idx}",
                )
            )
    return out


def _has_per_record_code_transform(cfg: PipelineConfig) -> bool:
    """True if any shape contains a ``python`` / ``custom_python`` transform."""
    if cfg.graph is not None:
        return any(
            node.type == "transform"
            and node.transform is not None
            and node.transform.type in _PER_RECORD_CODE_TRANSFORM_TYPES
            for node in cfg.graph.nodes
        )
    if cfg.tasks:
        return any(
            tc.type in _PER_RECORD_CODE_TRANSFORM_TYPES
            for task in cfg.tasks
            for tc in task.transforms
        )
    return any(tc.type in _PER_RECORD_CODE_TRANSFORM_TYPES for tc in cfg.transforms)


def _lint_dlq_recommended(cfg: PipelineConfig) -> list[LintWarning]:
    """Pipeline-level rule (ADR-0076): a ``python`` / ``custom_python``
    transform raises per record. With no ``dlq:`` configured, one bad row
    aborts the entire run (``TransformError``). Recommend a DLQ so the bad
    records route aside and the rest keep flowing — the canonical
    partial-success pattern (Phase II).

    Advisory only; fires once per pipeline. ``location`` is ``None`` — the
    fix lives in pipeline settings (the ``dlq`` block), not at any one
    node, so there's nothing for the builder to jump to.
    """
    if cfg.dlq is not None:
        return []
    if not _has_per_record_code_transform(cfg):
        return []
    return [
        LintWarning(
            code="dlq_recommended",
            message=(
                "This pipeline has a python/custom_python transform but no "
                "dead-letter queue (dlq) is configured. If the transform "
                "raises on a single record, the entire run fails. Configure "
                "a dlq to route failing records aside and keep processing "
                "the rest."
            ),
            location=None,
        )
    ]


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
    # ``sql`` dataset transforms (ADR-0093): the body is SQL, so the
    # Phase X sqlglot walker infers lineage automatically — the nudge is
    # noise unless the query is one sqlglot can't analyse.
    if tc.type == "sql" and _sql_transform_analysable(data):
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


def _sql_transform_analysable(data: dict[str, object]) -> bool:
    """True when the sqlglot lineage walker can read this ``sql``
    transform's query — mirrors the runtime inference in
    :mod:`etl_plugins.runtime.column_lineage`. The lint runs without the
    upstream column set, so a single fake column stands in for the view
    schema (enough for ``SELECT *`` expansion and parseability)."""
    from etl_plugins.runtime.sql_lineage import extract_sql_lineage

    query = data.get("query")
    view = data.get("view") or "input"
    if not isinstance(query, str) or not query.strip() or not isinstance(view, str):
        return False
    return extract_sql_lineage(query, dialect="duckdb", schema={view: {"_c": "TEXT"}}) is not None


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
        if source is None:  # operator kinds (ADR-0099) — no source/columns
            continue
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
            f"sink will create table {target} on first run if it's missing (auto_create_table=true)"
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


#: Mirrors ``etl_plugins.core.pipeline._PUSHDOWN_TABLE_RE`` — plain
#: (optionally schema-qualified) identifier the pushdown may inline.
_PLAIN_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _sql_pushdown_requested(tc: TransformConfig) -> bool:
    """True if this is a ``sql`` transform with ``pushdown: true`` (an
    ``extra``-field opt-in, ADR-0094)."""
    return tc.type == "sql" and tc.model_dump().get("pushdown") is True


def _lint_sql_pushdown_ineligible(cfg: PipelineConfig) -> list[LintWarning]:
    """ELT-pushdown eligibility check (ADR-0094, 2026-06-11).

    ``{type: sql, pushdown: true}`` asks the runtime to compose the task
    into one in-database ``INSERT INTO … WITH <view> AS (<source>) <query>``.
    The runtime falls back to the local DuckDB path when the task shape
    doesn't qualify — correct but surprising, so this rule names the first
    blocking condition at dry-run. Only config-visible conditions are
    checked here (connector ``supports_sql_pushdown`` needs a live
    registry; the run's ``data_paths`` reports the actual path taken).
    """
    warnings: list[LintWarning] = []

    if cfg.graph is not None:
        # Graph shape: pushdown engages only for the trivial chain
        # source → sql(pushdown) → sink (the shape the builder UI emits
        # for a simple pipeline). Mirror ``Pipeline._try_graph_fast_paths``.
        nodes = cfg.graph.nodes
        g_sources = [n for n in nodes if n.type == "source"]
        g_sinks = [n for n in nodes if n.type == "sink"]
        for node in nodes:
            if (
                node.type != "transform"
                or node.transform is None
                or not _sql_pushdown_requested(node.transform)
            ):
                continue
            g_blocker: str | None = None
            if len(nodes) != 3 or len(g_sources) != 1 or len(g_sinks) != 1:
                g_blocker = (
                    "pushdown composes only the trivial source → sql → sink "
                    f"chain (this graph has {len(nodes)} nodes)"
                )
            elif any(e.when for e in cfg.graph.edges):
                g_blocker = "an edge has a 'when' predicate"
            elif {(e.from_node, e.to_node) for e in cfg.graph.edges} != {
                (g_sources[0].id, node.id),
                (node.id, g_sinks[0].id),
            }:
                g_blocker = "the nodes aren't wired source → sql → sink"
            elif g_sinks[0].connection != g_sources[0].connection:
                g_blocker = (
                    f"source connection {g_sources[0].connection!r} and sink "
                    f"connection {g_sinks[0].connection!r} differ — pushdown "
                    "runs inside ONE database"
                )
            elif g_sinks[0].mode != "append":
                g_blocker = f"sink mode is {g_sinks[0].mode!r} — pushdown needs 'append'"
            elif not g_sinks[0].table or not _PLAIN_TABLE_RE.match(g_sinks[0].table):
                g_blocker = f"sink table {g_sinks[0].table!r} is not a plain identifier"
            if g_blocker is None:
                continue
            warnings.append(
                LintWarning(
                    code="sql_pushdown_ineligible",
                    message=(
                        f"pushdown: true won't engage — {g_blocker}. The transform "
                        "will run locally (DuckDB) instead of in-database."
                    ),
                    location=f"graph.nodes.{node.id}",
                )
            )
        return warnings

    has_explicit_tasks = bool(cfg.tasks)
    for task_idx, task in enumerate(cfg.effective_tasks()):
        if task.source is None:  # operator kinds (ADR-0099) — no transforms/source
            continue
        requested = [
            (tc_idx, tc) for tc_idx, tc in enumerate(task.transforms) if _sql_pushdown_requested(tc)
        ]
        if not requested:
            continue
        sinks = task.effective_sinks()
        blocker: str | None = None
        if len(task.transforms) != 1:
            blocker = (
                "the sql transform must be the task's ONLY transform "
                f"(this task has {len(task.transforms)})"
            )
        elif len(sinks) != 1:
            blocker = f"the task must have exactly one sink (this task has {len(sinks)})"
        elif sinks[0].connection != task.source.connection:
            blocker = (
                f"source connection {task.source.connection!r} and sink "
                f"connection {sinks[0].connection!r} differ — pushdown runs "
                "inside ONE database"
            )
        elif sinks[0].mode != "append":
            blocker = f"sink mode is {sinks[0].mode!r} — pushdown needs 'append'"
        elif sinks[0].when is not None:
            blocker = "the sink has a 'when' routing predicate"
        elif sinks[0].model_dump().get("pre_sql"):
            blocker = "the sink has 'pre_sql' (it must stay in the write transaction)"
        elif not sinks[0].table or not _PLAIN_TABLE_RE.match(sinks[0].table):
            blocker = f"sink table {sinks[0].table!r} is not a plain identifier"
        if blocker is None:
            continue
        for tc_idx, _tc in requested:
            if has_explicit_tasks:
                location = f"tasks.{task_idx}.transforms.{tc_idx}"
            else:
                location = f"transforms.{tc_idx}"
            warnings.append(
                LintWarning(
                    code="sql_pushdown_ineligible",
                    message=(
                        f"pushdown: true won't engage — {blocker}. The transform "
                        "will run locally (DuckDB) instead of in-database."
                    ),
                    location=location,
                )
            )
    return warnings


def _task_renderable(task: TaskConfig) -> dict[str, object]:
    """The templatable content of one task, mirroring the fields the core
    ``Pipeline._render_task_xcom`` resolves at execution time (source query +
    options, each sink's table + options + pre_sql). Dumping the whole source /
    sink models is a safe superset — a stray ``{{ map }}`` in any of them would
    likewise never be substituted."""
    return {
        "source": task.source.model_dump() if task.source is not None else None,
        "sinks": [s.model_dump() for s in task.effective_sinks()],
        # Operator kinds (ADR-0099): statements / proc args are also templatable.
        "statements": list(task.statements),
        "procedure": task.procedure,
        "args": list(task.args),
    }


def _lint_deferred_template_refs(cfg: PipelineConfig) -> list[LintWarning]:
    """ADR-0097/0098: ``{{ map.<key> }}`` / ``{{ xcom.<task>.<key> }}`` are
    deferred namespaces resolved per task at execution time. A reference that
    names no ``expand`` key (map) or no upstream task (xcom) never resolves —
    the literal token reaches the connector, or the per-task render raises
    ``ConfigError``. This rule catches both statically.

    Task-DAG shape only — the legacy single-task shape has no other tasks to
    pull xcom from and no ``expand`` field, and graph-node mapping/xcom is a
    future slice (ADR-0098 follow-up).
    """
    if not cfg.tasks:
        return []

    warnings: list[LintWarning] = []
    known_tasks = {t.name for t in cfg.tasks if t.name}
    # Transitive ``depends_on`` closure per task — an xcom pull is only safe
    # from a task guaranteed to have run first (a topological ancestor).
    upstream = _transitive_upstream(cfg.tasks)

    for task_idx, task in enumerate(cfg.tasks):
        location = f"tasks.{task_idx}"
        paths = template_paths(_task_renderable(task))
        expand_keys = set(task.expand)
        for path in sorted(paths):
            head, _, rest = path.partition(".")
            if head == "map":
                key = rest.split(".", 1)[0] if rest else ""
                if not task.expand:
                    warnings.append(
                        LintWarning(
                            code="map_ref_without_expand",
                            message=(
                                f"task {task.name!r} references {{{{ map.{key} }}}} "
                                "but declares no 'expand' — the token will not be "
                                "substituted. Add an 'expand' mapping (dynamic task "
                                "mapping, ADR-0098) or remove the reference."
                            ),
                            location=location,
                        )
                    )
                elif key not in expand_keys:
                    warnings.append(
                        LintWarning(
                            code="map_ref_unknown_key",
                            message=(
                                f"task {task.name!r} references {{{{ map.{key} }}}} "
                                f"but 'expand' declares only {sorted(expand_keys)}. "
                                "The token will not be substituted."
                            ),
                            location=location,
                        )
                    )
            elif head == "xcom":
                ref_task = rest.split(".", 1)[0] if rest else ""
                if ref_task not in known_tasks:
                    warnings.append(
                        LintWarning(
                            code="xcom_ref_unknown_task",
                            message=(
                                f"task {task.name!r} references "
                                f"{{{{ xcom.{ref_task}.* }}}} but no task named "
                                f"{ref_task!r} exists. The per-task render will "
                                "raise at execution time."
                            ),
                            location=location,
                        )
                    )
                elif ref_task != task.name and ref_task not in upstream[task.name]:
                    warnings.append(
                        LintWarning(
                            code="xcom_ref_not_upstream",
                            message=(
                                f"task {task.name!r} pulls xcom from {ref_task!r}, "
                                "which is not an upstream dependency — it may not "
                                "have run yet when this task executes. Add it to "
                                "'depends_on' so ordering is guaranteed."
                            ),
                            location=location,
                        )
                    )
    return warnings


def _transitive_upstream(tasks: list[TaskConfig]) -> dict[str, set[str]]:
    """Map each task name to the set of all its transitive ``depends_on``
    ancestors. Tolerates cycles / dangling edges (those are caught by the
    pipeline builder, not here)."""
    direct = {t.name: set(t.depends_on) for t in tasks if t.name}
    names = set(direct)
    closure: dict[str, set[str]] = {name: set() for name in names}
    for name in names:
        seen: set[str] = set()
        stack = list(direct.get(name, ()))
        while stack:
            up = stack.pop()
            if up in seen or up == name:
                continue
            seen.add(up)
            stack.extend(direct.get(up, ()))
        closure[name] = seen
    return closure


__all__ = ["LintWarning", "lint_pipeline"]
