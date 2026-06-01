"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ArrowLeftIcon,
  BanIcon,
  ExternalLinkIcon,
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
  runsApi,
  type LogLevel,
  type NodeRunEntry,
  type RunDetail,
  type RunLogEntry,
  type RunMetricEntry,
  type RunStatus,
} from "@/lib/api";
import { RunDagGraph } from "@/components/runs/run-dag-graph";
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

export default function RunDetailPage() {
  const { slug, id } = useParams<{ slug: string; id: string }>();
  const router = useRouter();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [run, setRun] = useState<RunDetail | null>(null);
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
              <Summary run={run} workspaceSlug={ws?.slug} t={t} />
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
                    failedNode ? (
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
                    ) : null
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
  return (
    <div className="max-h-[600px] overflow-y-auto rounded-md border border-border-subtle bg-bg font-mono text-xs">
      <ul>
        {logs.map((entry) => (
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
      <div ref={bottomRef} />
    </div>
  );
}

function Summary({
  run,
  workspaceSlug,
  t,
}: {
  run: RunDetail | null;
  workspaceSlug: string | undefined;
  t: Translate;
}) {
  if (!run) {
    return <div className="text-sm text-text-muted">{t("common.loading")}</div>;
  }
  // pipeline_id renders as a clickable link → the pipeline editor.
  // ExternalLinkIcon hint + title so users know the row is interactive.
  const pipelineLink = workspaceSlug ? (
    <Link
      href={`/w/${workspaceSlug}/pipelines/${run.pipeline_id}/edit`}
      className="inline-flex items-center gap-1 text-accent hover:underline"
      title={t("runDetail.openPipeline")}
    >
      <code>{run.pipeline_id.slice(0, 8)}…</code>
      <ExternalLinkIcon size={12} />
    </Link>
  ) : (
    <code>{run.pipeline_id.slice(0, 8)}…</code>
  );
  return (
    <dl className="grid grid-cols-1 gap-3 text-sm">
      <Field label={t("common.status")} value={<StatusBadge status={run.status} />} />
      <Field label={t("common.pipeline")} value={pipelineLink} />
      <Field
        label={t("common.version")}
        value={<code>{run.pipeline_version_id.slice(0, 8)}…</code>}
      />
      <Field label={t("common.scheduled")} value={fmt(run.scheduled_at)} />
      <Field label={t("runDetail.started")} value={fmt(run.started_at)} />
      <Field label={t("runDetail.finished")} value={fmt(run.finished_at)} />
      <Field label={t("common.duration")} value={fmtDuration(run.duration_seconds)} />
      <Field
        label={t("runDetail.records")}
        value={
          <code>
            {run.records_read.toLocaleString()} /{" "}
            {run.records_written.toLocaleString()}
          </code>
        }
      />
      {run.worker_id ? (
        <Field label={t("runDetail.worker")} value={<code>{run.worker_id}</code>} />
      ) : null}
      {run.heartbeat_at ? (
        <Field label={t("runDetail.heartbeat")} value={fmt(run.heartbeat_at)} />
      ) : null}
    </dl>
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
