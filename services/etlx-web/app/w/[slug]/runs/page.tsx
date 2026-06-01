"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  ActivityIcon,
  CalendarClockIcon,
  ExternalLinkIcon,
  EyeIcon,
  HandIcon,
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
import { Button } from "@/components/ui/button";
import {
  ApiError,
  pipelinesApi,
  runsApi,
  type PipelineSummary,
  type RunStatus,
  type RunSummary,
} from "@/lib/api";
import { migrationSummaryOf } from "@/lib/migration-utils";
import { useCurrentUser } from "@/components/providers/auth-provider";
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
  /** Phase ABU (2026-06-01) — used to render "by you" when the
   *  signed-in user fired the run, mirroring ABT on the audit log. */
  currentUserId: string | null,
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
      // Phase ABG (2026-06-01) — Trigger source chip. Quickly
      // identifies which runs were auto-fired by a schedule vs
      // user-triggered, so operators can spot "the cron is
      // misbehaving" or "this was a manual one-off".
      key: "trigger",
      header: t("runs.colTrigger"),
      className: "w-28",
      cell: (r) => {
        if (r.schedule_id) {
          return (
            <span
              className="inline-flex h-5 items-center gap-1 rounded-sm bg-accent/15 px-1.5 text-[11px] text-accent"
              title={t("runs.triggerScheduleTitle")}
            >
              <CalendarClockIcon size={11} />
              {t("runs.triggerSchedule")}
            </span>
          );
        }
        if (r.triggered_by_user_id) {
          const byYou = r.triggered_by_user_id === currentUserId;
          return (
            <span
              className="inline-flex h-5 items-center gap-1 rounded-sm bg-overlay px-1.5 text-[11px] text-text-secondary"
              title={
                byYou
                  ? t("runs.triggerManualByYouTitle")
                  : t("runs.triggerManualTitle")
              }
            >
              <HandIcon size={11} />
              {byYou ? t("runs.triggerManualByYou") : t("runs.triggerManual")}
            </span>
          );
        }
        return <span className="text-text-muted">—</span>;
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

/** Status filter options for the runs list dropdown. The empty string
 *  is the "all" choice and means we send no ``status=`` query param to
 *  the server (workspace-wide). Order mirrors a typical operator's
 *  mental sort: pending/running first (active), then terminal states
 *  by usefulness (failed first — the row you're hunting). Phase S
 *  (2026-05-28). */
const STATUS_OPTIONS: { value: "" | RunStatus; labelKey: keyof Messages }[] = [
  { value: "", labelKey: "runs.statusFilterAll" },
  { value: "pending", labelKey: "status.pending" },
  { value: "running", labelKey: "status.running" },
  { value: "failed", labelKey: "status.failed" },
  { value: "succeeded", labelKey: "status.succeeded" },
  { value: "cancelled", labelKey: "status.cancelled" },
];

/** Page size for the runs list — keep below the server's 500 ceiling
 *  while large enough that most workspaces fit in one fetch. ``Load
 *  more`` adds another batch up to the cap (server enforces). */
const PAGE_SIZE = 100;
const MAX_LOAD = 500;

export default function RunsPage() {
  const router = useRouter();
  const { slug } = useParams<{ slug: string }>();
  const search = useSearchParams();
  const pipelineFilter = search.get("pipeline");
  // Status filter — URL-synced via ``?status=`` so shared links land on
  // the same view. ``null`` means the filter is off (show all).
  const statusFilter = (search.get("status") as RunStatus | null) ?? null;
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  // Phase ABU (2026-06-01) — pass to buildColumns for "by you" chip.
  const currentUser = useCurrentUser();
  const [rows, setRows] = useState<RunSummary[] | null>(null);
  const [pipelines, setPipelines] = useState<PipelineSummary[]>([]);
  // Visible row count (grows on Load more). Polling re-fetches with
  // this limit so a long view stays current. ``maxedOut`` is true once
  // the last fetch returned fewer rows than ``limit`` — the queue is
  // exhausted and Load more should be disabled.
  const [limit, setLimit] = useState<number>(PAGE_SIZE);
  const [maxedOut, setMaxedOut] = useState<boolean>(false);
  const [loadingMore, setLoadingMore] = useState<boolean>(false);
  const rowMenu = useContextMenu();
  const rowMenuTargetRef = useRef<RunSummary | null>(null);

  // Filter change → reset paging to the first page. Without this the
  // user switches to "failed only" and keeps the old "succeeded too"
  // limit, which is confusing.
  useEffect(() => {
    setLimit(PAGE_SIZE);
    setMaxedOut(false);
  }, [pipelineFilter, statusFilter]);

  /** Update the ``?status=`` URL param (preserve any ``?pipeline=``).
   *  Empty value clears the filter. */
  const setStatusFilter = useCallback(
    (next: "" | RunStatus) => {
      const params = new URLSearchParams(search.toString());
      if (next) params.set("status", next);
      else params.delete("status");
      const qs = params.toString();
      router.push(qs ? `/w/${slug}/runs?${qs}` : `/w/${slug}/runs`);
    },
    [router, search, slug],
  );

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
        // Server-side filters keep the wire small. URL params (``?pipeline=`` /
        // ``?status=``) are the SSoT; UI controls write to URL + this read
        // sees them on the next tick.
        const query: Parameters<typeof runsApi.list>[1] = { limit };
        if (pipelineFilter) query.pipeline_id = pipelineFilter;
        if (statusFilter) query.status = statusFilter;
        const list = await runsApi.list(workspaceId, query);
        if (!cancelled) {
          setRows(list);
          // We hit the bottom of the queue when the server returned
          // fewer rows than we asked for — no point letting "Load more"
          // burn another roundtrip. Also fires when we hit the 500 cap.
          setMaxedOut(list.length < limit || list.length >= MAX_LOAD);
        }
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
  }, [ws, t, pipelineFilter, statusFilter, limit]);

  /** "Load more" — bump the limit by another page (server cap 500 still
   *  applies). The effect above re-fetches automatically when limit
   *  changes. Disabled when ``maxedOut`` (server returned a short
   *  page or we hit the cap). */
  const onLoadMore = useCallback(() => {
    setLoadingMore(true);
    setLimit((cur) => Math.min(cur + PAGE_SIZE, MAX_LOAD));
    // The actual fetch fires via the effect above; flip loadingMore
    // off on next render after rows update. The simple approach: clear
    // on the next effect tick by listening to rows changing.
    setTimeout(() => setLoadingMore(false), 0);
  }, []);

  const pipelineNameById = new Map(pipelines.map((p) => [p.id, p.name]));
  // Phase ABL (2026-06-01) — migration-aware "Open pipeline" link. The
  // generic pipeline editor is wrong for migrations: that surface is
  // managed under /w/.../migrations/[id], not /pipelines/[id]/edit.
  // Detect via the same ``migrationSummaryOf`` predicate the rest of
  // the app uses so this stays consistent with the migration tab.
  const isMigrationById = new Map(
    pipelines.map((p) => [p.id, migrationSummaryOf(p.current_config_json) !== null]),
  );
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
        actions={
          // Status filter dropdown (Phase S, 2026-05-28). URL-synced via
          // ?status= — share-link friendly. The visual is a plain
          // <select> styled to match Input — keeps the runs page free
          // of a heavier dropdown primitive while still being clearly
          // interactive (pointer cursor inherited from globals.css).
          <label className="flex items-center gap-1.5 text-xs text-text-secondary">
            <span className="text-text-muted">{t("runs.statusFilterLabel")}</span>
            <select
              value={statusFilter ?? ""}
              onChange={(e) => setStatusFilter(e.target.value as "" | RunStatus)}
              className="h-8 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
            >
              {STATUS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {t(opt.labelKey)}
                </option>
              ))}
            </select>
          </label>
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
                href={
                  isMigrationById.get(pipelineFilter)
                    ? `/w/${ws.slug}/migrations/${pipelineFilter}`
                    : `/w/${ws.slug}/pipelines/${pipelineFilter}/edit`
                }
                className="text-xs text-accent hover:underline"
              >
                {isMigrationById.get(pipelineFilter)
                  ? t("runs.openMigration")
                  : t("runs.openPipeline")}
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
              columns={buildColumns(
                t,
                pipelineNameById,
                currentUser?.id ?? null,
              )}
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
          {/* Pagination footer — only renders when we have actual rows.
              Shows "Showing X runs" + "Load more" when there's more to
              fetch + a hint when we hit the 500 cap. Phase S
              (2026-05-28). */}
          {rows && rows.length > 0 ? (
            <div className="mt-4 flex items-center justify-between border-t border-border-subtle pt-3 text-xs text-text-muted">
              <span>{t("runs.showing", { count: rows.length })}</span>
              {!maxedOut ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={onLoadMore}
                  loading={loadingMore}
                >
                  {t("runs.loadMore")}
                </Button>
              ) : rows.length >= MAX_LOAD ? (
                <span className="text-text-secondary">
                  {t("runs.atCap", { cap: MAX_LOAD })}
                </span>
              ) : (
                <span className="text-text-secondary">{t("runs.endOfList")}</span>
              )}
            </div>
          ) : null}
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
            if (!r || !ws) return;
            // Phase ABL (2026-06-01) — same migration-aware split as
            // the banner link so the right-click goes to the surface
            // the operator actually edits this pipeline on.
            const dest = isMigrationById.get(r.pipeline_id)
              ? `/w/${ws.slug}/migrations/${r.pipeline_id}`
              : `/w/${ws.slug}/pipelines/${r.pipeline_id}/edit`;
            router.push(dest);
          }}
        >
          {(() => {
            const r = rowMenuTargetRef.current;
            return r && isMigrationById.get(r.pipeline_id)
              ? t("runs.menuOpenMigration")
              : t("runs.menuOpenPipeline");
          })()}
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
