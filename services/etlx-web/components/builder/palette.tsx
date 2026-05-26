"use client";

import { useMemo, useState } from "react";
import { ChevronDownIcon, GripVerticalIcon, SearchIcon } from "lucide-react";
import { cn } from "@/lib/cn";
import {
  OPERATOR_KIND_GROUPS,
  operatorAllowedForMode,
  type OperatorKind,
  type OperatorSpec,
} from "@/lib/operators";
import { useLocale } from "@/components/providers/locale-provider";

export function Palette({
  mode = "batch",
  variant = "linear",
}: {
  /** Pipeline data mode — restricts which source/sink connectors are offered. */
  mode?: "batch" | "stream";
  /** Builder variant: linear hides graph-only operators (join / aggregate)
   *  because they don't fit the `source → transform* → sink` linear shape;
   *  graph shows them. (ADR-0041 I1.) */
  variant?: "linear" | "graph";
}) {
  const { t } = useLocale();
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const q = query.trim().toLowerCase();
  const matches = (spec: OperatorSpec) =>
    operatorAllowedForMode(spec, mode) &&
    (variant === "graph" || !spec.graphOnly) &&
    (!q ||
      spec.label.toLowerCase().includes(q) ||
      spec.description.toLowerCase().includes(q) ||
      (spec.connectorType ?? "").toLowerCase().includes(q));

  // Filter the kind→category tree by the search query + pipeline mode, dropping empties.
  const groups = useMemo(
    () =>
      OPERATOR_KIND_GROUPS.map((g) => ({
        ...g,
        categories: g.categories
          .map((c) => ({ ...c, specs: c.specs.filter(matches) }))
          .filter((c) => c.specs.length > 0),
      })).filter((g) => g.categories.length > 0),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [q, mode, variant],
  );

  const toggle = (key: string) =>
    setCollapsed((prev) => ({ ...prev, [key]: !prev[key] }));

  return (
    <aside className="flex w-64 shrink-0 flex-col gap-3 overflow-y-auto border-r border-border-subtle bg-surface px-3 py-4">
      <div>
        <div className="px-1 text-[11px] font-semibold uppercase tracking-widest text-text-muted">
          {t("builder.operators")}
        </div>
        <p className="mt-1 px-1 text-xs text-text-secondary">
          {t("builder.operatorsHint")}
        </p>
      </div>

      <div className="relative">
        <SearchIcon
          size={14}
          className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted"
        />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("builder.searchOperators")}
          className="h-9 w-full rounded-md border border-border-subtle bg-elevated pl-8 pr-2 text-sm text-text placeholder:text-text-muted focus-visible:border-accent focus-visible:outline-none"
        />
      </div>

      {groups.length === 0 ? (
        <p className="px-1 text-xs text-text-muted">{t("builder.noOperatorMatch")}</p>
      ) : (
        groups.map((group) => (
          <div key={group.kind} className="flex flex-col gap-1.5">
            <div className="px-1 text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
              {group.label}
            </div>
            {group.categories.map((cat) => {
              const key = `${group.kind}:${cat.category}`;
              // When searching, force-expand so results are visible.
              const isCollapsed = !q && collapsed[key];
              return (
                <div key={key} className="flex flex-col">
                  <button
                    type="button"
                    onClick={() => toggle(key)}
                    className="flex items-center gap-1 px-1 py-1 text-left text-[11px] font-medium text-text-muted transition duration-150 hover:text-text"
                  >
                    <ChevronDownIcon
                      size={12}
                      className={cn("transition duration-150", isCollapsed && "-rotate-90")}
                    />
                    {cat.category}
                    <span className="ml-auto text-[10px] text-text-muted">
                      {cat.specs.length}
                    </span>
                  </button>
                  {!isCollapsed
                    ? cat.specs.map((spec) => (
                        <OperatorDragHandle key={spec.id} spec={spec} />
                      ))
                    : null}
                </div>
              );
            })}
          </div>
        ))
      )}
    </aside>
  );
}

// DataTransfer MIME type for palette → canvas drag-and-drop (2026-05-26
// user request). Custom MIME so the canvas can ignore non-palette drags
// (files from desktop, text selections, …) without false positives.
export const PALETTE_DND_MIME = "application/x-etlx-operator-id";

/**
 * Drag-only palette item (2026-05-26 user request: '오퍼레이터가 클릭으로는
 * 생성되지 않게 해줘. 드래그 앤 드롭만 가능하도록'). Switched from a
 * ``<button>`` to a plain draggable ``<div>``: a focusable button that does
 * nothing on activation reads as broken (screen readers, click-only users)
 * — a div with an explicit ``cursor-grab`` + drag handle icon advertises
 * the drag affordance honestly. Keyboard / touch users who can't drag fall
 * back to the canvas right-click menu's 'Add node →' submenu, which lists
 * the same operators.
 */
function OperatorDragHandle({ spec }: { spec: OperatorSpec }) {
  const Icon = spec.icon;
  return (
    <div
      role="listitem"
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(PALETTE_DND_MIME, spec.id);
        // Plain text fallback so other apps see *something* sensible.
        e.dataTransfer.setData("text/plain", spec.label);
        e.dataTransfer.effectAllowed = "copy";
      }}
      title={spec.description}
      className={cn(
        "group flex select-none items-start gap-2 rounded-md border border-transparent px-2 py-2 text-left transition duration-150",
        "cursor-grab hover:border-border-subtle hover:bg-overlay active:cursor-grabbing",
      )}
    >
      <span
        aria-hidden
        className="mt-0.5 inline-flex h-6 w-6 items-center justify-center rounded-sm text-white"
        style={{ background: spec.accent }}
      >
        <Icon size={14} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium text-text">{spec.label}</span>
        <span className="block truncate text-[11px] text-text-muted">
          {spec.description}
        </span>
      </span>
      <GripVerticalIcon
        size={14}
        className="mt-1 text-text-muted opacity-0 transition duration-150 group-hover:opacity-100"
        aria-hidden
      />
    </div>
  );
}

// Re-export so callers importing the kind type from the palette keep working.
export type { OperatorKind };
