"""Static column-level lineage derivation (ADR-0041, Phase J + Phase X).

Walks a :class:`PipelineConfig` and emits column→column edges using the hybrid
strategy from ADR-0041:

* **SQL source query** → :mod:`etl_plugins.runtime.sql_lineage.extract_sql_lineage`
  (sqlglot's ``lineage`` walker). Handles CTEs, subqueries, joins, UNION,
  window functions, CASE / COALESCE — every output column is mapped to the
  full *set* of leaf-table columns that feed it. This is the Phase X upgrade
  over the original hand-written FROM/JOIN matcher.
* **Declarative transforms** (``rename`` / ``select`` / ``drop`` / ``cast`` /
  ``add_constant`` / ``filter`` / ``dedupe`` / ``assert``) → mapping update
  by inspection. The operators already declare what they do, so no execution
  is needed.
* **``python`` / ``custom_python`` / ``sql_exec`` / unknown** → marks the
  downstream sinks ``opaque``. We never fabricate edges through user code.
* **``SELECT *``** → expands when the caller passes a ``schemas`` map
  (``{connection: {table: {column: type}}}``) so sqlglot can resolve the
  star projection. Without a schema map it stays opaque, same as before.
  The service worker (Phase Z, ADR-0045) wires this via the optional
  ``SchemaInspector`` connector capability.
* **direct table read / un-parseable query** → opaque (no enumeration).

The same logic powers both pipeline shapes:

* single-task / Task-DAG: every task derives its own mapping through
  ``effective_tasks()``.
* graph: each sink walks back to a source through a linear transform chain
  (graph ``join`` nodes still mark all sinks opaque — multi-source graph
  lineage is a separate slice from the SQL multi-source case handled here).
"""

from __future__ import annotations

from typing import Any

from etl_plugins.config.models import (
    GraphConfig,
    GraphNodeConfig,
    PipelineConfig,
    TransformConfig,
)
from etl_plugins.core.asset import AssetKey, derive_asset_key
from etl_plugins.core.column_lineage import ColumnEdge, ColumnLineage, ColumnRef
from etl_plugins.runtime.sql_lineage import extract_sql_lineage

# Per-output upstream set: a tuple (immutable, stable order) of every
# :class:`ColumnRef` that contributes to one output column. Empty tuple
# means "column exists, upstream not traceable" (e.g. ``add_constant`` or a
# literal). The whole-mapping ``None`` value still means "opaque from this
# point on" (e.g. a ``python`` transform was encountered).
_Mapping = dict[str, tuple[ColumnRef, ...]]


def derive_column_lineage(
    cfg: PipelineConfig,
    *,
    schemas: dict[str, dict[str, dict[str, str]]] | None = None,
) -> ColumnLineage:
    """Derive the static column-level lineage of a pipeline.

    Best-effort: outputs the edges we *can* trace and lists assets we couldn't
    (``opaque_assets``). All three shapes (single-task / Task-DAG / graph)
    supported. Multi-source SQL queries (JOINs, multi-CTE) now resolve to
    multiple upstreams per output column rather than marking the sink opaque.

    Args:
        cfg: The pipeline configuration.
        schemas: Optional ``{connection_name: {table_name: {column_name: type}}}``.
            When provided, ``SELECT *`` projections expand against the
            schema of the source's connection so star queries trace per
            column. Without it (default), ``SELECT *`` remains opaque.
            Producers (the service worker) plug this in by fetching schema
            via the optional :class:`SchemaInspector` connector capability.
    """
    edges: list[ColumnEdge] = []
    opaque: dict[str, AssetKey] = {}  # str(key) → key, dedupe + preserve types

    def _mark_opaque(k: AssetKey | None) -> None:
        if k is not None:
            opaque.setdefault(str(k), k)

    if cfg.graph is not None:
        _process_graph(cfg.graph, edges, _mark_opaque, schemas)
    else:
        for task in cfg.effective_tasks():
            source_connection = task.source.connection
            sink_keys = [
                derive_asset_key(s.connection, s.model_dump()) for s in task.effective_sinks()
            ]
            mapping = _initial_mapping(source_connection, task.source.query, schemas)
            if mapping is None:
                for sk in sink_keys:
                    _mark_opaque(sk)
                continue
            mapping = _apply_transform_chain(mapping, task.transforms)
            if mapping is None:
                for sk in sink_keys:
                    _mark_opaque(sk)
                continue
            for sk in sink_keys:
                _emit_edges(mapping, sk, edges)

    return ColumnLineage(edges=edges, opaque_assets=list(opaque.values()))


