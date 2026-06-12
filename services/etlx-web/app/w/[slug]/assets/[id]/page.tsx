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
  type AssetColumnLineageGraphResponse,
  type AssetLineageGraphResponse,
  type AssetMaterializationEntry,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import { relativeTime, absoluteTime } from "@/lib/format-time";

export default function AssetDetailPage() {
  const { slug, id } = useParams<{ slug: string; id: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const router = useRouter();
  const { t } = useLocale();
  const [lineage, setLineage] = useState<AssetLineageGraphResponse | null>(null);
  const [assetDepth, setAssetDepth] = useState(3);
  const [mats, setMats] = useState<AssetMaterializationEntry[] | null>(null);
  const [columnLineage, setColumnLineage] =
    useState<AssetColumnLineageGraphResponse | null>(null);
  // Multi-hop drill-down depth (2026-06-12) — changing refetches the graph.
  const [lineageDepth, setLineageDepth] = useState(3);

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    Promise.all([
      assetsApi.lineageGraph(ws.id, id, assetDepth),
      assetsApi.materializations(ws.id, id),
      assetsApi.columnLineageGraph(ws.id, id, lineageDepth),
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
  }, [ws, id, t, lineageDepth, assetDepth]);

  return (
    <>
      <Header
        title={lineage ? lineage.asset_key : t("assets.title")}
        // Phase ADN (2026-06-04) — surface freshness in the header so an
        // analyst sees "when was this last produced?" the moment they
        // open the asset, not only by scrolling to the materializations
        // table. mats[0] is the latest (API orders desc).
        subtitle={
          mats && mats.length > 0
            ? t("assets.lastMaterializedAt", {
                time: relativeTime(mats[0].materialized_at, t),
              })
            : t("assets.lineage")
        }
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
              <div className="border-b border-border-subtle px-4 py-3 text-sm font-semibold text-text">
                {t("assets.lineage")}
              </div>
              <div className="p-2">
                <LineageGraph
                  graph={lineage}
                  depth={assetDepth}
                  onDepthChange={setAssetDepth}
                  onSelect={(assetId) => router.push(`/w/${slug}/assets/${assetId}`)}
                />
              </div>
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
              ) : columnLineage.assets.every((a) => a.columns.length === 0) ? (
                <div className="px-4 py-8 text-center text-sm text-text-muted">
                  {t("assets.columnLineageEmpty")}
                </div>
              ) : (
                <div className="p-2">
                  <ColumnLineageGraph
                    graph={columnLineage}
                    depth={lineageDepth}
                    onDepthChange={setLineageDepth}
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
                        {/* Phase ACX (2026-06-04) — relative time +
                            absolute on hover, matching the catalog list
                            and every other surface. */}
                        <td
                          className="px-4 py-2 text-text-secondary"
                          title={absoluteTime(m.materialized_at)}
                        >
                          {relativeTime(m.materialized_at, t)}
                        </td>
                        <td className="px-4 py-2 font-mono text-text">
                          {m.records_written.toLocaleString()}
                          {/* Phase AFM (2026-06-04) — delta vs the previous
                              (older) materialization. mats is newest-first,
                              so the prior run is mats[i+1]. A sudden drop in
                              rows written is an analyst's data-quality cue. */}
                          {i < mats.length - 1 &&
                          m.records_written !== mats[i + 1].records_written ? (
                            <span
                              className="ml-2 text-xs text-text-muted"
                              title={t("assets.matDeltaTitle")}
                            >
                              {m.records_written > mats[i + 1].records_written
                                ? "+"
                                : "−"}
                              {Math.abs(
                                m.records_written - mats[i + 1].records_written,
                              ).toLocaleString()}
                            </span>
                          ) : null}
                        </td>
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
