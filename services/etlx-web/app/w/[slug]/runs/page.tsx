"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ActivityIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { StatusBadge } from "@/components/ui/status-badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiError, runsApi, type RunSummary } from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

function formatTimestamp(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return d.toLocaleString();
}

function formatDuration(s: number | null): string {
  if (s == null) return "—";
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

function buildColumns(t: Translate): Column<RunSummary>[] {
  return [
    {
      key: "status",
      header: t("common.status"),
      className: "w-32",
      cell: (r) => <StatusBadge status={r.status} />,
    },
    {
      key: "pipeline",
      header: t("common.pipeline"),
      cell: (r) => (
        <span className="font-mono text-xs text-text-secondary">
          {r.pipeline_id.slice(0, 8)}…
        </span>
      ),
    },
    {
      key: "scheduled",
      header: t("common.scheduled"),
      cell: (r) => (
        <span className="text-text-secondary">
          {formatTimestamp(r.scheduled_at)}
        </span>
      ),
    },
    {
      key: "duration",
      header: t("common.duration"),
      cell: (r) => (
        <span className="text-text-secondary">
          {formatDuration(r.duration_seconds)}
        </span>
      ),
    },
    {
      key: "rw",
      header: t("runs.colReadWritten"),
      cell: (r) => (
        <span className="font-mono text-xs text-text-secondary">
          {r.records_read.toLocaleString()} /{" "}
          {r.records_written.toLocaleString()}
        </span>
      ),
    },
    {
      key: "error",
      header: t("common.error"),
      cell: (r) =>
        r.error_class ? (
          <span className="rounded-sm bg-error/10 px-2 py-0.5 font-mono text-xs text-error">
            {r.error_class}
          </span>
        ) : (
          <span className="text-text-muted">—</span>
        ),
    },
  ];
}

export default function RunsPage() {
  const router = useRouter();
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [rows, setRows] = useState<RunSummary[] | null>(null);

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;

    async function fetchOnce(workspaceId: string) {
      try {
        const list = await runsApi.list(workspaceId, { limit: 100 });
        if (!cancelled) setRows(list);
      } catch (err) {
        if (!cancelled) {
          toast.error(
            err instanceof ApiError ? err.message : t("runs.loadFailed"),
          );
          setRows([]);
        }
      }
    }

    void fetchOnce(ws.id);
    // Poll every 5s so a running pipeline visibly progresses.
    const id = window.setInterval(() => {
      void fetchOnce(ws.id);
    }, 5_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [ws, t]);

  return (
    <>
      <Header
        title={t("nav.runs")}
        subtitle={
          ws
            ? t("common.workspaceSubtitle", { name: ws.name })
            : t("common.loadingWorkspace")
        }
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        <Card>
          {rows === null ? (
            <div className="py-12 text-center text-sm text-text-muted">
              {t("common.loading")}
            </div>
          ) : (
            <DataTable
              columns={buildColumns(t)}
              rows={rows}
              onRowClick={(row) => {
                if (ws) router.push(`/w/${ws.slug}/runs/${row.id}`);
              }}
              emptyState={
                <EmptyState
                  icon={<ActivityIcon size={36} strokeWidth={1.5} />}
                  title={t("runs.emptyTitle")}
                  description={t("runs.emptyDesc")}
                />
              }
            />
          )}
        </Card>
      </main>
    </>
  );
}
