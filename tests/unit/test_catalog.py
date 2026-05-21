"""Catalog read API (ADR-0024 / ADR-0036, A3)."""

from __future__ import annotations

from etl_plugins.catalog import Catalog
from etl_plugins.core.asset import AssetKey, AssetLineage, AssetSpec, LineageEdge


def _lin(inp: AssetKey, out: AssetKey) -> AssetLineage:
    return AssetLineage(inputs=[inp], outputs=[out], edges=[LineageEdge(inp, out)])


def test_catalog_lists_assets_sorted() -> None:
    raw = AssetKey.of("lake", "raw")
    staged = AssetKey.of("wh", "staged")
    cat = Catalog.from_lineages([_lin(raw, staged)])
    keys = [n.key for n in cat.list_assets()]
    assert keys == sorted([raw, staged], key=str)


def test_catalog_get_asset_and_missing() -> None:
    raw = AssetKey.of("lake", "raw")
    staged = AssetKey.of("wh", "staged")
    cat = Catalog.from_lineages([_lin(raw, staged)])
    assert cat.get_asset(raw) is not None
    assert cat.get_asset(AssetKey.of("nope")) is None
    assert cat.lineage(AssetKey.of("nope")) is None


def test_catalog_lineage_view_transitive() -> None:
    raw = AssetKey.of("lake", "raw")
    staged = AssetKey.of("wh", "staged")
    mart = AssetKey.of("wh", "mart")
    cat = Catalog.from_lineages([_lin(raw, staged), _lin(staged, mart)])
    view = cat.lineage(mart)
    assert view is not None
    assert view.upstream == [staged]
    assert view.ancestors == sorted([raw, staged], key=str)
    raw_view = cat.lineage(raw)
    assert raw_view is not None
    assert raw_view.descendants == sorted([staged, mart], key=str)


def test_catalog_spec_enriches_kind_and_deps() -> None:
    orders = AssetKey.of("wh", "orders")
    daily = AssetKey.of("wh", "daily")
    cat = Catalog()
    cat.add_spec(AssetSpec(key=daily, deps=(orders,), kind="table", group="marts"))
    node = cat.get_asset(daily)
    assert node is not None
    assert node.kind == "table"
    assert node.spec is not None and node.spec.group == "marts"
    # declared dep became an edge
    view = cat.lineage(daily)
    assert view is not None
    assert view.upstream == [orders]


def test_catalog_set_kind() -> None:
    k = AssetKey.of("c", "t")
    cat = Catalog()
    cat.set_kind(k, "topic")
    node = cat.get_asset(k)
    assert node is not None
    assert node.kind == "topic"
