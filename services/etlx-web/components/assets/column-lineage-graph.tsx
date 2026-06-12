"use client";

/**
 * Column-level lineage view (ADR-0041 J3; redesigned 2026-06-12 on user
 * feedback — the React Flow version floated one box per column on a
 * pannable canvas, which read as confetti, hid the asset grouping, and
 * offered no way to trace a column).
 *
 * The redesign borrows the ERD designer's visual language:
 *
 *   - assets are entity CARDS (header + column rows), upstream cards on
 *     the left, the current asset on the right;
 *   - edges leave from the exact row port (right edge of an upstream row
 *     → left edge of a downstream row) as cubic béziers in one SVG
 *     overlay — no zoom/pan chrome for what is a static left→right map;
 *   - hovering a row highlights its full path (related rows + edges)
 *     and dims everything else; clicking pins the highlight.
 *
 * Layout is fully deterministic (fixed row heights), so edge coordinates
 * are computed, not measured.
 */

import { useMemo, useState } from "react";
import { ExternalLinkIcon } from "lucide-react";
import { useLocale } from "@/components/providers/locale-provider";
import { cn } from "@/lib/cn";
import type { AssetColumnEntry } from "@/lib/api";

const CARD_W = 240;
const SPAN_W = 170; // horizontal gap the edges cross
const HEADER_H = 36;
const ROW_H = 26;
const CARD_GAP = 16;
const PORT_R = 3;

type UpstreamGroup = { assetId: string; assetKey: string; columns: string[] };

type RowKey = string; // "up:<assetId>:<col>" | "dn:<col>"

const upKey = (assetId: string, col: string): RowKey => `up:${assetId}:${col}`;
const dnKey = (col: string): RowKey => `dn:${col}`;

function groupUpstreams(columns: AssetColumnEntry[]): UpstreamGroup[] {
  const byAsset = new Map<string, { assetKey: string; columns: Set<string> }>();
  for (const col of columns) {
    for (const up of col.upstreams) {
      const entry = byAsset.get(up.asset_id);
      if (entry) entry.columns.add(up.column);
      else byAsset.set(up.asset_id, { assetKey: up.asset_key, columns: new Set([up.column]) });
    }
  }
  return Array.from(byAsset.entries())
    .map(([assetId, v]) => ({
      assetId,
      assetKey: v.assetKey,
      columns: Array.from(v.columns).sort(),
    }))
    .sort((a, b) => a.assetKey.localeCompare(b.assetKey));
}

/** "conn/table" → emphasized table name with the connection muted. */
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

