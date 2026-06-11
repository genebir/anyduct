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
* graph: a topological walk computes one mapping per node — ``join``
  nodes union their inputs' columns, ``aggregate`` nodes reshape to
  group keys + aggregation outputs, ``sql`` transform nodes run the
  sqlglot inference (2026-06-12; v1 marked join/multi-source opaque).
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
    if tc.type == "sql":
        # Dataset-level SQL transform (ADR-0093): the body is SQL, which
        # the Phase X sqlglot worker can read — no manual column_mapping
        # needed for the common case. Analysis failure falls through to
        # opaque (and the executor's schema-passthrough fallback).
        inferred = _apply_sql_dataset_transform(mapping, data)
        if inferred is not None:
            return inferred
    # python / custom_python / sql_exec / unanalysable sql / anything we
    # don't recognize → opaque.
    return None


def _apply_sql_dataset_transform(mapping: _Mapping, data: dict[str, Any]) -> _Mapping | None:
    """Column lineage through a ``sql`` dataset transform (ADR-0093).

    The in-flight rows are registered as one relation (``view``, default
    ``input``) whose columns are exactly the upstream mapping's keys — so
    we hand sqlglot that schema, run the same lineage walker the source
    query uses (Phase X), and splice each output column's ``(view, col)``
    leaves back through the upstream mapping. Leaves that don't resolve
    to the view (inline CTE literals, VALUES) contribute no upstream —
    same semantics as ``add_constant``. Returns ``None`` when sqlglot
    can't analyse the query (caller treats the transform as opaque).
    """
    query = data.get("query")
    view = data.get("view") or "input"
    if not isinstance(query, str) or not query.strip() or not isinstance(view, str):
        return None
    if not mapping:
        return None
    # The sql transform executes in DuckDB (the local path) — parse with
    # its dialect so QUALIFY etc. resolve.
    schema = {view: dict.fromkeys(mapping, "TEXT")}
    resolved = extract_sql_lineage(query, dialect="duckdb", schema=schema)
    if resolved is None:
        return None
    view_lower = view.lower()
    out: _Mapping = {}
    for output_col, leaves in resolved.items():
        upstreams: list[ColumnRef] = []
        seen: set[tuple[AssetKey, str]] = set()
        for tbl, col in leaves:
            if not tbl or (tbl.split(".")[-1].lower() != view_lower):
                continue
            for ref in mapping.get(col, ()):
                key = (ref.asset, ref.column)
                if key in seen:
                    continue
                seen.add(key)
                upstreams.append(ref)
        out[output_col] = tuple(upstreams)
    return out


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
    """Topological walk computing one column mapping per node.

    Every node type is declarative enough to propagate:

    * ``source`` — sqlglot over its query (:func:`_initial_mapping`);
    * ``transform`` — :func:`_apply_transform` (incl. the sql dataset
      transform's sqlglot inference);
    * ``join`` — the runtime hash-join merges record dicts, so the output
      columns are the UNION of both inputs' columns; a name present on
      both sides gets the union of both upstreams (honest "could come
      from either" attribution);
    * ``aggregate`` — ``group_by`` keys keep their upstream, each
      aggregation's output column traces to its input ``column``
      (``count`` without a column ⇒ no upstream);
    * ``sink`` — passthrough of its single input (edges emitted).

    ``None`` anywhere (unparseable source SQL, an opaque transform,
    ``sql_exec``) poisons everything downstream of it — those sinks are
    marked opaque. Edge ``when`` predicates filter rows, not columns.
    """
    incoming: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for edge in graph.edges:
        incoming[edge.to_node].append(edge.from_node)

    mappings: dict[str, _Mapping | None] = {}
    for node in _topo_order(graph.nodes, incoming):
        parents = incoming[node.id]
        parent_mappings = [mappings.get(p) for p in parents]
        mappings[node.id] = _node_mapping(node, parent_mappings, schemas)

    for snk in (n for n in graph.nodes if n.type == "sink"):
        mapping = mappings.get(snk.id)
        if mapping is None:
            mark_opaque(_node_asset_key(snk))
        else:
            _emit_edges(mapping, _node_asset_key(snk), edges)


def _node_mapping(
    node: GraphNodeConfig,
    parents: list[_Mapping | None],
    schemas: dict[str, dict[str, dict[str, str]]] | None,
) -> _Mapping | None:
    if node.type == "source":
        return _initial_mapping(node.connection, node.query, schemas)
    if node.type == "transform" and node.transform is not None and len(parents) == 1:
        return None if parents[0] is None else _apply_transform(parents[0], node.transform)
    if node.type == "join" and len(parents) >= 2:
        return _join_mappings(parents)
    if node.type == "aggregate" and len(parents) == 1:
        return None if parents[0] is None else _aggregate_mapping(parents[0], node)
    if node.type == "sink" and len(parents) == 1:
        return parents[0]
    # sql_exec (zero-record side effect) / malformed wiring → opaque.
    return None


def _join_mappings(parents: list[_Mapping | None]) -> _Mapping | None:
    """Fan-in merge: union of columns; same-named columns union upstreams."""
    if not parents or any(p is None for p in parents):
        return None
    out: _Mapping = {}
    for parent in parents:
        assert parent is not None  # narrowed above; mypy can't see it
        for col, refs in parent.items():
            if col not in out:
                out[col] = refs
                continue
            seen = {(r.asset, r.column) for r in out[col]}
            merged = list(out[col])
            for ref in refs:
                if (ref.asset, ref.column) not in seen:
                    seen.add((ref.asset, ref.column))
                    merged.append(ref)
            out[col] = tuple(merged)
    return out


def _aggregate_mapping(mapping: _Mapping, node: GraphNodeConfig) -> _Mapping:
    """Aggregate reshapes columns: group keys + one column per aggregation.

    (Previously the v1 walker silently skipped aggregate nodes, leaving
    the pre-aggregation column set in the lineage — wrong columns on the
    sink. This makes the node's actual output shape authoritative.)
    """
    out: _Mapping = {}
    for key in node.group_by or []:
        out[key] = mapping.get(key, ())
    for agg in node.aggregations or []:
        out[agg.name] = mapping.get(agg.column, ()) if agg.column else ()
    return out


def _topo_order(
    nodes: list[GraphNodeConfig],
    incoming: dict[str, list[str]],
) -> list[GraphNodeConfig]:
    """Kahn's algorithm — GraphConfig validation already guarantees the
    graph is acyclic, so every node is emitted exactly once."""
    by_id = {n.id: n for n in nodes}
    degree = {n.id: len(incoming[n.id]) for n in nodes}
    downstream: dict[str, list[str]] = {n.id: [] for n in nodes}
    for node_id, ups in incoming.items():
        for up in ups:
            downstream[up].append(node_id)
    queue = [n.id for n in nodes if degree[n.id] == 0]
    order: list[GraphNodeConfig] = []
    while queue:
        cur = queue.pop(0)
        order.append(by_id[cur])
        for nxt in downstream[cur]:
            degree[nxt] -= 1
            if degree[nxt] == 0:
                queue.append(nxt)
    return order


def _node_asset_key(node: GraphNodeConfig) -> AssetKey | None:
    return derive_asset_key(node.connection, node.model_dump())


__all__ = ["derive_column_lineage"]
