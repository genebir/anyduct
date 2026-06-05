"use client";

/**
 * /w/[slug]/migrations — Phase AAN (2026-05-29) + AAN2 (dedicated form).
 *
 * Dedicated surface for cross-DB migration pipelines. A pipeline
 * counts as a migration here iff at least one of its sinks has
 * ``auto_create_table: true`` (ADR-0066 / 0071 / 0072 — the runtime
 * is on the hook for creating the destination table from the source
 * schema).
 *
 * Migrations don't open in the graph builder — their create / edit
 * lives at ``/migrations/new`` and ``/migrations/[id]`` (Phase AAN2)
 * so the surface stays focused: one source, one sink, four switches.
 * Pipelines builder is reserved for richer shapes (transforms, joins,
 * fan-out, etc.).
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import {
  ArrowRightLeftIcon,
  CalendarClockIcon,
  EditIcon,
  HandIcon,
  PlayIcon,
  PlusIcon,
  ShieldCheckIcon,
  Trash2Icon,
  ZapIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DataTable, type Column } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CronInput } from "@/components/schedules/cron-input";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  ApiError,
  connectionsApi,
  pipelinesApi,
  runsApi,
  schedulesApi,
  type PipelineSummary,
  type RunSummary,
  type ScheduleSummary,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";
import { relativeTime, absoluteTime } from "@/lib/format-time";
import { cronHuman } from "@/lib/cron";
import {
  type MigrationSummary,
  migrationSummaryOf,
} from "@/lib/migration-utils";

type Translate = (
  key: keyof Messages,
  vars?: Record<string, string | number>,
) => string;

type Row = PipelineSummary & {
  migration: MigrationSummary;
  lastRun: RunSummary | null;
  schedule: ScheduleSummary | null;
};

const RUNS_POLL_MS = 5_000;

function strategyChip(
  s: MigrationSummary["strategy"],
  t: Translate,
): { label: string; cls: string; title: string } {
  // Phase ABO (2026-06-01) — tooltip per strategy so the chip
  // doesn't require operator memory ("does mirror keep or wipe
  // existing rows?"). Wording matches the migration form's help
  // text so the vocabulary stays consistent across surfaces.
  if (s === "snapshot") {
    return {
      label: t("migrations.strategySnapshot"),
      cls: "bg-warning/15 text-warning",
      title: t("migrations.strategySnapshotTitle"),
    };
  }
  if (s === "append") {
    return {
      label: t("migrations.strategyAppend"),
      cls: "bg-info/15 text-info",
      title: t("migrations.strategyAppendTitle"),
    };
  }
  if (s === "mirror") {
    return {
      label: t("migrations.strategyMirror"),
      cls: "bg-accent/15 text-accent",
      title: t("migrations.strategyMirrorTitle"),
    };
  }
  return {
    label: "custom",
    cls: "bg-overlay text-text-muted",
    title: t("migrations.strategyCustomTitle"),
  };
}

function buildColumns(
  t: Translate,
  connNames: Set<string> | null,
): Column<Row>[] {
  // Phase ADD (2026-06-04) — a connection name that no longer exists in
  // the workspace (deleted/renamed) means this migration's next run
  // fails to build. Red-flag it like the pipelines list (ADC).
  // connNames null = not loaded → treat everything as present.
  const isMissing = (name: string | null): boolean =>
    !!name && connNames !== null && !connNames.has(name);
  return [
    {
      key: "name",
      header: t("common.pipeline"),
      cell: (r) => (
        <div>
          <div className="font-medium text-text">{r.name}</div>
          {r.description ? (
            <div className="text-xs text-text-muted">{r.description}</div>
          ) : null}
        </div>
      ),
    },
    {
      // Phase AAN3 — direction column reads as "src → dst" so the
      // migration intent is the first thing the operator sees.
      key: "direction",
      header: `${t("migrations.from")} → ${t("migrations.to")}`,
      cell: (r) => {
        const srcMissing = isMissing(r.migration.sourceConnection);
        const dstMissing = isMissing(r.migration.sinkConnection);
        return (
          <div className="flex items-center gap-1.5 text-xs">
            <span
              className={`font-mono ${srcMissing ? "text-error" : "text-text-secondary"}`}
              title={srcMissing ? t("pipelines.missingConnection", {
                names: r.migration.sourceConnection ?? "",
              }) : undefined}
            >
              {r.migration.sourceConnection ?? "—"}
              {srcMissing ? " ⚠" : ""}
            </span>
            <span className="text-accent">→</span>
            <span
              className={`font-mono ${dstMissing ? "text-error" : "text-text-secondary"}`}
              title={dstMissing ? t("pipelines.missingConnection", {
                names: r.migration.sinkConnection ?? "",
              }) : undefined}
            >
              {r.migration.sinkConnection ?? "—"}
              {dstMissing ? " ⚠" : ""}
              {r.migration.sinkTable ? ` / ${r.migration.sinkTable}` : ""}
            </span>
          </div>
        );
      },
    },
    {
      key: "strategy",
      header: t("migrations.colStrategy"),
      cell: (r) => {
        const { label, cls, title } = strategyChip(r.migration.strategy, t);
        return (
          <span
            className={`inline-flex h-5 items-center whitespace-nowrap rounded-sm px-1.5 text-[11px] font-medium ${cls}`}
            title={title}
          >
            {label}
          </span>
        );
      },
    },
    {
      // Phase AAP — health at a glance. "Last run" surfaces both the
      // status (badge color) and how long ago, so the operator can
      // skim the list and spot a stale or failed migration without
      // opening it.
      key: "last_run",
      header: t("migrations.colLastRun"),
      cell: (r) =>
        r.lastRun ? (
          <div className="flex items-center gap-2 whitespace-nowrap text-xs">
            <StatusBadge status={r.lastRun.status} />
            {/* Phase ACK (2026-06-04) — trigger source icon-only chip,
                same vocabulary as the runs list (ABG) and migration
                detail recent-runs (ABY). Lets the data engineer skim
                the list and see whether each migration's last run was
                cron-driven or hand-triggered without opening it. */}
            {r.lastRun.schedule_id ? (
              <CalendarClockIcon
                size={12}
                className="shrink-0 text-accent"
                aria-label={t("migrations.runTriggerSchedule")}
              >
                <title>{t("migrations.runTriggerSchedule")}</title>
              </CalendarClockIcon>
            ) : r.lastRun.triggered_by_user_id ? (
              <HandIcon
                size={12}
                className="shrink-0 text-text-muted"
                aria-label={t("migrations.runTriggerManual")}
              >
                <title>{t("migrations.runTriggerManual")}</title>
              </HandIcon>
            ) : (
              <ZapIcon
                size={12}
                className="shrink-0 text-text-muted"
                aria-label={t("migrations.runTriggerAuto")}
              >
                <title>{t("migrations.runTriggerAuto")}</title>
              </ZapIcon>
            )}
            <span
              className="text-text-muted"
              title={absoluteTime(
                r.lastRun.finished_at ??
                  r.lastRun.started_at ??
                  r.lastRun.created_at,
              )}
            >
              {relativeTime(
                r.lastRun.finished_at ??
                  r.lastRun.started_at ??
                  r.lastRun.created_at,
                t,
              )}
            </span>
            {/* Phase AEX (2026-06-04) — failure type at a glance, parallel
                to runs/pipelines lists (AEV/AEW). Only set on failures. */}
            {r.lastRun.error_class ? (
              <span
                className="max-w-[7rem] truncate font-mono text-[10px] text-error"
                title={r.lastRun.error_class}
              >
                {r.lastRun.error_class}
              </span>
            ) : null}
          </div>
        ) : (
          <span className="text-xs text-text-muted">
            {t("migrations.neverRun")}
          </span>
        ),
    },
    {
      // Phase AAZ (2026-06-01) — schedule indicator. Surfaces
      // whether each migration is automated or manual-only without
      // making the operator click into the detail page.
      key: "schedule",
      className: "w-32",
      header: t("migrations.colSchedule"),
      cell: (r) => {
        if (!r.schedule) {
          return (
            <span className="text-xs text-text-muted">
              {t("migrations.scheduleNone")}
            </span>
          );
        }
        const active = r.schedule.is_active;
        return (
          <span
            className={`inline-flex h-5 items-center gap-1 rounded-sm px-1.5 text-[11px] ${
              active
                ? "bg-accent/15 text-accent"
                : "bg-warning/15 text-warning"
            }`}
            title={
              // Phase ADG (2026-06-04) — human-readable cron + state,
              // e.g. "At 02:00 AM (paused)".
              r.schedule.cron_expr
                ? `${cronHuman(r.schedule.cron_expr) ?? r.schedule.cron_expr}${
                    active ? "" : " (paused)"
                  }`
                : undefined
            }
          >
            <CalendarClockIcon size={12} />
            <span className="font-mono">{r.schedule.cron_expr ?? "—"}</span>
          </span>
        );
      },
    },
  ];
}

