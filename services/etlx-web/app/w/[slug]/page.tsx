"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import {
  ActivityIcon,
  AlertTriangleIcon,
  CableIcon,
  CalendarClockIcon,
  ChevronRightIcon,
  WorkflowIcon,
} from "lucide-react";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/status-badge";
import { EmptyState } from "@/components/ui/empty-state";
import {
  ApiError,
  connectionsApi,
  pipelinesApi,
  runsApi,
  schedulesApi,
  type ConnectionSummary,
  type PipelineSummary,
  type RunSummary,
  type ScheduleSummary,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { cn } from "@/lib/cn";
import { toast } from "sonner";

interface ScheduleRow extends ScheduleSummary {
  pipeline_name: string;
}

const ONE_DAY_MS = 24 * 60 * 60 * 1000;

export default function WorkspaceHomePage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const [pipelines, setPipelines] = useState<PipelineSummary[] | null>(null);
  const [connections, setConnections] = useState<ConnectionSummary[] | null>(null);
  const [schedules, setSchedules] = useState<ScheduleRow[] | null>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;

    async function load(workspaceId: string) {
      try {
        const [ps, conns, rs] = await Promise.all([
          pipelinesApi.list(workspaceId),
          connectionsApi.list(workspaceId),
          runsApi.list(workspaceId, { limit: 50 }),
        ]);
        if (cancelled) return;
        setPipelines(ps);
        setConnections(conns);
        setRuns(rs);

        const groups = await Promise.all(
          ps.map(async (p) => {
            const list = await schedulesApi.list(workspaceId, p.id);
            return list.map((s) => ({ ...s, pipeline_name: p.name }));
          }),
        );
        if (!cancelled) setSchedules(groups.flat());
      } catch (err) {
        if (!cancelled) {
          toast.error(
            err instanceof ApiError ? err.message : "Couldn't load workspace.",
          );
        }
      }
    }

    void load(ws.id);
    const timer = window.setInterval(() => void load(ws.id), 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [ws]);

  const recentRuns = useMemo(() => runs?.slice(0, 10) ?? [], [runs]);
  const failingRuns = useMemo(
    () => (runs ?? []).filter((r) => r.status === "failed").slice(0, 5),
    [runs],
  );
  const runsToday = useMemo(() => {
    if (!runs) return 0;
    const cutoff = Date.now() - ONE_DAY_MS;
    return runs.filter((r) => new Date(r.created_at).getTime() >= cutoff)
      .length;
  }, [runs]);

  const activeSchedules = (schedules ?? []).filter((s) => s.is_active).length;

  return (
    <>
      <Header
        title={ws ? `${ws.name} overview` : "Loading…"}
        subtitle={
          ws
            ? "Live snapshot of your workspace — refreshes every 10 s."
            : undefined
        }
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Pipelines"
            value={pipelines?.length}
            icon={<WorkflowIcon size={18} />}
            href={ws ? `/w/${ws.slug}/pipelines` : "#"}
          />
          <StatCard
            label="Active schedules"
            value={schedules ? activeSchedules : undefined}
            icon={<CalendarClockIcon size={18} />}
            href={ws ? `/w/${ws.slug}/schedules` : "#"}
            sub={
              schedules && schedules.length > 0
                ? `${schedules.length - activeSchedules} paused`
                : undefined
            }
          />
          <StatCard
            label="Connections"
            value={connections?.length}
            icon={<CableIcon size={18} />}
            href={ws ? `/w/${ws.slug}/connections` : "#"}
          />
          <StatCard
            label="Runs today"
            value={runs ? runsToday : undefined}
            icon={<ActivityIcon size={18} />}
            href={ws ? `/w/${ws.slug}/runs` : "#"}
            sub={
              runs && runs.length > 0
                ? `${runs.length} in last batch`
                : undefined
            }
          />
        </section>

        <section className="grid gap-6 lg:grid-cols-[2fr_1fr]">
          <Card>
            <SectionTitle
              title="Recent runs"
              hint="Newest 10. Click to see logs + metrics."
              link={ws ? `/w/${ws.slug}/runs` : null}
            />
            {runs === null ? (
              <div className="py-8 text-center text-sm text-text-muted">
                Loading…
              </div>
            ) : recentRuns.length === 0 ? (
              <EmptyState
                icon={<ActivityIcon size={32} strokeWidth={1.5} />}
                title="No runs yet"
                description="Trigger a pipeline or attach a cron schedule to get started."
              />
            ) : (
              <ul className="divide-y divide-border-subtle">
                {recentRuns.map((r) => (
                  <li key={r.id}>
                    <Link
                      href={ws ? `/w/${ws.slug}/runs/${r.id}` : "#"}
                      className="grid grid-cols-[110px_1fr_110px_auto] items-center gap-3 py-2.5 transition duration-150 hover:bg-overlay"
                    >
                      <StatusBadge status={r.status} />
                      <div className="min-w-0">
                        <div className="truncate font-mono text-xs text-text-secondary">
                          run {r.id.slice(0, 8)}…
                        </div>
                        <div className="truncate text-[11px] text-text-muted">
                          pipeline {r.pipeline_id.slice(0, 8)}…
                          {r.schedule_id
                            ? "  ·  scheduled"
                            : "  ·  manual"}
                        </div>
                      </div>
                      <div className="text-right text-xs text-text-secondary">
                        {fmtDuration(r.duration_seconds)}
                      </div>
                      <ChevronRightIcon
                        size={14}
                        className="text-text-muted"
                      />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <SectionTitle
              title="Recent failures"
              hint="Top 5 failed in the latest batch."
              link={ws ? `/w/${ws.slug}/runs` : null}
            />
            {runs === null ? (
              <div className="py-8 text-center text-sm text-text-muted">
                Loading…
              </div>
            ) : failingRuns.length === 0 ? (
              <div className="flex items-center gap-2 rounded-md border border-success/30 bg-success/10 px-3 py-2 text-xs text-success">
                <AlertTriangleIcon size={14} />
                Nothing failing right now.
              </div>
            ) : (
              <ul className="space-y-2">
                {failingRuns.map((r) => (
                  <li key={r.id}>
                    <Link
                      href={ws ? `/w/${ws.slug}/runs/${r.id}` : "#"}
                      className="block rounded-md border border-error/40 bg-error/10 px-3 py-2 text-xs transition duration-150 hover:bg-error/20"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-error">
                          {r.error_class ?? "Failed"}
                        </span>
                        <span className="text-text-muted">
                          {fmtTime(r.finished_at ?? r.started_at)}
                        </span>
                      </div>
                      <div className="mt-0.5 truncate text-[11px] text-text-muted">
                        run {r.id.slice(0, 8)}… · pipeline{" "}
                        {r.pipeline_id.slice(0, 8)}…
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </section>
      </main>
    </>
  );
}

function StatCard({
  label,
  value,
  sub,
  icon,
  href,
}: {
  label: string;
  value: number | undefined;
  sub?: string;
  icon: React.ReactNode;
  href: string;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "block rounded-lg border border-border-subtle bg-elevated p-4 transition duration-200",
        "hover:border-border-strong hover:bg-overlay",
      )}
    >
      <div className="flex items-center justify-between text-text-muted">
        <span className="text-xs font-semibold uppercase tracking-wider">
          {label}
        </span>
        <span>{icon}</span>
      </div>
      <div className="mt-2 text-3xl font-semibold text-text">
        {value ?? "—"}
      </div>
      {sub ? (
        <div className="mt-1 text-xs text-text-muted">{sub}</div>
      ) : null}
    </Link>
  );
}

function SectionTitle({
  title,
  hint,
  link,
}: {
  title: string;
  hint?: string;
  link: string | null;
}) {
  return (
    <div className="mb-4 flex items-baseline justify-between gap-3 border-b border-border-subtle pb-3">
      <div>
        <div className="text-sm font-semibold text-text">{title}</div>
        {hint ? (
          <div className="text-[11px] text-text-muted">{hint}</div>
        ) : null}
      </div>
      {link ? (
        <Link
          href={link}
          className="text-xs text-text-secondary transition duration-150 hover:text-accent"
        >
          View all →
        </Link>
      ) : null}
    </div>
  );
}

function fmtDuration(s: number | null): string {
  if (s == null) return "—";
  if (s < 1) return `${Math.round(s * 1000)}ms`;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

function fmtTime(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  const ms = Date.now() - d.getTime();
  if (ms < 60_000) return "just now";
  if (ms < 3600_000) return `${Math.floor(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
  return d.toLocaleDateString();
}