export function ColumnLineageGraph({
  columns,
  onSelectAsset,
}: {
  columns: AssetColumnEntry[];
  onSelectAsset?: (assetId: string) => void;
}) {
  const { t } = useLocale();
  const [hovered, setHovered] = useState<RowKey | null>(null);
  const [pinned, setPinned] = useState<RowKey | null>(null);

  const model = useMemo(() => {
    const groups = groupUpstreams(columns);
    const downColumns = [...columns].sort((a, b) => a.name.localeCompare(b.name));

    // --- row y-coordinates (deterministic) -------------------------------
    const upRowY = new Map<RowKey, number>();
    const cards: { group: UpstreamGroup; top: number }[] = [];
    let y = 0;
    for (const g of groups) {
      cards.push({ group: g, top: y });
      let rowY = y + HEADER_H;
      for (const col of g.columns) {
        upRowY.set(upKey(g.assetId, col), rowY + ROW_H / 2);
        rowY += ROW_H;
      }
      y = rowY + CARD_GAP;
    }
    const leftHeight = Math.max(0, y - CARD_GAP);
    const rightHeight = HEADER_H + downColumns.length * ROW_H;
    const height = Math.max(leftHeight, rightHeight);

    const dnRowY = new Map<RowKey, number>();
    downColumns.forEach((col, i) => {
      dnRowY.set(dnKey(col.name), HEADER_H + i * ROW_H + ROW_H / 2);
    });

    // --- edges + relation sets for highlight ------------------------------
    type EdgeModel = { id: string; from: RowKey; to: RowKey; y1: number; y2: number };
    const edges: EdgeModel[] = [];
    const related = new Map<RowKey, Set<RowKey>>(); // row → {rows it lights up}
    const touch = (a: RowKey, b: RowKey) => {
      if (!related.has(a)) related.set(a, new Set([a]));
      related.get(a)!.add(b);
    };
    for (const col of columns) {
      const to = dnKey(col.name);
      for (const up of col.upstreams) {
        const from = upKey(up.asset_id, up.column);
        const y1 = upRowY.get(from);
        const y2 = dnRowY.get(to);
        if (y1 == null || y2 == null) continue;
        edges.push({ id: `${from}->${to}`, from, to, y1, y2 });
        touch(from, to);
        touch(to, from);
      }
    }
    return { groups: cards, downColumns, height, edges, related, upRowY, dnRowY };
  }, [columns]);

  if (columns.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center text-sm text-text-muted" role="status">
        {t("assets.columnLineageEmpty")}
      </div>
    );
  }

  const active = pinned ?? hovered;
  const activeSet = active ? (model.related.get(active) ?? new Set([active])) : null;
  const isDim = (key: RowKey) => activeSet !== null && !activeSet.has(key);
  const isLit = (key: RowKey) => activeSet !== null && activeSet.has(key);

  const rowProps = (key: RowKey) => ({
    onMouseEnter: () => setHovered(key),
    onMouseLeave: () => setHovered(null),
    onClick: (e: React.MouseEvent) => {
      e.stopPropagation();
      setPinned((cur) => (cur === key ? null : key));
    },
  });

  const totalW = CARD_W * 2 + SPAN_W;

  return (
    <div data-testid="column-lineage-graph">
      <div className="px-2 pb-1 text-right text-[11px] text-text-muted">
        {t("assets.clHint")}
      </div>
      <div
        className="max-h-[560px] overflow-auto rounded-md border border-border-subtle bg-bg p-4"
        onClick={() => setPinned(null)}
        role="presentation"
      >
        <div className="relative mx-auto" style={{ width: totalW, height: model.height }}>
          {/* ---- edges (one SVG overlay, row-port béziers) ---- */}
          <svg
            className="pointer-events-none absolute inset-0"
            width={totalW}
            height={model.height}
            aria-hidden="true"
          >
            {model.edges.map((e) => {
              const x1 = CARD_W;
              const x2 = CARD_W + SPAN_W;
              const lit = isLit(e.from) && isLit(e.to) && activeSet !== null;
              const dim = activeSet !== null && !lit;
              const mid = SPAN_W / 2;
              return (
                <g key={e.id}>
                  <path
                    d={`M ${x1} ${e.y1} C ${x1 + mid} ${e.y1}, ${x2 - mid} ${e.y2}, ${x2} ${e.y2}`}
                    fill="none"
                    stroke={lit ? "rgb(var(--accent))" : "rgb(var(--border-strong))"}
                    strokeWidth={lit ? 1.75 : 1.25}
                    opacity={dim ? 0.12 : lit ? 1 : 0.55}
                  />
                  <circle
                    cx={x1}
                    cy={e.y1}
                    r={PORT_R}
                    fill={lit ? "rgb(var(--accent))" : "rgb(var(--border-strong))"}
                    opacity={dim ? 0.12 : 1}
                  />
                  <circle
                    cx={x2}
                    cy={e.y2}
                    r={PORT_R}
                    fill={lit ? "rgb(var(--accent))" : "rgb(var(--border-strong))"}
                    opacity={dim ? 0.12 : 1}
                  />
                </g>
              );
            })}
          </svg>

          {/* ---- left: upstream asset cards ---- */}
          {model.groups.map(({ group, top }) => (
            <div
              key={group.assetId}
              className="absolute left-0 overflow-hidden rounded-lg border border-border-default bg-elevated shadow-sm"
              style={{ top, width: CARD_W }}
            >
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectAsset?.(group.assetId);
                }}
                title={t("assets.clOpenAsset", { key: group.assetKey })}
                className="flex w-full cursor-pointer items-center gap-1.5 border-b border-border-subtle bg-overlay/60 px-2.5 text-left hover:bg-overlay"
                style={{ height: HEADER_H }}
              >
                <AssetKeyLabel assetKey={group.assetKey} />
                <ExternalLinkIcon size={11} className="ml-auto shrink-0 text-text-muted" />
              </button>
              {group.columns.map((col) => {
                const key = upKey(group.assetId, col);
                return (
                  <div
                    key={col}
                    {...rowProps(key)}
                    className={cn(
                      "flex cursor-pointer items-center px-2.5 font-mono text-[11px] transition-colors",
                      isLit(key)
                        ? "bg-accent/15 text-text"
                        : isDim(key)
                          ? "text-text-muted opacity-40"
                          : "text-text-secondary hover:bg-overlay/60",
                    )}
                    style={{ height: ROW_H }}
                  >
                    <span className="truncate" title={col}>
                      {col}
                    </span>
                  </div>
                );
              })}
            </div>
          ))}

          {/* ---- right: the current asset card ---- */}
          <div
            className="absolute overflow-hidden rounded-lg border border-accent/60 bg-elevated shadow-sm"
            style={{ left: CARD_W + SPAN_W, top: 0, width: CARD_W }}
          >
            <div
              className="flex items-center gap-1.5 border-b border-border-subtle bg-accent px-2.5"
              style={{ height: HEADER_H }}
            >
              <span className="text-[9px] font-semibold uppercase tracking-widest text-white/80">
                {t("assets.clThisAsset")}
              </span>
            </div>
            {model.downColumns.map((col) => {
              const key = dnKey(col.name);
              return (
                <div
                  key={col.name}
                  {...rowProps(key)}
                  className={cn(
                    "flex cursor-pointer items-center gap-1.5 px-2.5 font-mono text-[11px] transition-colors",
                    isLit(key)
                      ? "bg-accent/15 text-text"
                      : isDim(key)
                        ? "text-text-muted opacity-40"
                        : "text-text hover:bg-overlay/60",
                  )}
                  style={{ height: ROW_H }}
                >
                  <span className="truncate" title={col.name}>
                    {col.name}
                  </span>
                  {col.upstreams.length === 0 ? (
                    <span
                      className="ml-auto shrink-0 rounded bg-overlay px-1 text-[9px] uppercase tracking-wider text-text-muted"
                      title={t("assets.clNoUpstreamHint")}
                    >
                      {t("assets.clNoUpstream")}
                    </span>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
