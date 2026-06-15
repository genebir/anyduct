"use client";

/**
 * Verify the current ERD against a live database connection (Phase AKK).
 * Compares the design's tables/columns with the real schema fetched through
 * the SchemaInspector REST endpoints and reports drift: tables missing in
 * the DB, DB-only tables, column-level adds/removes and type differences.
 * Read-only — nothing is changed on either side.
 */

import { useEffect, useMemo, useState } from "react";
import { AlertTriangleIcon, CheckCircle2Icon, ChevronDownIcon, ChevronRightIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { connectionsApi, type ConnectionSummary } from "@/lib/api";
import { normalizeImportType, type ErdDesign } from "@/lib/erd-design";
import { useLocale } from "@/components/providers/locale-provider";

interface TableReport {
  name: string;
  logical?: string;
  status: "ok" | "diff" | "missing";
  erdOnly: string[];
  dbOnly: string[];
  typeDiff: { col: string; erd: string; db: string }[];
}

const unq = (s: string) => s.split(".").pop()!.toLowerCase();
const normType = (s: string) => normalizeImportType(s.trim()).toUpperCase().replace(/\s+/g, "");

export function VerifyDbDialog({
  workspaceId,
  design,
  onClose,
}: {
  workspaceId: string;
  design: ErdDesign;
  onClose: () => void;
}) {
  const { t } = useLocale();
  const [conns, setConns] = useState<ConnectionSummary[] | null>(null);
  const [connId, setConnId] = useState("");
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [reports, setReports] = useState<TableReport[] | null>(null);
  const [dbOnlyTables, setDbOnlyTables] = useState<string[]>([]);
  const [openRows, setOpenRows] = useState<Set<string>>(new Set());
  const [showDbOnly, setShowDbOnly] = useState(false);

  useEffect(() => {
    connectionsApi
      .list(workspaceId)
      .then(setConns)
      .catch(() => setConns([]));
  }, [workspaceId]);

  const run = async () => {
    if (!connId || running) return;
    setRunning(true);
    setErr(null);
    setReports(null);
    setProgress(0);
    try {
      const { tables: dbTables } = await connectionsApi.tables(workspaceId, connId);
      const dbByName = new Map(dbTables.map((x) => [unq(x), x]));
      const erdNames = new Set(design.tables.map((tb) => unq(tb.name)));
      setDbOnlyTables(dbTables.filter((x) => !erdNames.has(unq(x))));
      const out: TableReport[] = [];
      let done = 0;
      // Small batches keep the server happy on 100+ table models.
      const queue = [...design.tables];
      const workers = Array.from({ length: 5 }, async () => {
        for (;;) {
          const tb = queue.shift();
          if (!tb) return;
          const dbName = dbByName.get(unq(tb.name));
          if (!dbName) {
            out.push({ name: tb.name, logical: tb.logical, status: "missing", erdOnly: [], dbOnly: [], typeDiff: [] });
          } else {
            try {
              const { columns } = await connectionsApi.columns(workspaceId, connId, dbName);
              const dbCols = new Map(columns.map((c) => [c.name.toLowerCase(), c]));
              const erdCols = new Map(tb.columns.map((c) => [c.name.toLowerCase(), c]));
              const erdOnly = [...erdCols.keys()].filter((k) => !dbCols.has(k)).map((k) => erdCols.get(k)!.name);
              const dbOnly = [...dbCols.keys()].filter((k) => !erdCols.has(k)).map((k) => dbCols.get(k)!.name);
              const typeDiff: TableReport["typeDiff"] = [];
              for (const [k, ec] of erdCols) {
                const dc = dbCols.get(k);
                if (dc && ec.type && dc.type && normType(ec.type) !== normType(dc.type)) {
                  typeDiff.push({ col: ec.name, erd: ec.type, db: dc.type });
                }
              }
              out.push({
                name: tb.name,
                logical: tb.logical,
                status: erdOnly.length || dbOnly.length || typeDiff.length ? "diff" : "ok",
                erdOnly,
                dbOnly,
                typeDiff,
              });
            } catch {
              out.push({ name: tb.name, logical: tb.logical, status: "missing", erdOnly: [], dbOnly: [], typeDiff: [] });
            }
          }
          done += 1;
          setProgress(done);
        }
      });
      await Promise.all(workers);
      out.sort((a, b) => (a.status === b.status ? a.name.localeCompare(b.name) : a.status === "ok" ? 1 : -1));
      setReports(out);
    } catch {
      setErr(t("erdVerify.error"));
    } finally {
      setRunning(false);
    }
  };

  const summary = useMemo(() => {
    if (!reports) return null;
    return {
      ok: reports.filter((r) => r.status === "ok").length,
      diff: reports.filter((r) => r.status === "diff").length,
      missing: reports.filter((r) => r.status === "missing").length,
    };
  }, [reports]);

  const toggleRow = (name: string) =>
    setOpenRows((s) => {
      const n = new Set(s);
      if (n.has(name)) n.delete(name);
      else n.add(name);
      return n;
    });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-[640px] flex-col rounded-lg border border-border-subtle bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
          <h2 className="text-sm font-semibold text-text">{t("erdVerify.title")}</h2>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label={t("common.close")}>
            <XIcon size={14} />
          </Button>
        </div>
        <div className="flex items-center gap-2 border-b border-border-subtle px-4 py-3">
          <select
            value={connId}
            onChange={(e) => setConnId(e.target.value)}
            className="h-8 min-w-0 flex-1 rounded-md border border-border-subtle bg-bg px-2 text-xs text-text"
          >
            <option value="">{t("erdVerify.pickConnection")}</option>
            {(conns ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.type})
              </option>
            ))}
          </select>
          <Button size="sm" variant="secondary" onClick={() => void run()} disabled={!connId || running}>
            {running
              ? t("erdVerify.running", { done: progress, total: design.tables.length })
              : t("erdVerify.run")}
          </Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {err ? <p className="text-sm text-error">{err}</p> : null}
          {!reports && !err ? (
            <p className="text-xs text-text-muted">{t("erdVerify.hint")}</p>
          ) : null}
          {reports && summary ? (
            <>
              <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
                <span className="rounded-full bg-success/10 px-2 py-0.5 text-success">
                  {t("erdVerify.okCount", { n: summary.ok })}
                </span>
                {summary.diff > 0 ? (
                  <span className="rounded-full bg-warning/10 px-2 py-0.5 text-warning">
                    {t("erdVerify.diffCount", { n: summary.diff })}
                  </span>
                ) : null}
                {summary.missing > 0 ? (
                  <span className="rounded-full bg-error/10 px-2 py-0.5 text-error">
                    {t("erdVerify.missingCount", { n: summary.missing })}
                  </span>
                ) : null}
                {dbOnlyTables.length > 0 ? (
                  <button
                    type="button"
                    onClick={() => setShowDbOnly((v) => !v)}
                    className="cursor-pointer rounded-full bg-overlay px-2 py-0.5 text-text-secondary hover:text-text"
                  >
                    {t("erdVerify.dbOnlyCount", { n: dbOnlyTables.length })}
                  </button>
                ) : null}
              </div>
              {showDbOnly ? (
                <div className="mb-3 rounded-md border border-border-subtle/60 bg-bg/40 p-2 text-[11px] text-text-muted">
                  <p className="mb-1 font-medium text-text-secondary">{t("erdVerify.dbOnlyTitle")}</p>
                  <p className="break-all font-mono">{dbOnlyTables.join(", ")}</p>
                </div>
              ) : null}
              <ul className="space-y-1">
                {reports.map((r) => {
                  const open = openRows.has(r.name);
                  const expandable = r.status === "diff";
                  return (
                    <li key={r.name} className="rounded-md border border-border-subtle/50 bg-bg/40">
                      <button
                        type="button"
                        onClick={() => expandable && toggleRow(r.name)}
                        className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left ${expandable ? "cursor-pointer" : "cursor-default"}`}
                      >
                        {r.status === "ok" ? (
                          <CheckCircle2Icon size={13} className="shrink-0 text-success" />
                        ) : r.status === "diff" ? (
                          <AlertTriangleIcon size={13} className="shrink-0 text-warning" />
                        ) : (
                          <XIcon size={13} className="shrink-0 text-error" />
                        )}
                        <span className="min-w-0 flex-1 truncate font-mono text-xs text-text">
                          {r.name}
                          {r.logical && r.logical !== r.name ? (
                            <span className="ml-1.5 text-[11px] font-sans text-text-muted">{r.logical}</span>
                          ) : null}
                        </span>
                        <span className="shrink-0 text-[11px] text-text-muted">
                          {r.status === "ok"
                            ? t("erdVerify.rowOk")
                            : r.status === "missing"
                              ? t("erdVerify.rowMissing")
                              : t("erdVerify.rowDiff", {
                                  n: r.erdOnly.length + r.dbOnly.length + r.typeDiff.length,
                                })}
                        </span>
                        {expandable ? (
                          open ? (
                            <ChevronDownIcon size={13} className="shrink-0 text-text-muted" />
                          ) : (
                            <ChevronRightIcon size={13} className="shrink-0 text-text-muted" />
                          )
                        ) : null}
                      </button>
                      {open && expandable ? (
                        <div className="border-t border-border-subtle/40 px-2.5 py-1.5 text-[11px]">
                          {r.erdOnly.length > 0 ? (
                            <p className="text-text-secondary">
                              <span className="text-warning">{t("erdVerify.erdOnlyCols")}</span>{" "}
                              <span className="font-mono">{r.erdOnly.join(", ")}</span>
                            </p>
                          ) : null}
                          {r.dbOnly.length > 0 ? (
                            <p className="text-text-secondary">
                              <span className="text-warning">{t("erdVerify.dbOnlyCols")}</span>{" "}
                              <span className="font-mono">{r.dbOnly.join(", ")}</span>
                            </p>
                          ) : null}
                          {r.typeDiff.map((d) => (
                            <p key={d.col} className="text-text-secondary">
                              <span className="font-mono">{d.col}</span>: ERD{" "}
                              <span className="font-mono text-warning">{d.erd}</span> ↔ DB{" "}
                              <span className="font-mono text-warning">{d.db}</span>
                            </p>
                          ))}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}
