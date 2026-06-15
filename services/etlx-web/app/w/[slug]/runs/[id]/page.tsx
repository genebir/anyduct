"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeftIcon,
  BanIcon,
  CalendarClockIcon,
  ExternalLinkIcon,
  HandIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SearchXIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  ApiError,
  pipelinesApi,
  runsApi,
  type DlqPreviewResponse,
  type LogLevel,
  type NodeRunEntry,
  type PipelineSummary,
  type PipelineVersionEntry,
  type RunDetail,
  type RunLogEntry,
  type RunMetricEntry,
  type RunStatus,
} from "@/lib/api";
import { RunDagGraph } from "@/components/runs/run-dag-graph";
import { relativeTime, absoluteTime } from "@/lib/format-time";
import { migrationSummaryOf } from "@/lib/migration-utils";
import { useCurrentUser } from "@/components/providers/auth-provider";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";
import { cn } from "@/lib/cn";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

const TERMINAL: ReadonlySet<RunStatus> = new Set([
  "succeeded",
  "failed",
  "cancelled",
]);

function fmt(ts: string | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts).toLocaleString();
}

function fmtDuration(s: number | null): string {
  if (s == null) return "—";
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s.toFixed(2)} s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

const LEVEL_CLASSES: Record<LogLevel, string> = {
  debug: "text-text-muted",
  info: "text-text-secondary",
  warning: "text-warning",
  error: "text-error",
};

/** Phase AER (2026-06-04) — ordinal for the "min level" log filter. */
const LEVEL_ORDER: Record<LogLevel, number> = {
  debug: 0,
  info: 1,
  warning: 2,
  error: 3,
};

