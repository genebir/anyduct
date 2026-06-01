"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { LayersIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DataTable, type Column } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiError, assetsApi, type AssetSummary } from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

function buildColumns(t: Translate): Column<AssetSummary>[] {
  return [
    {
      key: "asset_key",
      header: t("assets.colKey"),
      cell: (r) => <span className="font-mono text-xs text-text">{r.asset_key}</span>,
    },
    {
      key: "kind",
      header: t("assets.colKind"),
      cell: (r) =>
        r.kind ? (
          <span className="rounded-sm bg-overlay px-2 py-0.5 font-mono text-xs text-text-secondary">
            {r.kind}
          </span>
        ) : (
          <span className="text-text-muted">—</span>
        ),
    },
    {
      key: "last_materialized_at",
      header: t("assets.colLastMaterialized"),
      cell: (r) =>
        r.last_materialized_at ? (
          <span className="text-text-secondary">
            {new Date(r.last_materialized_at).toLocaleString()}
          </span>
        ) : (
          <span className="text-text-muted">{t("assets.never")}</span>
        ),
    },
  ];
}

export default function AssetsPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const router = useRouter();
  const { t } = useLocale();
  const [rows, setRows] = useState<AssetSummary[] | null>(null);
  /** Phase ABH (2026-06-01) — list-level search + kind filter. As
   *  cross-DB migration runs accumulate catalog assets quickly
   *  (e.g. 7 sinks from one schema-mode bulk run), operators need
   *  to scan dozens within seconds. */
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState("");

  const availableKinds = useMemo(() => {
    if (!rows) return [] as string[];
    const set = new Set<string>();
    for (const r of rows) {
      if (r.kind) set.add(r.kind);
    }
    return [...set].sort();
  }, [rows]);

  const filteredRows = useMemo(() => {
    if (!rows) return null;
    const term = search.trim().toLowerCase();
    return rows.filter((r) => {
      if (term && !r.asset_key.toLowerCase().includes(term)) return false;
      if (kindFilter && r.kind !== kindFilter) return false;
      return true;
    });
  }, [rows, search, kindFilter]);

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    assetsApi
      .list(ws.id)
      .then((list) => {
        if (!cancelled) setRows(list);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        toast.error(err instanceof ApiError ? err.message : t("assets.loadFailed"));
        setRows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [ws, t]);

  return (
    <>
      <Header
        title={t("assets.title")}
        subtitle={
          ws ? t("assets.subtitle") : t("common.loadingWorkspace")
        }
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {/* Phase ABH (2026-06-01) — search + kind filter. Hidden
            below 5 rows so a fresh workspace stays uncluttered. */}
        {rows !== null && rows.length > 5 ? (
          <div className="grid items-end gap-2 sm:grid-cols-[1fr_auto_auto]">
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("assets.searchPlaceholder")}
            />
            <select
              value={kindFilter}
              onChange={(e) => setKindFilter(e.target.value)}
              className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
            >
              <option value="">{t("assets.filterKindAll")}</option>
              {availableKinds.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
            {search || kindFilter ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch("");
                  setKindFilter("");
                }}
              >
                {t("common.clear")}
              </Button>
            ) : null}
          </div>
        ) : null}
        <Card>
          {rows === null ? (
            <div className="py-12 text-center text-sm text-text-muted">{t("common.loading")}</div>
          ) : filteredRows !== null && filteredRows.length === 0 && (search || kindFilter) ? (
            <div className="py-8 text-center text-sm text-text-muted">
              {t("assets.searchNoMatch")}
            </div>
          ) : (
            <DataTable
              columns={buildColumns(t)}
              rows={filteredRows ?? []}
              onRowClick={(row) => router.push(`/w/${slug}/assets/${row.id}`)}
              emptyState={
                <EmptyState
                  icon={<LayersIcon size={36} strokeWidth={1.5} />}
                  title={t("assets.emptyTitle")}
                  description={t("assets.emptyDesc")}
                />
              }
            />
          )}
        </Card>
      </main>
    </>
  );
}
