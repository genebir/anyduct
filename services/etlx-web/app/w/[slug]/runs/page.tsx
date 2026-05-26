"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  ActivityIcon,
  ExternalLinkIcon,
  EyeIcon,
  RotateCcwIcon,
  WorkflowIcon,
  XIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import {
  ContextMenu,
  ContextMenuItem,
  ContextMenuSeparator,
  useContextMenu,
} from "@/components/ui/context-menu";
import { StatusBadge } from "@/components/ui/status-badge";
import { EmptyState } from "@/components/ui/empty-state";
import {
  ApiError,
  pipelinesApi,
  runsApi,
  type PipelineSummary,
  type RunSummary,
} from "@/lib/api";
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

function buildColumns(
  t: Translate,
  pipelineNameById: Map<string, string>,
): Column<RunSummary>[] {
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
      cell: (r) => {
        const name = pipelineNameById.get(r.pipeline_id);
        return name ? (
          <span className="text-text-secondary">{name}</span>
        ) : (
          <span className="font-mono text-xs text-text-secondary">
            {r.pipeline_id.slice(0, 8)}…
          </span>
        );
      },
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
  const search = useSearchParams();
  const pipelineFilter = search.get("pipeline");
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [rows, setRows] = useState<RunSummary[] | null>(null);
  const [pipelines, setPipelines] = useState<PipelineSummary[]>([]);
  const rowMenu = useContextMenu();
  const rowMenuTargetRef = useRef<RunSummary | null>(null);

  // Pipeline list is a one-shot — used to render readable names in the
  // table and the filter banner instead of bare UUIDs.
  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    void pipelinesApi.list(ws.id).then((ps) => {
      if (!cancelled) setPipelines(ps);
    });
    return () => {
      cancelled = true;
    };
  }, [ws]);

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;

    async function fetchOnce(workspaceId: string) {
      try {
        // ``?pipeline=<id>`` URL query → server-side filter, so we don't
        // shovel hundreds of unrelated runs to the client.
        const query: Parameters<typeof runsApi.list>[1] = { limit: 100 };
        if (pipelineFilter) query.pipeline_id = pipelineFilter;
        const list = await runsApi.list(workspaceId, query);
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
  }, [ws, t, pipelineFilter]);

  const pipelineNameById = new Map(pipelines.map((p) => [p.id, p.name]));
  const filteredPipelineName = pipelineFilter
    ? pipelineNameById.get(pipelineFilter) ?? pipelineFilter.slice(0, 8) + "…"
    : null;

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
      {/* Pipeline filter banner — shown when arriving from the pipeline
          editor's "View runs" link. One-click clear returns to the unfiltered
          workspace-wide list. */}
      {pipelineFilter && filteredPipelineName ? (
        <div className="flex shrink-0 items-center justify-between gap-2 border-b border-accent/40 bg-accent/10 px-6 py-2 text-sm">
          <span className="text-text">
            {t("runs.filteredByPipeline", { name: filteredPipelineName })}
          </span>
          <div className="flex items-center gap-2">
            {ws && pipelineFilter ? (
              <Link
                href={`/w/${ws.slug}/pipelines/${pipelineFilter}/edit`}
                className="text-xs text-accent hover:underline"
              >
                {t("runs.openPipeline")}
              </Link>
            ) : null}
            <Link
              href={ws ? `/w/${ws.slug}/runs` : "#"}
              className="inline-flex items-center gap-1 rounded-sm px-2 py-1 text-xs text-text-secondary hover:bg-overlay hover:text-text"
              aria-label={t("runs.clearFilter")}
            >
              <XIcon size={12} />
              {t("runs.clearFilter")}
            </Link>
          </div>
        </div>
      ) : null}
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        <Card>
          {rows === null ? (
            <div className="py-12 text-center text-sm text-text-muted">
              {t("common.loading")}
            </div>
          ) : (
            <DataTable
              columns={buildColumns(t, pipelineNameById)}
              rows={rows}
              onRowClick={(row) => {
                if (ws) router.push(`/w/${ws.slug}/runs/${row.id}`);
              }}
              onRowContextMenu={(row, e) => {
                rowMenuTargetRef.current = row;
                rowMenu.openOnEvent(e);
              }}
              emptyState={
                <EmptyState
                  icon={<ActivityIcon size={36} strokeWidth={1.5} />}
                  title={t("runs.emptyTitle")}
                  description={
                    pipelineFilter
                      ? t("runs.emptyDescForPipeline")
                      : t("runs.emptyDesc")
                  }
                />
              }
            />
          )}
        </Card>
      </main>

      {/* Row right-click — quick actions without leaving the list. */}
      <ContextMenu menu={rowMenu}>
        <ContextMenuItem
          icon={<EyeIcon size={14} />}
          onSelect={() => {
            const r = rowMenuTargetRef.current;
            if (r && ws) router.push(`/w/${ws.slug}/runs/${r.id}`);
          }}
        >
          {t("runs.menuOpen")}
        </ContextMenuItem>
        <ContextMenuItem
          icon={<WorkflowIcon size={14} />}
          onSelect={() => {
            const r = rowMenuTargetRef.current;
            if (r && ws) router.push(`/w/${ws.slug}/pipelines/${r.pipeline_id}/edit`);
          }}
        >
          {t("runs.menuOpenPipeline")}
        </ContextMenuItem>
        <ContextMenuItem
          icon={<ExternalLinkIcon size={14} />}
          onSelect={() => {
            const r = rowMenuTargetRef.current;
            if (r && ws)
              router.push(`/w/${ws.slug}/runs?pipeline=${r.pipeline_id}`);
          }}
        >
          {t("runs.menuFilterPipeline")}
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          icon={<RotateCcwIcon size={14} />}
          disabled={(() => {
            const r = rowMenuTargetRef.current;
            return !r || (r.status !== "failed" && r.status !== "cancelled");
          })()}
          onSelect={async () => {
            const r = rowMenuTargetRef.current;
            if (!r || !ws) return;
            try {
              const fresh = await runsApi.retry(ws.id, r.id);
              toast.success(t("runs.menuRetried", { id: fresh.id.slice(0, 8) }));
            } catch (err) {
              toast.error(
                err instanceof ApiError ? err.message : t("runs.menuRetryFailed"),
              );
            }
          }}
        >
          {t("runs.menuRetry")}
        </ContextMenuItem>
      </ContextMenu>
    </>
  );
}
