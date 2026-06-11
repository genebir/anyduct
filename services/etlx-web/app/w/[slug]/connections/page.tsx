"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  CableIcon,
  NetworkIcon,
  PencilIcon,
  PlusIcon,
  ShieldCheckIcon,
  Trash2Icon,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { TableSkeleton } from "@/components/ui/skeleton";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/empty-state";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ConnectionForm } from "@/components/connections/connection-form";
import {
  ApiError,
  connectionsApi,
  pipelinesApi,
  type ConnectionSummary,
} from "@/lib/api";
import { buildConnectionUsage } from "@/lib/connection-usage";
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

/** Phase AEQ (2026-06-04) — a one-line, secret-safe summary of a
 *  connection's config for the name tooltip, so an operator can tell two
 *  same-type connections apart ("which DB does pg_prod point at?")
 *  without opening anything. Secret fields are ``${SECRET:...}``
 *  placeholders in config_json — masked to ``***`` here; only the
 *  non-secret bits (host / database / port / bucket …) are shown. */
function connectionConfigSummary(config: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(config)) {
    if (v === null || v === undefined || typeof v === "object") continue;
    const s = String(v);
    // Placeholder for a secret-backed field → mask, don't leak the ref.
    parts.push(`${k}: ${s.includes("${") || /secret/i.test(s) ? "***" : s}`);
  }
  return parts.join(" · ");
}

