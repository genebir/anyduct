"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeftIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { LineageGraph } from "@/components/assets/lineage-graph";
import { ColumnLineageGraph } from "@/components/assets/column-lineage-graph";
import {
  ApiError,
  assetsApi,
  type AssetColumnLineageResponse,
  type AssetLineageResponse,
  type AssetMaterializationEntry,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";

export default function AssetDetailPage() {
  const { slug, id } = useParams<{ slug: string; id: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const router = useRouter();
  const { t } = useLocale();
  const [lineage, setLineage] = useState<AssetLineageResponse | null>(null);
  const [mats, setMats] = useState<AssetMaterializationEntry[] | null>(null);
  const [columnLineage, setColumnLineage] = useState<AssetColumnLineageResponse | null>(null);

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    Promise.all([
      assetsApi.lineage(ws.id, id),
      assetsApi.materializations(ws.id, id),
      assetsApi.columnLineage(ws.id, id),
    ])
      .then(([lin, m, col]) => {
        if (cancelled) return;
        setLineage(lin);
        setMats(m);
        setColumnLineage(col);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        toast.error(err instanceof ApiError ? err.message : t("assets.detailLoadFailed"));
      });
    return () => {
      cancelled = true;
    };
  }, [ws, id, t]);

  return (
    <>
      <Header
        title={lineage ? lineage.asset_key : t("assets.title")}
        subtitle={t("assets.lineage")}
        actions={
          <Link
            href={`/w/${slug}/assets`}
            aria-label={t("assets.backAria")}
            className="inline-flex items-center gap-1.5 rounded-md border border-border-subtle px-3 py-1.5 text-sm text-text-secondary transition duration-150 hover:bg-overlay hover:text-text"
          >
            <ArrowLeftIcon size={15} />
            {t("nav.assets")}
          </Link>
        }
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {lineage === null ? (
          <div className="py-12 text-center text-sm text-text-muted">{t("common.loading")}</div>
        ) : (
          <>
            <Card className="p-0">
              <LineageGraph
                current={{ id: lineage.id, asset_key: lineage.asset_key }}
                upstream={lineage.upstream}
                downstream={lineage.downstream}
                onSelect={(assetId) => router.push(`/w/${slug}/assets/${assetId}`)}
              />
            </Card>

            <Card className="p-0">
              <div className="border-b border-border-subtle px-4 py-3 text-sm font-semibold text-text">
                {t("assets.columnLineage")}
              </div>
              {columnLineage === null ? (
                <div className="px-4 py-8 text-center text-sm text-text-muted">
                  {t("common.loading")}
                </div>
              ) : columnLineage.opaque ? (
                <div
                  className="m-4 rounded-md border border-border-subtle bg-overlay/40 px-4 py-3 text-sm text-text-secondary"
                  role="status"
                >
                  <div className="font-medium text-text">
                    {t("assets.columnLineageOpaqueTitle")}
                  </div>
                  <div className="mt-1 text-xs text-text-muted">
                    {t("assets.columnLineageOpaqueDesc")}
                  </div>
                </div>
              ) : columnLineage.columns.length === 0 ? (
                <div className="px-4 py-8 text-center text-sm text-text-muted">
                  {t("assets.columnLineageEmpty")}
                </div>
              ) : (
                <div className="p-2">
                  <ColumnLineageGraph
                    columns={columnLineage.columns}
                    onSelectAsset={(assetId) => router.push(`/w/${slug}/assets/${assetId}`)}
                  />
                </div>
              )}
            </Card>

            <Card>
              <div className="border-b border-border-subtle px-4 py-3 text-sm font-semibold text-text">
                {t("assets.materializations")}
              </div>
              {mats && mats.length > 0 ? (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border-subtle text-left text-xs uppercase tracking-wider text-text-muted">
                      <th className="px-4 py-2 font-medium">{t("assets.colWhen")}</th>
                      <th className="px-4 py-2 font-medium">{t("assets.colWritten")}</th>
                      <th className="px-4 py-2 font-medium">{t("assets.colRun")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mats.map((m, i) => (
                      <tr key={i} className="border-b border-border-subtle/50">
                        <td className="px-4 py-2 text-text-secondary">
                          {new Date(m.materialized_at).toLocaleString()}
                        </td>
                        <td className="px-4 py-2 font-mono text-text">{m.records_written}</td>
                        <td className="px-4 py-2">
                          {m.run_id ? (
                            <Link
                              href={`/w/${slug}/runs/${m.run_id}`}
                              className="text-accent hover:underline"
                            >
                              {t("assets.openRun")}
                            </Link>
                          ) : (
                            <span className="text-text-muted">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="px-4 py-8 text-center text-sm text-text-muted">
                  {t("assets.matEmpty")}
                </div>
              )}
            </Card>
          </>
        )}
      </main>
    </>
  );
}
