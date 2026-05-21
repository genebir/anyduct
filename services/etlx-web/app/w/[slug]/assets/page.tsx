"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { LayersIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
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
        <Card>
          {rows === null ? (
            <div className="py-12 text-center text-sm text-text-muted">{t("common.loading")}</div>
          ) : (
            <DataTable
              columns={buildColumns(t)}
              rows={rows}
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
