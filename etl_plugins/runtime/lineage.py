"""Static lineage derivation from a :class:`PipelineConfig` (ADR-0036).

"Derived-first" lineage: without any user declaration, every source is an input
asset and every sink an output asset, with an ``input → output`` edge per task.
Keys are ``(connection, target)`` where *target* is the connector's natural
addressable thing — table / topic / object key / collection. The cross-pipeline
graph emerges when two pipelines reference the same key (see
:class:`~etl_plugins.core.asset.AssetGraph`).

This is *static* — computed from config alone, no run required — so the catalog
and the builder can show lineage before anything executes. Runtime emit
(OpenLineage) builds on the same key derivation in a later slice.

**Phase X follow-up (2026-05-28, ADR-0043):** when ``source.query`` is parseable
SQL, we additionally register every *referenced* base table as an input asset
(JOINs, CTEs reading from raw tables, UNION branches, correlated subqueries).
That keeps the asset-axis graph in step with the column-lineage axis — both now
recognise all upstream tables a query touches, not just the regex-picked first
``FROM``. The primary key derivation in :func:`derive_asset_key` is unchanged
(so existing keying / catalog rows stay stable); we only *add* assets and edges.
"""

from __future__ import annotations

from etl_plugins.config.models import GraphNodeConfig, PipelineConfig, SourceConfig, TaskConfig
from etl_plugins.core.asset import (
    AssetKey,
    AssetLineage,
    LineageEdge,
    asset_kind,
    derive_asset_key,
)
from etl_plugins.runtime.sql_lineage import extract_referenced_tables


def derive_lineage(cfg: PipelineConfig) -> AssetLineage:
    """Derive the static input/output assets + edges of a pipeline.

    Handles all shapes: single-task, Task-orchestration DAG (``effective_tasks``),
    and dataflow graph (``graph`` source/sink nodes). Keys are deduped with
    first-seen order preserved; ``kinds`` records each key's kind label.
    """
    inputs: list[AssetKey] = []
    outputs: list[AssetKey] = []
    edges: list[LineageEdge] = []
    kinds: dict[AssetKey, str | None] = {}
    seen_in: set[AssetKey] = set()
    seen_out: set[AssetKey] = set()
    seen_edge: set[tuple[AssetKey, AssetKey]] = set()

    def _record_kind(k: AssetKey, kind: str | None) -> None:
        # First non-null wins; never clobber a known kind with None.
        if kind and not kinds.get(k):
            kinds[k] = kind
        kinds.setdefault(k, kind)

    def _add_in(k: AssetKey | None, kind: str | None = None) -> None:
        if k is None:
            return
        _record_kind(k, kind)
        if k not in seen_in:
            seen_in.add(k)
            inputs.append(k)

    def _add_out(k: AssetKey | None, kind: str | None = None) -> None:
        if k is None:
            return
        _record_kind(k, kind)
        if k not in seen_out:
            seen_out.add(k)
            outputs.append(k)

    def _add_edge(u: AssetKey | None, d: AssetKey | None) -> None:
        if u is not None and d is not None and (u, d) not in seen_edge:
            seen_edge.add((u, d))
            edges.append(LineageEdge(upstream=u, downstream=d))

    if cfg.graph is not None:
        # Tree-shaped graph (ADR-0030): every sink derives from every source.
        # The flat ``src_keys`` list is a depth-1 fan-in across all sources;
        # extra source-side tables (JOIN'd bases, CTE roots) join it too.
        src_keys: list[AssetKey] = []
        for node in cfg.graph.nodes:
            data = node.model_dump()
            if node.type == "source":
                primary = derive_asset_key(node.connection, data)
                _add_in(primary, asset_kind(data))
                if primary is not None:
                    src_keys.append(primary)
                for k in _query_referenced_asset_keys(node):
                    if k != primary:
                        _add_in(k, "table")
                        src_keys.append(k)
            elif node.type == "sink":
                sink_key = derive_asset_key(node.connection, data)
                _add_out(sink_key, asset_kind(data))
                for sk in src_keys:
                    _add_edge(sk, sink_key)
    else:
        for task in cfg.effective_tasks():
            src_data = task.source.model_dump()
            primary_in = derive_asset_key(task.source.connection, src_data)
            _add_in(primary_in, asset_kind(src_data))
            extras = [k for k in _query_referenced_asset_keys(task) if k != primary_in]
            for k in extras:
                _add_in(k, "table")
            for snk in task.effective_sinks():
                snk_data = snk.model_dump()
                out_key = derive_asset_key(snk.connection, snk_data)
                _add_out(out_key, asset_kind(snk_data))
                _add_edge(primary_in, out_key)
                for k in extras:
                    _add_edge(k, out_key)

    # Phase II (ADR-0053, 2026-05-29): the DLQ destination is an output
    # asset too — operators clicking through asset lineage should see
    # both the clean sink and the DLQ sink. Without this row the
    # question "where did my failed records go?" has no answer in the
    # catalog. Edges go from every input to the DLQ so the topology
    # mirrors the actual partial-success data flow.
    if cfg.dlq is not None:
        dlq_data = cfg.dlq.model_dump()
        dlq_key = derive_asset_key(cfg.dlq.connection, dlq_data)
        _add_out(dlq_key, asset_kind(dlq_data))
        for src_key in inputs:
            _add_edge(src_key, dlq_key)

    return AssetLineage(inputs=inputs, outputs=outputs, edges=edges, kinds=kinds)


def _query_referenced_asset_keys(
    source_holder: TaskConfig | GraphNodeConfig,
) -> list[AssetKey]:
    """Resolve every base table the source's SQL query references and key it
    on the source's ``connection``.

    Returns ``[]`` when there's no parseable SQL query (non-SQL sources, table
    reads, Kafka/HTTP/Mongo, etc.) — the caller's primary-key derivation stays
    the only asset for those. The function is intentionally narrow: only the
    *source*'s query matters here; sink writes go through ``derive_asset_key``
    on their explicit table/topic/key field.
    """
    source: SourceConfig | GraphNodeConfig
    if isinstance(source_holder, TaskConfig):
        source = source_holder.source
    else:
        source = source_holder
        if source.type != "source":
            return []
    query = source.query
    connection = source.connection
    if not query or not connection:
        return []
    names = extract_referenced_tables(query)
    return [AssetKey.of(connection, name) for name in names]


__all__ = ["derive_lineage"]
