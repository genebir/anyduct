"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import {
  CalendarClockIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  Trash2Icon,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { DataTable, type Column } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  ScheduleCreateForm,
  ScheduleEditForm,
} from "@/components/schedules/schedule-form";
import {
  ApiError,
  pipelinesApi,
  schedulesApi,
  type PipelineSummary,
  type ScheduleSummary,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";
import { cn } from "@/lib/cn";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

interface ScheduleRow extends ScheduleSummary {
  pipeline_name: string;
}

type FormState =
  | { kind: "closed" }
  | { kind: "create"; pipelineId: string | "" }
  | { kind: "edit"; row: ScheduleRow };

function buildColumns(t: Translate): Column<ScheduleRow>[] {
  return [
    { key: "name", header: t("schedules.colSchedule"), cell: (r) => r.name },
    {
      key: "pipeline",
      header: t("common.pipeline"),
      cell: (r) => (
        <span className="text-text-secondary">{r.pipeline_name}</span>
      ),
    },
    {
      key: "mode",
      header: t("common.mode"),
      cell: (r) => (
        <span className="rounded-sm bg-overlay px-2 py-0.5 font-mono text-xs text-text-secondary">
          {r.mode}
        </span>
      ),
    },
    {
      key: "cron",
      header: t("common.cron"),
      cell: (r) =>
        r.cron_expr ? (
          <code className="font-mono text-xs text-text-secondary">
            {r.cron_expr}
          </code>
        ) : (
          <span className="text-text-muted">—</span>
        ),
    },
    {
      key: "active",
      header: t("common.status"),
      cell: (r) =>
        r.is_active ? (
          <span className="text-success">{t("common.active")}</span>
        ) : (
          <span className="text-text-muted">{t("common.paused")}</span>
        ),
    },
  ];
}

export default function SchedulesPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [pipelines, setPipelines] = useState<PipelineSummary[]>([]);
  const [rows, setRows] = useState<ScheduleRow[] | null>(null);
  const [form, setForm] = useState<FormState>({ kind: "closed" });
  const [pendingDelete, setPendingDelete] = useState<ScheduleRow | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [toggling, setToggling] = useState<string | null>(null);

  async function refresh(workspaceId: string) {
    try {
      const ps = await pipelinesApi.list(workspaceId);
      setPipelines(ps);
      const groups = await Promise.all(
        ps.map(async (p) => {
          const list = await schedulesApi.list(workspaceId, p.id);
          return list.map((s) => ({ ...s, pipeline_name: p.name }));
        }),
      );
      setRows(groups.flat());
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("schedules.loadFailed"),
      );
      setRows([]);
    }
  }

  useEffect(() => {
    if (!ws) return;
    void refresh(ws.id);
  }, [ws]);

  async function onToggle(row: ScheduleRow) {
    if (!ws) return;
    setToggling(row.id);
    try {
      const updated = await schedulesApi.toggle(ws.id, row.pipeline_id, row.id);
      toast.success(
        t("schedules.toggled", {
          name: row.name,
          state: updated.is_active ? t("common.active") : t("common.paused"),
        }),
      );
      await refresh(ws.id);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("schedules.toggleFailed"),
      );
    } finally {
      setToggling(null);
    }
  }

  async function onConfirmDelete() {
    if (!ws || !pendingDelete) return;
    setDeleting(true);
    try {
      await schedulesApi.delete(ws.id, pendingDelete.pipeline_id, pendingDelete.id);
      toast.success(t("schedules.deleted", { name: pendingDelete.name }));
      setPendingDelete(null);
      await refresh(ws.id);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("schedules.deleteFailed"),
      );
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <Header
        title={t("nav.schedules")}
        subtitle={
          ws
            ? t("common.workspaceSubtitle", { name: ws.name })
            : t("common.loadingWorkspace")
        }
        actions={
          <Button
            variant="primary"
            size="md"
            onClick={() =>
              setForm((f) =>
                f.kind === "create"
                  ? { kind: "closed" }
                  : { kind: "create", pipelineId: pipelines[0]?.id ?? "" },
              )
            }
            disabled={pipelines.length === 0}
          >
            <PlusIcon size={16} />
            {t("schedules.new")}
          </Button>
        }
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {form.kind === "create" && ws ? (
          <Card>
            <CardHeader
              title={t("schedules.selectPipeline")}
              description={t("schedules.selectPipelineDesc")}
            />
            <select
              value={form.pipelineId}
              onChange={(e) =>
                setForm({ kind: "create", pipelineId: e.target.value })
              }
              className="mb-4 h-10 w-full rounded-md border border-border-subtle bg-elevated px-3 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
            >
              {pipelines.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            {form.pipelineId ? (
              <ScheduleCreateForm
                workspaceId={ws.id}
                pipelineId={form.pipelineId}
                onSaved={async () => {
                  setForm({ kind: "closed" });
                  await refresh(ws.id);
                }}
                onCancel={() => setForm({ kind: "closed" })}
              />
            ) : null}
          </Card>
        ) : null}

        {form.kind === "edit" && ws ? (
          <ScheduleEditForm
            workspaceId={ws.id}
            pipelineId={form.row.pipeline_id}
            existing={form.row}
            onSaved={async () => {
              setForm({ kind: "closed" });
              await refresh(ws.id);
            }}
            onCancel={() => setForm({ kind: "closed" })}
          />
        ) : null}

        <Card>
          {rows === null ? (
            <div className="py-12 text-center text-sm text-text-muted">
              {t("common.loading")}
            </div>
          ) : (
            <DataTable
              columns={[
                ...buildColumns(t),
                {
                  key: "actions",
                  header: "",
                  className: "w-64 text-right",
                  cell: (row) => (
                    <div className="flex justify-end gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        loading={toggling === row.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          void onToggle(row);
                        }}
                        aria-label={
                          row.is_active ? t("common.pause") : t("common.resume")
                        }
                        className={cn(
                          row.is_active ? "" : "text-success",
                        )}
                      >
                        {row.is_active ? (
                          <PauseIcon size={14} />
                        ) : (
                          <PlayIcon size={14} />
                        )}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          setForm({ kind: "edit", row });
                        }}
                        aria-label={t("schedules.editAria", { name: row.name })}
                      >
                        <PencilIcon size={14} />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          setPendingDelete(row);
                        }}
                        aria-label={t("schedules.deleteAria", { name: row.name })}
                        className="hover:text-error"
                      >
                        <Trash2Icon size={14} />
                      </Button>
                    </div>
                  ),
                },
              ]}
              rows={rows}
              emptyState={
                <EmptyState
                  icon={<CalendarClockIcon size={36} strokeWidth={1.5} />}
                  title={t("schedules.emptyTitle")}
                  description={
                    pipelines.length === 0
                      ? t("schedules.emptyNoPipelines")
                      : t("schedules.emptyDesc")
                  }
                  action={
                    pipelines.length === 0 ? undefined : (
                      <Button
                        onClick={() =>
                          setForm({
                            kind: "create",
                            pipelineId: pipelines[0].id,
                          })
                        }
                      >
                        <PlusIcon size={16} />
                        {t("schedules.new")}
                      </Button>
                    )
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
            ? t("schedules.deleteTitle", { name: pendingDelete.name })
            : t("schedules.deleteTitleFallback")
        }
        description={t("schedules.deleteDesc")}
        confirmLabel={t("common.delete")}
        destructive
        loading={deleting}
        onConfirm={onConfirmDelete}
        onCancel={() => (deleting ? undefined : setPendingDelete(null))}
      />
    </>
  );
}