export default function RunDetailPage() {
  const { slug, id } = useParams<{ slug: string; id: string }>();
  const router = useRouter();
  const ws = useWorkspaceFromSlug(slug);
  // Phase ABW (2026-06-01) — used to render "by you" on the trigger
  // source field in Summary.
  const currentUser = useCurrentUser();
  const { t } = useLocale();
  const [run, setRun] = useState<RunDetail | null>(null);
  // Phase ABL (2026-06-01) — pipeline summary lookup. Used to decide
  // whether the "pipeline" link points to the migration page or the
  // generic editor. One-shot fetch per run since pipeline identity
  // doesn't change mid-run.
  const [pipeline, setPipeline] = useState<PipelineSummary | null>(null);
  // Phase ABR (2026-06-01) — the *exact* version that ran. The
  // pipeline's current config may have been edited since this run,
  // so debugging a failure requires the config AS IT WAS, not as
  // it is now.
  const [ranVersion, setRanVersion] = useState<PipelineVersionEntry | null>(
    null,
  );
  const [logs, setLogs] = useState<RunLogEntry[] | null>(null);
  const [metrics, setMetrics] = useState<RunMetricEntry[] | null>(null);
  const [nodeRuns, setNodeRuns] = useState<NodeRunEntry[] | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  // Phase M (2026-05-26): drill-down filter for the log panel. ``null``
  // means "all logs" — the default. Setting it to a node id (via the
  // NODE chip in LogView or the click on a node card in RunDagGraph)
  // appends ``?node_id=<id>`` to the next /logs poll so the server
  // returns only that node's window. The polling effect below reads
  // this value via a ref so changing the filter doesn't tear down the
  // whole tick loop.
  const [nodeFilter, setNodeFilter] = useState<string | null>(null);
  const nodeFilterRef = useRef<string | null>(null);
  useEffect(() => {
    nodeFilterRef.current = nodeFilter;
  }, [nodeFilter]);
  // Tracked separately so the page can render a clear "this run doesn't
  // exist" empty state instead of a permanent loading shimmer when the
  // backend returns 404 (deleted / wrong workspace / stale list). Carries
  // the server's ``detail`` + the URL pieces we sent so the empty state
  // can show *why* — invaluable when the row was right there in the list
  // and the user is staring at the page asking "but it exists?".
  const [notFound, setNotFound] = useState<{
    status: number;
    detail: string;
    url: string;
  } | null>(null);
  const logsEndRef = useRef<HTMLDivElement | null>(null);
  // Toast side-resource failures (logs / metrics / node-runs) at most once
  // per mount — polling re-runs the tick every 2s while a run is live and
  // a stuck 500 on logs would otherwise spam the operator every refresh.
  const toastedSideErrorRef = useRef<Set<string>>(new Set());

  // Poll the trio (run / logs / metrics) until status is terminal. Once
  // terminal, fetch one more time and stop.
  useEffect(() => {
    if (!ws) return;
    let cancelled = false;

    async function tick(workspaceId: string): Promise<boolean> {
      // Fetch the run FIRST and on its own — Promise.all conflated 404s
      // from any of the four sub-resources with "run missing" before, but
      // they have independent shapes (logs / metrics / node-runs may be
      // empty for a valid run; only get() is the existence check).
      let r: RunDetail;
      try {
        r = await runsApi.get(workspaceId, id);
      } catch (err) {
        if (cancelled) return true;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound({
            status: err.status,
            detail: err.message || "run not found",
            url: `/workspaces/${workspaceId}/runs/${id}`,
          });
          return true;
        }
        toast.error(
          err instanceof ApiError ? err.message : t("runDetail.loadFailed"),
        );
        return true;
      }
      // Run exists — fetch each side resource INDEPENDENTLY. If we bundle
      // them in a Promise.all, one rejection drops all three (e.g., a
      // serialization error on logs would also blank out metrics + node-
      // runs). Settle each on its own so panels with healthy data still
      // render. Failures land as the panel's own "empty list" + a one-
      // time toast so the user knows it's a fetch problem, not "no data".
      setRun(r);
      // Phase ABL (2026-06-01) — one-shot pipeline fetch. Best-effort:
      // a 404 here just means the link falls back to the generic
      // editor (the same as before this slice).
      if (!cancelled && r.pipeline_id && !pipeline) {
        void pipelinesApi
          .get(workspaceId, r.pipeline_id)
          .then((p) => {
            if (!cancelled) setPipeline(p);
          })
          .catch(() => {
            /* best-effort */
          });
      }
      // Phase ABR (2026-06-01) — fetch versions ONCE per run and pick
      // the one matching ``pipeline_version_id``. Versions are
      // immutable so this never needs to re-fetch.
      if (!cancelled && r.pipeline_id && r.pipeline_version_id && !ranVersion) {
        const targetVid = r.pipeline_version_id;
        void pipelinesApi
          .listVersions(workspaceId, r.pipeline_id)
          .then((versions) => {
            if (cancelled) return;
            const match = versions.find((v) => v.id === targetVid);
            if (match) setRanVersion(match);
          })
          .catch(() => {
            /* best-effort */
          });
      }
      // Honour the active node filter on every poll — re-applying it
      // each tick (instead of carrying client-side state) keeps the
      // server query selective even for long runs with thousands of
      // log lines, and means the autoscroll bottom-ref still works.
      const logsQuery = { limit: 1000 } as {
        limit?: number;
        node_id?: string;
      };
      if (nodeFilterRef.current) logsQuery.node_id = nodeFilterRef.current;
      const [logsR, metricsR, nodeRunsR] = await Promise.allSettled([
        runsApi.logs(workspaceId, id, logsQuery),
        runsApi.metrics(workspaceId, id),
        runsApi.nodeRuns(workspaceId, id),
      ]);
      if (cancelled) return true;
      if (logsR.status === "fulfilled") {
        setLogs(logsR.value);
      } else {
        // Empty list (not null) so the LogView shows "no logs yet" instead
        // of the indefinite loading spinner.
        setLogs([]);
        const e = logsR.reason;
        if (!toastedSideErrorRef.current.has("logs")) {
          toastedSideErrorRef.current.add("logs");
          toast.error(
            e instanceof ApiError
              ? `logs: ${e.status} ${e.message}`
              : `logs: ${String(e)}`,
          );
        }
        // eslint-disable-next-line no-console
        console.warn("runDetail logs failed", e);
      }
      if (metricsR.status === "fulfilled") {
        setMetrics(metricsR.value);
      } else {
        setMetrics([]);
        // eslint-disable-next-line no-console
        console.warn("runDetail metrics failed", metricsR.reason);
      }
      if (nodeRunsR.status === "fulfilled") {
        setNodeRuns(nodeRunsR.value);
      } else {
        setNodeRuns([]);
        // eslint-disable-next-line no-console
        console.warn("runDetail node-runs failed", nodeRunsR.reason);
      }
      return TERMINAL.has(r.status);
    }

    let timer: number | undefined;
    void tick(ws.id).then((done) => {
      if (cancelled || done) return;
      const schedule = () => {
        timer = window.setTimeout(async () => {
          if (cancelled) return;
          const finished = await tick(ws.id);
          if (!finished && !cancelled) schedule();
        }, 2_000);
      };
      schedule();
    });

    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [ws, id, t]);

  // Autoscroll the log pane when new lines arrive.
  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [logs?.length]);

  // Filter change → immediate refetch (don't wait up to 2s for the next
  // polling tick). Phase M (2026-05-26). Skipped while run/ws aren't
  // ready — the initial load + polling loop will pick up the filter on
  // its first tick.
  useEffect(() => {
    if (!ws || !run) return;
    let cancelled = false;
    const query = nodeFilter
      ? { limit: 1000, node_id: nodeFilter }
      : { limit: 1000 };
    runsApi.logs(ws.id, run.id, query).then(
      (fresh) => {
        if (!cancelled) setLogs(fresh);
      },
      () => {
        // Filter applied to an empty/erroring window — surface as the
        // panel's empty state. The polling loop's own error handling
        // already covers the toast.
        if (!cancelled) setLogs([]);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [ws, run, nodeFilter]);

  const grouped = useMemo(() => groupMetrics(metrics ?? []), [metrics]);

  // Phase AFB (2026-06-04) — records routed to the dead-letter queue.
  // Core emits the ``etl_plugins.errors`` counter with ``routed: "dlq"``
  // for each batch a transform fails on (pipeline.py). AEZ surfaces the
  // generic read-vs-written gap; this names the DLQ portion specifically
  // so the operator knows bad records were captured (not just dropped).
  const dlqRouted = useMemo(
    () =>
      (metrics ?? [])
        .filter(
          (m) =>
            m.name === "etl_plugins.errors" &&
            (m.attrs_json as { routed?: unknown }).routed === "dlq",
        )
        .reduce((sum, m) => sum + m.value, 0),
    [metrics],
  );

  // First failed node in execution order — drives the "Failed at"
  // chip on the run-level error card (Phase N, 2026-05-28). Order
  // matters because a graph can have multiple failures; the first
  // one is usually the root cause and the rest cascade. We pick the
  // earliest ``finished_at`` among failed nodes; ties broken by
  // node_id for stability.
  const failedNode = useMemo(() => {
    if (!nodeRuns) return null;
    const failed = nodeRuns.filter((n) => n.status === "failed");
    if (failed.length === 0) return null;
    return [...failed].sort((a, b) => {
      const ta = a.finished_at ? Date.parse(a.finished_at) : Number.POSITIVE_INFINITY;
      const tb = b.finished_at ? Date.parse(b.finished_at) : Number.POSITIVE_INFINITY;
      if (ta !== tb) return ta - tb;
      return a.node_id.localeCompare(b.node_id);
    })[0];
  }, [nodeRuns]);

  async function onRetry() {
    if (!ws || !run) return;
    setRetrying(true);
    try {
      const fresh = await runsApi.retry(ws.id, run.id);
      toast.success(t("runDetail.retryQueued", { id: fresh.id.slice(0, 8) }));
      // Phase ABK (2026-06-01) — auto-navigate to the new run. The
      // operator clicks Retry to *monitor* the next attempt, not to
      // dwell on the failed one. Without this they have to find the
      // new run on the runs list themselves. Stay on the failed page
      // only if the retry API somehow returns the same id (defensive).
      if (fresh.id !== run.id) {
        router.push(`/w/${slug}/runs/${fresh.id}`);
      }
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("runDetail.retryFailed"),
      );
    } finally {
      setRetrying(false);
    }
  }

  // Phase P (2026-05-28): request cancellation. PENDING rows flip to
  // CANCELLED immediately (server-side); RUNNING rows come back with
  // ``cancel_requested_at`` stamped and the worker lands the actual
  // status flip at the next node boundary. Either way we patch the
  // local ``run`` so the UI reflects the new state without waiting
  // for the next polling tick.
  async function onCancel() {
    if (!ws || !run) return;
    setCancelling(true);
    try {
      const fresh = await runsApi.cancel(ws.id, run.id);
      setRun(fresh);
      toast.success(
        fresh.status === "cancelled"
          ? t("runDetail.cancelledNow")
          : t("runDetail.cancelRequested"),
      );
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("runDetail.cancelFailed"),
      );
    } finally {
      setCancelling(false);
      setCancelOpen(false);
    }
  }

  return (
    <>
      <Header
        title={
          <span className="flex items-center gap-3">
            <Link
              href={ws ? `/w/${ws.slug}/runs` : "#"}
              aria-label={t("runDetail.backAria")}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-text-muted transition duration-150 hover:bg-overlay hover:text-text"
            >
              <ArrowLeftIcon size={16} />
            </Link>
            {t("runDetail.title", { id: id.slice(0, 8) })}
            {run ? <StatusBadge status={run.status} /> : null}
            {/* Phase DLQ-6 (2026-06-04) — a "succeeded" run that routed
                records to the DLQ looks fully healthy at a glance (green
                badge). Flag it as partial so the operator notices data was
                dropped, not just lost in the right-column count. */}
            {run?.status === "succeeded" && dlqRouted > 0 ? (
              <span
                className="inline-flex items-center rounded-sm border border-warning/40 bg-warning/10 px-2 py-0.5 text-xs font-medium text-warning"
                title={t("runDetail.partialHint", { count: dlqRouted })}
              >
                {t("runDetail.partial")}
              </span>
            ) : null}
          </span>
        }
        subtitle={
          run
            ? `${ws?.name ?? ""} · ${
                TERMINAL.has(run.status)
                  ? t("runDetail.final")
                  : t("runDetail.live")
              }`
            : t("common.loading")
        }
        actions={
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="md"
              onClick={() => {
                if (ws) void runsApi.get(ws.id, id).then(setRun);
              }}
              aria-label={t("common.refresh")}
            >
              <RefreshCwIcon size={16} />
              {t("common.refresh")}
            </Button>
            {/* Cancel button — visible only for cancellable states
                (pending / running) and only when no cancel request is
                already in flight. Phase P (2026-05-28). */}
            {run &&
            (run.status === "pending" || run.status === "running") &&
            !run.cancel_requested_at ? (
              <Button
                onClick={() => setCancelOpen(true)}
                variant="destructive"
                loading={cancelling}
              >
                <BanIcon size={16} />
                {t("runDetail.cancel")}
              </Button>
            ) : null}
            {/* "Cancelling…" chip for the gap between cancel request
                and worker-final CANCELLED status. Stops the user from
                wondering "did the click work?" while the next wave
                boundary is still in flight. */}
            {run &&
            run.cancel_requested_at &&
            run.status === "running" ? (
              <span className="inline-flex items-center gap-1.5 rounded-sm border border-warning/40 bg-warning/10 px-2 py-1 text-xs font-medium text-warning">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-warning" aria-hidden />
                {t("runDetail.cancelling")}
              </span>
            ) : null}
            {run &&
            (run.status === "failed" || run.status === "cancelled") ? (
              <Button onClick={onRetry} loading={retrying}>
                <RotateCcwIcon size={16} />
                {t("common.retry")}
              </Button>
            ) : null}
          </div>
        }
      />
      <ConfirmDialog
        open={cancelOpen}
        title={t("runDetail.cancelConfirmTitle")}
        description={
          run?.status === "pending"
            ? t("runDetail.cancelConfirmPendingDesc")
            : t("runDetail.cancelConfirmRunningDesc")
        }
        confirmLabel={t("runDetail.cancel")}
        cancelLabel={t("common.cancel")}
        destructive
        loading={cancelling}
        onConfirm={onCancel}
        onCancel={() => setCancelOpen(false)}
      />
      <main className="flex-1 overflow-y-auto px-6 py-8">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
          {notFound ? (
            <Card>
              <EmptyState
                icon={<SearchXIcon size={36} strokeWidth={1.5} />}
                title={t("runDetail.notFoundTitle")}
                description={
                  <>
                    <div>{t("runDetail.notFoundDesc")}</div>
                    {/* Server diagnostic — without this an operator can't
                        tell "row was deleted" from "wrong workspace" from
                        "the API itself is broken". Copy-pasteable so a
                        bug report has the URL the page actually called. */}
                    <pre className="mt-3 overflow-x-auto rounded-md border border-border-subtle bg-elevated px-3 py-2 text-left font-mono text-[11px] text-text-muted">
                      {notFound.status} {notFound.detail}
                      {"\n"}
                      {notFound.url}
                    </pre>
                  </>
                }
                action={
                  ws ? (
                    <Link
                      href={`/w/${ws.slug}/runs`}
                      className="inline-flex items-center gap-1.5 rounded-md border border-border-subtle px-3 py-1.5 text-sm text-text-secondary transition duration-150 hover:bg-overlay hover:text-text"
                    >
                      <ArrowLeftIcon size={14} />
                      {t("runDetail.backToList")}
                    </Link>
                  ) : null
                }
              />
            </Card>
          ) : null}
          {!notFound && nodeRuns && nodeRuns.length > 0 ? (
            <Card>
              <CardHeader
                title={t("runDetail.dag")}
                description={t("runDetail.dagDesc", { count: nodeRuns.length })}
              />
              {/* Clicking a node card filters the log panel below to
                  that node's window (Phase M, 2026-05-26 user request).
                  Same node clicked twice clears the filter. */}
              <RunDagGraph
                nodes={nodeRuns}
                selectedNodeId={nodeFilter}
                onSelectNode={(id) =>
                  setNodeFilter((cur) => (cur === id ? null : id))
                }
              />
            </Card>
          ) : null}
        {!notFound ? (
        <div className="grid w-full gap-6 lg:grid-cols-[2fr_1fr]">
          <Card>
            <CardHeader
              title={t("runDetail.logs")}
              description={
                logs === null
                  ? t("common.loading")
                  : // Phase ADB (2026-06-04) — the logs query is capped at
                    // 1000; surface that instead of silently showing a
                    // partial tail as if it were the whole log.
                    logs.length >= 1000
                    ? t("runDetail.entriesCapped", { count: logs.length })
                    : t("runDetail.entries", { count: logs.length })
              }
              action={
                nodeFilter ? (
                  // Active filter chip — click clears the filter. Same
                  // role as a "Clear" button in a search bar.
                  <button
                    type="button"
                    onClick={() => setNodeFilter(null)}
                    className="inline-flex items-center gap-1 rounded-sm border border-accent/40 bg-accent/10 px-2 py-1 text-xs font-medium text-accent hover:bg-accent/20"
                    title={t("runDetail.clearNodeFilter")}
                  >
                    <span className="font-mono">{nodeFilter}</span>
                    <span aria-hidden>×</span>
                  </button>
                ) : null
              }
            />
            <LogView
              logs={logs}
              bottomRef={logsEndRef}
              nodeFilter={nodeFilter}
              onSelectNode={setNodeFilter}
              t={t}
            />
          </Card>

          <div className="flex flex-col gap-6">
            <Card>
              <CardHeader title={t("runDetail.summary")} />
              <Summary
                run={run}
                workspaceSlug={ws?.slug}
                isMigration={
                  pipeline !== null &&
                  migrationSummaryOf(pipeline.current_config_json) !== null
                }
                currentUserId={currentUser?.id ?? null}
                dlqRouted={dlqRouted}
                t={t}
              />
            </Card>
            <Card>
              <CardHeader
                title={t("runDetail.metrics")}
                description={
                  metrics === null
                    ? t("common.loading")
                    : t("runDetail.points", { count: metrics.length })
                }
              />
              <MetricsView grouped={grouped} t={t} />
            </Card>
            {run?.error_message ? (
              <Card>
                <CardHeader
                  title={t("common.error")}
                  // Phase N (2026-05-28): when the failure is a graph
                  // node, surface WHICH node directly in the header so
                  // the operator doesn't have to scan the DAG for the
                  // red card. Clicking pins the log panel filter +
                  // selects the same node in the DAG (accent ring) so
                  // both surfaces line up.
                  action={
                    <div className="flex items-center gap-2">
                      {failedNode ? (
                        <button
                          type="button"
                          onClick={() => setNodeFilter(failedNode.node_id)}
                          className="inline-flex items-center gap-1 rounded-sm border border-error/40 bg-error/10 px-2 py-1 text-xs font-medium text-error hover:bg-error/20"
                          title={t("runDetail.openFailedNodeTitle", {
                            node: failedNode.node_id,
                          })}
                        >
                          <span className="font-mono">{failedNode.node_id}</span>
                          <span aria-hidden>→</span>
                        </button>
                      ) : null}
                      {/* Phase AEY (2026-06-04) — copy the full failure
                          (class + message + node root cause) so it can be
                          pasted into an issue / search. Reuses the AET
                          clipboard pattern. */}
                      <button
                        type="button"
                        onClick={() => {
                          const parts: string[] = [];
                          if (run.error_class) parts.push(run.error_class);
                          if (run.error_message) parts.push(run.error_message);
                          if (
                            failedNode?.error_message &&
                            failedNode.error_message !== run.error_message
                          )
                            parts.push(
                              `[${failedNode.node_id}] ${failedNode.error_message}`,
                            );
                          navigator.clipboard.writeText(parts.join("\n")).then(
                            () => toast.success(t("runDetail.errorCopied")),
                            () => toast.error(t("runDetail.logsCopyFailed")),
                          );
                        }}
                        className="rounded-sm border border-border-subtle bg-overlay px-2 py-1 text-xs text-text-secondary hover:text-text"
                      >
                        {t("runDetail.copy")}
                      </button>
                    </div>
                  }
                />
                <div className="space-y-2 font-mono text-xs">
                  {run.error_class ? (
                    <div className="text-error">{run.error_class}</div>
                  ) : null}
                  <pre className="overflow-auto whitespace-pre-wrap break-words text-text-secondary">
                    {run.error_message}
                  </pre>
                  {failedNode?.error_message &&
                  failedNode.error_message !== run.error_message ? (
                    // The run-level message is usually the wrapped
                    // failure ("node X failed: ..."), but the node's
                    // own error_message often contains the unwrapped
                    // root cause (a connector traceback, a SQL error).
                    // Show both when they differ so the operator
                    // doesn't have to dig.
                    <div className="mt-2 border-t border-border-subtle/60 pt-2">
                      <div className="mb-1 text-[10px] uppercase tracking-wider text-text-muted">
                        {t("runDetail.nodeError", { node: failedNode.node_id })}
                      </div>
                      <pre className="overflow-auto whitespace-pre-wrap break-words text-text-secondary">
                        {failedNode.error_message}
                      </pre>
                    </div>
                  ) : null}
                </div>
              </Card>
            ) : null}
            {/* Phase ABR (2026-06-01) — "Config that ran" panel. The
                pipeline may have been edited between this run and
                now, so debugging needs the config AS IT WAS. Toggle
                kept collapsed by default — the JSON is verbose and
                most of the time the operator just wants the error +
                logs. */}
            <ConfigPanel
              ranVersion={ranVersion}
              t={t}
            />
            {/* Phase DLQ-2 (2026-06-04) — when this run routed records to
                the DLQ (dlqRouted from metrics, AFB), let the operator
                actually SEE them. Pipeline-scoped + lazy (opens a DB
                read), so it's collapsed until clicked. */}
            {dlqRouted > 0 && ws && run ? (
              <DlqRecordsCard
                workspaceId={ws.id}
                pipelineId={run.pipeline_id}
                t={t}
              />
            ) : null}
          </div>
        </div>
        ) : null}
        </div>
      </main>
    </>
  );
}

