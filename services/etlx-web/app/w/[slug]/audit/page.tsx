"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { ChevronDownIcon, ChevronRightIcon, ScrollTextIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiError, auditApi, type AuditLogEntry } from "@/lib/api";
import { useCurrentUser } from "@/components/providers/auth-provider";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import { cn } from "@/lib/cn";

const RESOURCE_TYPES = [
  "",
  "workspace",
  "membership",
  "connection",
  "pipeline",
  "schedule",
  "run",
];

/** Quick-filter chips for the action dropdown (Phase U, 2026-05-28).
 *  Empty value = no action filter ("all actions"). The two data-plane
 *  events from the worker land here too so an operator can scope the
 *  audit feed to "what SQL/code actually ran" without typing.
 *
 *  Ordering: data-plane first (the new value-add), then the most
 *  common control-plane events. Anything else still works — the
 *  filter is a free-string match server-side, the dropdown is just a
 *  shortcut. */
/** Action shortcut list. Phase ABN (2026-06-01) — expanded after a
 *  persona-driven audit of which actions actually fire in
 *  production: ``pipeline.triggers_set`` (auto-materialize chains),
 *  ``connection.delete``, ``schedule.delete``, ``schedule.toggled``,
 *  ``variable.*``, ``workspace.*``. Anything else still works via
 *  free-form URL ``?action=`` — the dropdown is just a shortcut. */
const ACTIONS = [
  "",
  // Data-plane (what actually ran)
  "run.sql_read",
  "run.sql_executed",
  "run.python_executed",
  // Run lifecycle
  "run.trigger",
  "run.cancel",
  "run.retry",
  // Pipelines
  "pipeline.create",
  "pipeline.update",
  "pipeline.delete",
  "pipeline.triggers_set",
  // Connections
  "connection.create",
  "connection.update",
  "connection.delete",
  // Schedules
  "schedule.create",
  "schedule.update",
  "schedule.delete",
  "schedule.toggled",
  // Sensors
  "sensor.create",
  "sensor.update",
  "sensor.delete",
  // Workspace
  "workspace.create",
  "workspace.update",
  "workspace.delete",
  "membership.add",
  "membership.role_changed",
  "membership.remove",
  // Variables (workspace-level)
  "variable.create",
  "variable.update",
  "variable.delete",
];

const PAGE_SIZE = 100;