# ---------- single-task / per-sink core ----------


def _initial_mapping(
    connection: str | None,
    query: str | None,
    schemas: dict[str, dict[str, dict[str, str]]] | None = None,
) -> _Mapping | None:
    """Resolve the source query to ``{output_col: tuple[ColumnRef, ...]}``.

    Returns ``None`` to mark the path opaque (no connection, no SQL query to
    parse, ``SELECT *`` without schema, or sqlglot couldn't parse the query).

    When ``schemas`` is supplied, the lookup ``schemas[connection]`` is fed
    to ``extract_sql_lineage`` so ``SELECT *`` projections expand. The
    schema entry is in the same shape sqlglot's ``qualify`` expects —
    ``{table: {column: type}}``.
    """
    if not connection or not query:
        return None
    schema = schemas.get(connection) if schemas else None
    resolved = extract_sql_lineage(query, schema=schema)
    if resolved is None:
        return None
    out: _Mapping = {}
    for output_col, leaves in resolved.items():
        refs = tuple(
            ColumnRef(AssetKey.of(connection, tbl), col) for tbl, col in leaves if tbl and col
        )
        out[output_col] = refs
    return out


def _apply_transform_chain(mapping: _Mapping, transforms: list[TransformConfig]) -> _Mapping | None:
    """Walk the transform list, returning the final mapping or ``None`` if any
    transform makes the path opaque (``python``, ``sql_exec``, unknown type)."""
    for tc in transforms:
        next_mapping = _apply_transform(mapping, tc)
        if next_mapping is None:
            return None
        mapping = next_mapping
    return mapping


def _apply_transform(mapping: _Mapping, tc: TransformConfig) -> _Mapping | None:
    """One transform → updated column mapping. ``None`` ⇒ opaque from here.

    Multi-upstream columns flow through every safe transform unchanged: a
    ``rename`` of ``a → id`` keeps the original upstream tuple, just under a
    new output key. ``select``/``drop`` filter the output set without
    rewriting upstreams. ``cast``/``filter``/``dedupe``/``assert`` are
    structural pass-throughs.

    Phase CC (ADR-0047, 2026-05-29): when the transform declares an
    explicit ``column_mapping`` it overrides the type-based handling.
    The declaration is the user's ground-truth for output → source
    column attribution; we honour it verbatim. This is the only way to
    get *accurate* (not just heuristic) lineage through a transform
    whose body the static analyser can't read — ``python`` /
    ``custom_python`` / ``sql_exec`` — when the python code does
    something the schema-passthrough fallback can't guess (column
    rename inside python, a-column-feeds-b-column moves, etc.).
    """
    data = tc.model_dump()
    column_mapping = data.get("column_mapping")
    if column_mapping is not None:
        explicit = _apply_explicit_column_mapping(mapping, column_mapping)
        if explicit is not None:
            return explicit
        # A malformed declaration (not a dict, etc.) → don't trust the
        # hint, fall through to the type-based handler below. For opaque
        # types like python that means we still end up opaque, but at
        # least we didn't silently propagate a stale mapping.
    if tc.type == "rename":
        renames = data.get("mapping") or {}
        return {renames.get(k, k): v for k, v in mapping.items()}
    if tc.type == "select":
        keep = set(data.get("columns") or [])
        return {k: v for k, v in mapping.items() if k in keep}
    if tc.type == "drop":
        gone = set(data.get("columns") or [])
        return {k: v for k, v in mapping.items() if k not in gone}
    if tc.type == "add_constant":
        col = data.get("column")
        if not col:
            return mapping
        return {**mapping, col: ()}  # new column, empty upstream tuple
    if tc.type in {"cast", "filter", "dedupe", "assert"}:
        # cast = type only; filter/dedupe/assert = row-level decisions
        # (assert may fail the run, but it never reshapes the columns).
        return mapping
    # python / custom_python / sql_exec / anything we don't recognize → opaque.
    return None


