"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import {
  ActivityIcon,
  AlertTriangleIcon,
  ArrowRightLeftIcon,
  BoxesIcon,
  CableIcon,
  CalendarClockIcon,
  ChevronRightIcon,
  DatabaseIcon,
  RadarIcon,
  WorkflowIcon,
} from "lucide-react";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/status-badge";
import { EmptyState } from "@/components/ui/empty-state";
import {
  ApiError,
  assetsApi,
  connectionsApi,
  erdApi,
  pipelinesApi,
  runsApi,
  schedulesApi,
  sensorsApi,
  type AssetSummary,
  type ConnectionSummary,
  type ErdDiagramSummary,
  type PipelineSummary,
  type RunSummary,
  type ScheduleSummary,
  type SensorSummary,
} from "@/lib/api";
import { CronExpressionParser } from "cron-parser";
import { useCurrentUser } from "@/components/providers/auth-provider";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import { relativeTime, absoluteTime } from "@/lib/format-time";
import { cn } from "@/lib/cn";
import { toast } from "sonner";
import { migrationSummaryOf } from "@/lib/migration-utils";
import {
  buildConnectionUsage,
  extractConnectionNames,
} from "@/lib/connection-usage";

interface ScheduleRow extends ScheduleSummary {
  pipeline_name: string;
}

const ONE_DAY_MS = 24 * 60 * 60 * 1000;

