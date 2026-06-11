"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useParams } from "next/navigation";
import { Trash2Icon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card, CardHeader } from "@/components/ui/card";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  pipelinesApi,
  variablesApi,
  type WorkspaceVariableEntry,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import { inferType } from "@/lib/variable-types";
import { buildVariableUsage } from "@/lib/variable-usage";

const NAME_RE = /^[a-zA-Z_][a-zA-Z0-9_]*$/;

function parseVarValue(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export default function VariablesPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const canEdit = ws?.role === "owner" || ws?.role === "editor" || ws?.role == null;

  // null = not loaded yet (distinguishes loading from a genuinely
  // empty list — previously ``[]`` flashed "no variables" during load).
  const [vars, setVars] = useState<WorkspaceVariableEntry[] | null>(null);
  /** Phase ACR (2026-06-04) — variable name → referencing pipelines.
   *  null until the pipelines fetch resolves; cells render neutral
   *  until then so a failed fetch never reads as "unused". */
  const [usage, setUsage] = useState<
    Map<string, { id: string; name: string }[]> | null
  >(null);
  const [name, setName] = useState("");
  const [valueText, setValueText] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  async function load(workspaceId: string) {
    try {
      setVars(await variablesApi.list(workspaceId));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("variables.loadFailed"));
    }
  }

  useEffect(() => {
    if (ws) void load(ws.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ws?.id]);

  // Phase ACR (2026-06-04) — index variable usage from pipelines. Soft-
  // fail: keep usage null on error so cells stay neutral, never a false
  // "unused".
  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    (async () => {
      try {
        const pipelines = await pipelinesApi.list(ws.id);
        if (cancelled) return;
        setUsage(
          buildVariableUsage(
            pipelines.map((p) => ({
              id: p.id,
              name: p.name,
              config: p.current_config_json,
            })),
          ),
        );
      } catch {
        if (!cancelled) setUsage(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws?.id]);

  function editRow(v: WorkspaceVariableEntry) {
    setName(v.name);
    setValueText(typeof v.value === "string" ? v.value : JSON.stringify(v.value));
    setDescription(v.description ?? "");
  }

  async function onSave(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!ws || !canEdit) return;
    if (!NAME_RE.test(name.trim())) {
      toast.error(t("variables.nameInvalid"));
      return;
    }
    setSaving(true);
    try {
      await variablesApi.set(ws.id, name.trim(), {
        value: parseVarValue(valueText),
        description: description.trim() || null,
      });
      toast.success(t("variables.saved", { name: name.trim() }));
      setName("");
      setValueText("");
      setDescription("");
      await load(ws.id);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("variables.saveFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function onDelete(varName: string) {
    if (!ws) return;
    try {
      await variablesApi.delete(ws.id, varName);
      await load(ws.id);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("variables.deleteFailed"));
    } finally {
      setPendingDelete(null);
    }
  }

  // Phase ACR (2026-06-04) — pipelines referencing the variable queued
  // for deletion. Empty when usage hasn't loaded or it's truly unused.
  const deleteRefs = (pendingDelete && usage?.get(pendingDelete)) || [];

  return (
    <>
      <Header
        title={t("nav.variables")}
        subtitle={
          ws
            ? t("common.workspaceSubtitle", { name: ws.name })
            : t("common.loadingWorkspace")
        }
      />
      <main className="mx-auto w-full max-w-3xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        <Card>
          <CardHeader title={t("variables.title")} description={t("variables.desc")} />
          {vars === null ? (
            <TableSkeleton rows={4} />
          ) : vars.length === 0 ? (
            <p className="text-sm text-text-muted">{t("variables.empty")}</p>
          ) : (
            <ul className="divide-y divide-border-subtle">
              {vars.map((v) => {
                // Phase AAL (2026-05-29): show the inferred type as a
                // small badge so the workspace variables list speaks
                // the same vocabulary as the pipeline-settings panel
                // (string / number / boolean / JSON).
                const inferred = inferType(v.value);
                return (
                <li key={v.name} className="flex items-center gap-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <code className="text-sm font-medium text-text">{`\${var.${v.name}}`}</code>
                    <span
                      className="ml-2 inline-flex h-4 items-center rounded-sm bg-overlay px-1 text-[10px] font-semibold uppercase text-text-muted"
                      title={t("variables.typeBadge", { type: inferred })}
                    >
                      {inferred}
                    </span>
                    <span className="ml-2 text-sm text-text-secondary">
                      {JSON.stringify(v.value)}
                    </span>
                    {v.description ? (
                      <p className="truncate text-xs text-text-muted">{v.description}</p>
                    ) : null}
                  </div>
                  {/* Phase ACR (2026-06-04) — usage at a glance. usage
                      null → render nothing (not loaded / fetch failed)
                      so an empty index never reads as "unused". */}
                  {usage !== null
                    ? (() => {
                        const refs = usage.get(v.name) ?? [];
                        return refs.length > 0 ? (
                          <span
                            className="shrink-0 text-xs text-text-secondary"
                            title={refs.map((p) => p.name).join("\n")}
                          >
                            {t("variables.usedByCount", { count: refs.length })}
                          </span>
                        ) : (
                          <span className="shrink-0 text-xs text-text-muted">
                            {t("variables.usedByNone")}
                          </span>
                        );
                      })()
                    : null}
                  {canEdit ? (
                    <>
                      <Button variant="ghost" size="sm" onClick={() => editRow(v)}>
                        {t("common.edit")}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={t("variables.deleteAria", { name: v.name })}
                        onClick={() => setPendingDelete(v.name)}
                      >
                        <Trash2Icon size={16} />
                      </Button>
                    </>
                  ) : null}
                </li>
                );
              })}
            </ul>
          )}
          {canEdit ? (
            <form onSubmit={onSave} className="mt-4 grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  {t("variables.name")}
                </span>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="warehouse"
                  required
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  {t("variables.value")}
                </span>
                <Input
                  value={valueText}
                  onChange={(e) => setValueText(e.target.value)}
                  placeholder='analytics  or  5000  or  ["a","b"]'
                />
              </label>
              <label className="flex flex-col gap-1.5 sm:col-span-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  {t("variables.description")}
                </span>
                <Input value={description} onChange={(e) => setDescription(e.target.value)} />
              </label>
              <div className="sm:col-span-2 flex items-center gap-3">
                <Button type="submit" loading={saving}>
                  {t("variables.save")}
                </Button>
                {/* Phase ADM (2026-06-04) — set is an upsert keyed by
                    name, so editing then changing the name silently
                    creates a *new* variable. Show whether the current
                    name updates an existing one or creates a new one. */}
                {name.trim() ? (
                  <span className="text-xs text-text-muted">
                    {(vars ?? []).some((v) => v.name === name.trim())
                      ? t("variables.willUpdate", { name: name.trim() })
                      : t("variables.willCreate")}
                  </span>
                ) : (
                  <span className="text-xs text-text-muted">
                    {t("variables.valueHelp")}
                  </span>
                )}
              </div>
            </form>
          ) : null}
          <ConfirmDialog
            open={pendingDelete !== null}
            title={t("variables.deleteTitle")}
            description={t("variables.deleteConfirm", { name: pendingDelete ?? "" })}
            body={
              // Phase ACR (2026-06-04) — warn when the variable is still
              // interpolated by pipelines; their next run would fail to
              // resolve ``${var.<name>}``. Reuses the usage index.
              deleteRefs.length > 0 ? (
                <div className="rounded-md border border-warning/40 bg-warning/10 p-3 text-xs">
                  <p className="font-medium text-warning">
                    {t("variables.deleteInUseWarn", { count: deleteRefs.length })}
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
            onConfirm={() => pendingDelete && onDelete(pendingDelete)}
            onCancel={() => setPendingDelete(null)}
          />
        </Card>
      </main>
    </>
  );
}
