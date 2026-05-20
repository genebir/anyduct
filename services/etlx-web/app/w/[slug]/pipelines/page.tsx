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
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";
import { blankBuilder, serialize } from "@/lib/pipeline-config";
import { cn } from "@/lib/cn";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

function buildColumns(t: Translate): Column<PipelineSummary>[] {
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
  ];
}

export default function PipelinesPage() {
  const router = useRouter();
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [rows, setRows] = useState<PipelineSummary[] | null>(null);
  const [triggering, setTriggering] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newMode, setNewMode] = useState<"batch" | "stream">("batch");
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

  async function onCreate() {
    if (!ws || !newName.trim()) return;
    setSubmitting(true);
    try {
      const config = serialize(blankBuilder(), {
        name: newName.trim(),
        mode: newMode,
      });
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
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-1 flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  {t("pipelines.nameLabel")}
                </span>
                <Input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder={t("pipelines.namePlaceholder")}
                />
              </label>
              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  {t("pipelines.mode")}
                </span>
                <div className="flex gap-1 rounded-md border border-border-subtle bg-elevated p-1">
                  {(["batch", "stream"] as const).map((m) => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => setNewMode(m)}
                      className={cn(
                        "rounded-sm px-3 py-1.5 text-sm transition duration-150",
                        newMode === m
                          ? "bg-overlay font-medium text-text"
                          : "text-text-secondary hover:text-text",
                      )}
                    >
                      {m}
                    </button>
                  ))}
                </div>
              </div>
              <Button
                variant="ghost"
                onClick={() => setCreating(false)}
                disabled={submitting}
              >
                {t("common.cancel")}
              </Button>
              <Button onClick={onCreate} loading={submitting}>
                {t("pipelines.createOpen")}
              </Button>
            </div>
            <p className="mt-3 text-xs text-text-muted">
              {t("pipelines.createHelp")} {t("pipelines.modeHint")}
            </p>
          </Card>
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
              rows={rows}
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
    </>
  );
}
