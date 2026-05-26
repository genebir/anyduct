"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import {
  ArrowLeftIcon,
  ExternalLinkIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  SearchXIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
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
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [logs, setLogs] = useState<RunLogEntry[] | null>(null);
  const [metrics, setMetrics] = useState<RunMetricEntry[] | null>(null);
  const [nodeRuns, setNodeRuns] = useState<NodeRunEntry[] | null>(null);
  const [retrying, setRetrying] = useState(false);
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
      // Run exists — fetch the rest in parallel. Failures here log but
      // don't poison the page (worst case logs/metrics/node-runs stay
      // null and the panels show their own "no entries" copy).
      try {
        const [l, m, n] = await Promise.all([
          runsApi.logs(workspaceId, id, { limit: 1000 }),
          runsApi.metrics(workspaceId, id),
          runsApi.nodeRuns(workspaceId, id),
        ]);
        if (cancelled) return true;
        setRun(r);
        setLogs(l);
        setMetrics(m);
        setNodeRuns(n);
        return TERMINAL.has(r.status);
      } catch (err) {
        if (cancelled) return true;
        // Log/metric/node-run side resource failed but the run itself is
        // fine — surface the data we got, toast the partial failure.
        setRun(r);
        if (err instanceof ApiError) {
          // eslint-disable-next-line no-console
          console.warn("runDetail subresource failed", err.status, err.message);
        }
        return TERMINAL.has(r.status);
      }
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

  const grouped = useMemo(() => groupMetrics(metrics ?? []), [metrics]);

  async function onRetry() {
    if (!ws || !run) return;
    setRetrying(true);
    try {
      const fresh = await runsApi.retry(ws.id, run.id);
      toast.success(t("runDetail.retryQueued", { id: fresh.id.slice(0, 8) }));
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("runDetail.retryFailed"),
      );
    } finally {
      setRetrying(false);
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
              <RunDagGraph nodes={nodeRuns} />
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
            />
            <LogView logs={logs} bottomRef={logsEndRef} t={t} />
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
                <CardHeader title={t("common.error")} />
                <div className="space-y-2 font-mono text-xs">
                  {run.error_class ? (
                    <div className="text-error">{run.error_class}</div>
                  ) : null}
                  <pre className="overflow-auto whitespace-pre-wrap break-words text-text-secondary">
                    {run.error_message}
                  </pre>
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
  t,
}: {
  logs: RunLogEntry[] | null;
  bottomRef: React.RefObject<HTMLDivElement | null>;
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
        {t("runDetail.noLogs")}
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
