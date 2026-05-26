"""Static column-level lineage derivation (ADR-0041, Phase J).

Walks a :class:`PipelineConfig` and emits column→column edges with the hybrid
strategy agreed in ADR-0041:

* **SQL source query** → :mod:`sqlglot` to enumerate output columns + their
  source column origins (warehouse-to-warehouse pipelines, the common case).
* **Declarative transforms** (``rename`` / ``select`` / ``drop`` / ``cast`` /
  ``add_constant`` / ``filter`` / ``dedupe``) → mapping update by inspection
  (no execution). Column mappings are free here — the operators already encode
  what they do.
* **``python`` / ``sql_exec`` / unknown** → marks the downstream sinks ``opaque``
  (the UI shows "opaque" instead of fabricating partial edges).
* **SELECT ``*`` / direct table read / multi-source joins (v1)** → opaque.

Single-task and Task-DAG shapes use ``effective_tasks()``; graph shape walks
each sink back to its source through linear transform chains (single source,
no ``join`` nodes — v1; ``join`` lineage is a later slice that pairs with the
materialize engine knowing per-join column semantics).
"""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

from etl_plugins.config.models import (
    GraphConfig,
    GraphNodeConfig,
    PipelineConfig,
    TransformConfig,
)
from etl_plugins.core.asset import AssetKey, derive_asset_key
from etl_plugins.core.column_lineage import ColumnEdge, ColumnLineage, ColumnRef

# Mapping: output_column → its current upstream :class:`ColumnRef` (or ``None``
# when the column exists but has no traceable upstream, e.g. ``add_constant``
# or a complex expression). A whole-mapping ``None`` value means the task is
# opaque from this point on (e.g. ``python`` transform encountered).
_Mapping = dict[str, "ColumnRef | None"]


def derive_column_lineage(cfg: PipelineConfig) -> ColumnLineage:
    """Derive the static column-level lineage of a pipeline.

    Best-effort: outputs the edges we *can* trace and lists assets we couldn't
    (``opaque_assets``). Both shapes (single-task / Task-DAG / graph) supported;
    multi-source / join graphs mark downstream sinks opaque until the join
    column lineage slice lands.
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
            src_key = derive_asset_key(task.source.connection, task.source.model_dump())
            sink_keys = [
                derive_asset_key(s.connection, s.model_dump()) for s in task.effective_sinks()
            ]
            mapping = _initial_mapping(src_key, task.source.query)
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


def _initial_mapping(src_key: AssetKey | None, query: str | None) -> _Mapping | None:
    """Map the source's output columns to their origin :class:`ColumnRef`.

    Returns ``None`` to mark the path opaque (no source asset, no SQL query to
    parse, ``SELECT *``, a join, or sqlglot couldn't parse the query).
    """
    if src_key is None or not query:
        return None
    cols = _parse_select_columns(query)
    if cols is None:
        return None
    return {out: (ColumnRef(src_key, src) if src else None) for out, src in cols.items()}


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
    """One transform → updated column mapping. ``None`` ⇒ opaque from here."""
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
        return {**mapping, col: None}  # new column, no upstream
    if tc.type in {"cast", "filter", "dedupe"}:
        # cast = type only; filter/dedupe = row-level — none touch the column set.
        return mapping
    # python / sql_exec / anything we don't recognize → opaque.
    return None


def _emit_edges(mapping: _Mapping, sink_key: AssetKey | None, edges: list[ColumnEdge]) -> None:
    if sink_key is None:
        return
    for col, upstream in mapping.items():
        edges.append(
            ColumnEdge(
                downstream=ColumnRef(sink_key, col),
                upstreams=(upstream,) if upstream is not None else (),
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
    graphs mark all sink keys opaque."""
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
    src_key = _node_asset_key(src)
    base_mapping = _initial_mapping(src_key, src.query)

    for snk in sinks:
        snk_key = _node_asset_key(snk)
        if base_mapping is None:
            mark_opaque(snk_key)
            continue
        # Walk from sink back to source, collecting transform nodes in order.
        chain = _path_transforms(snk.id, src.id, by_id, incoming)
        if chain is None:
            # Unreachable or branched path → opaque for safety.
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


# ---------- SQL parsing ----------


def _parse_select_columns(query: str) -> dict[str, str | None] | None:
    """Return ``{output_alias: source_column_or_None}`` for a simple ``SELECT …
    FROM <table>``. ``None`` value = output column exists but its upstream is
    not simple-traceable (function call, arithmetic). Returns ``None`` (top
    level) for un-parseable, non-``SELECT``, ``SELECT *``, or multi-source
    (``JOIN`` / subquery) queries — those mark the path opaque."""
    try:
        parsed = sqlglot.parse_one(query)
    except Exception:
        return None
    if not isinstance(parsed, exp.Select):
        return None
    # sqlglot uses ``from_`` (Python keyword) for the FROM clause; ``joins``
    # are a sibling arg on the Select itself (not nested under from_).
    if parsed.args.get("from_") is None:
        return None
    if parsed.args.get("joins"):
        return None  # multi-source JOIN — v1 marks opaque

    result: dict[str, str | None] = {}
    for col_exp in parsed.expressions:
        if isinstance(col_exp, exp.Star):
            return None  # SELECT * — can't enumerate columns
        alias = col_exp.alias_or_name
        inner = col_exp.unalias() if isinstance(col_exp, exp.Alias) else col_exp
        if isinstance(inner, exp.Column):
            result[alias] = inner.name
        else:
            result[alias] = None  # complex expression — column exists, upstream opaque
    return result


__all__ = ["derive_column_lineage"]
