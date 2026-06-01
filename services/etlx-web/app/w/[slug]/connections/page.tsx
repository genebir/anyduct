"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { CableIcon, PencilIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/empty-state";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ConnectionForm } from "@/components/connections/connection-form";
import { ApiError, connectionsApi, type ConnectionSummary } from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

type FormState =
  | { kind: "closed" }
  | { kind: "create" }
  | { kind: "edit"; row: ConnectionSummary };

/** Phase ABS (2026-06-01) — transient per-row test result. Session-
 *  scope (no server persistence) so the row visually flags the
 *  latest test outcome without polluting backend audit. */
type TestState = { kind: "ok"; at: number } | { kind: "fail"; at: number; error: string } | null;

function buildColumns(
  t: Translate,
  testResults: Map<string, TestState>,
): Column<ConnectionSummary>[] {
  return [
    {
      key: "name",
      header: t("common.name"),
      cell: (r) => {
        const tr = testResults.get(r.id);
        return (
          <div className="flex items-center gap-2">
            <span>{r.name}</span>
            {tr?.kind === "ok" ? (
              <span
                className="inline-flex h-4 items-center gap-1 rounded-sm bg-success/15 px-1 text-[10px] uppercase tracking-wider text-success"
                title={t("connections.testResultOkTitle")}
              >
                {t("connections.testResultOk")}
              </span>
            ) : tr?.kind === "fail" ? (
              <span
                className="inline-flex h-4 items-center gap-1 rounded-sm bg-error/15 px-1 text-[10px] uppercase tracking-wider text-error"
                title={tr.error}
              >
                {t("connections.testResultFail")}
              </span>
            ) : null}
          </div>
        );
      },
    },
    {
      key: "type",
      header: t("connections.colConnector"),
      cell: (r) => (
        <span className="rounded-sm bg-overlay px-2 py-0.5 font-mono text-xs text-text-secondary">
          {r.type}
        </span>
      ),
    },
    {
      key: "secrets",
      header: t("connections.colSecrets"),
      cell: (r) =>
        r.secret_refs.length === 0 ? (
          <span className="text-text-muted">—</span>
        ) : (
          <span className="text-text-secondary">
            {t("connections.refs", { count: r.secret_refs.length })}
          </span>
        ),
    },
  ];
}

