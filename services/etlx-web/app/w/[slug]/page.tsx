"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import {
  ActivityIcon,
  AlertTriangleIcon,
  ArrowRightLeftIcon,
  CableIcon,
  CalendarClockIcon,
  ChevronRightIcon,
  RadarIcon,
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
  sensorsApi,
  type ConnectionSummary,
  type PipelineSummary,
  type RunSummary,
  type ScheduleSummary,
  type SensorSummary,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";
import { cn } from "@/lib/cn";
import { toast } from "sonner";
import { migrationSummaryOf } from "@/lib/migration-utils";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

interface ScheduleRow extends ScheduleSummary {
  pipeline_name: string;
}

const ONE_DAY_MS = 24 * 60 * 60 * 1000;

export default function WorkspaceHomePage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [pipelines, setPipelines] = useState<PipelineSummary[] | null>(null);
  const [connections, setConnections] = useState<ConnectionSummary[] | null>(null);
  const [schedules, setSchedules] = useState<ScheduleRow[] | null>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [sensors, setSensors] = useState<SensorSummary[] | null>(null);

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;

    async function load(workspaceId: string) {
      try {
        // Phase T (2026-05-28): added sensorsApi.list to the dashboard
        // fan-out. Promise.allSettled instead of all so a single
        // failing endpoint doesn't blank the entire page — each panel
        // falls back to its own loading/empty state.
        const [psR, connsR, rsR, sensR] = await Promise.allSettled([
          pipelinesApi.list(workspaceId),
          connectionsApi.list(workspaceId),
          runsApi.list(workspaceId, { limit: 50 }),
          sensorsApi.list(workspaceId),
        ]);
        if (cancelled) return;
        const ps =
          psR.status === "fulfilled" ? psR.value : ([] as PipelineSummary[]);
        if (psR.status === "fulfilled") setPipelines(ps);
        if (connsR.status === "fulfilled") setConnections(connsR.value);
        if (rsR.status === "fulfilled") setRuns(rsR.value);
        if (sensR.status === "fulfilled") setSensors(sensR.value);

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
            err instanceof ApiError ? err.message : t("common.loadFailed"),
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
  }, [ws, t]);

  const recentRuns = useMemo(() => runs?.slice(0, 10) ?? [], [runs]);
  const failingRuns = useMemo(
    () => (runs ?? []).filter((r) => r.status === "failed").slice(0, 5),
    [runs],
  );

  // Phase T (2026-05-28): pipeline_id → name lookup. Recent + failing
  // rows render the actual pipeline name (truncated to a sensible
  // width) instead of the UUID-prefix stand-in. Falls back to the old
  // UUID slice for pipelines that have since been deleted but still
  // have runs lingering.
  const pipelineNameById = useMemo(
    () => new Map((pipelines ?? []).map((p) => [p.id, p.name])),
    [pipelines],
  );

  // Phase AAV (2026-06-01) — migration-specific aggregate so the
  // dashboard surfaces what the operator actually built (often N
  // schema-mode rows in one go). Counted off the same ``pipelines``
  // payload so no extra request is needed.
  const migrationStats = useMemo(() => {
    if (!pipelines) return null;
    const migrationIds = new Set<string>();
    for (const p of pipelines) {
      if (migrationSummaryOf(p.current_config_json)) migrationIds.add(p.id);
    }
    let total24h = 0;
    let succeeded24h = 0;
    let inFlight = 0;
    // Phase ABI/ABJ (2026-06-01) — track which migrations have *ever*
    // been run. Persona dogfood showed 62/70 sitting un-run after a
    // bulk schema-mode create — surfacing that count makes the gap
    // visible at-a-glance on the dashboard (otherwise the success
    // rate sub-line shows nothing because there's no 24h activity).
    const ranAtLeastOnce = new Set<string>();
    if (runs) {
      const cutoff = Date.now() - ONE_DAY_MS;
      for (const r of runs) {
        if (!migrationIds.has(r.pipeline_id)) continue;
        ranAtLeastOnce.add(r.pipeline_id);
        if (new Date(r.created_at).getTime() < cutoff) continue;
        total24h += 1;
        if (r.status === "succeeded") succeeded24h += 1;
        if (r.status === "pending" || r.status === "running") inFlight += 1;
      }
    }
    const neverRun = migrationIds.size - ranAtLeastOnce.size;
    return {
      count: migrationIds.size,
      total24h,
      succeeded24h,
      inFlight,
      neverRun,
    };
  }, [pipelines, runs]);

  /** Today's runs window — used for both the count and the success-rate
   *  pill below. Single pass so we walk the array once. */
  const todayStats = useMemo(() => {
    if (!runs) return { total: 0, succeeded: 0, failed: 0, inFlight: 0 };
    const cutoff = Date.now() - ONE_DAY_MS;
    let total = 0;
    let succeeded = 0;
    let failed = 0;
    let inFlight = 0;
    for (const r of runs) {
      if (new Date(r.created_at).getTime() < cutoff) continue;
      total += 1;
      if (r.status === "succeeded") succeeded += 1;
      else if (r.status === "failed") failed += 1;
      else if (r.status === "running" || r.status === "pending") inFlight += 1;
    }
    return { total, succeeded, failed, inFlight };
  }, [runs]);

  // Success rate over today's *finished* runs (we exclude in-flight so
  // the rate stays meaningful while a wave is still landing). Display
  // as integer percentage; only show when there's a non-zero
  // denominator (otherwise "100%" of zero runs is misleading).
  const todaySuccessRate = useMemo(() => {
    const finished = todayStats.succeeded + todayStats.failed;
    if (finished === 0) return null;
    return Math.round((todayStats.succeeded / finished) * 100);
  }, [todayStats]);

  const activeSchedules = (schedules ?? []).filter((s) => s.is_active).length;
  const activeSensors = (sensors ?? []).filter((s) => s.is_active).length;

  return (
    <>
      <Header
        title={ws ? t("overview.title", { name: ws.name }) : t("common.loading")}
        subtitle={ws ? t("overview.subtitle") : undefined}
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
          <StatCard
            label={t("nav.pipelines")}
            value={pipelines?.length}
            icon={<WorkflowIcon size={18} />}
            href={ws ? `/w/${ws.slug}/pipelines` : "#"}
          />
          {/* Phase AAV (2026-06-01) — migrations as a first-class
              dashboard signal. Sub line shows the in-flight count or
              today's success rate so the operator sees migration
              health at a glance. */}
          <StatCard
            label={t("nav.migrations")}
            value={migrationStats?.count}
            icon={<ArrowRightLeftIcon size={18} />}
            href={ws ? `/w/${ws.slug}/migrations` : "#"}
            sub={
              migrationStats && migrationStats.total24h > 0
                ? migrationStats.inFlight > 0
                  ? t("overview.inFlightCount", { n: migrationStats.inFlight })
                  : t("overview.successRate", {
                      n: Math.round(
                        (migrationStats.succeeded24h / migrationStats.total24h) *
                          100,
                      ),
                    })
                : // Phase ABJ (2026-06-01) — when nothing's run in the
                  // last 24h, surface "N migrations never run" so the
                  // operator sees the backlog. Silent when caught up.
                  migrationStats && migrationStats.neverRun > 0
                  ? t("overview.migrationsNeverRun", {
                      n: migrationStats.neverRun,
                    })
                  : undefined
            }
          />
          <StatCard
            label={t("overview.activeSchedules")}
            value={schedules ? activeSchedules : undefined}
            icon={<CalendarClockIcon size={18} />}
            href={ws ? `/w/${ws.slug}/schedules` : "#"}
            sub={
              schedules && schedules.length > 0
                ? t("overview.paused", { n: schedules.length - activeSchedules })
                : undefined
            }
          />
          <StatCard
            label={t("nav.sensors")}
            value={sensors ? activeSensors : undefined}
            icon={<RadarIcon size={18} />}
            href={ws ? `/w/${ws.slug}/sensors` : "#"}
            sub={
              sensors && sensors.length > 0
                ? t("overview.pausedSensors", {
                    n: sensors.length - activeSensors,
                  })
                : undefined
            }
          />
          <StatCard
            label={t("nav.connections")}
            value={connections?.length}
            icon={<CableIcon size={18} />}
            href={ws ? `/w/${ws.slug}/connections` : "#"}
          />
          <StatCard
            label={t("overview.runsToday")}
            value={runs ? todayStats.total : undefined}
            icon={<ActivityIcon size={18} />}
            href={ws ? `/w/${ws.slug}/runs` : "#"}
            sub={
              // Phase T (2026-05-28): replace the generic "inLastBatch"
              // sub with a health summary. Three parts in priority
              // order: in-flight (operator action signal) → success
              // rate (health) → in-batch count (calibration). Falls
              // back to the original "no runs yet" hint when empty.
              runs && todayStats.total > 0
                ? [
                    todayStats.inFlight > 0
                      ? t("overview.inFlightCount", { n: todayStats.inFlight })
                      : null,
                    todaySuccessRate !== null
                      ? t("overview.successRate", { pct: todaySuccessRate })
                      : null,
                  ]
                    .filter(Boolean)
                    .join(" · ")
                : undefined
            }
          />
        </section>

        <section className="grid gap-6 lg:grid-cols-[2fr_1fr]">
          <Card>
            <SectionTitle
              title={t("overview.recentRuns")}
              hint={t("overview.recentRunsHint")}
              viewAllLabel={t("overview.viewAll")}
              link={ws ? `/w/${ws.slug}/runs` : null}
            />
            {runs === null ? (
              <div className="py-8 text-center text-sm text-text-muted">
                {t("common.loading")}
              </div>
            ) : recentRuns.length === 0 ? (
              <EmptyState
                icon={<ActivityIcon size={32} strokeWidth={1.5} />}
                title={t("overview.noRunsTitle")}
                description={t("overview.noRunsDesc")}
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
                        {/* Phase T (2026-05-28): lead with the pipeline
                            NAME instead of the run UUID — operators
                            scan for "which pipeline" first, then the
                            run id. Run id shifts down to a muted line
                            so it stays available for cross-reference
                            without dominating the row. */}
                        <div className="truncate text-sm font-medium text-text">
                          {pipelineNameById.get(r.pipeline_id) ??
                            t("overview.pipelineRef", {
                              id: r.pipeline_id.slice(0, 8),
                            })}
                        </div>
                        <div className="truncate font-mono text-[11px] text-text-muted">
                          {t("overview.run", { id: r.id.slice(0, 8) })}
                          {"  ·  "}
                          {r.schedule_id
                            ? t("overview.scheduled")
                            : t("overview.manual")}
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
              title={t("overview.recentFailures")}
              hint={t("overview.recentFailuresHint")}
              viewAllLabel={t("overview.viewAll")}
              link={ws ? `/w/${ws.slug}/runs` : null}
            />
            {runs === null ? (
              <div className="py-8 text-center text-sm text-text-muted">
                {t("common.loading")}
              </div>
            ) : failingRuns.length === 0 ? (
              <div className="flex items-center gap-2 rounded-md border border-success/30 bg-success/10 px-3 py-2 text-xs text-success">
                <AlertTriangleIcon size={14} />
                {t("overview.nothingFailing")}
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
                          {r.error_class ?? t("status.failed")}
                        </span>
                        <span className="text-text-muted">
                          {fmtTime(r.finished_at ?? r.started_at, t)}
                        </span>
                      </div>
                      <div className="mt-0.5 truncate text-[11px] text-text-muted">
                        {/* Phase T: pipeline NAME first in failures
                            too — operator's "which pipeline broke?"
                            answer should be at first glance. */}
                        <span className="font-medium text-text-secondary">
                          {pipelineNameById.get(r.pipeline_id) ??
                            t("overview.pipelineRef", {
                              id: r.pipeline_id.slice(0, 8),
                            })}
                        </span>
                        {" · "}
                        {t("overview.run", { id: r.id.slice(0, 8) })}
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
  viewAllLabel,
}: {
  title: string;
  hint?: string;
  link: string | null;
  viewAllLabel: string;
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
          {viewAllLabel}
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

function fmtTime(ts: string | null, t: Translate): string {
  if (!ts) return "—";
  const d = new Date(ts);
  const ms = Date.now() - d.getTime();
  if (ms < 60_000) return t("time.justNow");
  if (ms < 3600_000) return t("time.minutesAgo", { n: Math.floor(ms / 60_000) });
  if (ms < 86_400_000) return t("time.hoursAgo", { n: Math.floor(ms / 3_600_000) });
  return d.toLocaleDateString();
}
