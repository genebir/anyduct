"use client";

/**
 * Multi-hop TABLE-level lineage DAG (2026-06-12 — matches the column
 * lineage redesign's visual language; supersedes the 1-hop React Flow
 * canvas). Lanes per hop: upstream assets left of the asset being
 * viewed (negative depths), downstream right. Hovering a card traces
 * its transitive path, clicking pins the trace, and the ↗ affordance
 * navigates to the asset (same grammar as the column-lineage view).
 */

import { useMemo, useState } from "react";
import { ExternalLinkIcon } from "lucide-react";
import { useLocale } from "@/components/providers/locale-provider";
import { cn } from "@/lib/cn";
import type { AssetLineageGraphResponse } from "@/lib/api";

const CARD_W = 210;
const CARD_H = 46;
const SPAN_W = 110;
const CARD_GAP = 16;
const PORT_R = 2.5;

function AssetKeyLabel({ assetKey, current }: { assetKey: string; current?: boolean }) {
  const slash = assetKey.indexOf("/");
  const conn = slash >= 0 ? assetKey.slice(0, slash + 1) : "";
  const table = slash >= 0 ? assetKey.slice(slash + 1) : assetKey;
  return (
    <span className="min-w-0 truncate font-mono text-xs" title={assetKey}>
      <span className={current ? "text-white/70" : "text-text-muted"}>{conn}</span>
      <span className={cn("font-semibold", current ? "text-white" : "text-text")}>{table}</span>
    </span>
  );
}

