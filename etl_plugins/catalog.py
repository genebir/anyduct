"""Read-only data catalog over the asset graph (ADR-0024 / ADR-0036, A3).

A :class:`Catalog` is a thin query surface on top of
:class:`~etl_plugins.core.asset.AssetGraph`: list the known assets, look one up,
and read its lineage (upstream/downstream + transitive). It is built by feeding
it the :class:`~etl_plugins.core.asset.AssetLineage` of each pipeline (derived
statically via :func:`etl_plugins.runtime.lineage.derive_lineage`, or captured
at runtime), optionally enriched with declared :class:`AssetSpec` metadata.

In-memory only here — the service (Step B) builds a DB-backed equivalent and
exposes it over REST, but the query shape stays identical so the UI talks to one
model.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from etl_plugins.core.asset import AssetGraph, AssetKey, AssetLineage, AssetSpec


@dataclass(frozen=True)
class AssetNode:
    """One asset in the catalog: its key plus whatever metadata is known."""

    key: AssetKey
    kind: str | None = None
    spec: AssetSpec | None = None


@dataclass(frozen=True)
class AssetLineageView:
    """Lineage of one asset: direct + transitive upstream/downstream keys."""

    key: AssetKey
    upstream: list[AssetKey] = field(default_factory=list)
    downstream: list[AssetKey] = field(default_factory=list)
    ancestors: list[AssetKey] = field(default_factory=list)
    descendants: list[AssetKey] = field(default_factory=list)


def _sorted(keys: Iterable[AssetKey]) -> list[AssetKey]:
    return sorted(keys, key=str)


class Catalog:
    """In-memory read model of assets + lineage."""

    def __init__(self) -> None:
        self._graph = AssetGraph()
        self._specs: dict[AssetKey, AssetSpec] = {}
        self._kinds: dict[AssetKey, str] = {}

    # ---------- build ------------------------------------------------------

    def add_lineage(self, lineage: AssetLineage) -> None:
        """Merge one pipeline's lineage. Cross-pipeline edges form automatically
        when keys coincide (see :class:`AssetGraph`)."""
        self._graph.add_lineage(lineage)

    def set_kind(self, key: AssetKey, kind: str | None) -> None:
        self._graph.add_asset(key)
        if kind:
            self._kinds[key] = kind

    def add_spec(self, spec: AssetSpec) -> None:
        """Attach declared metadata (and its declared deps as edges)."""
        self._graph.add_asset(spec.key)
        self._specs[spec.key] = spec
        if spec.kind:
            self._kinds[spec.key] = spec.kind
        for dep in spec.deps:
            self._graph.add_edge(dep, spec.key)

    @classmethod
    def from_lineages(cls, lineages: Iterable[AssetLineage]) -> Catalog:
        cat = cls()
        for lin in lineages:
            cat.add_lineage(lin)
        return cat

    # ---------- read -------------------------------------------------------

    @property
    def graph(self) -> AssetGraph:
        return self._graph

    def _node(self, key: AssetKey) -> AssetNode:
        return AssetNode(key=key, kind=self._kinds.get(key), spec=self._specs.get(key))

    def list_assets(self) -> list[AssetNode]:
        """Every known asset, ordered by key string."""
        return [self._node(k) for k in _sorted(self._graph.keys)]

    def get_asset(self, key: AssetKey) -> AssetNode | None:
        return self._node(key) if key in self._graph.keys else None

    def lineage(self, key: AssetKey) -> AssetLineageView | None:
        if key not in self._graph.keys:
            return None
        return AssetLineageView(
            key=key,
            upstream=_sorted(self._graph.upstream(key)),
            downstream=_sorted(self._graph.downstream(key)),
            ancestors=_sorted(self._graph.ancestors(key)),
            descendants=_sorted(self._graph.descendants(key)),
        )


__all__ = ["AssetLineageView", "AssetNode", "Catalog"]
