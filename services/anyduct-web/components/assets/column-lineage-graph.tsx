"use client";

/**
 * Multi-hop column-lineage DAG (2026-06-12 — the conventional catalog
 * drill-down, DataHub/OpenMetadata-style; supersedes both the floating
 * React Flow boxes AND the single-hop two-column rewrite).
 *
 *   - one LANE per hop: the asset being viewed sits rightmost (depth 0),
 *     its direct upstreams one lane left (depth 1), and so on;
 *   - assets are entity cards (header + column rows); columns that take
 *     no part in lineage are collapsed behind a "+N" toggle (the root
 *     card always shows everything — it's the asset being inspected);
 *   - row-port béziers connect upstream→downstream columns across lanes;
 *   - hovering a column traces its FULL transitive path (all hops, both
 *     directions) and dims the rest; clicking pins the trace;
 *   - a hop-depth control refetches deeper/shallower graphs, and a
 *     "more upstream" chip appears when the server truncated the walk.
 *
 * Layout is deterministic (fixed row heights + barycenter lane ordering),
 * so edge coordinates are computed, never measured.
 */

import { useMemo, useState } from "react";
import { ExternalLinkIcon } from "lucide-react";
import { useLocale } from "@/components/providers/locale-provider";
import { cn } from "@/lib/cn";
import type { AssetColumnLineageGraphResponse } from "@/lib/api";

const CARD_W = 220;
const SPAN_W = 130; // horizontal gap edges cross between lanes
const HEADER_H = 34;
const ROW_H = 25;
const TOGGLE_H = 22;
const CARD_GAP = 18;
const PORT_R = 2.5;

type RowKey = string; // `${assetId}:${column}`
const rowKey = (assetId: string, col: string): RowKey => `${assetId}:${col}`;

/** "conn/table" → emphasized table name with the connection muted. */
function AssetKeyLabel({ assetKey, current }: { assetKey: string; current?: boolean }) {
  const slash = assetKey.indexOf("/");
  const conn = slash >= 0 ? assetKey.slice(0, slash + 1) : "";
  const table = slash >= 0 ? assetKey.slice(slash + 1) : assetKey;
  return (
    <span className="min-w-0 truncate font-mono text-xs" title={assetKey}>
      <span className={current ? "text-on-accent" : "text-text-muted"}>{conn}</span>
      <span className={cn("font-semibold", current ? "text-on-accent" : "text-text")}>{table}</span>
    </span>
  );
}

interface CardLayout {
  assetId: string;
  assetKey: string;
  depth: number;
  x: number;
  top: number;
  /** Visible rows in render order with their absolute port y. */
  rows: { name: string; y: number }[];
  hiddenCount: number;
  height: number;
}

