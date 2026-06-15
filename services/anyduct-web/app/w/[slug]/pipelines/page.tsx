"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import {
  ActivityIcon,
  AlertTriangleIcon,
  CalendarClockIcon,
  CalendarPlusIcon,
  EditIcon,
  HandIcon,
  PlayIcon,
  PlusIcon,
  ShieldCheckIcon,
  Trash2Icon,
  WorkflowIcon,
  ZapIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DataTable, type Column } from "@/components/ui/data-table";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  ContextMenu,
  ContextMenuItem,
  ContextMenuSeparator,
  useContextMenu,
} from "@/components/ui/context-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { BackfillDialog } from "@/components/pipelines/backfill-dialog";
import { TriggerParamsDialog } from "@/components/pipelines/trigger-params-dialog";
import {
  ApiError,
  connectionsApi,
  pipelinesApi,
  runsApi,
  type PipelineSummary,
  type RunSummary,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import { relativeTime, absoluteTime } from "@/lib/format-time";
import { extractConnectionNames } from "@/lib/connection-usage";
import type { Messages } from "@/lib/i18n/messages";
import {
  DEFAULT_DLQ,
  DEFAULT_RETRY,
  serializeGraph,
} from "@/lib/pipeline-config";
import { migrationSummaryOf } from "@/lib/migration-utils";
import { cn } from "@/lib/cn";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

const RUNS_POLL_MS = 5_000;

function buildColumns(
  t: Translate,
  lastRunByPipeline: Map<string, RunSummary>,
  connNames: Set<string> | null,
): Column<PipelineSummary>[] {
  return [
    {
      key: "name",
      header: t("common.pipeline"),
      cell: (r) => {
        // Phase ADC (2026-06-04) — flag pipelines that reference a
        // connection no longer in the workspace (deleted/renamed). The
        // next run would fail to build; surfacing it here catches the
        // breakage before the operator triggers it. connNames null =
        // not loaded → no warning (avoids false positives).
        const missing = connNames
          ? [...extractConnectionNames(r.current_config_json)].filter(
              (n) => !connNames.has(n),
            )
          : [];
        return (
          <div>
            <div className="font-medium text-text">{r.name}</div>
            {r.description ? (
              <div className="text-xs text-text-muted">{r.description}</div>
            ) : null}
            {missing.length > 0 ? (
              <div
                className="mt-0.5 inline-flex items-center gap-1 rounded-sm bg-error/10 px-1.5 py-0.5 text-[11px] text-error"
                title={missing.join(", ")}
              >
                <AlertTriangleIcon size={11} />
                {t("pipelines.missingConnection", { names: missing.join(", ") })}
              </div>
            ) : null}
          </div>
        );
      },
    },
    {
      key: "mode",
      header: t("common.mode"),
      cell: (r) => {
        const cfg = r.current_config_json as { mode?: string } | null;
        const stream = cfg?.mode === "stream";
        return (
          <span className="inline-flex items-center gap-1">
            <span
              className={cn(
                // whitespace-nowrap: without it a narrow cell wraps the CJK
                // label one character per line (user report 2026-06-12).
                "inline-flex items-center gap-1 whitespace-nowrap rounded-sm border px-1.5 py-0.5 text-[11px] font-medium",
                stream
                  ? "border-info/40 bg-info/10 text-info"
                  : "border-border-subtle bg-overlay text-text-secondary",
              )}
            >
              <span
                aria-hidden
                className={cn("h-1.5 w-1.5 rounded-full", stream ? "bg-info" : "bg-text-muted")}
              />
              {stream ? t("pipelines.modeStream") : t("pipelines.modeBatch")}
            </span>
          </span>
        );
      },
    },
    {
      key: "version",
      header: t("common.version"),
      cell: (r) =>
        r.current_version ? (
          <span className="font-mono text-xs text-text-secondary">
            v{r.current_version}
          </span>
        ) : (
          <span className="text-text-muted">—</span>
        ),
    },
    {
      // Phase ACS (2026-06-04) — last-run health, mirroring the
      // migrations list (AAP) + its trigger icon (ACK). A regular
      // pipeline operator gets the same at-a-glance "did it last
      // succeed, when, and was it cron or manual?" without opening the
      // runs page. Data comes from one workspace-wide runs fetch.
      key: "last_run",
      header: t("pipelines.colLastRun"),
      cell: (r) => {
        const run = lastRunByPipeline.get(r.id);
        if (!run) {
          return (
            <span className="text-xs text-text-muted">
              {t("pipelines.neverRun")}
            </span>
          );
        }
        const when = run.finished_at ?? run.started_at ?? run.created_at;
        return (
          <div className="flex items-center gap-2 whitespace-nowrap text-xs">
            <StatusBadge status={run.status} />
            {run.schedule_id ? (
              <CalendarClockIcon
                size={12}
                className="shrink-0 text-accent"
                aria-label={t("migrations.runTriggerSchedule")}
              >
                <title>{t("migrations.runTriggerSchedule")}</title>
              </CalendarClockIcon>
            ) : run.triggered_by_user_id ? (
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
            <span className="text-text-muted" title={absoluteTime(when)}>
              {relativeTime(when, t)}
            </span>
            {/* Phase AEW (2026-06-04) — failure type at a glance, parallel
                to the runs list (AEV). error_class is only set on failures. */}
            {run.error_class ? (
              <span
                className="max-w-[7rem] truncate font-mono text-[10px] text-error"
                title={run.error_class}
              >
                {run.error_class}
              </span>
            ) : null}
          </div>
        );
      },
    },
  ];
}

export default function PipelinesPage() {
  const router = useRouter();
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [rows, setRows] = useState<PipelineSummary[] | null>(null);
  /** Phase ACS (2026-06-04) — most recent run per pipeline_id, from one
   *  workspace-wide runs fetch (polled) so a Trigger elsewhere lands on
   *  this list without a reload. Mirrors the migrations list. */
  const [lastRunByPipeline, setLastRunByPipeline] = useState<
    Map<string, RunSummary>
  >(new Map());
  /** Phase ADC (2026-06-04) — set of existing connection names, to flag
   *  pipelines that reference a deleted/renamed one. null until loaded
   *  so we don't warn on missing data. */
  const [connNames, setConnNames] = useState<Set<string> | null>(null);
  // Phase AAR (2026-06-01) — user request "마이그레이션 job을
  // 파이프라인이 아니라 마이그레이션 탭에서 관리하도록 해주고".
  // Migration pipelines are surfaced on /migrations; hide them
  // from the generic Pipelines list so the two surfaces don't
  // overlap. ``migrationSummaryOf`` returns non-null exactly when
  // the pipeline's sink has ``auto_create_table=true``.
  const visibleRows = useMemo(() => {
    if (rows === null) return null;
    return rows.filter((p) => migrationSummaryOf(p.current_config_json) === null);
  }, [rows]);
  /** Phase ABD (2026-06-01) — name/description search, same UX as
   *  migrations/connections/sensors. */
  const [search, setSearch] = useState("");
  /** Phase ADA (2026-06-04) — Last run axis filter, mirroring the
   *  migrations list (ABI) now that pipelines carry last-run data
   *  (ACS). Lets the operator triage "never run" / "failed" subsets. */
  const [lastRunFilter, setLastRunFilter] = useState<
    "" | "never" | "failed" | "ok"
  >("");
  /** Phase ADT (2026-06-04) — "broken" filter (references a missing
   *  connection), URL-presettable via ``?broken=1`` for the dashboard
   *  ADS deep-link. */
  const searchParams = useSearchParams();
  const [brokenFilter, setBrokenFilter] = useState(
    searchParams.get("broken") === "1",
  );
  const filteredRows = useMemo(() => {
    if (visibleRows === null) return null;
    const term = search.trim().toLowerCase();
    return visibleRows.filter((p) => {
      if (
        term &&
        !p.name.toLowerCase().includes(term) &&
        !(p.description ?? "").toLowerCase().includes(term)
      )
        return false;
      if (lastRunFilter) {
        const run = lastRunByPipeline.get(p.id) ?? null;
        if (lastRunFilter === "never" && run !== null) return false;
        if (lastRunFilter === "failed" && run?.status !== "failed") return false;
        if (lastRunFilter === "ok" && run?.status !== "succeeded") return false;
      }
      if (brokenFilter) {
        // connNames null = not loaded → don't hide anything yet.
        if (connNames === null) return false;
        const refs = extractConnectionNames(p.current_config_json);
        if (![...refs].some((r) => !connNames.has(r))) return false;
      }
      return true;
    });
  }, [
    visibleRows,
    search,
    lastRunFilter,
    lastRunByPipeline,
    brokenFilter,
    connNames,
  ]);
  const [triggering, setTriggering] = useState<string | null>(null);
  // 자유도 1단계: pipeline whose params dialog is open (null = closed).
  const [paramsFor, setParamsFor] = useState<PipelineSummary | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<PipelineSummary | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);
  const [backfillRow, setBackfillRow] = useState<PipelineSummary | null>(null);
  // Bulk multi-select (2026-06-12, user request) — mirrors the
  // migrations surface: Clear → Dry run → Trigger → Delete.
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkDryRunning, setBulkDryRunning] = useState(false);
  const [bulkTriggering, setBulkTriggering] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [confirmBulkDelete, setConfirmBulkDelete] = useState(false);
  const toggleSelection = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const rowMenu = useContextMenu();
  const rowMenuTargetRef = useRef<PipelineSummary | null>(null);

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

  // Phase ADC (2026-06-04) — fetch connection names once to flag
  // pipelines referencing a missing connection. Soft-fail → null.
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

  // Phase ACS (2026-06-04) — poll workspace runs and keep the most
  // recent per pipeline. Soft-fail: the list still renders without the
  // status chip.
  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    const fetchRuns = async () => {
      try {
        const list = await runsApi.list(ws.id, { limit: 200 });
        if (cancelled) return;
        const m = new Map<string, RunSummary>();
        for (const r of list) {
          // runsApi.list orders by created_at desc — first seen per
          // pipeline is the most recent.
          if (!m.has(r.pipeline_id)) m.set(r.pipeline_id, r);
        }
        setLastRunByPipeline(m);
      } catch {
        // soft-fail
      }
    };
    void fetchRuns();
    const handle = setInterval(() => void fetchRuns(), RUNS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [ws]);

  async function onCreate() {
    if (!ws || !newName.trim()) return;
    setSubmitting(true);
    try {
      // Phase AAS follow-up 3 (2026-06-01) — user request "그냥 템플릿
      // 기능은 지워줘. 필요없을 것 같아". Create-from-template was
      // removed; a fresh pipeline is just an empty graph + the user's
      // chosen name + batch mode. The editor's empty-canvas overlay
      // (Phase L1) walks them through dragging the first source.
      const config = serializeGraph(
        { nodes: [], edges: [] },
        {
          name: newName.trim(),
          mode: "batch",
          retry: { ...DEFAULT_RETRY },
          dlq: { ...DEFAULT_DLQ },
        },
      );
      const created = await pipelinesApi.create(ws.id, {
        name: newName.trim(),
        config,
      });
      toast.success(t("pipelines.created", { name: created.name }));
      router.push(`/w/${ws.slug}/pipelines/${created.id}/edit`);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("pipelines.createFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function onConfirmDelete() {
    if (!ws || !pendingDelete) return;
    setDeleting(true);
    try {
      await pipelinesApi.delete(ws.id, pendingDelete.id);
      toast.success(t("pipelines.deleted", { name: pendingDelete.name }));
      setPendingDelete(null);
      const list = await pipelinesApi.list(ws.id);
      setRows(list);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("pipelines.deleteFailed"),
      );
    } finally {
      setDeleting(false);
    }
  }

  async function onTrigger(row: PipelineSummary) {
    if (!ws) return;
    // 자유도 1단계: if the pipeline declares params, let the operator
    // override them before the run; otherwise trigger in one click.
    const declared = (row.current_config_json as { params?: Record<string, unknown> } | null)
      ?.params;
    if (declared && Object.keys(declared).length > 0) {
      setParamsFor(row);
      return;
    }
    setTriggering(row.id);
    try {
      await pipelinesApi.trigger(ws.id, row.id);
      toast.success(t("pipelines.runQueued", { name: row.name }));
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("pipelines.triggerFailed"),
      );
    } finally {
      setTriggering(null);
    }
  }

  // Phase ADW (2026-06-04) — does this pipeline reference a connection
  // no longer in the workspace? Drives the disabled "Trigger" (a run
  // would just fail to build). connNames null → not loaded → not broken.
  function isBroken(p: PipelineSummary): boolean {
    if (connNames === null) return false;
    const refs = extractConnectionNames(p.current_config_json);
    return [...refs].some((r) => !connNames.has(r));
  }

  async function onBulkDryRun() {
    if (!ws || selectedIds.size === 0) return;
    setBulkDryRunning(true);
    let ok = 0;
    let fail = 0;
    for (const id of selectedIds) {
      try {
        const res = await pipelinesApi.dryRun(ws.id, id);
        if (res.ok) ok += 1;
        else fail += 1;
      } catch {
        fail += 1;
      }
    }
    setBulkDryRunning(false);
    if (fail === 0) toast.success(t("pipelines.bulkDryRunOk", { n: ok }));
    else toast.warning(t("pipelines.bulkDryRunPartial", { ok, fail }));
  }

  async function onBulkTrigger() {
    if (!ws || selectedIds.size === 0) return;
    setBulkTriggering(true);
    let ok = 0;
    let fail = 0;
    let skipped = 0;
    for (const id of selectedIds) {
      const row = rows?.find((r) => r.id === id);
      // Broken / never-saved rows would just fail to build — skip and say so.
      if (!row || !row.current_version || isBroken(row)) {
        skipped += 1;
        continue;
      }
      try {
        await pipelinesApi.trigger(ws.id, id);
        ok += 1;
      } catch {
        fail += 1;
      }
    }
    setBulkTriggering(false);
    if (fail === 0 && skipped === 0) {
      toast.success(t("pipelines.bulkRunQueued", { n: ok }));
    } else {
      toast.warning(t("pipelines.bulkRunPartial", { ok, fail: fail + skipped }));
    }
  }

  async function onBulkDelete() {
    if (!ws || selectedIds.size === 0) return;
    setBulkDeleting(true);
    let ok = 0;
    let fail = 0;
    for (const id of selectedIds) {
      try {
        await pipelinesApi.delete(ws.id, id);
        ok += 1;
      } catch {
        fail += 1;
      }
    }
    setBulkDeleting(false);
    setSelectedIds(new Set());
    setRows((prev) => (prev ? prev.filter((r) => !selectedIds.has(r.id)) : prev));
    if (fail === 0) toast.success(t("pipelines.bulkDeleted", { n: ok }));
    else toast.warning(t("pipelines.bulkDeletePartial", { ok, fail }));
  }

  // Phase ADQ (2026-06-04) — pre-flight validation from the list, so an
  // operator can catch a missing connection / secret before triggering
  // (the "validate → run" order the migrations surface already uses).
  // Toast-only; the builder keeps the rich inline DryRunPanel.
  async function onDryRun(row: PipelineSummary) {
    if (!ws) return;
    try {
      const result = await pipelinesApi.dryRun(ws.id, row.id);
      if (result.ok) {
        const okCount = result.connectors.filter((c) => c.ok).length;
        toast.success(
          t("migrations.dryRunOk", {
            n: okCount,
            total: result.connectors.length,
          }),
        );
      } else {
        const errs =
          result.errors.length > 0
            ? result.errors
            : result.connectors
                .filter((c) => !c.ok)
                .map((c) => `${c.name}: ${c.error ?? "unknown"}`);
        for (const e of errs) {
          toast.error(t("migrations.dryRunFailed", { error: e }));
        }
      }
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <>
      <Header
        title={t("nav.pipelines")}
        subtitle={
          ws
            ? t("common.workspaceSubtitle", { name: ws.name })
            : t("common.loadingWorkspace")
        }
        actions={
          <Button
            variant="primary"
            size="md"
            onClick={() => setCreating((v) => !v)}
          >
            <PlusIcon size={16} />
            {t("pipelines.new")}
          </Button>
        }
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {creating ? (
          <Card>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                {t("pipelines.nameLabel")}
              </span>
              <Input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder={t("pipelines.namePlaceholder")}
                autoFocus
              />
            </label>

            <div className="mt-5 flex items-center justify-between gap-3">
              <p className="text-xs text-text-muted">
                {t("pipelines.createHelp")}
              </p>
              <div className="flex shrink-0 gap-2">
                <Button
                  variant="ghost"
                  onClick={() => setCreating(false)}
                  disabled={submitting}
                >
                  {t("common.cancel")}
                </Button>
                <Button onClick={onCreate} loading={submitting} disabled={!newName.trim()}>
                  {t("pipelines.createOpen")}
                </Button>
              </div>
            </div>
          </Card>
        ) : null}
        {/* Phase ABD (2026-06-01) — search box. Hidden below 5 rows
            so a fresh workspace stays uncluttered. */}
        {/* Phase ADT — also render when the broken filter is active
            (dashboard deep-link) so it's clearable with a short list. */}
        {visibleRows !== null && (visibleRows.length > 5 || brokenFilter) ? (
          <div className="grid items-end gap-2 sm:grid-cols-[1fr_auto_auto_auto]">
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("pipelines.searchPlaceholder")}
            />
            {/* Phase ADA — reuses migrations.filterLastRun* labels;
                wording is generic ("Last run: failed"). */}
            <select
              value={lastRunFilter}
              onChange={(e) =>
                setLastRunFilter(
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
            <select
              value={brokenFilter ? "broken" : ""}
              onChange={(e) => setBrokenFilter(e.target.value === "broken")}
              className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
            >
              <option value="">{t("pipelines.filterHealthAll")}</option>
              <option value="broken">{t("pipelines.filterHealthBroken")}</option>
            </select>
            {search || lastRunFilter || brokenFilter ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch("");
                  setLastRunFilter("");
                  setBrokenFilter(false);
                }}
              >
                {t("common.clear")}
              </Button>
            ) : null}
          </div>
        ) : null}
        {selectedIds.size > 0 ? (
          <div className="flex items-center justify-between gap-3 rounded-md border border-accent/40 bg-accent/5 px-3 py-2">
            <span className="text-xs text-text">
              {t("pipelines.selectedCount", { n: selectedIds.size })}
            </span>
            <div className="flex gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSelectedIds(new Set())}
                disabled={bulkDryRunning || bulkTriggering || bulkDeleting}
              >
                {t("pipelines.clearSelection")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                loading={bulkDryRunning}
                disabled={bulkTriggering || bulkDeleting}
                onClick={() => void onBulkDryRun()}
                title={t("pipelines.dryRunSelectedHint")}
              >
                <ShieldCheckIcon size={14} />
                {t("pipelines.dryRunSelected")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                loading={bulkTriggering}
                disabled={bulkDryRunning || bulkDeleting}
                onClick={() => void onBulkTrigger()}
              >
                <PlayIcon size={14} />
                {t("pipelines.runSelected")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={bulkDryRunning || bulkTriggering || bulkDeleting}
                onClick={() => setConfirmBulkDelete(true)}
              >
                <Trash2Icon size={14} />
                {t("pipelines.deleteSelected")}
              </Button>
            </div>
          </div>
        ) : null}
        <Card>
          {visibleRows === null ? (
            <div className="px-4 py-4"><TableSkeleton /></div>
          ) : filteredRows !== null &&
            filteredRows.length === 0 &&
            (search || lastRunFilter || brokenFilter) ? (
            <div className="py-8 text-center text-sm text-text-muted">
              {t("pipelines.searchNoMatch")}
            </div>
          ) : (
            <DataTable
              columns={[
                {
                  key: "_select",
                  className: "w-8",
                  header: (
                    <Checkbox
                      checked={
                        (filteredRows?.length ?? 0) > 0 &&
                        (filteredRows ?? []).every((r) => selectedIds.has(r.id))
                      }
                      indeterminate={
                        (filteredRows ?? []).some((r) => selectedIds.has(r.id)) &&
                        !(filteredRows ?? []).every((r) => selectedIds.has(r.id))
                      }
                      aria-label={t("pipelines.selectAllVisibleAria")}
                      onChange={() => {
                        const visible = filteredRows ?? [];
                        const all =
                          visible.length > 0 &&
                          visible.every((r) => selectedIds.has(r.id));
                        setSelectedIds((prev) => {
                          const next = new Set(prev);
                          for (const r of visible) {
                            if (all) next.delete(r.id);
                            else next.add(r.id);
                          }
                          return next;
                        });
                      }}
                    />
                  ),
                  cell: (row) => (
                    <Checkbox
                      checked={selectedIds.has(row.id)}
                      onChange={() => toggleSelection(row.id)}
                      onClick={(e) => e.stopPropagation()}
                      aria-label={t("pipelines.selectRowAria", { name: row.name })}
                    />
                  ),
                },
                ...buildColumns(t, lastRunByPipeline, connNames),
                {
                  key: "actions",
                  header: "",
                  className: "w-80 text-right",
                  cell: (row) => (
                    <div className="flex justify-end gap-1">
                      <Link
                        href={
                          ws ? `/w/${ws.slug}/pipelines/${row.id}/edit` : "#"
                        }
                      >
                        <Button size="sm" variant="secondary">
                          {t("pipelines.openBuilder")}
                        </Button>
                      </Link>
                      <Button
                        size="sm"
                        variant="ghost"
                        loading={triggering === row.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          void onTrigger(row);
                        }}
                        // Phase ADW — block Trigger when a connection is
                        // missing; the run would fail to build.
                        disabled={!row.current_version || isBroken(row)}
                        title={
                          isBroken(row)
                            ? t("pipelines.missingConnection", {
                                names: [
                                  ...extractConnectionNames(
                                    row.current_config_json,
                                  ),
                                ]
                                  .filter((n) => !connNames?.has(n))
                                  .join(", "),
                              })
                            : undefined
                        }
                      >
                        {t("common.trigger")}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          setBackfillRow(row);
                        }}
                        disabled={!row.current_version}
                      >
                        {t("backfill.action")}
                      </Button>
                      {/* Quick jump to this pipeline's runs (filtered) —
                          mirrors the editor-header link so users can drill
                          to history straight from the list. */}
                      <Link
                        href={ws ? `/w/${ws.slug}/runs?pipeline=${row.id}` : "#"}
                        onClick={(e) => e.stopPropagation()}
                        aria-label={t("pipelines.viewRunsAria", { name: row.name })}
                      >
                        <Button size="sm" variant="ghost" title={t("pipelines.viewRunsTitle")}>
                          <ActivityIcon size={14} />
                        </Button>
                      </Link>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          setPendingDelete(row);
                        }}
                        aria-label={t("pipelines.deleteAria", { name: row.name })}
                        className="hover:text-error"
                      >
                        <Trash2Icon size={14} />
                      </Button>
                    </div>
                  ),
                },
              ]}
              rows={filteredRows ?? []}
              onRowContextMenu={(row, e) => {
                rowMenuTargetRef.current = row;
                rowMenu.openOnEvent(e);
              }}
              emptyState={
                <EmptyState
                  icon={<WorkflowIcon size={36} strokeWidth={1.5} />}
                  title={t("pipelines.emptyTitle")}
                  description={t("pipelines.emptyDesc")}
                  action={
                    <Button onClick={() => setCreating(true)}>
                      <PlusIcon size={16} />
                      {t("pipelines.new")}
                    </Button>
                  }
                />
              }
            />
          )}
        </Card>
      </main>

      <ConfirmDialog
        open={confirmBulkDelete}
        title={t("pipelines.bulkDeleteTitle", { n: selectedIds.size })}
        description={t("pipelines.bulkDeleteDesc")}
        confirmLabel={t("common.delete")}
        destructive
        loading={bulkDeleting}
        onConfirm={() => {
          setConfirmBulkDelete(false);
          void onBulkDelete();
        }}
        onCancel={() => setConfirmBulkDelete(false)}
      />
      <ConfirmDialog
        open={pendingDelete !== null}
        title={
          pendingDelete
            ? t("pipelines.deleteTitle", { name: pendingDelete.name })
            : t("pipelines.deleteTitleFallback")
        }
        description={t("pipelines.deleteDesc")}
        confirmLabel={t("common.delete")}
        destructive
        loading={deleting}
        onConfirm={onConfirmDelete}
        onCancel={() => (deleting ? undefined : setPendingDelete(null))}
      />

      {ws ? (
        <BackfillDialog
          open={backfillRow !== null}
          workspaceId={ws.id}
          pipeline={backfillRow}
          onClose={() => setBackfillRow(null)}
        />
      ) : null}

      {ws ? (
        <TriggerParamsDialog
          open={paramsFor !== null}
          workspaceId={ws.id}
          pipeline={paramsFor}
          onClose={() => setParamsFor(null)}
        />
      ) : null}

      {/* Row right-click: mirrors the per-row toolbar so power users can
          flow through pipelines without aiming at small buttons. */}
      <ContextMenu menu={rowMenu}>
        <ContextMenuItem
          icon={<EditIcon size={14} />}
          onSelect={() => {
            const r = rowMenuTargetRef.current;
            if (r && ws) router.push(`/w/${ws.slug}/pipelines/${r.id}/edit`);
          }}
        >
          {t("pipelines.menuOpenBuilder")}
        </ContextMenuItem>
        <ContextMenuItem
          icon={<ActivityIcon size={14} />}
          onSelect={() => {
            const r = rowMenuTargetRef.current;
            if (r && ws) router.push(`/w/${ws.slug}/runs?pipeline=${r.id}`);
          }}
        >
          {t("pipelines.menuViewRuns")}
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          icon={<ShieldCheckIcon size={14} />}
          disabled={(() => {
            const r = rowMenuTargetRef.current;
            return !r || !r.current_version;
          })()}
          onSelect={() => {
            const r = rowMenuTargetRef.current;
            if (r) void onDryRun(r);
          }}
        >
          {t("pipelines.menuDryRun")}
        </ContextMenuItem>
        <ContextMenuItem
          icon={<PlayIcon size={14} />}
          disabled={(() => {
            const r = rowMenuTargetRef.current;
            // Phase ADW — also block when a connection is missing.
            return !r || !r.current_version || isBroken(r);
          })()}
          onSelect={() => {
            const r = rowMenuTargetRef.current;
            if (r) void onTrigger(r);
          }}
        >
          {t("pipelines.menuTrigger")}
        </ContextMenuItem>
        <ContextMenuItem
          icon={<CalendarPlusIcon size={14} />}
          disabled={(() => {
            const r = rowMenuTargetRef.current;
            // Phase ADW — backfill is also a run; block when broken.
            return !r || !r.current_version || isBroken(r);
          })()}
          onSelect={() => {
            const r = rowMenuTargetRef.current;
            if (r) setBackfillRow(r);
          }}
        >
          {t("pipelines.menuBackfill")}
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          icon={<Trash2Icon size={14} />}
          destructive
          onSelect={() => {
            const r = rowMenuTargetRef.current;
            if (r) setPendingDelete(r);
          }}
        >
          {t("pipelines.menuDelete")}
        </ContextMenuItem>
      </ContextMenu>
    </>
  );
}
