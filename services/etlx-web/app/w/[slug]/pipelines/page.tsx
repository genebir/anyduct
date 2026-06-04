"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  ActivityIcon,
  CalendarClockIcon,
  CalendarPlusIcon,
  EditIcon,
  HandIcon,
  PlayIcon,
  PlusIcon,
  Trash2Icon,
  WorkflowIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
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
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { BackfillDialog } from "@/components/pipelines/backfill-dialog";
import {
  ApiError,
  pipelinesApi,
  runsApi,
  type PipelineSummary,
  type RunSummary,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import { relativeTime, absoluteTime } from "@/lib/format-time";
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
): Column<PipelineSummary>[] {
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
      key: "mode",
      header: t("common.mode"),
      cell: (r) => {
        const cfg = r.current_config_json as { mode?: string } | null;
        const stream = cfg?.mode === "stream";
        return (
          <span className="inline-flex items-center gap-1">
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[11px] font-medium",
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
          <div className="flex items-center gap-2 text-xs">
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
            ) : null}
            <span className="text-text-muted" title={absoluteTime(when)}>
              {relativeTime(when, t)}
            </span>
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
      return true;
    });
  }, [visibleRows, search, lastRunFilter, lastRunByPipeline]);
  const [triggering, setTriggering] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<PipelineSummary | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);
  const [backfillRow, setBackfillRow] = useState<PipelineSummary | null>(null);
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
        {visibleRows !== null && visibleRows.length > 5 ? (
          <div className="grid items-end gap-2 sm:grid-cols-[1fr_auto_auto]">
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
            {search || lastRunFilter ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch("");
                  setLastRunFilter("");
                }}
              >
                {t("common.clear")}
              </Button>
            ) : null}
          </div>
        ) : null}
        <Card>
          {visibleRows === null ? (
            <div className="py-12 text-center text-sm text-text-muted">
              {t("common.loading")}
            </div>
          ) : filteredRows !== null &&
            filteredRows.length === 0 &&
            (search || lastRunFilter) ? (
            <div className="py-8 text-center text-sm text-text-muted">
              {t("pipelines.searchNoMatch")}
            </div>
          ) : (
            <DataTable
              columns={[
                ...buildColumns(t, lastRunByPipeline),
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
                        disabled={!row.current_version}
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
          icon={<PlayIcon size={14} />}
          disabled={(() => {
            const r = rowMenuTargetRef.current;
            return !r || !r.current_version;
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
            return !r || !r.current_version;
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