export function ColumnLineageGraph({
  graph,
  depth,
  direction = "upstream",
  onDepthChange,
  onSelectAsset,
}: {
  graph: AssetColumnLineageGraphResponse;
  /** Current hop depth (the page owns it — changing refetches). */
  depth: number;
  /** "upstream" = provenance (root right, sources left); "downstream" =
   *  impact analysis (root left, consumers right). Mirrors lane placement. */
  direction?: "upstream" | "downstream";
  onDepthChange?: (depth: number) => void;
  onSelectAsset?: (assetId: string) => void;
}) {
  const { t } = useLocale();
  const [hovered, setHovered] = useState<RowKey | null>(null);
  const [pinned, setPinned] = useState<RowKey | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // Column search (2026-06-12): matches light up with their FULL
  // transitive trace (same visual as hover/pin); hover/pin take
  // precedence while active. Matches hidden behind the "+N more"
  // collapse are revealed so a hit can never hide.
  const [search, setSearch] = useState("");
  const query = search.trim().toLowerCase();

  const model = useMemo(() => {
    const maxLane = Math.max(0, ...graph.assets.map((a) => a.depth));
    // Upstream: root (depth 0) sits rightmost, sources fan left. Downstream
    // (impact): mirror — root leftmost, consumers fan right.
    const laneX = (d: number) =>
      (direction === "downstream" ? d : maxLane - d) * (CARD_W + SPAN_W);

    // Columns that participate in lineage (linked) per asset.
    const linked = new Map<string, Set<string>>();
    const touchLinked = (assetId: string, col: string) => {
      if (!linked.has(assetId)) linked.set(assetId, new Set());
      linked.get(assetId)!.add(col);
    };
    for (const e of graph.edges) {
      touchLinked(e.from_asset_id, e.from_column);
      touchLinked(e.to_asset_id, e.to_column);
    }

    // Visible rows per asset: root shows ALL columns; upstream cards show
    // linked columns, the rest collapse behind a "+N" toggle.
    const visibleRows = new Map<string, { rows: string[]; hidden: number }>();
    for (const a of graph.assets) {
      const linkSet = linked.get(a.id) ?? new Set<string>();
      const showAll = a.depth === 0 || expanded.has(a.id);
      const rows = showAll
        ? a.columns
        : a.columns.filter(
            (c) => linkSet.has(c) || (query !== "" && c.toLowerCase().includes(query)),
          );
      visibleRows.set(a.id, { rows, hidden: a.columns.length - rows.length });
    }
    const cardHeight = (assetId: string) => {
      const v = visibleRows.get(assetId)!;
      return HEADER_H + v.rows.length * ROW_H + (v.hidden > 0 ? TOGGLE_H : 0);
    };

    // --- lane ordering: barycenter over already-placed downstream rows ---
    const byLane = new Map<number, typeof graph.assets>();
    for (const a of graph.assets) {
      if (!byLane.has(a.depth)) byLane.set(a.depth, []);
      byLane.get(a.depth)!.push(a);
    }
    const rowY = new Map<RowKey, number>();
    const cards: CardLayout[] = [];
    for (let d = 0; d <= maxLane; d++) {
      const lane = [...(byLane.get(d) ?? [])];
      if (d > 0) {
        // Mean y of the rows this card feeds (already placed, lanes < d).
        const bary = (assetId: string): number => {
          const ys: number[] = [];
          for (const e of graph.edges) {
            if (e.from_asset_id !== assetId) continue;
            const y = rowY.get(rowKey(e.to_asset_id, e.to_column));
            if (y != null) ys.push(y);
          }
          return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : Number.MAX_SAFE_INTEGER;
        };
        lane.sort((a, b) => bary(a.id) - bary(b.id) || a.asset_key.localeCompare(b.asset_key));
      }
      let top = 0;
      for (const a of lane) {
        const v = visibleRows.get(a.id)!;
        const rows = v.rows.map((name, i) => ({
          name,
          y: top + HEADER_H + i * ROW_H + ROW_H / 2,
        }));
        for (const r of rows) rowY.set(rowKey(a.id, r.name), r.y);
        cards.push({
          assetId: a.id,
          assetKey: a.asset_key,
          depth: d,
          x: laneX(d),
          top,
          rows,
          hiddenCount: v.hidden,
          height: cardHeight(a.id),
        });
        top += cardHeight(a.id) + CARD_GAP;
      }
    }
    const height = Math.max(0, ...cards.map((c) => c.top + c.height));
    const width = (maxLane + 1) * CARD_W + maxLane * SPAN_W;

    // --- edges with computed endpoints ---
    const cardX = new Map(cards.map((c) => [c.assetId, c.x]));
    const edges = graph.edges.flatMap((e) => {
      const from = rowKey(e.from_asset_id, e.from_column);
      const to = rowKey(e.to_asset_id, e.to_column);
      const y1 = rowY.get(from);
      const y2 = rowY.get(to);
      const fx = cardX.get(e.from_asset_id);
      const tx = cardX.get(e.to_asset_id);
      if (y1 == null || y2 == null || fx == null || tx == null) return [];
      return [{ id: `${from}->${to}`, from, to, x1: fx + CARD_W, y1, x2: tx, y2 }];
    });

    // --- transitive adjacency for the trace ---
    const adjacency = new Map<RowKey, Set<RowKey>>();
    const link = (a: RowKey, b: RowKey) => {
      if (!adjacency.has(a)) adjacency.set(a, new Set());
      adjacency.get(a)!.add(b);
    };
    for (const e of edges) {
      link(e.from, e.to);
      link(e.to, e.from);
    }
    return { cards, edges, adjacency, height, width, maxLane };
  }, [graph, expanded, query, direction]);

  // Search highlight: union of every match's transitive closure.
  const searchSet = useMemo(() => {
    if (query === "") return null;
    const seen = new Set<RowKey>();
    const queue: RowKey[] = [];
    for (const card of model.cards) {
      for (const row of card.rows) {
        if (row.name.toLowerCase().includes(query)) {
          const key = rowKey(card.assetId, row.name);
          if (!seen.has(key)) {
            seen.add(key);
            queue.push(key);
          }
        }
      }
    }
    const matches = seen.size;
    while (queue.length) {
      const cur = queue.pop()!;
      for (const next of model.adjacency.get(cur) ?? []) {
        if (!seen.has(next)) {
          seen.add(next);
          queue.push(next);
        }
      }
    }
    return { set: seen, matches };
  }, [query, model.cards, model.adjacency]);

  const active = pinned ?? hovered;
  const activeSet = useMemo(() => {
    if (!active) return null;
    // Full transitive closure in both directions — "everything that
    // contributes to / is derived from this column" across all hops.
    const seen = new Set<RowKey>([active]);
    const queue = [active];
    while (queue.length) {
      const cur = queue.pop()!;
      for (const next of model.adjacency.get(cur) ?? []) {
        if (!seen.has(next)) {
          seen.add(next);
          queue.push(next);
        }
      }
    }
    return seen;
  }, [active, model.adjacency]);

  const effectiveSet = activeSet ?? searchSet?.set ?? null;
  const isLit = (key: RowKey) => effectiveSet !== null && effectiveSet.has(key);
  const isDim = (key: RowKey) => effectiveSet !== null && !effectiveSet.has(key);

  const rowProps = (key: RowKey) => ({
    onMouseEnter: () => setHovered(key),
    onMouseLeave: () => setHovered(null),
    onClick: (e: React.MouseEvent) => {
      e.stopPropagation();
      setPinned((cur) => (cur === key ? null : key));
    },
  });

  return (
    <div data-testid="column-lineage-graph">
      <div className="flex items-center gap-2 px-2 pb-2">
        <span className="text-[11px] text-text-muted">{t("assets.clDepth")}</span>
        <div className="flex overflow-hidden rounded-md border border-border-subtle">
          {[1, 2, 3, 4, 5].map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => onDepthChange?.(d)}
              className={cn(
                "cursor-pointer px-2 py-0.5 text-[11px] transition-colors",
                d === depth
                  ? "bg-accent text-on-accent"
                  : "bg-elevated text-text-secondary hover:bg-overlay",
              )}
            >
              {d}
            </button>
          ))}
        </div>
        {graph.truncated ? (
          <span
            className="rounded bg-warning/15 px-1.5 py-0.5 text-[10px] text-warning"
            title={t("assets.clTruncatedHint")}
          >
            {t(direction === "downstream" ? "assets.clTruncatedDown" : "assets.clTruncated")}
          </span>
        ) : null}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("assets.clSearch")}
          aria-label={t("assets.clSearch")}
          className="h-6 w-44 rounded-md border border-border-subtle bg-elevated px-2 font-mono text-[11px] text-text placeholder:text-text-muted focus:border-accent focus:outline-none"
        />
        {searchSet !== null ? (
          <span
            className={cn(
              "text-[11px]",
              searchSet.matches === 0 ? "text-warning" : "text-text-muted",
            )}
          >
            {t("assets.clSearchCount", { count: String(searchSet.matches) })}
          </span>
        ) : null}
        <span className="ml-auto text-[11px] text-text-muted">{t("assets.clHint")}</span>
      </div>
      <div
        className="max-h-[560px] overflow-auto rounded-md border border-border-subtle bg-bg p-4"
        onClick={() => setPinned(null)}
        role="presentation"
      >
        <div className="relative mx-auto" style={{ width: model.width, height: model.height }}>
          {/* ---- edges ---- */}
          <svg
            className="pointer-events-none absolute inset-0"
            width={model.width}
            height={model.height}
            aria-hidden="true"
          >
            {model.edges.map((e) => {
              const lit = effectiveSet !== null && isLit(e.from) && isLit(e.to);
              const dim = effectiveSet !== null && !lit;
              const bend = Math.max(40, (e.x2 - e.x1) / 2);
              return (
                <g key={e.id}>
                  <path
                    d={`M ${e.x1} ${e.y1} C ${e.x1 + bend} ${e.y1}, ${e.x2 - bend} ${e.y2}, ${e.x2} ${e.y2}`}
                    fill="none"
                    stroke={lit ? "rgb(var(--accent))" : "rgb(var(--border-strong))"}
                    strokeWidth={lit ? 1.75 : 1.25}
                    opacity={dim ? 0.1 : lit ? 1 : 0.5}
                  />
                  <circle
                    cx={e.x1}
                    cy={e.y1}
                    r={PORT_R}
                    fill={lit ? "rgb(var(--accent))" : "rgb(var(--border-strong))"}
                    opacity={dim ? 0.1 : 1}
                  />
                  <circle
                    cx={e.x2}
                    cy={e.y2}
                    r={PORT_R}
                    fill={lit ? "rgb(var(--accent))" : "rgb(var(--border-strong))"}
                    opacity={dim ? 0.1 : 1}
                  />
                </g>
              );
            })}
          </svg>

          {/* ---- asset cards ---- */}
          {model.cards.map((card) => {
            const isRoot = card.depth === 0;
            return (
              <div
                key={card.assetId}
                className={cn(
                  "absolute overflow-hidden rounded-lg border bg-elevated shadow-sm",
                  isRoot ? "border-accent/60" : "border-border-default",
                )}
                style={{ left: card.x, top: card.top, width: CARD_W }}
              >
                {isRoot ? (
                  <div
                    className="flex items-center gap-1.5 border-b border-border-subtle bg-accent px-2.5"
                    style={{ height: HEADER_H }}
                  >
                    <AssetKeyLabel assetKey={card.assetKey} current />
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectAsset?.(card.assetId);
                    }}
                    title={t("assets.clOpenAsset", { key: card.assetKey })}
                    className="flex w-full cursor-pointer items-center gap-1.5 border-b border-border-subtle bg-overlay/60 px-2.5 text-left hover:bg-overlay"
                    style={{ height: HEADER_H }}
                  >
                    <AssetKeyLabel assetKey={card.assetKey} />
                    <ExternalLinkIcon size={11} className="ml-auto shrink-0 text-text-muted" />
                  </button>
                )}
                {card.rows.map((row) => {
                  const key = rowKey(card.assetId, row.name);
                  return (
                    <div
                      key={row.name}
                      {...rowProps(key)}
                      className={cn(
                        "flex cursor-pointer items-center px-2.5 font-mono text-[11px] transition-colors",
                        isLit(key)
                          ? "bg-accent/15 text-text"
                          : isDim(key)
                            ? "text-text-muted opacity-40"
                            : isRoot
                              ? "text-text hover:bg-overlay/60"
                              : "text-text-secondary hover:bg-overlay/60",
                      )}
                      style={{ height: ROW_H }}
                    >
                      <span className="truncate" title={row.name}>
                        {/* "*" is the aggregate-fallback pseudo column —
                            COUNT(*) depends on the whole table, not one
                            column. Render it as a readable label. */}
                        {row.name === "*" ? t("assets.clWholeTable") : row.name}
                      </span>
                    </div>
                  );
                })}
                {card.hiddenCount > 0 ? (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setExpanded((cur) => {
                        const next = new Set(cur);
                        next.add(card.assetId);
                        return next;
                      });
                    }}
                    className="flex w-full cursor-pointer items-center px-2.5 text-[10px] text-text-muted hover:bg-overlay/60 hover:text-text-secondary"
                    style={{ height: TOGGLE_H }}
                    title={t("assets.clMoreColumnsHint")}
                  >
                    {t("assets.clMoreColumns", { count: String(card.hiddenCount) })}
                  </button>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
