"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { ArrowLeftIcon, RefreshCwIcon, RotateCcwIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  ApiError,
  runsApi,
  type LogLevel,
  type RunDetail,
  type RunLogEntry,
  type RunMetricEntry,
  type RunStatus,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { cn } from "@/lib/cn";

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
  const [run, setRun] = useState<RunDetail | null>(null);
  const [logs, setLogs] = useState<RunLogEntry[] | null>(null);
  const [metrics, setMetrics] = useState<RunMetricEntry[] | null>(null);
  const [retrying, setRetrying] = useState(false);
  const logsEndRef = useRef<HTMLDivElement | null>(null);

  // Poll the trio (run / logs / metrics) until status is terminal. Once
  // terminal, fetch one more time and stop.
  useEffect(() => {
    if (!ws) return;
    let cancelled = false;

    async function tick(workspaceId: string): Promise<boolean> {
      try {
        const [r, l, m] = await Promise.all([
          runsApi.get(workspaceId, id),
          runsApi.logs(workspaceId, id, { limit: 1000 }),
          runsApi.metrics(workspaceId, id),
        ]);
        if (cancelled) return true;
        setRun(r);
        setLogs(l);
        setMetrics(m);
        return TERMINAL.has(r.status);
      } catch (err) {
        if (!cancelled) {
          toast.error(
            err instanceof ApiError ? err.message : "Couldn't load run.",
          );
        }
        return true;
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
  }, [ws, id]);

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
      toast.success(`Queued retry as ${fresh.id.slice(0, 8)}…`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Retry failed.");
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
              aria-label="Back to runs"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-text-muted transition duration-150 hover:bg-overlay hover:text-text"
            >
              <ArrowLeftIcon size={16} />
            </Link>
            Run {id.slice(0, 8)}…
            {run ? <StatusBadge status={run.status} /> : null}
          </span>
        }
        subtitle={
          run
            ? `${ws?.name ?? ""} · ${
                TERMINAL.has(run.status) ? "Final" : "Live · refreshing every 2 s"
              }`
            : "Loading…"
        }
        actions={
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="md"
              onClick={() => {
                if (ws) void runsApi.get(ws.id, id).then(setRun);
              }}
              aria-label="Refresh"
            >
              <RefreshCwIcon size={16} />
              Refresh
            </Button>
            {run &&
            (run.status === "failed" || run.status === "cancelled") ? (
              <Button onClick={onRetry} loading={retrying}>
                <RotateCcwIcon size={16} />
                Retry
              </Button>
            ) : null}
          </div>
        }
      />
      <main className="flex-1 overflow-y-auto px-6 py-8">
        <div className="mx-auto grid w-full max-w-6xl gap-6 lg:grid-cols-[2fr_1fr]">
          <Card>
            <CardHeader
              title="Logs"
              description={
                logs === null
                  ? "Loading…"
                  : `${logs.length} entr${logs.length === 1 ? "y" : "ies"}`
              }
            />
            <LogView logs={logs} bottomRef={logsEndRef} />
          </Card>

          <div className="flex flex-col gap-6">
            <Card>
              <CardHeader title="Summary" />
              <Summary run={run} />
            </Card>
            <Card>
              <CardHeader
                title="Metrics"
                description={
                  metrics === null
                    ? "Loading…"
                    : `${metrics.length} point${metrics.length === 1 ? "" : "s"}`
                }
              />
              <MetricsView grouped={grouped} />
            </Card>
            {run?.error_message ? (
              <Card>
                <CardHeader title="Error" />
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
      </main>
    </>
  );
}

function LogView({
  logs,
  bottomRef,
}: {
  logs: RunLogEntry[] | null;
  bottomRef: React.RefObject<HTMLDivElement | null>;
}) {
  if (logs === null) {
    return <div className="py-8 text-center text-sm text-text-muted">Loading…</div>;
  }
  if (logs.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-text-muted">
        No logs yet. They'll appear as the worker emits structlog events.
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

function Summary({ run }: { run: RunDetail | null }) {
  if (!run) {
    return <div className="text-sm text-text-muted">Loading…</div>;
  }
  return (
    <dl className="grid grid-cols-1 gap-3 text-sm">
      <Field label="Status" value={<StatusBadge status={run.status} />} />
      <Field label="Pipeline" value={<code>{run.pipeline_id.slice(0, 8)}…</code>} />
      <Field
        label="Version"
        value={<code>{run.pipeline_version_id.slice(0, 8)}…</code>}
      />
      <Field label="Scheduled" value={fmt(run.scheduled_at)} />
      <Field label="Started" value={fmt(run.started_at)} />
      <Field label="Finished" value={fmt(run.finished_at)} />
      <Field label="Duration" value={fmtDuration(run.duration_seconds)} />
      <Field
        label="Records (read / written)"
        value={
          <code>
            {run.records_read.toLocaleString()} /{" "}
            {run.records_written.toLocaleString()}
          </code>
        }
      />
      {run.worker_id ? (
        <Field label="Worker" value={<code>{run.worker_id}</code>} />
      ) : null}
      {run.heartbeat_at ? (
        <Field label="Last heartbeat" value={fmt(run.heartbeat_at)} />
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

function MetricsView({ grouped }: { grouped: MetricGroup[] }) {
  if (grouped.length === 0) {
    return (
      <div className="text-sm text-text-muted">
        No metric points yet. Records counters land here after the first task
        finishes.
      </div>
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
