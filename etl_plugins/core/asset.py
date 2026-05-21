"""Asset / lineage first-class model (ADR-0024, ADR-0036).

A *data asset* is a persistent thing a pipeline reads or writes — a table, a
topic, an object. Modelling assets as first-class lets us answer "what produced
this table?" and "what breaks if I change it?" (lineage), and eventually drive
asset-aware orchestration (materialize downstream when upstream refreshes).

Two layers:

* **Derived (zero-config)** — every source is an *input* asset and every sink an
  *output* asset; an ``input → output`` edge is the lineage. Keys are derived
  from ``(connection, target)`` (table / topic / object key / collection). No
  user declaration needed — see :func:`etl_plugins.runtime.lineage.derive_lineage`.
* **Declared (optional)** — :class:`AssetSpec` lets a user attach explicit
  ``deps`` / ``group`` / freshness for asset-driven scheduling (a later phase).

This module is pure model + graph; it imports nothing from config/runtime so it
stays a leaf the rest of the core can depend on. Backward compatible: existing
``Pipeline`` / ``Connector`` code is untouched — assets are a layer on top.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# Fields a connector uses to name its target, in priority order. The first one
# present on a source/sink config (or Task field set) is the asset's "target".
_TARGET_PRIORITY = ("table", "topic", "key", "collection", "query")
_KIND_BY_FIELD = {
    "table": "table",
    "topic": "topic",
    "key": "object",
    "collection": "collection",
    "query": "query",
}


def derive_asset_key(connection: str | None, fields: Mapping[str, Any]) -> AssetKey | None:
    """Derive an :class:`AssetKey` from a connection name + a source/sink field
    set (config dict or Task options). The single source of truth for the
    derived-first rule (ADR-0036), shared by static config derivation and
    runtime emit. ``None`` connection ⇒ no asset."""
    if not connection:
        return None
    for f in _TARGET_PRIORITY:
        v = fields.get(f)
        if isinstance(v, str) and v:
            return AssetKey.of(connection, v)
    return AssetKey.of(connection)


def asset_kind(fields: Mapping[str, Any]) -> str | None:
    """The asset 'kind' label (table/topic/object/...) for a field set."""
    for f in _TARGET_PRIORITY:
        v = fields.get(f)
        if isinstance(v, str) and v:
            return _KIND_BY_FIELD[f]
    return None


@dataclass(frozen=True)
class AssetKey:
    """Stable identifier for a data asset, e.g. ``AssetKey(("warehouse",
    "public.orders"))`` → ``"warehouse/public.orders"``."""

    path: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.path or any(not p for p in self.path):
            raise ValueError(f"AssetKey path must be non-empty parts, got {self.path!r}")

    @classmethod
    def of(cls, *parts: str) -> AssetKey:
        """Build a key from non-empty parts (empty parts dropped)."""
        return cls(tuple(p for p in parts if p))

    @classmethod
    def parse(cls, s: str) -> AssetKey:
        """Parse a ``"a/b/c"`` rendering back into a key."""
        return cls(tuple(p for p in s.split("/") if p))

    def __str__(self) -> str:
        return "/".join(self.path)


@dataclass(frozen=True)
class LineageEdge:
    """A directed dependency: ``upstream`` feeds ``downstream``."""

    upstream: AssetKey
    downstream: AssetKey


@dataclass(frozen=True)
class AssetSpec:
    """Declarative description of one asset (Dagster software-defined-asset
    style). Only ``key`` is required; the rest are optional metadata used by
    the catalog and (later) asset-aware scheduling."""

    key: AssetKey
    deps: tuple[AssetKey, ...] = ()
    kind: str | None = None  # table | topic | object | http | ...
    group: str | None = None
    description: str | None = None


@dataclass
class AssetLineage:
    """Static lineage of a single pipeline (no run needed): the assets it reads
    (``inputs``), writes (``outputs``), and the ``input → output`` edges. Keys
    are deduped, first-seen order preserved."""

    inputs: list[AssetKey] = field(default_factory=list)
    outputs: list[AssetKey] = field(default_factory=list)
    edges: list[LineageEdge] = field(default_factory=list)


class AssetGraph:
    """A global asset dependency graph assembled from edges (e.g. the union of
    every pipeline's :class:`AssetLineage`). Cross-pipeline lineage emerges
    automatically: when pipeline A writes asset X and pipeline B reads X, both
    reference the same :class:`AssetKey`, so X links them.
    """

    def __init__(self) -> None:
        self._keys: set[AssetKey] = set()
        self._upstream: dict[AssetKey, set[AssetKey]] = {}
        self._downstream: dict[AssetKey, set[AssetKey]] = {}

    def add_asset(self, key: AssetKey) -> None:
        self._keys.add(key)
        self._upstream.setdefault(key, set())
        self._downstream.setdefault(key, set())

    def add_edge(self, upstream: AssetKey, downstream: AssetKey) -> None:
        self.add_asset(upstream)
        self.add_asset(downstream)
        self._upstream[downstream].add(upstream)
        self._downstream[upstream].add(downstream)

    def add_lineage(self, lineage: AssetLineage) -> None:
        for k in (*lineage.inputs, *lineage.outputs):
            self.add_asset(k)
        for e in lineage.edges:
            self.add_edge(e.upstream, e.downstream)

    @property
    def keys(self) -> set[AssetKey]:
        return set(self._keys)

    def upstream(self, key: AssetKey) -> set[AssetKey]:
        """Direct upstream (parent) assets of ``key``."""
        return set(self._upstream.get(key, set()))

    def downstream(self, key: AssetKey) -> set[AssetKey]:
        """Direct downstream (child) assets of ``key``."""
        return set(self._downstream.get(key, set()))

    def ancestors(self, key: AssetKey) -> set[AssetKey]:
        """All transitive upstream assets (cycle-safe)."""
        return self._reachable(key, self._upstream)

    def descendants(self, key: AssetKey) -> set[AssetKey]:
        """All transitive downstream assets (cycle-safe)."""
        return self._reachable(key, self._downstream)

    @staticmethod
    def _reachable(start: AssetKey, adj: dict[AssetKey, set[AssetKey]]) -> set[AssetKey]:
        seen: set[AssetKey] = set()
        stack = list(adj.get(start, set()))
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adj.get(cur, set()))
        return seen


__all__ = [
    "AssetGraph",
    "AssetKey",
    "AssetLineage",
    "AssetSpec",
    "LineageEdge",
    "asset_kind",
    "derive_asset_key",
]
