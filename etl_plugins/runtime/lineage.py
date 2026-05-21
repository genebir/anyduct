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
"""

from __future__ import annotations

from etl_plugins.config.models import PipelineConfig, SinkConfig, SourceConfig
from etl_plugins.core.asset import AssetKey, AssetLineage, LineageEdge, derive_asset_key


def _source_key(src: SourceConfig) -> AssetKey | None:
    return derive_asset_key(src.connection, src.model_dump())


def _sink_key(snk: SinkConfig) -> AssetKey | None:
    return derive_asset_key(snk.connection, snk.model_dump())


def derive_lineage(cfg: PipelineConfig) -> AssetLineage:
    """Derive the static input/output assets + edges of a pipeline.

    Handles all shapes: single-task, Task-orchestration DAG (``effective_tasks``),
    and dataflow graph (``graph`` source/sink nodes). Keys are deduped with
    first-seen order preserved.
    """
    inputs: list[AssetKey] = []
    outputs: list[AssetKey] = []
    edges: list[LineageEdge] = []
    seen_in: set[AssetKey] = set()
    seen_out: set[AssetKey] = set()
    seen_edge: set[tuple[AssetKey, AssetKey]] = set()

    def _add_in(k: AssetKey | None) -> None:
        if k is not None and k not in seen_in:
            seen_in.add(k)
            inputs.append(k)

    def _add_out(k: AssetKey | None) -> None:
        if k is not None and k not in seen_out:
            seen_out.add(k)
            outputs.append(k)

    def _add_edge(u: AssetKey | None, d: AssetKey | None) -> None:
        if u is not None and d is not None and (u, d) not in seen_edge:
            seen_edge.add((u, d))
            edges.append(LineageEdge(upstream=u, downstream=d))

    if cfg.graph is not None:
        src_keys: list[AssetKey] = []
        for node in cfg.graph.nodes:
            data = node.model_dump()
            if node.type == "source":
                k = derive_asset_key(node.connection, data)
                _add_in(k)
                if k is not None:
                    src_keys.append(k)
            elif node.type == "sink":
                k = derive_asset_key(node.connection, data)
                _add_out(k)
                # Tree-shaped graph (ADR-0030): every sink derives from the
                # single source. Edge each source → each sink.
                for sk in src_keys:
                    _add_edge(sk, k)
    else:
        for task in cfg.effective_tasks():
            in_key = _source_key(task.source)
            _add_in(in_key)
            for snk in task.effective_sinks():
                out_key = _sink_key(snk)
                _add_out(out_key)
                _add_edge(in_key, out_key)

    return AssetLineage(inputs=inputs, outputs=outputs, edges=edges)


__all__ = ["derive_lineage"]