function LogView({
  logs,
  bottomRef,
  nodeFilter,
  onSelectNode,
  t,
}: {
  logs: RunLogEntry[] | null;
  bottomRef: React.RefObject<HTMLDivElement | null>;
  /** Currently-applied node filter — affects which logs render + the
   *  "X" chip near the panel header. ``null`` = show all. */
  nodeFilter: string | null;
  /** Clicking the NODE chip on a row sets the filter to that node;
   *  passing ``null`` clears it. */
  onSelectNode: (nodeId: string | null) => void;
  t: Translate;
}) {
  const [minLevel, setMinLevel] = useState<LogLevel | "">("");
  const [logSearch, setLogSearch] = useState("");
  if (logs === null) {
    return (
      <div className="py-8 text-center text-sm text-text-muted">
        {t("common.loading")}
      </div>
    );
  }
  if (logs.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-text-muted">
        {nodeFilter !== null ? t("runDetail.noLogsForNode") : t("runDetail.noLogs")}
      </div>
    );
  }
  // Phase AER (2026-06-04) — client-side "min level" filter over the
  // already-fetched logs, so an operator debugging a noisy run can jump
  // straight to the warnings/errors without scrolling past info lines.
  // Phase AER (level) + AES (text search) — both client-side over the
  // already-fetched logs; compose with the server-side node filter (M).
  const q = logSearch.trim().toLowerCase();
  const shown = logs.filter(
    (e) =>
      (!minLevel || LEVEL_ORDER[e.level] >= LEVEL_ORDER[minLevel]) &&
      (!q || e.message.toLowerCase().includes(q)),
  );
  // Phase AET (2026-06-04) — copy exactly what's on screen (after the
  // search/level/node filters), so an operator can paste the relevant
  // lines into an issue or Slack instead of the whole 1000-line dump.
  // Reuses the clipboard pattern from the "config that ran" panel (ABR).
  async function copyLogs() {
    const text = shown
      .map((e) => {
        const ctx =
          Object.keys(e.context_json).length > 0
            ? "  " +
              Object.entries(e.context_json)
                .map(
                  ([k, v]) =>
                    `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`,
                )
                .join(" ")
            : "";
        return `${e.ts} ${e.level.toUpperCase()} ${e.node_id ?? "-"} ${e.message}${ctx}`;
      })
      .join("\n");
    try {
      await navigator.clipboard.writeText(text);
      toast.success(t("runDetail.logsCopied", { count: shown.length }));
    } catch {
      toast.error(t("runDetail.logsCopyFailed"));
    }
  }
  // Phase AEU (2026-06-04) — at-a-glance error/warning tallies over the
  // FULL fetched log set (not the filtered view), so opening a failed run
  // immediately answers "did anything go wrong?" without scrolling. Each
  // chip is a one-click shortcut into the AER min-level filter.
  const errorCount = logs.filter((e) => e.level === "error").length;
  const warnCount = logs.filter((e) => e.level === "warning").length;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-text-muted">
        {errorCount > 0 ? (
          <button
            type="button"
            onClick={() => setMinLevel("error")}
            title={t("runDetail.filterToErrors")}
            className="inline-flex items-center rounded-sm border border-error/40 bg-error/10 px-2 py-1 font-medium text-error hover:bg-error/20"
          >
            {t("runDetail.logErrorCount", { count: errorCount })}
          </button>
        ) : null}
        {warnCount > 0 ? (
          <button
            type="button"
            onClick={() => setMinLevel("warning")}
            title={t("runDetail.filterToWarnings")}
            className="inline-flex items-center rounded-sm border border-warning/40 bg-warning/10 px-2 py-1 font-medium text-warning hover:bg-warning/20"
          >
            {t("runDetail.logWarnCount", { count: warnCount })}
          </button>
        ) : null}
        {/* Phase AES — message substring search. */}
        <input
          value={logSearch}
          onChange={(e) => setLogSearch(e.target.value)}
          placeholder={t("runDetail.logSearchPlaceholder")}
          className="h-7 w-48 rounded-md border border-border-subtle bg-elevated px-2 text-xs text-text focus-visible:border-accent focus-visible:outline-none"
        />
        <span>{t("runDetail.logLevel")}</span>
        <select
          value={minLevel}
          onChange={(e) => setMinLevel(e.target.value as LogLevel | "")}
          className="h-7 rounded-md border border-border-subtle bg-elevated px-1.5 text-xs text-text focus-visible:border-accent focus-visible:outline-none"
        >
          <option value="">{t("runDetail.logLevelAll")}</option>
          <option value="info">info+</option>
          <option value="warning">warning+</option>
          <option value="error">error</option>
        </select>
        {minLevel || q ? (
          <span>
            {t("runDetail.logLevelShowing", {
              shown: shown.length,
              total: logs.length,
            })}
          </span>
        ) : null}
        <button
          type="button"
          onClick={() => void copyLogs()}
          disabled={shown.length === 0}
          className="ml-auto rounded-sm border border-border-subtle bg-overlay px-2 py-1 text-xs text-text-secondary hover:text-text disabled:opacity-40 disabled:hover:text-text-secondary"
        >
          {t("runDetail.copyLogs")}
        </button>
      </div>
      <div className="max-h-[600px] overflow-y-auto rounded-md border border-border-subtle bg-bg font-mono text-xs">
      <ul>
        {shown.map((entry) => (
          <li
            key={entry.id}
            className="flex gap-3 border-b border-border-subtle/60 px-3 py-1.5 last:border-b-0"
          >
            <time className="shrink-0 text-text-muted">
              {new Date(entry.ts).toLocaleTimeString([], { hour12: false })}
            </time>
            <span
              className={cn(
                "w-12 shrink-0 font-semibold uppercase",
                LEVEL_CLASSES[entry.level],
              )}
            >
              {entry.level}
            </span>
            {/* NODE column — sits immediately right of LEVEL per the
                Phase M (2026-05-26) user request "LEVEL 오른쪽에 해당 NODE
                정보 넣어서". Click filters the log panel to that node's
                window; the chip is a button so it gets the same pointer
                cursor + focus ring as other interactive bits. Run-level
                logs (build / connector setup / summary) render ``—`` as
                the placeholder so the columns stay aligned. */}
            <button
              type="button"
              onClick={() =>
                onSelectNode(entry.node_id === nodeFilter ? null : entry.node_id)
              }
              disabled={!entry.node_id}
              title={
                entry.node_id
                  ? t("runDetail.filterByNodeTitle", { node: entry.node_id })
                  : t("runDetail.runLevelLog")
              }
              className={cn(
                "w-32 shrink-0 truncate text-left",
                entry.node_id
                  ? "text-accent hover:underline"
                  : "text-text-muted/60 cursor-default",
                entry.node_id === nodeFilter && entry.node_id
                  ? "font-semibold underline"
                  : "",
              )}
            >
              {entry.node_id ?? "—"}
            </button>
            <div className="min-w-0 flex-1">
              <span className="text-text">{entry.message}</span>
              {Object.keys(entry.context_json).length > 0 ? (
                <span className="ml-2 text-text-muted">
                  {Object.entries(entry.context_json)
                    .map(
                      ([k, v]) =>
                        `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`,
                    )
                    .join("  ")}
                </span>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      {shown.length === 0 ? (
        <div className="py-6 text-center text-text-muted">
          {t("runDetail.noLogsAtLevel")}
        </div>
      ) : null}
      <div ref={bottomRef} />
      </div>
    </div>
  );
}

function Summary({
  run,
  workspaceSlug,
  isMigration,
  /** Phase ABW (2026-06-01) — used to render "by you" vs the bare
   *  UUID prefix on the trigger source line. */
  currentUserId,
  /** Phase AFB (2026-06-04) — count of records routed to the DLQ,
   *  derived from the run's metrics by the parent. */
  dlqRouted,
  t,
}: {
  run: RunDetail | null;
  workspaceSlug: string | undefined;
  /** Phase ABL — true when the pipeline summary parses as a
   *  migration; routes the link to /migrations/[id] instead. */
  isMigration: boolean;
  currentUserId: string | null;
  dlqRouted: number;
  t: Translate;
}) {
  if (!run) {
    return <div className="text-sm text-text-muted">{t("common.loading")}</div>;
  }
  // pipeline_id renders as a clickable link → the pipeline editor
  // (or migration page when the pipeline is a migration). The
  // ExternalLinkIcon + title hint that the row is interactive.
  const pipelineHref = workspaceSlug
    ? isMigration
      ? `/w/${workspaceSlug}/migrations/${run.pipeline_id}`
      : `/w/${workspaceSlug}/pipelines/${run.pipeline_id}/edit`
    : null;
  const pipelineLink = pipelineHref ? (
    <Link
      href={pipelineHref}
      className="inline-flex items-center gap-1 text-accent hover:underline"
      title={isMigration ? t("runDetail.openMigration") : t("runDetail.openPipeline")}
    >
      <code>{run.pipeline_id.slice(0, 8)}…</code>
      <ExternalLinkIcon size={12} />
    </Link>
  ) : (
    <code>{run.pipeline_id.slice(0, 8)}…</code>
  );
  // Phase AEI (2026-06-04) — run origin/lineage from result_json (the
  // server returns it on RunDetail but the page never surfaced it). Tells
  // the operator *why* this run exists: a manual retry, an auto-trigger
  // off another run's materialization, or a backfill window.
  const rj = (run.result_json ?? {}) as Record<string, unknown>;
  const retryOf = typeof rj.retry_of === "string" ? rj.retry_of : null;
  const triggerChain = Array.isArray(rj.trigger_chain)
    ? (rj.trigger_chain.filter((x): x is string => typeof x === "string"))
    : [];
  const autoTriggerBy = triggerChain.length > 0 ? triggerChain[triggerChain.length - 1] : null;
  const backfill =
    rj.backfill && typeof rj.backfill === "object"
      ? (rj.backfill as Record<string, unknown>)
      : null;
  // Phase P3b (ADR-0095) — partitioned backfill sub-run marker.
  const partition =
    rj.partition && typeof rj.partition === "object"
      ? (rj.partition as Record<string, unknown>)
      : null;
  // ADR-0093 P2 — which data path each task took. "pushdown" never moved
  // the rows at all; "arrow" bulk-copied them past the Record plane;
  // "records" is the classic row-by-row path. Answers "why was this run
  // fast/slow" right on the page instead of in worker logs.
  const dataPaths =
    rj.data_paths && typeof rj.data_paths === "object"
      ? Object.entries(rj.data_paths as Record<string, unknown>).filter(
          (e): e is [string, string] => typeof e[1] === "string",
        )
      : [];
  const uniquePaths = [...new Set(dataPaths.map(([, p]) => p))];
  // Per-path task list — a multi-task run (task DAG) can mix paths
  // (e.g. copy tasks on Arrow, an append log task pushed down). The
  // summary chips collapse to distinct path TYPES; this maps each type
  // back to the tasks that took it so the chip tooltip can answer "which
  // task?" without expanding the whole DAG (2026-06-15).
  const tasksByPath = new Map<string, string[]>();
  for (const [task, p] of dataPaths) {
    const bucket = tasksByPath.get(p);
    if (bucket) bucket.push(task);
    else tasksByPath.set(p, [task]);
  }
  const pathLabel = (p: string) =>
    p === "pushdown"
      ? t("runDetail.pathPushdown")
      : p === "arrow"
        ? t("runDetail.pathArrow")
        : p === "graph"
          ? t("runDetail.pathGraph")
          : t("runDetail.pathRecords");
  const runHref = (rid: string) =>
    workspaceSlug ? `/w/${workspaceSlug}/runs/${rid}` : "#";
  const runLink = (rid: string) => (
    <Link
      href={runHref(rid)}
      className="inline-flex items-center gap-1 text-accent hover:underline"
    >
      <code>{rid.slice(0, 8)}…</code>
      <ExternalLinkIcon size={12} />
    </Link>
  );
  // Phase AFI (2026-06-04) — heartbeat liveness. A running run heartbeats
  // ~every 10s; if the last beat is older than 60s the worker may be
  // stalled (the ZombieReaper will eventually fail it). Flag it so the
  // operator sees a stuck run without waiting for the reaper. Re-evaluated
  // on each poll while the run is live.
  const heartbeatStale =
    run.status === "running" &&
    run.heartbeat_at != null &&
    Date.now() - Date.parse(run.heartbeat_at) > 60_000;
  // Phase AFJ (2026-06-04) — duration_seconds is null until the run
  // finishes, so a running run showed "—". Show the live elapsed time
  // (started_at → now), re-evaluated on each poll, so the operator can
  // see how long an in-flight run has been going.
  const runningElapsed =
    run.status === "running" && run.started_at
      ? (Date.now() - Date.parse(run.started_at)) / 1000
      : null;
  return (
    <dl className="grid grid-cols-1 gap-3 text-sm">
      <Field label={t("common.status")} value={<StatusBadge status={run.status} />} />
      <Field label={t("common.pipeline")} value={pipelineLink} />
      <Field
        label={t("common.version")}
        value={<code>{run.pipeline_version_id.slice(0, 8)}…</code>}
      />
      <Field label={t("common.scheduled")} value={fmt(run.scheduled_at)} />
      {/* Phase ABW (2026-06-01) — trigger source. Same info as the
          runs-list chip, surfaced on the detail page so the operator
          knows what fired this run without going back. */}
      <Field
        label={t("runDetail.triggerSource")}
        value={
          run.schedule_id ? (
            <span className="inline-flex items-center gap-1 text-accent">
              <CalendarClockIcon size={12} />
              {t("runDetail.triggerScheduled")}
            </span>
          ) : run.triggered_by_user_id ? (
            <span className="inline-flex items-center gap-1 text-text-secondary">
              <HandIcon size={12} />
              {run.triggered_by_user_id === currentUserId
                ? t("runDetail.triggerManualByYou")
                : t("runDetail.triggerManual")}
            </span>
          ) : (
            <span className="text-text-muted">—</span>
          )
        }
      />
      {/* Phase AEI — run lineage (retry / auto-trigger / backfill). */}
      {retryOf ? (
        <Field label={t("runDetail.retryOf")} value={runLink(retryOf)} />
      ) : null}
      {autoTriggerBy ? (
        <Field
          label={t("runDetail.autoTriggeredBy")}
          value={runLink(autoTriggerBy)}
        />
      ) : null}
      {backfill ? (
        <Field
          label={t("runDetail.backfillWindow")}
          value={
            <code className="text-xs">
              {String(backfill.cursor_from ?? "—")} →{" "}
              {String(backfill.cursor_to ?? "—")}
            </code>
          }
        />
      ) : null}
      {/* Phase P3b (ADR-0095) — partitioned backfill: this run is window
          index+1 of `of` parallel sub-runs; the group id ties siblings
          together (find them in the runs list around the same time). */}
      {partition ? (
        <Field
          label={t("runDetail.partition")}
          value={
            <span
              className="rounded bg-overlay px-1.5 py-0.5 text-xs text-text-secondary"
              title={t("runDetail.partitionHint", {
                group: String(partition.group ?? "—"),
              })}
            >
              {t("runDetail.partitionValue", {
                index: String(Number(partition.index ?? 0) + 1),
                of: String(partition.of ?? "?"),
              })}
            </span>
          }
        />
      ) : null}
      <Field label={t("runDetail.started")} value={fmt(run.started_at)} />
      <Field label={t("runDetail.finished")} value={fmt(run.finished_at)} />
      <Field
        label={t("common.duration")}
        value={
          runningElapsed != null ? (
            <span className="text-text-secondary" title={t("runDetail.elapsedTitle")}>
              {fmtDuration(runningElapsed)} · {t("runDetail.elapsedRunning")}
            </span>
          ) : (
            fmtDuration(run.duration_seconds)
          )
        }
      />
      <Field
        label={t("runDetail.records")}
        value={
          <span className="inline-flex items-baseline gap-2">
            <code>
              {run.records_read.toLocaleString()} /{" "}
              {run.records_written.toLocaleString()}
            </code>
            {/* Phase AEZ (2026-06-04) — surface the read-vs-written gap so a
                silent data drop (filter / dedupe / DLQ route) is visible
                instead of hidden inside the "X / Y" pair. */}
            {run.records_read > run.records_written ? (
              <span
                className="text-xs text-text-muted"
                title={t("runDetail.recordsDeltaHint")}
              >
                {t("runDetail.recordsDelta", {
                  count: run.records_read - run.records_written,
                })}
              </span>
            ) : null}
          </span>
        }
      />
      {uniquePaths.length > 0 ? (
        <Field
          label={t("runDetail.dataPath")}
          value={
            <span className="inline-flex flex-wrap items-center gap-1.5" title={t("runDetail.dataPathHint")}>
              {uniquePaths.map((p) => {
                const tasks = tasksByPath.get(p) ?? [];
                // Tooltip lists the tasks that took this path — for a
                // single-task run it's just the one task; for a DAG it
                // answers "which task is on Arrow vs records?".
                const chipTitle =
                  tasks.length > 1
                    ? t("runDetail.dataPathTasks", { tasks: tasks.join(", ") })
                    : tasks[0];
                return (
                  <span
                    key={p}
                    title={chipTitle}
                    className={`rounded px-1.5 py-0.5 text-xs ${
                      p === "pushdown" || p === "arrow"
                        ? "bg-accent/10 text-accent"
                        : "bg-overlay text-text-secondary"
                    }`}
                  >
                    {pathLabel(p)}
                    {tasks.length > 1 ? (
                      <span className="ml-1 opacity-70">×{tasks.length}</span>
                    ) : null}
                  </span>
                );
              })}
            </span>
          }
        />
      ) : null}
      {/* Phase AFB (2026-06-04) — name the DLQ-routed slice of the
          filtered records (AEZ) when a transform sent bad rows to a
          dead-letter queue, so the operator knows they were captured. */}
      {dlqRouted > 0 ? (
        <Field
          label={t("runDetail.dlqLabel")}
          value={
            <span className="text-warning" title={t("runDetail.dlqHint")}>
              {t("runDetail.dlqRouted", { count: dlqRouted })}
            </span>
          }
        />
      ) : null}
      {run.worker_id ? (
        <Field label={t("runDetail.worker")} value={<code>{run.worker_id}</code>} />
      ) : null}
      {run.heartbeat_at ? (
        <Field
          label={t("runDetail.heartbeat")}
          value={
            <span
              className={heartbeatStale ? "text-warning" : "text-text-secondary"}
              title={absoluteTime(run.heartbeat_at)}
            >
              {relativeTime(run.heartbeat_at, t)}
              {heartbeatStale ? ` · ${t("runDetail.heartbeatStale")}` : null}
            </span>
          }
        />
      ) : null}
    </dl>
  );
}

/** Phase ABR (2026-06-01) — collapsible "Config that ran" card.
 *  Pinned to the EXACT version that executed (not the pipeline's
 *  current head) so debug context survives edits.
 *
 *  Copy-to-clipboard is the main interaction: operators usually
 *  paste the JSON into a diff tool to compare with the head, or
 *  into an issue.  */
function ConfigPanel({
  ranVersion,
  t,
}: {
  ranVersion: PipelineVersionEntry | null;
  t: Translate;
}) {
  const [expanded, setExpanded] = useState(false);
  if (!ranVersion) {
    return null;
  }
  const pretty = JSON.stringify(ranVersion.config_json, null, 2);
  async function copy() {
    try {
      await navigator.clipboard.writeText(pretty);
      toast.success(t("runDetail.configCopied"));
    } catch {
      toast.error(t("runDetail.configCopyFailed"));
    }
  }
  return (
    <Card>
      <CardHeader
        title={t("runDetail.configThatRan")}
        description={t("runDetail.configVersionLabel", {
          n: ranVersion.version,
        })}
        action={
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void copy()}
              className="rounded-sm border border-border-subtle bg-overlay px-2 py-1 text-xs text-text-secondary hover:text-text"
            >
              {t("runDetail.copy")}
            </button>
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="rounded-sm border border-border-subtle bg-overlay px-2 py-1 text-xs text-text-secondary hover:text-text"
            >
              {expanded ? t("runDetail.collapse") : t("runDetail.expand")}
            </button>
          </div>
        }
      />
      {expanded ? (
        <pre className="max-h-[400px] overflow-auto rounded-md border border-border-subtle bg-bg p-3 font-mono text-[11px] text-text-secondary">
          {pretty}
        </pre>
      ) : null}
    </Card>
  );
}

/** Render a DLQ cell value compactly: objects/arrays as JSON, null as a
 *  dash, everything else stringified. */
function fmtDlqCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/** Map a server ``reason`` (ADR-0075) to a user-facing message key. */
function dlqReasonKey(reason: string | null): keyof Messages {
  switch (reason) {
    case "sink_not_readable":
      return "runDetail.dlqReasonSinkNotReadable";
    case "stream_dlq":
      return "runDetail.dlqReasonStream";
    case "connection_missing":
      return "runDetail.dlqReasonConnMissing";
    case "read_failed":
      return "runDetail.dlqReasonReadFailed";
    default:
      // no_dlq / unsafe_table / invalid_config / connection_build_failed
      return "runDetail.dlqReasonGeneric";
  }
}

/** Phase DLQ-2 (2026-06-04) — lazy viewer for the pipeline's dead-letter
 *  queue records (ADR-0075 server endpoint). Collapsed by default because
 *  expanding opens a bounded DB read; "unavailable" reasons map to a
 *  friendly message so a write-only sink (Kafka/HTTP) doesn't look broken. */
function DlqRecordsCard({
  workspaceId,
  pipelineId,
  t,
}: {
  workspaceId: string;
  pipelineId: string;
  t: Translate;
}) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<DlqPreviewResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  // Phase DLQ-4 (2026-06-04) — how many rows to pull (server caps at 200).
  const [limit, setLimit] = useState(50);

  async function load(n: number = limit) {
    setLoading(true);
    setFailed(false);
    try {
      setData(await pipelinesApi.dlqRecords(workspaceId, pipelineId, n));
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && data === null && !loading) void load();
  }

  // Column union across the returned records, preserving first-seen order.
  const columns = useMemo(() => {
    const recs = data?.records ?? [];
    const seen: string[] = [];
    for (const r of recs) {
      for (const k of Object.keys(r)) if (!seen.includes(k)) seen.push(k);
    }
    return seen;
  }, [data]);

  // Phase DLQ-3 (2026-06-04) — copy the records as JSON for a ticket.
  async function copyRecords() {
    if (!data?.records) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(data.records, null, 2));
      toast.success(t("runDetail.dlqCopied"));
    } catch {
      toast.error(t("runDetail.logsCopyFailed"));
    }
  }

  const btn =
    "rounded-sm border border-border-subtle bg-overlay px-2 py-1 text-xs text-text-secondary hover:text-text disabled:opacity-40";

  return (
    <Card>
      <CardHeader
        title={t("runDetail.dlqRecordsTitle")}
        description={
          data?.available
            ? t("runDetail.dlqRecordsCount", { count: data.records.length })
            : undefined
        }
        action={
          <div className="flex items-center gap-2">
            {/* Phase DLQ-3 — copy JSON + refresh (the DLQ table grows as
                later runs route more records; the lazy fetch is cached). */}
            {open && data?.available && data.records.length > 0 ? (
              <button type="button" onClick={() => void copyRecords()} className={btn}>
                {t("runDetail.copy")}
              </button>
            ) : null}
            {open ? (
              <button
                type="button"
                onClick={() => void load()}
                disabled={loading}
                className={btn}
              >
                {t("common.refresh")}
              </button>
            ) : null}
            {/* Phase DLQ-4 — row limit (server caps at 200). Refetches. */}
            {open ? (
              <select
                value={limit}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  setLimit(n);
                  void load(n);
                }}
                disabled={loading}
                className="h-7 rounded-sm border border-border-subtle bg-overlay px-1 text-xs text-text-secondary"
                title={t("runDetail.dlqLimit")}
              >
                <option value={50}>50</option>
                <option value={100}>100</option>
                <option value={200}>200</option>
              </select>
            ) : null}
            <button type="button" onClick={toggle} className={btn}>
              {open ? t("runDetail.collapse") : t("runDetail.view")}
            </button>
          </div>
        }
      />
      {open ? (
        loading ? (
          <div className="py-6 text-center text-sm text-text-muted">
            {t("common.loading")}
          </div>
        ) : failed ? (
          <div className="py-6 text-center text-sm text-error">
            {t("runDetail.dlqLoadFailed")}
          </div>
        ) : data?.available ? (
          data.records.length === 0 ? (
            <div className="py-6 text-center text-sm text-text-muted">
              {t("runDetail.dlqEmpty")}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <div className="mb-2 text-[11px] text-text-muted">
                {t("runDetail.dlqSource", {
                  connection: data.connection ?? "",
                  table: data.table ?? "",
                })}
              </div>
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-border-subtle text-text-muted">
                    {columns.map((c) => (
                      <th key={c} className="px-2 py-1 font-medium">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.records.map((r, i) => (
                    <tr key={i} className="border-b border-border-subtle/50">
                      {columns.map((c) => (
                        <td
                          key={c}
                          className="max-w-[16rem] truncate px-2 py-1 font-mono text-text-secondary"
                          title={fmtDlqCell(r[c])}
                        >
                          {fmtDlqCell(r[c])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {/* Phase DLQ-4 — no-silent-truncation: a full page means
                  there may be more rows than the current limit. */}
              {data.records.length >= limit ? (
                <div className="mt-2 text-[11px] text-text-muted">
                  {t("runDetail.dlqTruncated", { count: limit })}
                </div>
              ) : null}
            </div>
          )
        ) : (
          <div className="py-4 text-sm text-text-muted">
            {t(dlqReasonKey(data?.reason ?? null))}
            {data?.error ? (
              <pre className="mt-2 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border-subtle bg-bg p-2 font-mono text-[11px] text-text-secondary">
                {data.error}
              </pre>
            ) : null}
          </div>
        )
      ) : null}
    </Card>
  );
}

function Field({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
        {label}
      </dt>
      <dd className="truncate text-right text-text">{value}</dd>
    </div>
  );
}

interface MetricGroup {
  name: string;
  total: number;
  points: RunMetricEntry[];
}

function groupMetrics(metrics: RunMetricEntry[]): MetricGroup[] {
  const groups = new Map<string, MetricGroup>();
  for (const m of metrics) {
    const existing = groups.get(m.name);
    if (existing) {
      existing.total += m.value;
      existing.points.push(m);
    } else {
      groups.set(m.name, { name: m.name, total: m.value, points: [m] });
    }
  }
  return [...groups.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function MetricsView({
  grouped,
  t,
}: {
  grouped: MetricGroup[];
  t: Translate;
}) {
  if (grouped.length === 0) {
    return (
      <div className="text-sm text-text-muted">{t("runDetail.noMetrics")}</div>
    );
  }
  return (
    <ul className="flex flex-col gap-2 text-sm">
      {grouped.map((g) => (
        <li
          key={g.name}
          className="flex items-baseline justify-between gap-3 border-b border-border-subtle pb-2 last:border-b-0 last:pb-0"
        >
          <span className="truncate font-mono text-xs text-text-secondary">
            {g.name}
          </span>
          <span className="text-right text-text">
            {formatMetricValue(g.total)}
            <span className="ml-2 text-xs text-text-muted">
              ({g.points.length}×)
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function formatMetricValue(v: number): string {
  if (Number.isInteger(v)) return v.toLocaleString();
  if (v < 0.01) return v.toExponential(2);
  return v.toFixed(3);
}
