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
* **``SELECT *`` without schema / direct table read / un-parseable query** →
  opaque (no column enumeration possible).

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


def derive_column_lineage(cfg: PipelineConfig) -> ColumnLineage:
    """Derive the static column-level lineage of a pipeline.

    Best-effort: outputs the edges we *can* trace and lists assets we couldn't
    (``opaque_assets``). All three shapes (single-task / Task-DAG / graph)
    supported. Multi-source SQL queries (JOINs, multi-CTE) now resolve to
    multiple upstreams per output column rather than marking the sink opaque.
    """
    edges: list[ColumnEdge] = []
    opaque: dict[str, AssetKey] = {}  # str(key) → key, dedupe + preserve types

    def _mark_opaque(k: AssetKey | None) -> None:
        if k is not None:
            opaque.setdefault(str(k), k)

    if cfg.graph is not None:
        _process_graph(cfg.graph, edges, _mark_opaque)
    else:
        for task in cfg.effective_tasks():
            source_connection = task.source.connection
            sink_keys = [
                derive_asset_key(s.connection, s.model_dump()) for s in task.effective_sinks()
            ]
            mapping = _initial_mapping(source_connection, task.source.query)
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


def _initial_mapping(connection: str | None, query: str | None) -> _Mapping | None:
    """Resolve the source query to ``{output_col: tuple[ColumnRef, ...]}``.

    Returns ``None`` to mark the path opaque (no connection, no SQL query to
    parse, ``SELECT *`` without schema, or sqlglot couldn't parse the query).
    """
    if not connection or not query:
        return None
    resolved = extract_sql_lineage(query)
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
    """
    data = tc.model_dump()
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
    base_mapping = _initial_mapping(src.connection, src.query)

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
