"use client";

/**
 * /w/[slug]/migrations/[id] — Phase AAN2 (2026-05-29).
 *
 * Dedicated edit/detail page for a migration pipeline. Loads the
 * pipeline through ``pipelinesApi.get``, parses the config into
 * form state via ``parseMigrationConfig``, and saves back as a
 * fresh ``PipelineConfig`` JSON.
 *
 * If the parsing fails (the pipeline turns out to be a graph-mode
 * or fan-out pipeline that someone migrated by hand), we bail with
 * a friendly notice that routes the user to the generic pipelines
 * builder instead — we'd rather opt them out than silently lose
 * data through round-trip.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  CalendarClockIcon,
  HandIcon,
  PlayIcon,
  ShieldCheckIcon,
  Trash2Icon,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { StatusBadge } from "@/components/ui/status-badge";
import { CronInput } from "@/components/schedules/cron-input";
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
import { relativeTime, absoluteTime } from "@/lib/format-time";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import { MigrationForm } from "@/components/migrations/migration-form";
import {
  buildMigrationConfig,
  parseMigrationConfig,
  validateMigrationForm,
  type MigrationFormData,
} from "@/lib/migration-config";

const RUNS_POLL_MS = 5_000;
const RUNS_LIMIT = 5;

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export default function MigrationDetailPage() {
  const router = useRouter();
  const { slug, id } = useParams<{ slug: string; id: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();

  const [pipeline, setPipeline] = useState<PipelineSummary | null>(null);
  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [form, setForm] = useState<MigrationFormData | null>(null);
  /** ``true`` only when the loaded config wasn't a migration shape —
   *  we render the bail-out card and don't show the form. */
  const [outsideMigrationShape, setOutsideMigrationShape] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Recent runs panel (Phase AAN4) — close the loop so the user
  // sees the migration in motion without leaving the page.
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [triggering, setTriggering] = useState(false);
  // Phase ABP (2026-06-01) — dry-run state. Surfaces connection
  // validity + connector instantiation before the user commits to a
  // wall-clock Run now (which can take minutes for a big sink).
  const [dryRunning, setDryRunning] = useState(false);
  // Phase ABX (2026-06-01) — keep the latest dry-run result inline
  // so the user can re-read the connector statuses after the toast
  // disappears. ``null`` = never run; otherwise the latest result.
  const [dryRunResult, setDryRunResult] = useState<
    import("@/lib/api").DryRunResponse | null
  >(null);
  // Phase AAU (2026-06-01) — quick schedule. One migration ⇒ at most
  // one cron schedule for our UX; the underlying server supports
  // many, but the migration surface is narrower on purpose. We pick
  // the first (or null) and present it as a single toggle + cron
  // input on the detail page.
  const [schedule, setSchedule] = useState<ScheduleSummary | null>(null);
  const [scheduleLoaded, setScheduleLoaded] = useState(false);
  const [scheduleDraft, setScheduleDraft] = useState("");
  const [savingSchedule, setSavingSchedule] = useState(false);

  useEffect(() => {
    if (!ws || !id) return;
    let cancelled = false;
    (async () => {
      try {
        const [p, cs, scheds] = await Promise.all([
          pipelinesApi.get(ws.id, id),
          connectionsApi.list(ws.id),
          // Phase AAU (2026-06-01) — load any existing schedule so
          // the operator sees the current automation state at a
          // glance. Soft-fail (empty array on error) so a network
          // wobble doesn't block the form.
          schedulesApi.list(ws.id, id).catch(() => [] as ScheduleSummary[]),
        ]);
        if (cancelled) return;
        setPipeline(p);
        setConnections(cs);
        const first = scheds[0] ?? null;
        setSchedule(first);
        setScheduleDraft(first?.cron_expr ?? "");
        setScheduleLoaded(true);
        const parsed = parseMigrationConfig(p.current_config_json);
        if (!parsed) {
          setOutsideMigrationShape(true);
          return;
        }
        setForm(parsed);
      } catch (err) {
        if (!cancelled) {
          toast.error(
            err instanceof ApiError ? err.message : t("pipelines.loadFailed"),
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws, id, t]);

  // Recent runs poller (Phase AAN4). Pipeline-scoped + small limit
  // matches the operator's mental model: "what did this migration do
  // recently?". Polls every 5s so a triggered run lands without a
  // page reload — mirrors the runs page's cadence.
  useEffect(() => {
    if (!ws || !id) return;
    let cancelled = false;
    const fetchRuns = async () => {
      try {
        const list = await runsApi.list(ws.id, {
          pipeline_id: id,
          limit: RUNS_LIMIT,
        });
        if (!cancelled) setRuns(list);
      } catch {
        // Soft-fail — don't toast on every poll tick if the network
        // wobbles. The page still shows whatever last landed.
        if (!cancelled && runs === null) setRuns([]);
      }
    };
    void fetchRuns();
    const handle = setInterval(() => void fetchRuns(), RUNS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ws, id]);

  async function onSubmit() {
    if (!ws || !pipeline || !form) return;
    const errs = validateMigrationForm(form);
    if (Object.keys(errs).length > 0) {
      toast.error(t("migrations.errRequired"));
      return;
    }
    setSubmitting(true);
    try {
      const config = buildMigrationConfig(pipeline.name, form);
      const updated = await pipelinesApi.update(ws.id, pipeline.id, { config });
      setPipeline(updated);
      toast.success(t("migrations.saved"));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function onRunNow() {
    if (!ws || !pipeline) return;
    setTriggering(true);
    try {
      const r = await pipelinesApi.trigger(ws.id, pipeline.id);
      toast.success(t("migrations.runQueued"));
      // Optimistic insert so the user sees the run row immediately;
      // the next poll tick reconciles with the server truth.
      setRuns((prev) => (prev ? [r, ...prev].slice(0, RUNS_LIMIT) : [r]));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    } finally {
      setTriggering(false);
    }
  }

  async function onDryRun() {
    if (!ws || !pipeline) return;
    setDryRunning(true);
    try {
      const result = await pipelinesApi.dryRun(ws.id, pipeline.id);
      // Phase ABX — keep the result inline so it survives the toast
      // dismiss. Operators often compare 2-3 runs in sequence.
      setDryRunResult(result);
      if (result.ok) {
        // Show count of validated connectors so the message is
        // informative ("checked 2 of them") not just a vague "ok".
        const okCount = result.connectors.filter((c) => c.ok).length;
        toast.success(
          t("migrations.dryRunOk", {
            n: okCount,
            total: result.connectors.length,
          }),
        );
      } else {
        // Show the first error verbatim — operators want the exact
        // string so they can grep their config for the typo. The rest
        // come through in additional toasts so nothing is hidden.
        const errs = result.errors.length > 0
          ? result.errors
          : result.connectors.filter((c) => !c.ok).map((c) => `${c.name}: ${c.error ?? "unknown"}`);
        for (const e of errs) {
          toast.error(t("migrations.dryRunFailed", { error: e }));
        }
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    } finally {
      setDryRunning(false);
    }
  }

  async function onSaveSchedule() {
    if (!ws || !pipeline) return;
    const expr = scheduleDraft.trim();
    if (!expr) {
      toast.error(t("migrations.scheduleCronRequired"));
      return;
    }
    setSavingSchedule(true);
    try {
      if (schedule) {
        const updated = await schedulesApi.update(ws.id, pipeline.id, schedule.id, {
          cron_expr: expr,
        });
        setSchedule(updated);
        toast.success(t("migrations.scheduleSaved"));
      } else {
        const created = await schedulesApi.create(ws.id, pipeline.id, {
          // Auto-name from the pipeline so the schedules table stays
          // navigable from the global schedules page.
          name: `${pipeline.name} (auto)`,
          mode: "batch",
          cron_expr: expr,
          is_active: true,
        });
        setSchedule(created);
        toast.success(t("migrations.scheduleSaved"));
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSavingSchedule(false);
    }
  }

  async function onToggleSchedule() {
    if (!ws || !pipeline || !schedule) return;
    setSavingSchedule(true);
    try {
      const updated = await schedulesApi.toggle(ws.id, pipeline.id, schedule.id);
      setSchedule(updated);
      toast.success(
        updated.is_active
          ? t("migrations.scheduleActivated")
          : t("migrations.schedulePaused"),
      );
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSavingSchedule(false);
    }
  }

  async function onClearSchedule() {
    if (!ws || !pipeline || !schedule) return;
    setSavingSchedule(true);
    try {
      await schedulesApi.delete(ws.id, pipeline.id, schedule.id);
      setSchedule(null);
      setScheduleDraft("");
      toast.success(t("migrations.scheduleCleared"));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSavingSchedule(false);
    }
  }

  async function onDelete() {
    if (!ws || !pipeline) return;
    setDeleting(true);
    try {
      await pipelinesApi.delete(ws.id, pipeline.id);
      toast.success(t("migrations.deleted"));
      router.push(`/w/${slug}/migrations`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  return (
    <>
      <Header
        title={pipeline?.name ?? t("migrations.formTitleEdit")}
        subtitle={t("migrations.formSubtitleEdit")}
        actions={
          pipeline && !outsideMigrationShape ? (
            <>
              {/* Phase ABP — Dry run first: cheap validation
                  catches typos before the user commits to a wall-
                  clock Run now (which may take minutes). */}
              <Button
                variant="secondary"
                size="sm"
                loading={dryRunning}
                disabled={!pipeline.current_version}
                onClick={() => void onDryRun()}
                title={
                  pipeline.current_version
                    ? t("migrations.dryRunHint")
                    : t("migrations.saveBeforeRun")
                }
              >
                <ShieldCheckIcon size={14} />
                {t("migrations.dryRun")}
              </Button>
              <Button
                size="sm"
                loading={triggering}
                disabled={!pipeline.current_version}
                onClick={() => void onRunNow()}
                title={
                  pipeline.current_version
                    ? undefined
                    : t("migrations.saveBeforeRun")
                }
              >
                <PlayIcon size={14} />
                {t("migrations.runNow")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2Icon size={14} />
                {t("migrations.delete")}
              </Button>
            </>
          ) : pipeline ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2Icon size={14} />
              {t("migrations.delete")}
            </Button>
          ) : null
        }
      />
      <main className="mx-auto w-full max-w-4xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {outsideMigrationShape && pipeline ? (
          <Card>
            <p className="text-sm text-text-secondary">
              {t("migrations.notMigration")}
            </p>
            <div className="mt-3">
              <Link href={`/w/${slug}/pipelines/${pipeline.id}/edit`}>
                <Button size="sm" variant="secondary">
                  {t("migrations.openInPipelines")}
                </Button>
              </Link>
            </div>
          </Card>
        ) : (
          <>
            {/* Phase ABX (2026-06-01) — inline dry-run result. Shows
                the last run's per-connector status as a persistent
                panel so the operator can re-read after the toast
                fades. Dismissable since some operators prefer a
                clean slate after they've absorbed it. */}
            {dryRunResult ? (
              <DryRunResultCard
                result={dryRunResult}
                onDismiss={() => setDryRunResult(null)}
                t={t}
              />
            ) : null}
            {/* Phase ADL (2026-06-04) — warn when this migration points
                at a connection that no longer exists (deleted/renamed).
                The form's connection selects would otherwise just show
                empty with no explanation. Only once connections have
                loaded (length>0) to avoid a load-race false positive. */}
            {form && connections.length > 0
              ? (() => {
                  const missing = [
                    form.sourceConnection,
                    form.sinkConnection,
                  ].filter(
                    (n) => n && !connections.some((c) => c.name === n),
                  );
                  return missing.length > 0 ? (
                    <div className="rounded-md border border-error/40 bg-error/10 px-4 py-3 text-sm text-error">
                      {t("pipelines.missingConnection", {
                        names: missing.join(", "),
                      })}
                    </div>
                  ) : null;
                })()
              : null}
            <MigrationForm
              workspaceId={ws?.id ?? ""}
              name={pipeline?.name ?? ""}
              onNameChange={() => {
                /* Name is locked on edit — rename lives on the
                 * pipelines page (a migration-specific rename would
                 * just duplicate that surface). */
              }}
              form={form}
              onChange={setForm}
              connections={connections}
              nameLocked
              submitting={submitting}
              onSubmit={onSubmit}
              onCancel={() => router.push(`/w/${slug}/migrations`)}
              submitLabel={t("common.save")}
            />
            <ScheduleCard
              loaded={scheduleLoaded}
              schedule={schedule}
              draft={scheduleDraft}
              onDraft={setScheduleDraft}
              saving={savingSchedule}
              onSave={() => void onSaveSchedule()}
              onToggle={() => void onToggleSchedule()}
              onClear={() => void onClearSchedule()}
              t={t}
            />
            <RecentRunsCard
              runs={runs}
              slug={slug}
              pipelineId={pipeline?.id ?? id}
              t={t}
              emptyHint={t("migrations.runsEmpty")}
              title={t("migrations.recentRuns")}
            />
          </>
        )}
      </main>
      <ConfirmDialog
        open={confirmDelete}
        title={t("migrations.delete")}
        description={t("migrations.deleteConfirm")}
        confirmLabel={t("common.delete")}
        destructive
        loading={deleting}
        onConfirm={() => void onDelete()}
        onCancel={() => setConfirmDelete(false)}
      />
    </>
  );
}

function RecentRunsCard({
  runs,
  slug,
  pipelineId,
  t,
  title,
  emptyHint,
}: {
  runs: RunSummary[] | null;
  slug: string;
  pipelineId: string;
  t: (k: never) => string;
  title: string;
  emptyHint: string;
}) {
  const tx = t as unknown as (k: string) => string;
  // Phase AAX follow-up (2026-06-01) — "View all" link in the
  // header drops the operator on the runs page already filtered to
  // this pipeline. The recent-runs panel is intentionally bounded
  // (5 rows) so digging deeper has to leave the migration page.
  const hasFailed =
    Array.isArray(runs) && runs.some((r) => r.status === "failed");
  return (
    <Card>
      <div className="flex items-baseline justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {title}
        </div>
        <div className="flex gap-2">
          {hasFailed ? (
            <Link
              href={`/w/${slug}/runs?pipeline=${pipelineId}&status=failed`}
              className="text-xs text-error hover:underline"
            >
              {tx("migrations.viewFailures")}
            </Link>
          ) : null}
          <Link
            href={`/w/${slug}/runs?pipeline=${pipelineId}`}
            className="text-xs text-text-muted hover:text-accent hover:underline"
          >
            {tx("migrations.viewAllRuns")}
          </Link>
        </div>
      </div>
      <div className="mt-3">
        {runs === null ? (
          <p className="text-xs text-text-muted">{tx("common.loading")}</p>
        ) : runs.length === 0 ? (
          <p className="text-xs text-text-muted">{emptyHint}</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {runs.map((r) => (
              <li
                key={r.id}
                className="flex items-center gap-3 py-2 text-sm"
              >
                <StatusBadge status={r.status} />
                {/* Phase ABY (2026-06-01) — trigger source icon-only
                    chip. Same vocabulary as the runs-list (ABG) but
                    icon-only since detail rows are narrower. */}
                {r.schedule_id ? (
                  <CalendarClockIcon
                    size={12}
                    className="text-accent"
                    aria-label={tx("migrations.runTriggerSchedule")}
                  >
                    <title>{tx("migrations.runTriggerSchedule")}</title>
                  </CalendarClockIcon>
                ) : r.triggered_by_user_id ? (
                  <HandIcon
                    size={12}
                    className="text-text-muted"
                    aria-label={tx("migrations.runTriggerManual")}
                  >
                    <title>{tx("migrations.runTriggerManual")}</title>
                  </HandIcon>
                ) : (
                  <span className="w-3" aria-hidden />
                )}
                <Link
                  href={`/w/${slug}/runs/${r.id}`}
                  className="flex-1 truncate font-mono text-xs text-text-secondary hover:text-accent"
                >
                  {r.id.slice(0, 8)}
                </Link>
                <span className="text-xs tabular-nums text-text-muted">
                  {r.records_written.toLocaleString()} {tx("migrations.runRowsWritten")}
                </span>
                <span className="text-xs tabular-nums text-text-muted">
                  {formatDuration(r.duration_seconds)}
                </span>
                <span
                  className="w-20 text-right text-xs text-text-muted"
                  title={absoluteTime(
                    r.finished_at ?? r.started_at ?? r.created_at,
                  )}
                >
                  {relativeTime(
                    r.finished_at ?? r.started_at ?? r.created_at,
                    tx,
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

function ScheduleCard({
  loaded,
  schedule,
  draft,
  onDraft,
  saving,
  onSave,
  onToggle,
  onClear,
  t,
}: {
  loaded: boolean;
  schedule: ScheduleSummary | null;
  draft: string;
  onDraft: (v: string) => void;
  saving: boolean;
  onSave: () => void;
  onToggle: () => void;
  onClear: () => void;
  t: (k: never) => string;
}) {
  const tx = t as unknown as (k: string) => string;
  return (
    <Card>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <CalendarClockIcon
            size={16}
            className={
              schedule?.is_active
                ? "text-accent"
                : schedule
                  ? "text-warning"
                  : "text-text-muted"
            }
          />
          <div className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
            {tx("migrations.schedule")}
          </div>
          {schedule ? (
            <span
              className={`inline-flex h-4 items-center rounded-sm px-1 text-[10px] font-semibold uppercase ${
                schedule.is_active
                  ? "bg-accent/15 text-accent"
                  : "bg-warning/15 text-warning"
              }`}
            >
              {schedule.is_active
                ? tx("migrations.scheduleActive")
                : tx("migrations.schedulePausedChip")}
            </span>
          ) : null}
        </div>
        {schedule ? (
          <div className="flex gap-1">
            <Button
              variant="ghost"
              size="sm"
              onClick={onToggle}
              loading={saving}
            >
              {schedule.is_active
                ? tx("migrations.schedulePause")
                : tx("migrations.scheduleResume")}
            </Button>
            <Button variant="ghost" size="sm" onClick={onClear} loading={saving}>
              {tx("migrations.scheduleClear")}
            </Button>
          </div>
        ) : null}
      </div>
      <div className="mt-3 flex flex-col gap-2">
        <span className="text-xs text-text-secondary">
          {tx("migrations.scheduleCronLabel")}
        </span>
        {/* CronInput already ships preset chips + cronstrue
            description + next-firing preview (Step 10.4 / cron
            builder). Re-using it keeps the migration surface
            consistent with the global Schedules page UX. */}
        <CronInput value={draft} onChange={onDraft} disabled={!loaded || saving} />
        <div className="flex justify-end">
          <Button
            onClick={onSave}
            loading={saving}
            disabled={!loaded || !draft.trim()}
          >
            {schedule
              ? tx("migrations.scheduleUpdate")
              : tx("migrations.scheduleEnable")}
          </Button>
        </div>
      </div>
    </Card>
  );
}

/** Phase ABX (2026-06-01) — inline dry-run result card. Persists
 *  the latest connector check statuses after the toast disappears
 *  so operators can dwell on the details. */
function DryRunResultCard({
  result,
  onDismiss,
  t,
}: {
  result: import('@/lib/api').DryRunResponse;
  onDismiss: () => void;
  t: (k: never) => string;
}) {
  const tx = t as unknown as (k: string) => string;
  const okCount = result.connectors.filter((c) => c.ok).length;
  return (
    <Card>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div
            className={`text-xs font-semibold uppercase tracking-wider ${
              result.ok ? 'text-success' : 'text-error'
            }`}
          >
            {result.ok
              ? tx('migrations.dryRunResultOk')
              : tx('migrations.dryRunResultFail')}
          </div>
          <div className="mt-0.5 text-xs text-text-muted">
            {tx('migrations.dryRunResultSummary')
              .replace('{n}', String(okCount))
              .replace('{total}', String(result.connectors.length))}
          </div>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="text-xs text-text-muted hover:text-text"
          aria-label={tx('common.dismiss')}
        >
          ×
        </button>
      </div>
      {result.errors.length > 0 ? (
        <div className="mt-3 space-y-1">
          {result.errors.map((e, i) => (
            <pre
              key={i}
              className="overflow-auto whitespace-pre-wrap break-words rounded-sm border border-error/30 bg-error/5 p-2 font-mono text-[11px] text-error"
            >
              {e}
            </pre>
          ))}
        </div>
      ) : null}
      {result.connectors.length > 0 ? (
        <ul className="mt-3 divide-y divide-border-subtle">
          {result.connectors.map((c) => (
            <li
              key={c.name}
              className="flex items-center justify-between gap-3 py-2 text-xs"
            >
              <div className="flex items-center gap-2">
                <span
                  className={`h-2 w-2 rounded-full ${
                    c.ok ? 'bg-success' : 'bg-error'
                  }`}
                  aria-hidden
                />
                <span className="font-medium text-text">{c.name}</span>
                <span className="font-mono text-text-muted">{c.type}</span>
              </div>
              {c.error ? (
                <span className="truncate text-right font-mono text-text-secondary" title={c.error}>
                  {c.error}
                </span>
              ) : (
                <span className="text-text-muted">ok</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
