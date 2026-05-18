"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { CableIcon, PencilIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ConnectionForm } from "@/components/connections/connection-form";
import { ApiError, connectionsApi, type ConnectionSummary } from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";

type FormState =
  | { kind: "closed" }
  | { kind: "create" }
  | { kind: "edit"; row: ConnectionSummary };

const COLUMNS: Column<ConnectionSummary>[] = [
  { key: "name", header: "Name", cell: (r) => r.name },
  {
    key: "type",
    header: "Connector",
    cell: (r) => (
      <span className="rounded-sm bg-overlay px-2 py-0.5 font-mono text-xs text-text-secondary">
        {r.type}
      </span>
    ),
  },
  {
    key: "secrets",
    header: "Secrets",
    cell: (r) =>
      r.secret_refs.length === 0 ? (
        <span className="text-text-muted">—</span>
      ) : (
        <span className="text-text-secondary">
          {r.secret_refs.length} ref
          {r.secret_refs.length === 1 ? "" : "s"}
        </span>
      ),
  },
];

export default function ConnectionsPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const [rows, setRows] = useState<ConnectionSummary[] | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>({ kind: "closed" });
  const [pendingDelete, setPendingDelete] = useState<ConnectionSummary | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);

  async function refresh(workspaceId: string) {
    try {
      const list = await connectionsApi.list(workspaceId);
      setRows(list);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't load connections.",
      );
      setRows([]);
    }
  }

  useEffect(() => {
    if (!ws) return;
    void refresh(ws.id);
  }, [ws]);

  async function onTest(row: ConnectionSummary) {
    if (!ws) return;
    setTesting(row.id);
    try {
      const result = await connectionsApi.test(ws.id, row.id);
      if (result.ok) {
        toast.success(`${row.name} connected`);
      } else {
        toast.error(`${row.name}: ${result.error ?? "unknown error"}`);
      }
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Test failed unexpectedly.",
      );
    } finally {
      setTesting(null);
    }
  }

  async function onConfirmDelete() {
    if (!ws || !pendingDelete) return;
    setDeleting(true);
    try {
      await connectionsApi.delete(ws.id, pendingDelete.id);
      toast.success(`Deleted ${pendingDelete.name}`);
      setPendingDelete(null);
      await refresh(ws.id);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't delete connection.",
      );
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <Header
        title="Connections"
        subtitle={ws ? `Workspace ${ws.name}` : "Loading workspace…"}
        actions={
          <Button
            variant="primary"
            size="md"
            onClick={() =>
              setForm((f) =>
                f.kind === "create" ? { kind: "closed" } : { kind: "create" },
              )
            }
          >
            <PlusIcon size={16} />
            New connection
          </Button>
        }
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {form.kind === "create" && ws ? (
          <ConnectionForm
            workspaceId={ws.id}
            mode="create"
            onSaved={async () => {
              setForm({ kind: "closed" });
              await refresh(ws.id);
            }}
            onCancel={() => setForm({ kind: "closed" })}
          />
        ) : null}
        {form.kind === "edit" && ws ? (
          <ConnectionForm
            workspaceId={ws.id}
            mode="edit"
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
                        loading={testing === row.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          void onTest(row);
                        }}
                      >
                        Test
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
                  icon={<CableIcon size={36} strokeWidth={1.5} />}
                  title="No connections yet"
                  description="Connections store the credentials and host metadata for sources and sinks."
                  action={
                    <Button onClick={() => setForm({ kind: "create" })}>
                      <PlusIcon size={16} />
                      New connection
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
          pendingDelete ? `Delete ${pendingDelete.name}?` : "Delete connection?"
        }
        description={
          pendingDelete
            ? `${pendingDelete.secret_refs.length === 0 ? "No" : pendingDelete.secret_refs.length} secret${
                pendingDelete.secret_refs.length === 1 ? "" : "s"
              } will be removed from the backend. Pipelines that reference this connection by name will fail until you create a replacement.`
            : undefined
        }
        confirmLabel="Delete"
        destructive
        loading={deleting}
        onConfirm={onConfirmDelete}
        onCancel={() => (deleting ? undefined : setPendingDelete(null))}
      />
    </>
  );
}