export default function WorkspaceHomePage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  // Phase ABU/2 (2026-06-01) — friendly "by you" in Recent runs.
  const currentUser = useCurrentUser();
  const [pipelines, setPipelines] = useState<PipelineSummary[] | null>(null);
  const [connections, setConnections] = useState<ConnectionSummary[] | null>(null);
  const [schedules, setSchedules] = useState<ScheduleRow[] | null>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [sensors, setSensors] = useState<SensorSummary[] | null>(null);
  // Phase AFS (2026-06-04) — catalog asset count for the analyst's
  // dashboard entry point.
  const [assets, setAssets] = useState<AssetSummary[] | null>(null);
  const [erds, setErds] = useState<ErdDiagramSummary[] | null>(null);

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;

    async function load(workspaceId: string) {
      try {
        // Phase T (2026-05-28): added sensorsApi.list to the dashboard
        // fan-out. Promise.allSettled instead of all so a single
        // failing endpoint doesn't blank the entire page — each panel
        // falls back to its own loading/empty state.
        const [psR, connsR, rsR, sensR, assetsR, erdsR] = await Promise.allSettled([
          pipelinesApi.list(workspaceId),
          connectionsApi.list(workspaceId),
          runsApi.list(workspaceId, { limit: 50 }),
          sensorsApi.list(workspaceId),
          assetsApi.list(workspaceId),
          erdApi.list(workspaceId),
        ]);
        if (cancelled) return;
        const ps =
          psR.status === "fulfilled" ? psR.value : ([] as PipelineSummary[]);
        if (psR.status === "fulfilled") setPipelines(ps);
        if (connsR.status === "fulfilled") setConnections(connsR.value);
        if (rsR.status === "fulfilled") setRuns(rsR.value);
        if (sensR.status === "fulfilled") setSensors(sensR.value);
        if (assetsR.status === "fulfilled") setAssets(assetsR.value);
        if (erdsR.status === "fulfilled") setErds(erdsR.value);

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

  // Phase AFD (2026-06-04) — active schedules whose pipeline's most recent
  // run failed. A schedule firing on time but failing every run is a
  // top-priority signal that's otherwise invisible on the dashboard.
  // Heuristic over the recent runs window (dashboard fetches the last 50).
  const failingSchedules = useMemo(() => {
    if (!schedules || !runs) return 0;
    const lastByPipeline = new Map<string, RunSummary>();
    for (const r of runs) {
      if (!lastByPipeline.has(r.pipeline_id)) lastByPipeline.set(r.pipeline_id, r);
    }
    return schedules.filter(
      (s) => s.is_active && lastByPipeline.get(s.pipeline_id)?.status === "failed",
    ).length;
  }, [schedules, runs]);
  const activeSensors = (sensors ?? []).filter((s) => s.is_active).length;

  // Phase ADS (2026-06-04) — regular pipelines that reference a
  // connection no longer in the workspace (their next run fails to
  // build). Surfaces ADC's per-row flag as a dashboard signal. Needs
  // both payloads; counts non-migration pipelines only (migrations
  // have their own surface + signals).
  const brokenPipelines = useMemo(() => {
    if (!pipelines || !connections) return 0;
    const names = new Set(connections.map((c) => c.name));
    let n = 0;
    for (const p of pipelines) {
      if (migrationSummaryOf(p.current_config_json)) continue;
      const refs = extractConnectionNames(p.current_config_json);
      if ([...refs].some((r) => !names.has(r))) n += 1;
    }
    return n;
  }, [pipelines, connections]);

  // Phase ADJ (2026-06-04) — sensors whose target pipeline no longer
  // exists. They still poll but trigger nothing, so surface them as a
  // stronger signal than the paused count. Needs both payloads.
  const orphanedSensors = useMemo(() => {
    if (!sensors || !pipelines) return 0;
    const ids = new Set(pipelines.map((p) => p.id));
    return sensors.filter(
      (s) => s.target_pipeline_id && !ids.has(s.target_pipeline_id),
    ).length;
  }, [sensors, pipelines]);

  // Phase AFG (2026-06-04) — active sensors whose target pipeline's most
  // recent run failed. A sensor firing into a pipeline that fails every
  // run is a top-priority signal (parallel to failingSchedules, AFD).
  // Heuristic over the recent runs window (dashboard fetches the last 50).
  const failingSensors = useMemo(() => {
    if (!sensors || !runs) return 0;
    const lastByPipeline = new Map<string, RunSummary>();
    for (const r of runs) {
      if (!lastByPipeline.has(r.pipeline_id)) lastByPipeline.set(r.pipeline_id, r);
    }
    return sensors.filter(
      (s) =>
        s.is_active &&
        s.target_pipeline_id &&
        lastByPipeline.get(s.target_pipeline_id)?.status === "failed",
    ).length;
  }, [sensors, runs]);

  // Phase ACY (2026-06-04) — count connections no pipeline references,
  // a cleanup signal for the operator. Reuses ACL's usage index over
  // the pipelines + connections the dashboard already fetched, so no
  // extra request. null until both payloads land.
  const unusedConnections = useMemo(() => {
    if (!connections || !pipelines) return null;
    const usage = buildConnectionUsage(
      pipelines.map((p) => ({
        id: p.id,
        name: p.name,
        config: p.current_config_json,
      })),
    );
    return connections.filter((c) => !(usage.get(c.name)?.length)).length;
  }, [connections, pipelines]);
  /** Phase ACB (2026-06-01) — earliest next-firing across all active
   *  cron schedules. Used to give the "Active schedules" card a
   *  forward-looking sub-line ("next in 12m"). Returns ``null`` when
   *  no active cron is present (paused-only, stream, or invalid). */
  const nextFireSoon = useMemo(() => {
    if (!schedules) return null;
    let soonestMs: number | null = null;
    for (const s of schedules) {
      if (!s.is_active || !s.cron_expr) continue;
      try {
        // Server fires cron in UTC (croniter) — match it here.
        const next = CronExpressionParser.parse(s.cron_expr, { tz: "UTC" }).next().toDate();
        const ms = next.getTime() - Date.now();
        if (ms < 0) continue;
        if (soonestMs === null || ms < soonestMs) soonestMs = ms;
      } catch {
        /* invalid cron — skip */
      }
    }
    if (soonestMs === null) return null;
    if (soonestMs < 60_000) return t("overview.scheduleFireSoon");
    if (soonestMs < 3_600_000)
      return t("overview.scheduleFireInMinutes", {
        n: Math.round(soonestMs / 60_000),
      });
    if (soonestMs < 86_400_000)
      return t("overview.scheduleFireInHours", {
        n: Math.round(soonestMs / 3_600_000),
      });
    return t("overview.scheduleFireInDays", {
      n: Math.round(soonestMs / 86_400_000),
    });
  }, [schedules, t]);

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
            /* Phase ACE (2026-06-01) — exclude migrations from the
               Pipelines count to match the /pipelines page which
               filters them out (so the card and list agree). */
            value={
              pipelines
                ? pipelines.filter(
                    (p) => migrationSummaryOf(p.current_config_json) === null,
                  ).length
                : undefined
            }
            icon={<WorkflowIcon size={18} />}
            // Phase ADT (2026-06-04) — deep-link to the broken subset
            // when that's the signal (ADH/ADI/ADK pattern).
            href={
              ws
                ? brokenPipelines > 0
                  ? `/w/${ws.slug}/pipelines?broken=1`
                  : `/w/${ws.slug}/pipelines`
                : "#"
            }
            // Phase ADS (2026-06-04) — flag pipelines that reference a
            // missing connection (ADC) as a top-level health signal.
            sub={
              brokenPipelines > 0
                ? t("overview.pipelinesBroken", { n: brokenPipelines })
                : undefined
            }
          />
          {/* Phase AAV (2026-06-01) — migrations as a first-class
              dashboard signal. Sub line shows the in-flight count or
              today's success rate so the operator sees migration
              health at a glance. */}
          <StatCard
            label={t("nav.migrations")}
            value={migrationStats?.count}
            icon={<ArrowRightLeftIcon size={18} />}
            // Phase ADH (2026-06-04) — when the surfaced signal is
            // "N never run" (no 24h activity), deep-link straight to the
            // pre-filtered subset so the click lands on the actionable
            // rows (matches ABM/ABZ deep-link presets).
            href={
              ws
                ? migrationStats &&
                  migrationStats.total24h === 0 &&
                  migrationStats.neverRun > 0
                  ? `/w/${ws.slug}/migrations?lastRun=never`
                  : `/w/${ws.slug}/migrations`
                : "#"
            }
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
            // Phase AFE (2026-06-04) — when the signal is "N failing",
            // deep-link to the pre-filtered subset (ADH/ADI/ADK/ADT pattern)
            // so the click lands on the actionable rows.
            href={
              ws
                ? failingSchedules > 0
                  ? `/w/${ws.slug}/schedules?lastRun=failed`
                  : `/w/${ws.slug}/schedules`
                : "#"
            }
            sub={
              // Phase AFD (2026-06-04) — a failing active schedule is the
              // most urgent signal, so it wins over the forward-looking
              // "next in Xm" (ACB) and the paused count.
              failingSchedules > 0
                ? t("overview.schedulesFailing", { n: failingSchedules })
                : // Phase ACB (2026-06-01) — prefer the forward-looking
                  // "next in Xm" when any active cron is present; fall
                  // back to the paused count when nothing is active.
                  (nextFireSoon ??
                  (schedules && schedules.length > 0
                    ? t("overview.paused", {
                        n: schedules.length - activeSchedules,
                      })
                    : undefined))
            }
          />
          <StatCard
            label={t("nav.sensors")}
            value={sensors ? activeSensors : undefined}
            icon={<RadarIcon size={18} />}
            // Phase ADK (2026-06-04) — deep-link to the orphaned subset
            // when that's the signal (ADH/ADI pattern). Phase AFH — a
            // failing target wins and deep-links to its own filtered
            // subset (?lastRun=failed), matching schedules (AFE).
            href={
              ws
                ? failingSensors > 0
                  ? `/w/${ws.slug}/sensors?lastRun=failed`
                  : orphanedSensors > 0
                    ? `/w/${ws.slug}/sensors?filter=orphaned`
                    : `/w/${ws.slug}/sensors`
                : "#"
            }
            sub={
              // Phase AFG (2026-06-04) — a failing target is the most
              // urgent signal, then orphaned (ADK), then the paused count.
              failingSensors > 0
                ? t("overview.sensorsFailing", { n: failingSensors })
                : orphanedSensors > 0
                  ? t("overview.sensorsOrphaned", { n: orphanedSensors })
                  : sensors && sensors.length > 0
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
            // Phase ADI (2026-06-04) — deep-link to the unused subset
            // when that's the surfaced signal, mirroring ADH.
            href={
              ws
                ? unusedConnections && unusedConnections > 0
                  ? `/w/${ws.slug}/connections?usage=unused`
                  : `/w/${ws.slug}/connections`
                : "#"
            }
            sub={
              unusedConnections && unusedConnections > 0
                ? t("overview.connectionsUnused", { n: unusedConnections })
                : undefined
            }
          />
          {/* Phase AFS (2026-06-04) — catalog entry point for the analyst.
              Sub-line flags opaque assets (no column-level lineage, AEH) as
              a traceability signal. */}
          <StatCard
            label={t("nav.assets")}
            value={assets?.length}
            icon={<DatabaseIcon size={18} />}
            // Phase AFT (2026-06-04) — deep-link to the opaque subset when
            // that's the surfaced signal (ADI/ADK pattern).
            href={
              ws
                ? assets && assets.some((a) => a.column_lineage_opaque)
                  ? `/w/${ws.slug}/assets?lineage=opaque`
                  : `/w/${ws.slug}/assets`
                : "#"
            }
            sub={
              assets && assets.filter((a) => a.column_lineage_opaque).length > 0
                ? t("overview.assetsOpaque", {
                    n: assets.filter((a) => a.column_lineage_opaque).length,
                  })
                : undefined
            }
          />
          <StatCard
            label={t("nav.erd")}
            value={erds?.length}
            icon={<BoxesIcon size={18} />}
            href={ws ? `/w/${ws.slug}/erd` : "#"}
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
                          {/* Phase AEK — distinguish auto (system-fired)
                              from manual; previously both-null fell to
                              "manual" incorrectly. */}
                          {r.schedule_id
                            ? t("overview.scheduled")
                            : r.triggered_by_user_id
                              ? r.triggered_by_user_id === currentUser?.id
                                ? t("overview.manualByYou")
                                : t("overview.manual")
                              : t("overview.auto")}
                        </div>
                      </div>
                      <div className="text-right text-xs text-text-secondary">
                        {/* Phase AFV (2026-06-04) — live elapsed for an
                            in-flight run, matching runs list (AFK) / run
                            detail (AFJ); else the final duration. */}
                        {r.status === "running" && r.started_at
                          ? `${fmtDuration(
                              (Date.now() - Date.parse(r.started_at)) / 1000,
                            )} · ${t("runDetail.elapsedRunning")}`
                          : fmtDuration(r.duration_seconds)}
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
              /* Phase ABZ (2026-06-01) — pre-apply status=failed so
                 the link is "see all failures", not "see all runs". */
              link={ws ? `/w/${ws.slug}/runs?status=failed` : null}
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
                        <span
                          className="text-text-muted"
                          title={absoluteTime(r.finished_at ?? r.started_at)}
                        >
                          {relativeTime(r.finished_at ?? r.started_at, t)}
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