export default function AuditPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  // Phase ABT (2026-06-01) — used to render "you" instead of a bare
  // UUID prefix when the actor matches the signed-in user.
  const currentUser = useCurrentUser();
  const [rows, setRows] = useState<AuditLogEntry[] | null>(null);
  const [resourceType, setResourceType] = useState("");
  const [resourceId, setResourceId] = useState("");
  // Phase U (2026-05-28): filter by action name. Empty string = no
  // filter ("all actions"). Particularly useful to scope to the new
  // data-plane events (``run.sql_executed`` / ``run.python_executed``)
  // without scrolling through hundreds of pipeline.create rows.
  const [actionFilter, setActionFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    (async () => {
      try {
        const list = await auditApi.query(ws.id, {
          resource_type: resourceType || undefined,
          resource_id: resourceId.trim() || undefined,
          action: actionFilter || undefined,
          limit: PAGE_SIZE,
          offset,
        });
        if (!cancelled) setRows(list);
      } catch (err) {
        if (!cancelled) {
          toast.error(
            err instanceof ApiError ? err.message : t("audit.loadFailed"),
          );
          setRows([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws, resourceType, resourceId, actionFilter, offset, t]);

  return (
    <>
      <Header
        title={t("nav.audit")}
        subtitle={
          ws
            ? t("common.workspaceSubtitle", { name: ws.name })
            : t("common.loadingWorkspace")
        }
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        <Card>
          <CardHeader
            title={t("audit.filters")}
            description={t("audit.filtersDesc")}
          />
          <div className="grid gap-4 md:grid-cols-[1fr_1fr_2fr_auto]">
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                {t("audit.resourceType")}
              </span>
              <select
                value={resourceType}
                onChange={(e) => {
                  setResourceType(e.target.value);
                  setOffset(0);
                }}
                className="h-10 rounded-md border border-border-subtle bg-elevated px-3 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
              >
                {RESOURCE_TYPES.map((rt) => (
                  <option key={rt} value={rt}>
                    {rt === "" ? t("audit.allResources") : rt}
                  </option>
                ))}
              </select>
            </label>
            {/* Action filter (Phase U, 2026-05-28). The dropdown is a
                shortcut to the most useful values; arbitrary action
                strings work via URL param if a power user needs
                them. */}
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                {t("audit.action")}
              </span>
              <select
                value={actionFilter}
                onChange={(e) => {
                  setActionFilter(e.target.value);
                  setOffset(0);
                }}
                className="h-10 rounded-md border border-border-subtle bg-elevated px-3 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
              >
                {ACTIONS.map((a) => (
                  <option key={a} value={a}>
                    {a === "" ? t("audit.allActions") : a}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                {t("audit.resourceId")}
              </span>
              <Input
                value={resourceId}
                onChange={(e) => {
                  setResourceId(e.target.value);
                  setOffset(0);
                }}
                placeholder={t("audit.resourceIdPlaceholder")}
              />
            </label>
            <div className="flex items-end justify-end gap-2">
              <Button
                variant="ghost"
                onClick={() => {
                  setResourceType("");
                  setResourceId("");
                  setActionFilter("");
                  setOffset(0);
                }}
              >
                {t("common.reset")}
              </Button>
            </div>
          </div>
        </Card>

        <Card>
          {rows === null ? (
            <div className="py-12 text-center text-sm text-text-muted">
              {t("common.loading")}
            </div>
          ) : rows.length === 0 ? (
            <EmptyState
              icon={<ScrollTextIcon size={36} strokeWidth={1.5} />}
              title={t("audit.noRowsTitle")}
              description={offset > 0 ? t("audit.noRowsDesc") : t("audit.emptyDesc")}
            />
          ) : (
            <>
              <ul className="divide-y divide-border-subtle">
                {rows.map((row) => (
                  <AuditRow
                    key={row.id}
                    row={row}
                    systemLabel={t("audit.system")}
                    beforeLabel={t("audit.before")}
                    afterLabel={t("audit.after")}
                    youLabel={t("audit.you")}
                    currentUserId={currentUser?.id ?? null}
                    t={t}
                    open={!!expanded[row.id]}
                    onToggle={() =>
                      setExpanded((prev) => ({
                        ...prev,
                        [row.id]: !prev[row.id],
                      }))
                    }
                  />
                ))}
              </ul>

              <div className="mt-4 flex items-center justify-between border-t border-border-subtle pt-4 text-xs text-text-muted">
                <span>
                  {t("audit.showing", {
                    from: offset + 1,
                    to: offset + rows.length,
                  })}
                  {rows.length === PAGE_SIZE
                    ? `  ${t("audit.moreAvailable")}`
                    : ""}
                </span>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={offset === 0}
                    onClick={() =>
                      setOffset((o) => Math.max(0, o - PAGE_SIZE))
                    }
                  >
                    {t("common.previous")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={rows.length < PAGE_SIZE}
                    onClick={() => setOffset((o) => o + PAGE_SIZE)}
                  >
                    {t("common.next")}
                  </Button>
                </div>
              </div>
            </>
          )}
        </Card>
      </main>
    </>
  );
}

function AuditRow({
  row,
  open,
  onToggle,
  systemLabel,
  beforeLabel,
  afterLabel,
  youLabel,
  currentUserId,
  t,
}: {
  row: AuditLogEntry;
  open: boolean;
  onToggle: () => void;
  systemLabel: string;
  beforeLabel: string;
  afterLabel: string;
  youLabel: string;
  currentUserId: string | null;
  t: (k: keyof import("@/lib/i18n/messages").Messages) => string;
}) {
  const hasDiff =
    row.before_json !== null ||
    (row.after_json !== null && Object.keys(row.after_json).length > 0);
  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        className="grid w-full grid-cols-[auto_140px_180px_1fr_180px] items-center gap-3 px-2 py-3 text-left transition duration-150 hover:bg-overlay"
      >
        <span className="text-text-muted">
          {hasDiff ? (
            open ? (
              <ChevronDownIcon size={14} />
            ) : (
              <ChevronRightIcon size={14} />
            )
          ) : (
            <span className="inline-block h-3.5 w-3.5" />
          )}
        </span>
        <time className="font-mono text-xs text-text-muted">
          {new Date(row.created_at).toLocaleString()}
        </time>
        <span className="rounded-sm border border-border-subtle bg-overlay px-2 py-0.5 text-xs font-semibold text-text-secondary">
          {row.action}
        </span>
        <span className="truncate text-sm text-text">
          <code className="text-text-secondary">{row.resource_type}</code>
          {row.resource_id ? (
            <code className="ml-2 text-text-muted">
              {row.resource_id.slice(0, 8)}…
            </code>
          ) : null}
        </span>
        <span className="truncate text-right font-mono text-xs text-text-muted">
          {row.actor_user_id
            ? row.actor_user_id === currentUserId
              ? youLabel
              : `${row.actor_user_id.slice(0, 8)}…`
            : systemLabel}
        </span>
      </button>
      {open && hasDiff ? (
        <div className="grid gap-3 px-8 pb-4 sm:grid-cols-2">
          <JsonBlock label={beforeLabel} value={row.before_json} t={t} />
          <JsonBlock label={afterLabel} value={row.after_json} t={t} />
        </div>
      ) : null}
    </li>
  );
}

function JsonBlock({
  label,
  value,
  t,
}: {
  label: string;
  value: Record<string, unknown> | null;
  t: (k: keyof import("@/lib/i18n/messages").Messages) => string;
}) {
  const text = useMemo(() => {
    if (value === null) return "—";
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }, [value]);
  // Phase ACC (2026-06-01) — quick copy for operators investigating
  // a change. ConfigPanel (ABR) ships the same affordance — keep the
  // gesture consistent across surfaces.
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      toast.success(t("audit.jsonCopied"));
    } catch {
      toast.error(t("audit.jsonCopyFailed"));
    }
  }
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
          {label}
        </span>
        {value !== null ? (
          <button
            type="button"
            onClick={() => void copy()}
            className="rounded-sm border border-border-subtle bg-overlay px-1.5 py-0.5 text-[10px] text-text-muted hover:text-text"
          >
            {t("runDetail.copy")}
          </button>
        ) : null}
      </div>
      <pre
        className={cn(
          "max-h-72 overflow-auto rounded-md border border-border-subtle bg-bg p-3 font-mono text-[11px] leading-snug text-text",
          value === null && "text-text-muted",
        )}
      >
        {text}
      </pre>
    </div>
  );
}
