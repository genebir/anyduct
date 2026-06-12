"use client";

/**
 * Create migrations straight from the ERD (Phase AKP) — the designer stops
 * being draw-only and becomes an entry point into execution. Pick source/
 * destination connections and a strategy; one migration pipeline is created
 * per selected table, reusing the migration form's config builder. The ERD
 * contributes what a plain table list can't: PRIMARY KEY columns feed
 * mirror's key_columns, and standard audit columns feed append's cursor.
 */

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { XIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ApiError, connectionsApi, pipelinesApi, type ConnectionSummary } from "@/lib/api";
import type { DesignTable, ErdDesign } from "@/lib/erd-design";
import {
  buildMigrationConfig,
  DEFAULT_MIGRATION_FORM,
  MIGRATION_SUPPORTED_TYPES,
  suggestCursorColumn,
  suggestKeyColumn,
  type MigrationStrategy,
} from "@/lib/migration-config";
import { useLocale } from "@/components/providers/locale-provider";
import { Checkbox } from "@/components/ui/checkbox";

/** Append cursor from ERD columns: conventional *_at first, then the Korean
 *  standard audit timestamps (MDFCN_DT 수정일시 > REG_DT 등록일시 > *_DT). */
function cursorFor(t: DesignTable): string | null {
  const cols = t.columns.map((c) => ({ name: c.name, type: c.type }));
  const conv = suggestCursorColumn(cols);
  if (conv) return conv;
  const ci = (n: string) => t.columns.find((c) => c.name.toUpperCase() === n)?.name ?? null;
  return ci("MDFCN_DT") ?? ci("REG_DT") ?? t.columns.find((c) => /_DT$/i.test(c.name))?.name ?? null;
}

/** Mirror key from the ERD's PK flags (the designer's unique advantage),
 *  falling back to the conventional ``id`` guess. */
function keysFor(t: DesignTable): string | null {
  const pks = t.columns.filter((c) => c.pk).map((c) => c.name);
  if (pks.length > 0) return pks.join(",");
  return suggestKeyColumn(t.columns.map((c) => ({ name: c.name, type: c.type })));
}