export default function MigrationsPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [rows, setRows] = useState<PipelineSummary[] | null>(null);
  /** Most recent run per pipeline_id. Refreshed every ``RUNS_POLL_MS``
   *  so a Trigger from elsewhere lands on this list without a page
   *  reload. */
  const [lastRunByPipeline, setLastRunByPipeline] = useState<
    Map<string, RunSummary>
  >(new Map());
  /** Phase ADD (2026-06-04) — existing connection names, to red-flag
   *  migrations referencing a deleted/renamed one. null until loaded. */
  const [connNames, setConnNames] = useState<Set<string> | null>(null);
  /** Phase AAZ (2026-06-01) — schedule status per pipeline. We
   *  fetch schedules per migration in parallel (N+1 over the list,
   *  acceptable for the typical <100 migrations workspace) so the
   *  list shows a ⏰ chip indicating active / paused / no schedule
   *  without forcing the operator into the detail page. */
  const [scheduleByPipeline, setScheduleByPipeline] = useState<
    Map<string, ScheduleSummary | null>
  >(new Map());
  /** Phase AAR follow-up (2026-06-01) — user request "마이그레이션
   *  목록에서 실행을 할 수가 없네". The pipeline_id whose Run button is
   *  currently pending so we can paint a spinner without locking the
   *  whole table. */
  const [triggeringId, setTriggeringId] = useState<string | null>(null);
  /** Phase AAT (2026-06-01) — search + filter so the list stays
   *  navigable after schema-mode mass-creation. Pure client-side over
   *  ``migrationRows`` so polling + Run-now stay reactive. */
  const [search, setSearch] = useState("");
  const [filterFrom, setFilterFrom] = useState("");
  const [filterTo, setFilterTo] = useState("");
  const [filterStrategy, setFilterStrategy] = useState("");
  /** Phase ABA (2026-06-01) — schedule status filter, reusing the
   *  AAZ ``scheduleByPipeline`` data so no extra fetch is needed. */
  const [filterSchedule, setFilterSchedule] = useState<
    "" | "active" | "paused" | "none"
  >("");
  /** Phase ABI (2026-06-01) — "Last run" axis filter. Persona
   *  dogfood found 62/70 migrations un-run after a bulk schema-mode
   *  create — operators need a one-click way to surface "needs
   *  first run" + "fix last failure" subsets without scrolling.
   *  Phase ABM (2026-06-01) — URL-syncable via ``?lastRun=`` so the
   *  post-bulk-create navigation can preset it ("you just created
   *  N — here they are, ready to Run"). */
  const searchParams = useSearchParams();
  const initialLastRun = (() => {
    const v = searchParams.get("lastRun");
    return v === "never" || v === "failed" || v === "ok" ? v : "";
  })();
  const [filterLastRun, setFilterLastRun] = useState<
    "" | "never" | "failed" | "ok"
  >(initialLastRun);
  /** Phase AAW (2026-06-01) — multi-select + bulk delete for the
   *  schema-mode case where users mass-create 20+ migrations and
   *  need to clean up some of them. */
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [confirmBulkDelete, setConfirmBulkDelete] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);

  function toggleSelection(id: string): void {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  /** Phase AAX (2026-06-01) — bulk Run now. After schema-mode mass
   *  creation the operator often wants to validate everything at
   *  once. We trigger sequentially so per-row failures surface
   *  with the offending migration's name instead of vanishing into
   *  a Promise.all rejection. */
  const [bulkRunning, setBulkRunning] = useState(false);
  /** Phase AAY (2026-06-01) — bulk schedule. Selected migrations
   *  share a cron; we POST one schedule per pipeline so each row
   *  ends up with its own (auto)-named schedule visible on the
   *  global Schedules page. */
  const [confirmBulkSchedule, setConfirmBulkSchedule] = useState(false);
  const [bulkScheduleCron, setBulkScheduleCron] = useState("0 3 * * *");
  const [bulkScheduling, setBulkScheduling] = useState(false);

  async function onBulkSchedule() {
    if (!ws || selectedIds.size === 0) return;
    const expr = bulkScheduleCron.trim();
    if (!expr) {
      toast.error(t("migrations.scheduleCronRequired"));
      return;
    }
    setBulkScheduling(true);
    let ok = 0;
    let fail = 0;
    const ids = [...selectedIds];
    for (const id of ids) {
      const row = migrationRows.find((r) => r.id === id);
      try {
        // Update first if a schedule already exists (idempotent UX —
        // operators expect bulk schedule to just apply the cron).
        const existing = await schedulesApi.list(ws.id, id);
        if (existing.length > 0) {
          await schedulesApi.update(ws.id, id, existing[0].id, {
            cron_expr: expr,
            is_active: true,
          });
        } else {
          await schedulesApi.create(ws.id, id, {
            name: `${row?.name ?? id.slice(0, 8)} (auto)`,
            mode: "batch",
            cron_expr: expr,
            is_active: true,
          });
        }
        ok += 1;
      } catch (err) {
        fail += 1;
        const m = err instanceof ApiError ? err.message : String(err);
        toast.error(`${row?.name ?? id}: ${m}`);
      }
    }
    setBulkScheduling(false);
    setConfirmBulkSchedule(false);
    if (fail === 0) {
      toast.success(t("migrations.bulkScheduled", { n: ok }));
    } else {
      toast.warning(t("migrations.bulkPartial", { ok, fail }));
    }
  }

  /** Phase ABQ (2026-06-01) — bulk Dry run. ABP gave operators a
   *  per-migration pre-flight; this scales it to a selection so a
   *  schema-mode batch (often 50+ rows) can be validated in one go
   *  before committing to bulk Run now. Sequential to keep order in
   *  the failure toasts (a Promise.all would scramble them). */
  const [bulkDryRunning, setBulkDryRunning] = useState(false);
  async function onBulkDryRun() {
    if (!ws || selectedIds.size === 0) return;
    setBulkDryRunning(true);
    let ok = 0;
    let fail = 0;
    let skipped = 0;
    const ids = [...selectedIds];
    for (const id of ids) {
      const row = migrationRows.find((r) => r.id === id);
      if (!row) {
        skipped += 1;
        continue;
      }
      if (!row.current_version) {
        skipped += 1;
        continue;
      }
      try {
        const r = await pipelinesApi.dryRun(ws.id, id);
        if (r.ok) {
          ok += 1;
        } else {
          fail += 1;
          const firstErr =
            r.errors[0] ??
            r.connectors.find((c) => !c.ok)?.error ??
            "unknown";
          toast.error(`${row.name}: ${firstErr}`);
        }
      } catch (err) {
        fail += 1;
        const m = err instanceof ApiError ? err.message : String(err);
        toast.error(`${row.name}: ${m}`);
      }
    }
    setBulkDryRunning(false);
    if (fail === 0 && skipped === 0) {
      toast.success(t("migrations.bulkDryRunOk", { n: ok }));
    } else if (ok > 0) {
      toast.warning(
        t("migrations.bulkDryRunPartial", { ok, fail: fail + skipped }),
      );
    } else {
      toast.error(t("migrations.bulkDryRunAllFailed", { n: fail }));
    }
  }

  async function onBulkRunNow() {
    if (!ws || selectedIds.size === 0) return;
    setBulkRunning(true);
    let ok = 0;
    let fail = 0;
    let skipped = 0;
    const ids = [...selectedIds];
    for (const id of ids) {
      const row = migrationRows.find((r) => r.id === id);
      if (!row) {
        skipped += 1;
        continue;
      }
      if (!row.current_version) {
        skipped += 1;
        toast.error(`${row.name}: ${t("migrations.saveBeforeRun")}`);
        continue;
      }
      try {
        const r = await pipelinesApi.trigger(ws.id, id);
        // Optimistic: paint the new run on this row so the list
        // updates without waiting for the next poll tick.
        setLastRunByPipeline((prev) => {
          const next = new Map(prev);
          next.set(id, r);
          return next;
        });
        ok += 1;
      } catch (err) {
        fail += 1;
        const m = err instanceof ApiError ? err.message : String(err);
        toast.error(`${row.name}: ${m}`);
      }
    }
    setBulkRunning(false);
    if (fail === 0 && skipped === 0) {
      toast.success(t("migrations.bulkRunQueued", { n: ok }));
    } else {
      toast.warning(
        t("migrations.bulkRunPartial", { ok, fail: fail + skipped }),
      );
    }
  }

  async function onBulkDelete() {
    if (!ws || selectedIds.size === 0) return;
    setBulkDeleting(true);
    let ok = 0;
    let fail = 0;
    const ids = [...selectedIds];
    for (const id of ids) {
      try {
        await pipelinesApi.delete(ws.id, id);
        ok += 1;
      } catch (err) {
        fail += 1;
        const m = err instanceof ApiError ? err.message : String(err);
        const name =
          migrationRows.find((r) => r.id === id)?.name ?? id.slice(0, 8);
        toast.error(`${name}: ${m}`);
      }
    }
    setBulkDeleting(false);
    setConfirmBulkDelete(false);
    setSelectedIds(new Set());
    // Refresh after bulk delete.
    try {
      const list = await pipelinesApi.list(ws.id);
      setRows(list);
    } catch {
      // Polling will catch up.
    }
    if (fail === 0) {
      toast.success(t("migrations.bulkDeleted", { n: ok }));
    } else {
      toast.warning(t("migrations.bulkPartial", { ok, fail }));
    }
  }

  async function onTrigger(row: PipelineSummary) {
    if (!ws) return;
    if (!row.current_version) {
      toast.error(t("migrations.saveBeforeRun"));
      return;
    }
    setTriggeringId(row.id);
    try {
      const r = await pipelinesApi.trigger(ws.id, row.id);
      toast.success(t("migrations.runQueued"));
      // Optimistic: paint the new run in the Last run column so the
      // operator sees feedback before the next poll tick.
      setLastRunByPipeline((prev) => {
        const next = new Map(prev);
        next.set(row.id, r);
        return next;
      });
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    } finally {
      setTriggeringId(null);
    }
  }

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    (async () => {
      try {
        const list = await pipelinesApi.list(ws.id);
        if (!cancelled) setRows(list);
      } catch (err) {
        if (!cancelled) {
          toast.error(
            err instanceof ApiError ? err.message : t("pipelines.loadFailed"),
          );
          setRows([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws, t]);

  // Phase ADD (2026-06-04) — connection names to red-flag migrations
  // that reference a missing connection. Soft-fail → null.
  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    (async () => {
      try {
        const list = await connectionsApi.list(ws.id);
        if (!cancelled) setConnNames(new Set(list.map((c) => c.name)));
      } catch {
        if (!cancelled) setConnNames(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws]);

  // Recent runs across the whole workspace, indexed by pipeline_id.
  // Phase AAP: gives every migration row a "last status + when" chip
  // so the operator sees health at a glance without clicking in. We
  // fetch a *workspace-wide* page of runs (single request, no N+1)
  // and let the dict picker pick the most recent per pipeline.
  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    const fetchRuns = async () => {
      try {
        const list = await runsApi.list(ws.id, { limit: 200 });
        if (cancelled) return;
        const m = new Map<string, RunSummary>();
        for (const r of list) {
          const prev = m.get(r.pipeline_id);
          // ``runsApi.list`` orders by created_at desc — the first one
          // we see per pipeline is already the most recent.
          if (!prev) m.set(r.pipeline_id, r);
        }
        setLastRunByPipeline(m);
      } catch {
        // Soft-fail. The list still renders without the status chip.
      }
    };
    void fetchRuns();
    const handle = setInterval(() => void fetchRuns(), RUNS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [ws]);

  // Phase AAZ (2026-06-01) — schedules per migration row. Poll
  // cadence matches runs so the post-bulk-schedule state lands in
  // sync. N+1 over the migration list; for a typical workspace
  // (<100 migrations) this is fine. Soft-fail per pipeline so a
  // single 5xx doesn't blank the column.
  useEffect(() => {
    if (!ws || !rows) return;
    let cancelled = false;
    const migrationIds = rows
      .filter((p) => migrationSummaryOf(p.current_config_json))
      .map((p) => p.id);
    if (migrationIds.length === 0) return;
    const fetchAll = async () => {
      const out = new Map<string, ScheduleSummary | null>();
      await Promise.all(
        migrationIds.map(async (pid) => {
          try {
            const list = await schedulesApi.list(ws.id, pid);
            out.set(pid, list[0] ?? null);
          } catch {
            out.set(pid, null);
          }
        }),
      );
      if (!cancelled) setScheduleByPipeline(out);
    };
    void fetchAll();
    const handle = setInterval(() => void fetchAll(), RUNS_POLL_MS * 2);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [ws, rows]);

  // Client-side filter — keeps the page a pure view of the pipelines
  // list. No server endpoint changes.
  const migrationRows = useMemo<Row[]>(() => {
    if (!rows) return [];
    const out: Row[] = [];
    for (const p of rows) {
      const migration = migrationSummaryOf(p.current_config_json);
      if (migration) {
        out.push({
          ...p,
          migration,
          lastRun: lastRunByPipeline.get(p.id) ?? null,
          schedule: scheduleByPipeline.get(p.id) ?? null,
        });
      }
    }
    return out;
  }, [rows, lastRunByPipeline, scheduleByPipeline]);

  // Phase AAT (2026-06-01) — fan-out of distinct values for the
  // filter dropdowns. Computed off ``migrationRows`` so the available
  // options follow the actual data (no orphaned options pointing at
  // connections that no migration uses).
  const distinctSources = useMemo(() => {
    const s = new Set<string>();
    for (const r of migrationRows) {
      if (r.migration.sourceConnection) s.add(r.migration.sourceConnection);
    }
    return [...s].sort();
  }, [migrationRows]);
  const distinctSinks = useMemo(() => {
    const s = new Set<string>();
    for (const r of migrationRows) {
      if (r.migration.sinkConnection) s.add(r.migration.sinkConnection);
    }
    return [...s].sort();
  }, [migrationRows]);

  const filteredRows = useMemo(() => {
    const term = search.trim().toLowerCase();
    return migrationRows.filter((r) => {
      if (
        term &&
        !r.name.toLowerCase().includes(term) &&
        !(r.description ?? "").toLowerCase().includes(term)
      )
        return false;
      if (filterFrom && r.migration.sourceConnection !== filterFrom)
        return false;
      if (filterTo && r.migration.sinkConnection !== filterTo) return false;
      if (filterStrategy && r.migration.strategy !== filterStrategy)
        return false;
      if (filterSchedule) {
        if (filterSchedule === "none" && r.schedule !== null) return false;
        if (filterSchedule === "active" && (!r.schedule || !r.schedule.is_active))
          return false;
        if (filterSchedule === "paused" && (!r.schedule || r.schedule.is_active))
          return false;
      }
      if (filterLastRun) {
        if (filterLastRun === "never" && r.lastRun !== null) return false;
        if (filterLastRun === "failed" && r.lastRun?.status !== "failed")
          return false;
        if (filterLastRun === "ok" && r.lastRun?.status !== "succeeded")
          return false;
      }
      return true;
    });
  }, [
    migrationRows,
    search,
    filterFrom,
    filterTo,
    filterStrategy,
    filterSchedule,
    filterLastRun,
  ]);

  const columns = buildColumns(t, connNames);
  // Phase AAW (2026-06-01) — checkbox column injected as the first
  // column. Header has a tri-state "select all visible" toggle.
  const allVisibleSelected =
    filteredRows.length > 0 &&
    filteredRows.every((r) => selectedIds.has(r.id));
  const selectColumn: Column<Row> = {
    key: "_select",
    className: "w-8",
    header: (
      <input
        type="checkbox"
        className="h-3.5 w-3.5 cursor-pointer accent-accent"
        checked={allVisibleSelected}
        aria-label={t("migrations.selectAllVisibleAria")}
        onChange={() => {
          if (allVisibleSelected) {
            // Deselect all currently visible.
            setSelectedIds((prev) => {
              const next = new Set(prev);
              for (const r of filteredRows) next.delete(r.id);
              return next;
            });
          } else {
            setSelectedIds((prev) => {
              const next = new Set(prev);
              for (const r of filteredRows) next.add(r.id);
              return next;
            });
          }
        }}
      />
    ),
    cell: (r) => (
      <input
        type="checkbox"
        className="h-3.5 w-3.5 cursor-pointer accent-accent"
        checked={selectedIds.has(r.id)}
        onChange={() => toggleSelection(r.id)}
        onClick={(e) => e.stopPropagation()}
        aria-label={t("migrations.selectRowAria", { name: r.name })}
      />
    ),
  };

  return (
    <>
      <Header
        title={t("migrations.title")}
        subtitle={
          ws ? t("common.workspaceSubtitle", { name: ws.name }) : undefined
        }
        actions={
          <Link href={`/w/${slug}/migrations/new`}>
            <Button size="sm" disabled={!ws}>
              <PlusIcon size={14} />
              {t("migrations.new")}
            </Button>
          </Link>
        }
      />
      <main className="mx-auto w-full max-w-5xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        <p className="text-sm text-text-muted">{t("migrations.desc")}</p>

        {/* Phase AAT (2026-06-01) — search + filter bar. Stays
            hidden until there are enough migrations to need it so a
            fresh workspace doesn't look noisy. */}
        {migrationRows.length > 5 ? (
          <div className="grid items-end gap-2 sm:grid-cols-[1fr_auto_auto_auto_auto_auto]">
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("migrations.searchPlaceholder")}
            />
            <select
              value={filterFrom}
              onChange={(e) => setFilterFrom(e.target.value)}
              className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
            >
              <option value="">{t("migrations.filterFromAll")}</option>
              {distinctSources.map((c) => (
                <option key={c} value={c}>
                  {t("migrations.from")}: {c}
                </option>
              ))}
            </select>
            <select
              value={filterTo}
              onChange={(e) => setFilterTo(e.target.value)}
              className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
            >
              <option value="">{t("migrations.filterToAll")}</option>
              {distinctSinks.map((c) => (
                <option key={c} value={c}>
                  {t("migrations.to")}: {c}
                </option>
              ))}
            </select>
            <select
              value={filterStrategy}
              onChange={(e) => setFilterStrategy(e.target.value)}
              className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
            >
              <option value="">{t("migrations.filterStrategyAll")}</option>
              <option value="snapshot">{t("migrations.strategySnapshot")}</option>
              <option value="append">{t("migrations.strategyAppend")}</option>
              <option value="mirror">{t("migrations.strategyMirror")}</option>
              <option value="custom">custom</option>
            </select>
            <select
              value={filterSchedule}
              onChange={(e) =>
                setFilterSchedule(
                  e.target.value as "" | "active" | "paused" | "none",
                )
              }
              className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
            >
              <option value="">{t("migrations.filterScheduleAll")}</option>
              <option value="active">{t("migrations.filterScheduleActive")}</option>
              <option value="paused">{t("migrations.filterSchedulePaused")}</option>
              <option value="none">{t("migrations.filterScheduleNone")}</option>
            </select>
            {/* Phase ABI (2026-06-01) — Last run axis: never / failed / ok. */}
            <select
              value={filterLastRun}
              onChange={(e) =>
                setFilterLastRun(
                  e.target.value as "" | "never" | "failed" | "ok",
                )
              }
              className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
            >
              <option value="">{t("migrations.filterLastRunAll")}</option>
              <option value="never">{t("migrations.filterLastRunNever")}</option>
              <option value="failed">{t("migrations.filterLastRunFailed")}</option>
              <option value="ok">{t("migrations.filterLastRunOk")}</option>
            </select>
            {search ||
            filterFrom ||
            filterTo ||
            filterStrategy ||
            filterSchedule ||
            filterLastRun ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch("");
                  setFilterFrom("");
                  setFilterTo("");
                  setFilterStrategy("");
                  setFilterSchedule("");
                  setFilterLastRun("");
                }}
              >
                {t("migrations.clearFilters")}
              </Button>
            ) : null}
          </div>
        ) : null}

        {/* Phase AAW (2026-06-01) — bulk actions bar surfaces only
            when at least one row is selected so it doesn't claim
            space in the default view. AAX added bulk Run now. */}
        {selectedIds.size > 0 ? (
          <div className="flex items-center justify-between gap-3 rounded-md border border-accent/40 bg-accent/5 px-3 py-2">
            <span className="text-xs text-text">
              {t("migrations.selectedCount", { n: selectedIds.size })}
            </span>
            <div className="flex gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSelectedIds(new Set())}
                disabled={bulkRunning || bulkDeleting || bulkDryRunning}
              >
                {t("migrations.clearSelection")}
              </Button>
              {/* Phase ABQ — Dry run leftmost ("check before commit"). */}
              <Button
                variant="ghost"
                size="sm"
                loading={bulkDryRunning}
                disabled={bulkRunning || bulkDeleting || bulkScheduling}
                onClick={() => void onBulkDryRun()}
                title={t("migrations.dryRunHint")}
              >
                <ShieldCheckIcon size={14} />
                {t("migrations.dryRunSelected")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                loading={bulkRunning}
                disabled={bulkDeleting || bulkScheduling || bulkDryRunning}
                onClick={() => void onBulkRunNow()}
              >
                <PlayIcon size={14} />
                {t("migrations.runSelected")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmBulkSchedule(true)}
                disabled={bulkRunning || bulkDeleting || bulkScheduling}
              >
                <CalendarClockIcon size={14} />
                {t("migrations.scheduleSelected")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmBulkDelete(true)}
                className="hover:text-error"
                disabled={bulkRunning || bulkScheduling}
              >
                <Trash2Icon size={14} />
                {t("migrations.deleteSelected")}
              </Button>
            </div>
          </div>
        ) : null}

        {rows === null ? null : migrationRows.length === 0 ? (
          <EmptyState
            icon={<ArrowRightLeftIcon size={28} />}
            title={t("migrations.title")}
            description={t("migrations.empty")}
            action={
              <Link href={`/w/${slug}/migrations/new`}>
                <Button disabled={!ws}>
                  <PlusIcon size={14} />
                  {t("migrations.new")}
                </Button>
              </Link>
            }
          />
        ) : filteredRows.length === 0 ? (
          <EmptyState
            icon={<ArrowRightLeftIcon size={28} />}
            title={t("migrations.filterNoMatch")}
            description={t("migrations.filterNoMatchDesc")}
            action={
              <Button
                variant="secondary"
                onClick={() => {
                  setSearch("");
                  setFilterFrom("");
                  setFilterTo("");
                  setFilterStrategy("");
                  setFilterSchedule("");
                }}
              >
                {t("migrations.clearFilters")}
              </Button>
            }
          />
        ) : (
          <DataTable
            columns={[
              selectColumn,
              ...columns,
              {
                key: "actions",
                header: "",
                className: "w-56 text-right",
                cell: (r) => (
                  <div className="flex justify-end gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={triggeringId === r.id}
                      disabled={!r.current_version || triggeringId !== null}
                      onClick={(e) => {
                        e.stopPropagation();
                        void onTrigger(r);
                      }}
                      title={
                        r.current_version
                          ? t("migrations.runNow")
                          : t("migrations.saveBeforeRun")
                      }
                    >
                      <PlayIcon size={14} />
                      {t("migrations.runNow")}
                    </Button>
                    <Link
                      href={`/w/${slug}/migrations/${r.id}`}
                      aria-label={t("common.edit")}
                    >
                      <Button size="sm" variant="secondary">
                        <EditIcon size={14} />
                        {t("common.edit")}
                      </Button>
                    </Link>
                  </div>
                ),
              },
            ]}
            rows={filteredRows}
          />
        )}
      </main>
      <ConfirmDialog
        open={confirmBulkDelete}
        title={t("migrations.bulkDeleteTitle")}
        description={t("migrations.bulkDeleteConfirm", { n: selectedIds.size })}
        confirmLabel={t("common.delete")}
        destructive
        loading={bulkDeleting}
        onConfirm={() => void onBulkDelete()}
        onCancel={() => setConfirmBulkDelete(false)}
      />
      <ConfirmDialog
        open={confirmBulkSchedule}
        title={t("migrations.bulkScheduleTitle")}
        description={t("migrations.bulkScheduleConfirm", { n: selectedIds.size })}
        body={
          <CronInput
            value={bulkScheduleCron}
            onChange={setBulkScheduleCron}
            disabled={bulkScheduling}
          />
        }
        confirmLabel={t("migrations.scheduleEnable")}
        loading={bulkScheduling}
        confirmDisabled={!bulkScheduleCron.trim()}
        onConfirm={() => void onBulkSchedule()}
        onCancel={() => setConfirmBulkSchedule(false)}
      />
    </>
  );
}