function buildColumns(
  t: Translate,
  testResults: Map<string, TestState>,
  usage: Map<string, { id: string; name: string }[]> | null,
): Column<ConnectionSummary>[] {
  return [
    {
      key: "name",
      header: t("common.name"),
      cell: (r) => {
        const tr = testResults.get(r.id);
        const cfgSummary = connectionConfigSummary(r.config_json);
        return (
          <div className="flex items-center gap-2">
            <span title={cfgSummary || undefined}>{r.name}</span>
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
    {
      // Phase ACL (2026-06-04) — "Used by" count. Lets the operator
      // judge delete-safety at a glance ("0 pipelines" → safe to
      // remove) and gives the analyst a feed-in context. Referencing
      // pipeline names go in the title so hovering reveals exactly
      // which pipelines pin the connection.
      key: "used_by",
      className: "w-28",
      header: t("connections.colUsedBy"),
      cell: (r) => {
        // usage === null → pipelines not loaded (or fetch failed). Show
        // a neutral "—" rather than "unused" so a transient fetch
        // failure never reads as "safe to delete".
        if (usage === null) {
          return <span className="text-xs text-text-muted">—</span>;
        }
        const refs = usage.get(r.name) ?? [];
        if (refs.length === 0) {
          return (
            <span className="text-xs text-text-muted">
              {t("connections.usedByNone")}
            </span>
          );
        }
        return (
          <span
            className="text-xs text-text-secondary"
            title={refs.map((p) => p.name).join("\n")}
          >
            {t("connections.usedByCount", { count: refs.length })}
          </span>
        );
      },
    },
  ];
}

export default function ConnectionsPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [rows, setRows] = useState<ConnectionSummary[] | null>(null);
  /** Phase ACL (2026-06-04) — pipelines, fetched once, to compute the
   *  "Used by" count. Usage changes rarely so no polling. */
  const [usage, setUsage] = useState<
    Map<string, { id: string; name: string }[]> | null
  >(null);
  const [testing, setTesting] = useState<string | null>(null);
  /** Phase ACT (2026-06-04) — bulk "Test all" in progress. */
  const [testingAll, setTestingAll] = useState(false);
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
  /** Phase ADI (2026-06-04) — usage filter, URL-presettable via
   *  ``?usage=unused`` so the dashboard "N unused" card deep-links
   *  straight to the cleanup subset. */
  const searchParams = useSearchParams();
  const [usageFilter, setUsageFilter] = useState<"" | "unused">(
    searchParams.get("usage") === "unused" ? "unused" : "",
  );

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
      // usage===null → not loaded; don't hide rows (avoid a false
      // "everything unused" view while pipelines are still fetching).
      if (usageFilter === "unused" && usage !== null) {
        if ((usage.get(r.name)?.length ?? 0) > 0) return false;
      }
      return true;
    });
  }, [rows, search, typeFilter, usageFilter, usage]);

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

  // Phase ACL (2026-06-04) — fetch pipelines once and index connection
  // usage. Soft-fail: a usage lookup miss just renders "—".
  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    (async () => {
      try {
        const pipelines = await pipelinesApi.list(ws.id);
        if (cancelled) return;
        setUsage(
          buildConnectionUsage(
            pipelines.map((p) => ({
              id: p.id,
              name: p.name,
              config: p.current_config_json,
            })),
          ),
        );
      } catch {
        // Keep usage null on failure → cells render neutral "—",
        // never a false "unused".
        if (!cancelled) setUsage(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws]);

  /** Run one connection test and record its outcome in the per-row
   *  chip (ABS) — no toast, so the bulk path (ACT) doesn't spam. */
  async function runTest(
    workspaceId: string,
    row: ConnectionSummary,
  ): Promise<{ ok: boolean; error?: string }> {
    try {
      const result = await connectionsApi.test(workspaceId, row.id);
      if (result.ok) {
        setTestResults((prev) =>
          new Map(prev).set(row.id, { kind: "ok", at: Date.now() }),
        );
        return { ok: true };
      }
      const errMsg = result.error ?? t("connections.unknownError");
      setTestResults((prev) =>
        new Map(prev).set(row.id, { kind: "fail", at: Date.now(), error: errMsg }),
      );
      return { ok: false, error: errMsg };
    } catch (err) {
      const errMsg =
        err instanceof ApiError ? err.message : t("connections.testFailed");
      setTestResults((prev) =>
        new Map(prev).set(row.id, { kind: "fail", at: Date.now(), error: errMsg }),
      );
      return { ok: false, error: errMsg };
    }
  }

  async function onTest(row: ConnectionSummary) {
    if (!ws) return;
    setTesting(row.id);
    const res = await runTest(ws.id, row);
    if (res.ok) {
      toast.success(t("connections.connected", { name: row.name }));
    } else {
      toast.error(
        t("connections.testErrorNamed", { name: row.name, error: res.error ?? "" }),
      );
    }
    setTesting(null);
  }

  // Phase ACT (2026-06-04) — test every connection in the current
  // (filtered) view sequentially. Sequential rather than parallel so
  // we don't open N backend connections at once; each row's chip
  // updates as it completes. One summary toast at the end.
  async function onTestAll() {
    if (!ws || testingAll) return;
    setTestingAll(true);
    let ok = 0;
    let fail = 0;
    // Test ALL connections, not just the filtered view, so the label
    // ("Test all") is honest even when a type filter is applied.
    for (const row of rows ?? []) {
      setTesting(row.id);
      const res = await runTest(ws.id, row);
      if (res.ok) ok += 1;
      else fail += 1;
    }
    setTesting(null);
    setTestingAll(false);
    if (fail === 0) {
      toast.success(t("connections.testAllOk", { count: ok }));
    } else {
      toast.error(t("connections.testAllSummary", { ok, fail }));
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

  // Phase ACM (2026-06-04) — pipelines that reference the connection
  // queued for deletion. Empty when usage hasn't loaded or it's
  // genuinely unused.
  const deleteRefs =
    (pendingDelete && usage?.get(pendingDelete.name)) || [];

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
          <div className="flex items-center gap-2">
            {/* Phase ACT (2026-06-04) — test the whole (filtered) list
                in one gesture after setting up several connections. */}
            <Button
              variant="ghost"
              size="md"
              onClick={onTestAll}
              loading={testingAll}
              disabled={!ws || (rows?.length ?? 0) === 0}
            >
              <ShieldCheckIcon size={16} />
              {t("connections.testAll")}
            </Button>
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
          </div>
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
            // Phase ADE (2026-06-04) — let the form warn on rename.
            usageCount={usage?.get(form.row.name)?.length ?? 0}
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
        {/* Phase ADI — also render when a usage filter is active (e.g.
            from the dashboard deep-link) so it can be cleared even with
            ≤5 connections. */}
        {rows !== null && (rows.length > 5 || usageFilter) ? (
          <div className="grid items-end gap-2 sm:grid-cols-[1fr_auto_auto_auto]">
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
            <select
              value={usageFilter}
              onChange={(e) =>
                setUsageFilter(e.target.value as "" | "unused")
              }
              className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
            >
              <option value="">{t("connections.filterUsageAll")}</option>
              <option value="unused">{t("connections.filterUsageUnused")}</option>
            </select>
            {search || typeFilter || usageFilter ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setSearch("");
                  setTypeFilter("");
                  setUsageFilter("");
                }}
              >
                {t("connections.clearFilters")}
              </Button>
            ) : null}
          </div>
        ) : null}

        <Card>
          {rows === null ? (
            <div className="px-4 py-4"><TableSkeleton /></div>
          ) : filteredRows.length === 0 && (search || typeFilter || usageFilter) ? (
            <div className="py-8 text-center text-sm text-text-muted">
              {t("connections.filterNoMatch")}
            </div>
          ) : (
            <DataTable
              columns={[
                ...buildColumns(t, testResults, usage),
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
                      <Link
                        href={`/w/${slug}/connections/${row.id}/erd`}
                        onClick={(e) => e.stopPropagation()}
                        aria-label={t("erd.viewAria", { name: row.name })}
                      >
                        <Button size="sm" variant="ghost">
                          <NetworkIcon size={14} />
                        </Button>
                      </Link>
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
        body={
          // Phase ACM (2026-06-04) — warn when the connection is still
          // referenced. Deleting it would break those pipelines' next
          // run, so list them explicitly rather than letting the
          // operator find out at runtime. Reuses the ACL usage index.
          deleteRefs.length > 0 ? (
            <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-xs">
              <p className="font-medium text-warning">
                {t("connections.deleteInUseWarn", {
                  count: deleteRefs.length,
                })}
              </p>
              <ul className="mt-2 list-disc space-y-0.5 pl-4 text-text-secondary">
                {deleteRefs.map((p) => (
                  <li key={p.id} className="font-mono">
                    {p.name}
                  </li>
                ))}
              </ul>
            </div>
          ) : undefined
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
