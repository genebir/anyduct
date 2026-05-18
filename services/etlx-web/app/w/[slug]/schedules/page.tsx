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
import { cn } from "@/lib/cn";

interface ScheduleRow extends ScheduleSummary {
  pipeline_name: string;
}

type FormState =
  | { kind: "closed" }
  | { kind: "create"; pipelineId: string | "" }
  | { kind: "edit"; row: ScheduleRow };

const COLUMNS: Column<ScheduleRow>[] = [
  { key: "name", header: "Schedule", cell: (r) => r.name },
  {
    key: "pipeline",
    header: "Pipeline",
    cell: (r) => <span className="text-text-secondary">{r.pipeline_name}</span>,
  },
  {
    key: "mode",
    header: "Mode",
    cell: (r) => (
      <span className="rounded-sm bg-overlay px-2 py-0.5 font-mono text-xs text-text-secondary">
        {r.mode}
      </span>
    ),
  },
  {
    key: "cron",
    header: "Cron",
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
    header: "Status",
    cell: (r) =>
      r.is_active ? (
        <span className="text-success">Active</span>
      ) : (
        <span className="text-text-muted">Paused</span>
      ),
  },
];

export default function SchedulesPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
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
        err instanceof ApiError ? err.message : "Couldn't load schedules.",
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
        `${row.name}: ${updated.is_active ? "active" : "paused"}`,
      );
      await refresh(ws.id);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Toggle failed.",
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
      toast.success(`Deleted ${pendingDelete.name}`);
      setPendingDelete(null);
      await refresh(ws.id);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't delete schedule.",
      );
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <Header
        title="Schedules"
        subtitle={ws ? `Workspace ${ws.name}` : "Loading workspace…"}
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
            New schedule
          </Button>
        }
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {form.kind === "create" && ws ? (
          <Card>
            <CardHeader
              title="Select a pipeline"
              description="Each schedule belongs to exactly one pipeline."
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
              Loading…
            </div>
          ) : (
            <DataTable
              columns={[
                ...COLUMNS,
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
                        aria-label={row.is_active ? "Pause" : "Resume"}
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
                        aria-label={`Edit ${row.name}`}
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
                        aria-label={`Delete ${row.name}`}
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
                  title="No schedules yet"
                  description={
                    pipelines.length === 0
                      ? "Create a pipeline first — schedules attach to pipelines."
                      : "Attach a cron schedule to one of your pipelines to have the scheduler enqueue Run rows automatically."
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
                        New schedule
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
            ? `Delete ${pendingDelete.name}?`
            : "Delete schedule?"
        }
        description="The scheduler will stop enqueuing Run rows. Existing pending and running Runs are left untouched."
        confirmLabel="Delete"
        destructive
        loading={deleting}
        onConfirm={onConfirmDelete}
        onCancel={() => (deleting ? undefined : setPendingDelete(null))}
      />
    </>
  );
}
