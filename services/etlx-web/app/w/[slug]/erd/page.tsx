"use client";

/**
 * /w/[slug]/erd — Phase AHD. Server-backed list of ERD diagrams (like the
 * pipelines / migrations list). Pick one to open the designer, or create
 * a new one. Persisted via the REST API (ADR-0090), shared across users.
 */

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { BoxesIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiError, erdApi, type ErdDiagramSummary } from "@/lib/api";
import { EMPTY_DESIGN } from "@/lib/erd-design";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import { absoluteTime, relativeTime } from "@/lib/format-time";

export default function ErdListPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const router = useRouter();
  const { t } = useLocale();
  const [rows, setRows] = useState<ErdDiagramSummary[] | null>(null);
  const [creating, setCreating] = useState(false);

  const refresh = useCallback(async (wsId: string) => {
    try {
      setRows(await erdApi.list(wsId));
    } catch {
      setRows([]);
    }
  }, []);

  useEffect(() => {
    if (ws?.id) void refresh(ws.id);
  }, [ws?.id, refresh]);

  const onNew = async () => {
    if (!ws?.id || creating) return;
    setCreating(true);
    try {
      const created = await erdApi.create(ws.id, {
        name: `Untitled ${(rows?.length ?? 0) + 1}`,
        design_json: EMPTY_DESIGN,
      });
      router.push(`/w/${slug}/erd/${created.id}`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : t("erdList.createError"));
      setCreating(false);
    }
  };

  const onDelete = async (id: string) => {
    if (!ws?.id) return;
    try {
      await erdApi.delete(ws.id, id);
      await refresh(ws.id);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : t("common.error"));
    }
  };

  return (
    <div>
      <Header
        title={t("nav.erd")}
        subtitle={ws ? t("common.workspaceSubtitle", { name: ws.name }) : t("common.loadingWorkspace")}
        actions={
          <Button size="sm" variant="secondary" onClick={() => void onNew()} disabled={creating || !ws?.id}>
            <PlusIcon size={14} />
            {t("erdList.new")}
          </Button>
        }
      />
      <div className="p-4">
        {rows === null ? (
          <Card className="p-8 text-center text-sm text-text-muted">{t("common.loading")}</Card>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<BoxesIcon size={28} />}
            title={t("erdList.emptyTitle")}
            description={t("erdList.emptyDesc")}
          />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {rows.map((d) => (
              <Card key={d.id} className="flex items-center justify-between gap-2 p-3">
                <Link href={`/w/${slug}/erd/${d.id}`} className="min-w-0 flex-1">
                  <div className="truncate font-medium text-text">{d.name}</div>
                  <div className="mt-0.5 text-xs text-text-muted">
                    {t("erdList.tableCount", { n: d.table_count })} ·{" "}
                    <span title={absoluteTime(d.updated_at)}>{relativeTime(d.updated_at, t)}</span>
                  </div>
                </Link>
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label={t("erdList.deleteAria", { name: d.name })}
                  className="hover:text-error"
                  onClick={() => void onDelete(d.id)}
                >
                  <Trash2Icon size={14} />
                </Button>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