def _apply_explicit_column_mapping(
    mapping: _Mapping,
    declaration: object,
) -> _Mapping | None:
    """Honor a user-declared ``column_mapping`` on a transform (Phase CC).

    Form: ``{output_col: [source_col_name, ...]}``. Each ``source_col_name``
    must match a key currently in the upstream :data:`_Mapping`; the new
    output column inherits the union of those upstreams. An empty list
    means *"this is a new column, no upstream"* — the same semantics as
    ``add_constant``. A malformed declaration is treated as a no-op so a
    user mistake degrades to "lineage proceeds without the hint" rather
    than corrupting the chain.

    This is a **replace-mode** operation: only the columns listed in the
    declaration appear in the new mapping. The user is responsible for
    naming every output column they want the catalog to attribute. The
    rationale is determinism — a merge mode where unlisted columns "leak
    through from before the python ran" makes the catalog brittle when
    code is added or removed. The trade-off is verbosity, which is fine
    because the declaration only gets written for transforms whose body
    the static analyser already can't read.
    """
    if not isinstance(declaration, dict):
        return None  # bad shape — signal "I gave up", caller falls through
    new_mapping: _Mapping = {}
    for out_col, source_cols in declaration.items():
        if not isinstance(out_col, str) or not isinstance(source_cols, list):
            continue
        upstreams: list[ColumnRef] = []
        seen: set[tuple[AssetKey, str]] = set()
        for src_col in source_cols:
            if not isinstance(src_col, str):
                continue
            for ref in mapping.get(src_col, ()):
                key = (ref.asset, ref.column)
                if key in seen:
                    continue
                seen.add(key)
                upstreams.append(ref)
        new_mapping[out_col] = tuple(upstreams)
    return new_mapping


def _emit_edges(mapping: _Mapping, sink_key: AssetKey | None, edges: list[ColumnEdge]) -> None:
    if sink_key is None:
        return
    for col, upstreams in mapping.items():
        edges.append(
            ColumnEdge(
                downstream=ColumnRef(sink_key, col),
                upstreams=upstreams,
            )
        )


# ---------- graph shape ----------


def _process_graph(
    graph: GraphConfig,
    edges: list[ColumnEdge],
    mark_opaque: Any,  # Callable[[AssetKey | None], None]
    schemas: dict[str, dict[str, dict[str, str]]] | None = None,
) -> None:
    """v1: linear graph (one source, transform/sink nodes only) → walk each
    sink back to the source via incoming edges. ``join`` nodes or multi-source
    graphs mark all sink keys opaque (separate slice for graph-level joins —
    SQL-level joins are now handled inside :func:`_initial_mapping`)."""
    by_id = {n.id: n for n in graph.nodes}
    incoming: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for edge in graph.edges:
        incoming[edge.to_node].append(edge.from_node)

    sources = [n for n in graph.nodes if n.type == "source"]
    sinks = [n for n in graph.nodes if n.type == "sink"]
    has_join_or_multi = len(sources) != 1 or any(n.type == "join" for n in graph.nodes)

    if has_join_or_multi:
        for snk in sinks:
            mark_opaque(_node_asset_key(snk))
        return

    src = sources[0]
    base_mapping = _initial_mapping(src.connection, src.query, schemas)

    for snk in sinks:
        snk_key = _node_asset_key(snk)
        if base_mapping is None:
            mark_opaque(snk_key)
            continue
        chain = _path_transforms(snk.id, src.id, by_id, incoming)
        if chain is None:
            mark_opaque(snk_key)
            continue
        path_mapping: _Mapping = dict(base_mapping)
        path_opaque = False
        for transform_cfg in chain:
            next_mapping = _apply_transform(path_mapping, transform_cfg)
            if next_mapping is None:
                path_opaque = True
                break
            path_mapping = next_mapping
        if path_opaque:
            mark_opaque(snk_key)
        else:
            _emit_edges(path_mapping, snk_key, edges)


def _node_asset_key(node: GraphNodeConfig) -> AssetKey | None:
    return derive_asset_key(node.connection, node.model_dump())


def _path_transforms(
    sink_id: str,
    source_id: str,
    by_id: dict[str, GraphNodeConfig],
    incoming: dict[str, list[str]],
) -> list[TransformConfig] | None:
    """Walk parent chain ``sink → … → source`` collecting transform configs in
    source→sink order. Returns ``None`` if any node has ≠1 incoming edge (we
    only handle the linear case in v1; join nodes were already filtered)."""
    chain: list[TransformConfig] = []
    cur = sink_id
    seen: set[str] = set()
    while cur != source_id:
        if cur in seen:
            return None  # cycle (should be caught by GraphConfig validation)
        seen.add(cur)
        ups = incoming.get(cur, [])
        if len(ups) != 1:
            return None  # only linear chains in v1
        parent_id = ups[0]
        cur_node = by_id[cur]
        if cur_node.type == "transform" and cur_node.transform is not None:
            chain.append(cur_node.transform)
        cur = parent_id
    chain.reverse()
    return chain


__all__ = ["derive_column_lineage"]
