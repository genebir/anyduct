"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { PlusIcon, Trash2Icon, WorkflowIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DataTable, type Column } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ApiError, pipelinesApi, type PipelineSummary } from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { blankBuilder, serialize } from "@/lib/pipeline-config";

const COLUMNS: Column<PipelineSummary>[] = [
  {
    key: "name",
    header: "Pipeline",
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
    key: "version",
    header: "Version",
    cell: (r) =>
      r.current_version ? (
        <span className="font-mono text-xs text-text-secondary">
          v{r.current_version}
        </span>
      ) : (
        <span className="text-text-muted">—</span>
      ),
  },
];

export default function PipelinesPage() {
  const router = useRouter();
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const [rows, setRows] = useState<PipelineSummary[] | null>(null);
  const [triggering, setTriggering] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<PipelineSummary | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);

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
            err instanceof ApiError ? err.message : "Couldn't load pipelines.",
          );
          setRows([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws]);

  async function onCreate() {
    if (!ws || !newName.trim()) return;
    setSubmitting(true);
    try {
      const config = serialize(blankBuilder(), {
        name: newName.trim(),
        mode: "batch",
      });
      const created = await pipelinesApi.create(ws.id, {
        name: newName.trim(),
        config,
      });
      toast.success(`Created ${created.name}`);
      router.push(`/w/${ws.slug}/pipelines/${created.id}/edit`);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't create pipeline.",
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
      toast.success(`Deleted ${pendingDelete.name}`);
      setPendingDelete(null);
      const list = await pipelinesApi.list(ws.id);
      setRows(list);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't delete pipeline.",
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
      toast.success(`Run queued for ${row.name}`);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Trigger failed.",
      );
    } finally {
      setTriggering(null);
    }
  }

  return (
    <>
      <Header
        title="Pipelines"
        subtitle={ws ? `Workspace ${ws.name}` : "Loading workspace…"}
        actions={
          <Button
            variant="primary"
            size="md"
            onClick={() => setCreating((v) => !v)}
          >
            <PlusIcon size={16} />
            New pipeline
          </Button>
        }
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {creating ? (
          <Card>
            <div className="flex items-end gap-3">
              <label className="flex flex-1 flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  Pipeline name
                </span>
                <Input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="orders-sync"
                />
              </label>
              <Button
                variant="ghost"
                onClick={() => setCreating(false)}
                disabled={submitting}
              >
                Cancel
              </Button>
              <Button onClick={onCreate} loading={submitting}>
                Create &amp; open builder
              </Button>
            </div>
            <p className="mt-3 text-xs text-text-muted">
              Starts a blank pipeline with default Postgres source and sink —
              configure both in the visual builder.
            </p>
          </Card>
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
                      <Link
                        href={
                          ws ? `/w/${ws.slug}/pipelines/${row.id}/edit` : "#"
                        }
                      >
                        <Button size="sm" variant="secondary">
                          Open builder
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
                        Trigger
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
                  icon={<WorkflowIcon size={36} strokeWidth={1.5} />}
                  title="No pipelines yet"
                  description="Pipelines are graphs of source → transform → sink. Use the visual builder to design one without writing YAML."
                  action={
                    <Button onClick={() => setCreating(true)}>
                      <PlusIcon size={16} />
                      New pipeline
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
        title={pendingDelete ? `Delete ${pendingDelete.name}?` : "Delete pipeline?"}
        description="All versions and schedules of this pipeline are removed. Past runs stay in the runs table for audit. Pending and in-flight runs are left to complete on their own — the worker won't be able to re-fetch them after deletion."
        confirmLabel="Delete"
        destructive
        loading={deleting}
        onConfirm={onConfirmDelete}
        onCancel={() => (deleting ? undefined : setPendingDelete(null))}
      />
    </>
  );
}