export function CreateMigrationDialog({
  workspaceId,
  slug,
  design,
  initialTableIds,
  onClose,
}: {
  workspaceId: string;
  slug: string;
  design: ErdDesign;
  /** Preselected tables — the active tab's members or a context-menu node. */
  initialTableIds: string[];
  onClose: () => void;
}) {
  const { t } = useLocale();
  const router = useRouter();
  const [conns, setConns] = useState<ConnectionSummary[] | null>(null);
  const [sourceConn, setSourceConn] = useState("");
  const [sinkConn, setSinkConn] = useState("");
  const [strategy, setStrategy] = useState<MigrationStrategy>("snapshot");
  const [selected, setSelected] = useState<Set<string>>(new Set(initialTableIds));
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    connectionsApi
      .list(workspaceId)
      .then((all) => setConns(all.filter((c) => MIGRATION_SUPPORTED_TYPES.has(c.type))))
      .catch(() => setConns([]));
  }, [workspaceId]);

  const rows = useMemo(
    () =>
      design.tables.map((tb) => {
        const keys = keysFor(tb);
        const cursor = cursorFor(tb);
        const blocked =
          (strategy === "mirror" && !keys) || (strategy === "append" && !cursor);
        return { tb, keys, cursor, blocked };
      }),
    [design.tables, strategy],
  );
  const eligible = rows.filter((r) => selected.has(r.tb.id) && !r.blocked);

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const onCreate = async () => {
    if (!sourceConn || !sinkConn || eligible.length === 0 || creating) return;
    setCreating(true);
    let ok = 0;
    let fail = 0;
    for (const r of eligible) {
      const name = `migrate_${r.tb.name.split(".").pop()}`;
      const form = {
        ...DEFAULT_MIGRATION_FORM,
        sourceConnection: sourceConn,
        sourceTable: r.tb.name,
        sinkConnection: sinkConn,
        sinkTable: r.tb.name,
        strategy,
        keyColumns: strategy === "mirror" ? (r.keys ?? "") : "",
        cursorColumn: strategy === "append" ? (r.cursor ?? "") : "",
      };
      try {
        await pipelinesApi.create(workspaceId, { name, config: buildMigrationConfig(name, form) });
        ok += 1;
      } catch (e) {
        fail += 1;
        toast.error(`${r.tb.name}: ${e instanceof ApiError ? e.message : String(e)}`);
      }
    }
    setCreating(false);
    if (ok > 0) {
      toast.success(t("erdMigrate.created", { n: ok }));
      router.push(`/w/${slug}/migrations?lastRun=never`);
    } else if (fail === 0) {
      onClose();
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-[600px] flex-col rounded-lg border border-border-subtle bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
          <h2 className="text-sm font-semibold text-text">{t("erdMigrate.title")}</h2>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label={t("common.close")}>
            <XIcon size={14} />
          </Button>
        </div>
        <div className="grid grid-cols-2 gap-2 border-b border-border-subtle px-4 py-3">
          <label className="block text-xs">
            <span className="mb-1 block text-text-secondary">{t("erdMigrate.source")}</span>
            <select
              value={sourceConn}
              onChange={(e) => setSourceConn(e.target.value)}
              className="h-8 w-full rounded-md border border-border-subtle bg-bg px-2 text-xs text-text"
            >
              <option value="">{t("erdMigrate.pick")}</option>
              {(conns ?? []).map((c) => (
                <option key={c.id} value={c.name}>
                  {c.name} ({c.type})
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs">
            <span className="mb-1 block text-text-secondary">{t("erdMigrate.dest")}</span>
            <select
              value={sinkConn}
              onChange={(e) => setSinkConn(e.target.value)}
              className="h-8 w-full rounded-md border border-border-subtle bg-bg px-2 text-xs text-text"
            >
              <option value="">{t("erdMigrate.pick")}</option>
              {(conns ?? []).map((c) => (
                <option key={c.id} value={c.name}>
                  {c.name} ({c.type})
                </option>
              ))}
            </select>
          </label>
          <div className="col-span-2 flex items-center gap-3 pt-1">
            {(["snapshot", "append", "mirror"] as const).map((s) => (
              <label key={s} className="flex cursor-pointer items-center gap-1.5 text-xs text-text">
                <input
                  type="radio"
                  name="erd-migrate-strategy"
                  checked={strategy === s}
                  onChange={() => setStrategy(s)}
                />
                {t(s === "snapshot" ? "migrations.strategySnapshot" : s === "append" ? "migrations.strategyAppend" : "migrations.strategyMirror")}
              </label>
            ))}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          {rows.map((r) => (
            <label
              key={r.tb.id}
              className={`flex items-center gap-2 rounded-md px-2 py-1.5 ${
                r.blocked ? "opacity-50" : "cursor-pointer hover:bg-overlay/50"
              }`}
            >
              <Checkbox

                checked={selected.has(r.tb.id) && !r.blocked}
                disabled={r.blocked}
                onChange={() => toggle(r.tb.id)}
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-xs text-text">{r.tb.name}</span>
                {r.tb.logical && r.tb.logical !== r.tb.name ? (
                  <span className="block truncate text-[11px] text-text-muted">{r.tb.logical}</span>
                ) : null}
              </span>
              <span className="shrink-0 text-[10px] text-text-muted">
                {strategy === "mirror"
                  ? r.keys
                    ? t("erdMigrate.keyChip", { cols: r.keys })
                    : t("erdMigrate.noKey")
                  : strategy === "append"
                    ? r.cursor
                      ? t("erdMigrate.cursorChip", { col: r.cursor })
                      : t("erdMigrate.noCursor")
                    : null}
              </span>
            </label>
          ))}
        </div>
        <div className="flex items-center justify-between border-t border-border-subtle px-4 py-3">
          <span className="text-xs text-text-muted">
            {t("erdMigrate.summary", { n: eligible.length })}
          </span>
          <Button
            size="sm"
            variant="primary"
            onClick={() => void onCreate()}
            disabled={!sourceConn || !sinkConn || eligible.length === 0 || creating}
            loading={creating}
          >
            {t("erdMigrate.create", { n: eligible.length })}
          </Button>
        </div>
      </div>
    </div>
  );
}
