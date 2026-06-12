"use client";

/**
 * Import tables from a connection into the ERD designer (Phase AHA).
 * Pick a connection, then one or many tables; columns are fetched and
 * converted to ERD entities (with ``<x>_id`` FK inference). Merged into
 * the current design by the caller.
 */

import { useEffect, useState } from "react";
import { XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { connectionsApi, type ConnectionSummary } from "@/lib/api";
import { rawTablesToDesign, type ErdDesign, type ImportTable } from "@/lib/erd-design";
import { useLocale } from "@/components/providers/locale-provider";
import { Checkbox } from "@/components/ui/checkbox";

export function ImportTablesDialog({
  workspaceId,
  onClose,
  onImport,
}: {
  workspaceId: string;
  onClose: () => void;
  onImport: (design: ErdDesign) => void;
}) {
  const { t } = useLocale();
  const [conns, setConns] = useState<ConnectionSummary[] | null>(null);
  const [connId, setConnId] = useState("");
  const [tables, setTables] = useState<string[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    connectionsApi
      .list(workspaceId)
      .then(setConns)
      .catch(() => setConns([]));
  }, [workspaceId]);

  async function loadTables(id: string) {
    setConnId(id);
    setTables(null);
    setSelected(new Set());
    setErr(null);
    if (!id) return;
    setLoading(true);
    try {
      const r = await connectionsApi.tables(workspaceId, id);
      setTables(r.tables);
    } catch {
      setErr(t("erdImport.error"));
      setTables([]);
    } finally {
      setLoading(false);
    }
  }

  function toggle(tb: string) {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(tb)) n.delete(tb);
      else n.add(tb);
      return n;
    });
  }

  async function doImport() {
    if (selected.size === 0) return;
    setLoading(true);
    try {
      const chosen = [...selected];
      const raw: ImportTable[] = await Promise.all(
        chosen.map(async (table): Promise<ImportTable> => {
          try {
            const r = await connectionsApi.columns(workspaceId, connId, table);
            return { table, columns: r.columns };
          } catch {
            return { table, columns: [] };
          }
        }),
      );
      onImport(rawTablesToDesign(raw));
      onClose();
    } finally {
      setLoading(false);
    }
  }

  const filtered = (tables ?? []).filter((tb) => tb.toLowerCase().includes(filter.toLowerCase()));
  const allSelected = filtered.length > 0 && filtered.every((tb) => selected.has(tb));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgb(10_18_40_/_0.6)] p-4 backdrop-blur-md"
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-md flex-col gap-3 rounded-lg border border-border-subtle bg-surface p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-text">{t("erdImport.title")}</span>
          <button onClick={onClose} aria-label={t("common.close")} className="text-text-muted hover:text-text">
            <XIcon size={16} />
          </button>
        </div>

        <select
          value={connId}
          onChange={(e) => void loadTables(e.target.value)}
          className="h-9 rounded-md border border-border-subtle bg-bg px-2 text-sm text-text"
        >
          <option value="">{t("erdImport.selectConnection")}</option>
          {(conns ?? []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} ({c.type})
            </option>
          ))}
        </select>

        {connId ? (
          <>
            <Input
              placeholder={t("erdImport.search")}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="h-8 text-sm"
            />
            {err ? (
              <div className="text-xs text-error">{err}</div>
            ) : loading && tables === null ? (
              <div className="py-6 text-center text-sm text-text-muted">{t("common.loading")}</div>
            ) : filtered.length === 0 ? (
              <div className="py-6 text-center text-sm text-text-muted">{t("erdImport.empty")}</div>
            ) : (
              <>
                <label className="flex items-center gap-2 text-xs text-text-secondary">
                  <Checkbox

                    checked={allSelected}
                    onChange={() =>
                      setSelected((s) => {
                        const n = new Set(s);
                        if (allSelected) filtered.forEach((tb) => n.delete(tb));
                        else filtered.forEach((tb) => n.add(tb));
                        return n;
                      })
                    }
                  />
                  {t("erdImport.selectAll")}
                </label>
                <div className="flex max-h-64 flex-col gap-0.5 overflow-auto rounded-md border border-border-subtle p-1">
                  {filtered.map((tb) => (
                    <label key={tb} className="flex items-center gap-2 rounded px-1.5 py-1 hover:bg-overlay">
                      <Checkbox  checked={selected.has(tb)} onChange={() => toggle(tb)} />
                      <span className="truncate font-mono text-xs text-text">{tb}</span>
                    </label>
                  ))}
                </div>
              </>
            )}
          </>
        ) : null}

        <Button
          size="sm"
          variant="secondary"
          className="self-end"
          disabled={selected.size === 0 || loading}
          onClick={() => void doImport()}
        >
          {t("erdImport.import", { n: selected.size })}
        </Button>
      </div>
    </div>
  );
}