export function LineageGraph({
  graph,
  depth,
  onDepthChange,
  onSelect,
}: {
  graph: AssetLineageGraphResponse;
  depth: number;
  onDepthChange?: (depth: number) => void;
  onSelect?: (assetId: string) => void;
}) {
  const { t } = useLocale();
  const [hovered, setHovered] = useState<string | null>(null);
  // Click pins the trace (matches the column-lineage view); navigation
  // moves to the explicit ↗ affordance so the two never conflict.
  const [pinned, setPinned] = useState<string | null>(null);

  const model = useMemo(() => {
    const minDepth = Math.min(0, ...graph.assets.map((a) => a.depth));
    const maxDepth = Math.max(0, ...graph.assets.map((a) => a.depth));
    const laneX = (d: number) => (d - minDepth) * (CARD_W + SPAN_W);

    const byLane = new Map<number, typeof graph.assets>();
    for (const a of graph.assets) {
      if (!byLane.has(a.depth)) byLane.set(a.depth, []);
      byLane.get(a.depth)!.push(a);
    }

    // Place lanes outward from the root (0, -1, +1, -2, +2 …) so each
    // lane can barycenter-sort against its already-placed neighbours.
    const laneOrder: number[] = [0];
    for (let d = 1; d <= Math.max(-minDepth, maxDepth); d++) {
      if (byLane.has(-d)) laneOrder.push(-d);
      if (byLane.has(d)) laneOrder.push(d);
    }
    const centerY = new Map<string, number>();
    const cards: { id: string; assetKey: string; kind: string | null; depth: number; x: number; top: number }[] = [];
    for (const d of laneOrder) {
      const lane = [...(byLane.get(d) ?? [])];
      if (d !== 0) {
        const bary = (assetId: string): number => {
          const ys: number[] = [];
          for (const e of graph.edges) {
            const other =
              e.from_asset_id === assetId
                ? e.to_asset_id
                : e.to_asset_id === assetId
                  ? e.from_asset_id
                  : null;
            if (other === null) continue;
            const y = centerY.get(other);
            if (y != null) ys.push(y);
          }
          return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : Number.MAX_SAFE_INTEGER;
        };
        lane.sort((a, b) => bary(a.id) - bary(b.id) || a.asset_key.localeCompare(b.asset_key));
      }
      let top = 0;
      for (const a of lane) {
        cards.push({ id: a.id, assetKey: a.asset_key, kind: a.kind, depth: d, x: laneX(d), top });
        centerY.set(a.id, top + CARD_H / 2);
        top += CARD_H + CARD_GAP;
      }
    }
    const height = Math.max(CARD_H, ...cards.map((c) => c.top + CARD_H));
    const width = (maxDepth - minDepth + 1) * CARD_W + (maxDepth - minDepth) * SPAN_W;

    const cardX = new Map(cards.map((c) => [c.id, c.x]));
    const edges = graph.edges.flatMap((e) => {
      const y1 = centerY.get(e.from_asset_id);
      const y2 = centerY.get(e.to_asset_id);
      const fx = cardX.get(e.from_asset_id);
      const tx = cardX.get(e.to_asset_id);
      if (y1 == null || y2 == null || fx == null || tx == null) return [];
      return [
        {
          id: `${e.from_asset_id}->${e.to_asset_id}`,
          from: e.from_asset_id,
          to: e.to_asset_id,
          x1: fx + CARD_W,
          y1,
          x2: tx,
          y2,
        },
      ];
    });

    const adjacency = new Map<string, Set<string>>();
    const link = (a: string, b: string) => {
      if (!adjacency.has(a)) adjacency.set(a, new Set());
      adjacency.get(a)!.add(b);
    };
    for (const e of graph.edges) {
      link(e.from_asset_id, e.to_asset_id);
      link(e.to_asset_id, e.from_asset_id);
    }
    return { cards, edges, adjacency, height, width };
  }, [graph]);

  const active = pinned ?? hovered;
  const activeSet = useMemo(() => {
    if (!active) return null;
    const seen = new Set<string>([active]);
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

  const isLit = (id: string) => activeSet !== null && activeSet.has(id);
  const isDim = (id: string) => activeSet !== null && !activeSet.has(id);

  return (
    <div data-testid="lineage-graph">
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
                  ? "bg-accent text-white"
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
            {t("assets.clTruncated")}
          </span>
        ) : null}
        <span className="ml-auto text-[11px] text-text-muted">{t("assets.lgHint")}</span>
      </div>
      <div
        className="max-h-[480px] overflow-auto rounded-md border border-border-subtle bg-bg p-4"
        onClick={() => setPinned(null)}
        role="presentation"
      >
        <div className="relative mx-auto" style={{ width: model.width, height: model.height }}>
          <svg
            className="pointer-events-none absolute inset-0"
            width={model.width}
            height={model.height}
            aria-hidden="true"
          >
            {model.edges.map((e) => {
              const lit = activeSet !== null && isLit(e.from) && isLit(e.to);
              const dim = activeSet !== null && !lit;
              const bend = Math.max(36, (e.x2 - e.x1) / 2);
              return (
                <g key={e.id}>
                  <path
                    d={`M ${e.x1} ${e.y1} C ${e.x1 + bend} ${e.y1}, ${e.x2 - bend} ${e.y2}, ${e.x2} ${e.y2}`}
                    fill="none"
                    stroke={lit ? "rgb(var(--accent))" : "rgb(var(--border-strong))"}
                    strokeWidth={lit ? 1.75 : 1.25}
                    opacity={dim ? 0.1 : lit ? 1 : 0.55}
                  />
                  <circle cx={e.x1} cy={e.y1} r={PORT_R} fill={lit ? "rgb(var(--accent))" : "rgb(var(--border-strong))"} opacity={dim ? 0.1 : 1} />
                  <circle cx={e.x2} cy={e.y2} r={PORT_R} fill={lit ? "rgb(var(--accent))" : "rgb(var(--border-strong))"} opacity={dim ? 0.1 : 1} />
                </g>
              );
            })}
          </svg>
          {model.cards.map((card) => {
            const isRoot = card.depth === 0 && card.id === graph.id;
            return (
              <div
                key={card.id}
                onClick={(e) => {
                  e.stopPropagation();
                  setPinned((cur) => (cur === card.id ? null : card.id));
                }}
                onMouseEnter={() => setHovered(card.id)}
                onMouseLeave={() => setHovered(null)}
                title={t("assets.lgPinHint")}
                className={cn(
                  "absolute flex cursor-pointer items-center gap-1.5 overflow-hidden rounded-lg border px-2.5 text-left shadow-sm transition-opacity",
                  isRoot ? "border-accent/70 bg-accent" : "border-border-default bg-elevated hover:bg-overlay",
                  isLit(card.id) && !isRoot ? "border-accent/60" : "",
                  pinned === card.id ? "ring-2 ring-accent/40" : "",
                  isDim(card.id) ? "opacity-30" : "",
                )}
                style={{ left: card.x, top: card.top, width: CARD_W, height: CARD_H }}
              >
                <div className="flex min-w-0 flex-1 flex-col justify-center gap-0.5">
                  <AssetKeyLabel assetKey={card.assetKey} current={isRoot} />
                  <span
                    className={cn(
                      "text-[9px] uppercase tracking-wider",
                      isRoot ? "text-white/70" : "text-text-muted",
                    )}
                  >
                    {isRoot ? t("assets.clThisAsset") : (card.kind ?? "asset")}
                  </span>
                </div>
                {!isRoot ? (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect?.(card.id);
                    }}
                    title={t("assets.clOpenAsset", { key: card.assetKey })}
                    aria-label={t("assets.clOpenAsset", { key: card.assetKey })}
                    className="shrink-0 cursor-pointer rounded p-1 text-text-muted hover:bg-overlay hover:text-text"
                  >
                    <ExternalLinkIcon size={12} />
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