export default function ConnectionsPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [rows, setRows] = useState<ConnectionSummary[] | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  /** Phase ABS (2026-06-01) — session-scope per-row test outcomes.
   *  Toast is transient; this gives the table a glanceable indicator
   *  ("green" / "red") so the operator can scan the list afterwards
   *  without re-running tests. Cleared on page reload (intentional). */
  const [testResults, setTestResults] = useState<Map<string, TestState>>(
    new Map(),
  );
  const [form, setForm] = useState<FormState>({ kind: "closed" });
  const [pendingDelete, setPendingDelete] = useState<ConnectionSummary | null>(
    null,
  );
  const [deleting, setDeleting] = useState(false);
  /** Phase ABB (2026-06-01) — search + type filter mirror the
   *  migration list UX. Stays hidden when the list is short. */
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");

  const distinctTypes = useMemo(() => {
    if (!rows) return [];
    return [...new Set(rows.map((r) => r.type))].sort();
  }, [rows]);

  const filteredRows = useMemo(() => {
    if (!rows) return [];
    const term = search.trim().toLowerCase();
    return rows.filter((r) => {
      if (
        term &&
        !r.name.toLowerCase().includes(term) &&
        !r.type.toLowerCase().includes(term)
      )
        return false;
      if (typeFilter && r.type !== typeFilter) return false;
      return true;
    });
  }, [rows, search, typeFilter]);

  async function refresh(workspaceId: string) {
    try {
      const list = await connectionsApi.list(workspaceId);
      setRows(list);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("connections.loadFailed"),
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
        toast.success(t("connections.connected", { name: row.name }));
        setTestResults((prev) => {
          const next = new Map(prev);
          next.set(row.id, { kind: "ok", at: Date.now() });
          return next;
        });
      } else {
        const errMsg = result.error ?? t("connections.unknownError");
        toast.error(
          t("connections.testErrorNamed", {
            name: row.name,
            error: errMsg,
          }),
        );
        setTestResults((prev) => {
          const next = new Map(prev);
          next.set(row.id, { kind: "fail", at: Date.now(), error: errMsg });
          return next;
        });
      }
    } catch (err) {
      const errMsg =
        err instanceof ApiError ? err.message : t("connections.testFailed");
      toast.error(errMsg);
      setTestResults((prev) => {
        const next = new Map(prev);
        next.set(row.id, { kind: "fail", at: Date.now(), error: errMsg });
        return next;
      });
    } finally {
      setTesting(null);
    }
  }

  async function onConfirmDelete() {
    if (!ws || !pendingDelete) return;
    setDeleting(true);
    try {
      await connectionsApi.delete(ws.id, pendingDelete.id);
      toast.success(t("connections.deleted", { name: pendingDelete.name }));
      setPendingDelete(null);
      await refresh(ws.id);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("connections.deleteFailed"),
      );
    } finally {
      setDeleting(false);
    }
  }

  return (
    <>
      <Header
        title={t("nav.connections")}
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
                f.kind === "create" ? { kind: "closed" } : { kind: "create" },
              )
            }
          >
            <PlusIcon size={16} />
            {t("connections.new")}
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

        {/* Phase ABB (2026-06-01) — search + type filter. Hidden
            when the list is short so a fresh workspace doesn't look
            cluttered. */}
        {rows !== null && rows.length > 5 ? (
          <div className="grid items-end gap-2 sm:grid-cols-[1fr_auto_auto]">
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("connections.searchPlaceholder")}
            />
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
            >
              <option value="">{t("connections.filterTypeAll")}</option>
              {distinctTypes.map((tp) => (
                <option key={tp} value={tp}>
                  {tp}
                </option>
              ))}
            </select>
            {search || typeFilter ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch("");
                  setTypeFilter("");
                }}
              >
                {t("connections.clearFilters")}
              </Button>
            ) : null}
          </div>
        ) : null}

        <Card>
          {rows === null ? (
            <div className="py-12 text-center text-sm text-text-muted">
              {t("common.loading")}
            </div>
          ) : filteredRows.length === 0 && (search || typeFilter) ? (
            <div className="py-8 text-center text-sm text-text-muted">
              {t("connections.filterNoMatch")}
            </div>
          ) : (
            <DataTable
              columns={[
                ...buildColumns(t, testResults),
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
                        {t("common.test")}
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          setForm({ kind: "edit", row });
                        }}
                        aria-label={t("connections.editAria", { name: row.name })}
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
                        aria-label={t("connections.deleteAria", { name: row.name })}
                        className="hover:text-error"
                      >
                        <Trash2Icon size={14} />
                      </Button>
                    </div>
                  ),
                },
              ]}
              rows={filteredRows}
              emptyState={
                <EmptyState
                  icon={<CableIcon size={36} strokeWidth={1.5} />}
                  title={t("connections.emptyTitle")}
                  description={t("connections.emptyDesc")}
                  action={
                    <Button onClick={() => setForm({ kind: "create" })}>
                      <PlusIcon size={16} />
                      {t("connections.new")}
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
            ? t("connections.deleteTitle", { name: pendingDelete.name })
            : t("connections.deleteTitleFallback")
        }
        description={
          pendingDelete
            ? t("connections.deleteDesc", {
                count: pendingDelete.secret_refs.length,
              })
            : undefined
        }
        confirmLabel={t("common.delete")}
        destructive
        loading={deleting}
        onConfirm={onConfirmDelete}
        onCancel={() => (deleting ? undefined : setPendingDelete(null))}
      />
    </>
  );
}
