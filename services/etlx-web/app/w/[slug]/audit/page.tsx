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
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
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

const PAGE_SIZE = 100;

export default function AuditPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const [rows, setRows] = useState<AuditLogEntry[] | null>(null);
  const [resourceType, setResourceType] = useState("");
  const [resourceId, setResourceId] = useState("");
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
          limit: PAGE_SIZE,
          offset,
        });
        if (!cancelled) setRows(list);
      } catch (err) {
        if (!cancelled) {
          toast.error(
            err instanceof ApiError ? err.message : "Couldn't load audit log.",
          );
          setRows([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws, resourceType, resourceId, offset]);

  return (
    <>
      <Header
        title="Audit log"
        subtitle={ws ? `Workspace ${ws.name}` : "Loading workspace…"}
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        <Card>
          <CardHeader
            title="Filters"
            description="Server returns rows newest-first; resource type narrows by the row's affected resource."
          />
          <div className="grid gap-4 md:grid-cols-[1fr_2fr_auto]">
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                Resource type
              </span>
              <select
                value={resourceType}
                onChange={(e) => {
                  setResourceType(e.target.value);
                  setOffset(0);
                }}
                className="h-10 rounded-md border border-border-subtle bg-elevated px-3 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
              >
                {RESOURCE_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t === "" ? "All resources" : t}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                Resource ID
              </span>
              <Input
                value={resourceId}
                onChange={(e) => {
                  setResourceId(e.target.value);
                  setOffset(0);
                }}
                placeholder="exact match (UUID)"
              />
            </label>
            <div className="flex items-end justify-end gap-2">
              <Button
                variant="ghost"
                onClick={() => {
                  setResourceType("");
                  setResourceId("");
                  setOffset(0);
                }}
              >
                Reset
              </Button>
            </div>
          </div>
        </Card>

        <Card>
          {rows === null ? (
            <div className="py-12 text-center text-sm text-text-muted">
              Loading…
            </div>
          ) : rows.length === 0 ? (
            <EmptyState
              icon={<ScrollTextIcon size={36} strokeWidth={1.5} />}
              title="No audit rows match"
              description={
                offset > 0
                  ? "Try reducing the offset, or reset the filters."
                  : "Make a change in another tab (create a connection, add a member, etc.) — it'll appear here on next load."
              }
            />
          ) : (
            <>
              <ul className="divide-y divide-border-subtle">
                {rows.map((row) => (
                  <AuditRow
                    key={row.id}
                    row={row}
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
                  Showing {offset + 1}–{offset + rows.length}
                  {rows.length === PAGE_SIZE ? "  (more available)" : ""}
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
                    Previous
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={rows.length < PAGE_SIZE}
                    onClick={() => setOffset((o) => o + PAGE_SIZE)}
                  >
                    Next
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
}: {
  row: AuditLogEntry;
  open: boolean;
  onToggle: () => void;
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
          {row.actor_user_id ? `${row.actor_user_id.slice(0, 8)}…` : "system"}
        </span>
      </button>
      {open && hasDiff ? (
        <div className="grid gap-3 px-8 pb-4 sm:grid-cols-2">
          <JsonBlock label="Before" value={row.before_json} />
          <JsonBlock label="After" value={row.after_json} />
        </div>
      ) : null}
    </li>
  );
}

function JsonBlock({
  label,
  value,
}: {
  label: string;
  value: Record<string, unknown> | null;
}) {
  const text = useMemo(() => {
    if (value === null) return "—";
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }, [value]);
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
        {label}
      </span>
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
