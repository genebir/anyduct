"""Column-level lineage model (ADR-0041, Phase J).

Extends the table-level :mod:`etl_plugins.core.asset` model with column-grained
dependencies — answers "which source columns feed this output column?" so the
catalog can drill from a column to its origins.

Pure model + no derivation logic; the actual SQL parsing + transform walking
lives in :mod:`etl_plugins.runtime.column_lineage` (which depends on config +
sqlglot). This module stays a leaf so ``core`` can depend on it.

``opaque_assets`` flags output assets whose column mapping couldn't be derived
(``python`` transforms, ``SELECT *`` queries, or joins in v1) — the UI should
show "opaque" for those rather than imply missing data is missing edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from etl_plugins.core.asset import AssetKey


@dataclass(frozen=True)
class ColumnRef:
    """A column on an asset, e.g. ``ColumnRef(AssetKey.of("wh", "orders"), "total")``."""

    asset: AssetKey
    column: str

    def __str__(self) -> str:
        return f"{self.asset}:{self.column}"


@dataclass(frozen=True)
class ColumnEdge:
    """A directed n→1 column dependency.

    ``upstreams`` is empty when an output column has no traceable upstream —
    e.g. an ``add_constant`` (truly no upstream) or an opaque expression
    (``UPPER(b)`` — we know the output column exists but can't trace ``b``).
    Multi-source dependencies (after a join) become ≥2 upstreams from
    different assets when join column lineage lands.
    """

    downstream: ColumnRef
    upstreams: tuple[ColumnRef, ...] = ()


@dataclass
class ColumnLineage:
    """Static column-level lineage of a pipeline. ``edges`` carries column→column
    dependencies (one ``ColumnEdge`` per output column). ``opaque_assets`` flags
    output assets whose mapping was un-derivable; their downstream columns are
    not enumerated here at all."""

    edges: list[ColumnEdge] = field(default_factory=list)
    opaque_assets: list[AssetKey] = field(default_factory=list)


__all__ = ["ColumnEdge", "ColumnLineage", "ColumnRef"]
