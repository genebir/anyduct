"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ActivityIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { StatusBadge } from "@/components/ui/status-badge";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiError, runsApi, type RunSummary } from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";

function formatTimestamp(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return d.toLocaleString();
}

function formatDuration(s: number | null): string {
  if (s == null) return "—";
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  if (s < 60) return `${s.toFixed(1)} s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

const COLUMNS: Column<RunSummary>[] = [
  {
    key: "status",
    header: "Status",
    className: "w-32",
    cell: (r) => <StatusBadge status={r.status} />,
  },
  {
    key: "pipeline",
    header: "Pipeline",
    cell: (r) => (
      <span className="font-mono text-xs text-text-secondary">
        {r.pipeline_id.slice(0, 8)}…
      </span>
    ),
  },
  {
    key: "scheduled",
    header: "Scheduled",
    cell: (r) => (
      <span className="text-text-secondary">
        {formatTimestamp(r.scheduled_at)}
      </span>
    ),
  },
  {
    key: "duration",
    header: "Duration",
    cell: (r) => (
      <span className="text-text-secondary">
        {formatDuration(r.duration_seconds)}
      </span>
    ),
  },
  {
    key: "rw",
    header: "Read / Written",
    cell: (r) => (
      <span className="font-mono text-xs text-text-secondary">
        {r.records_read.toLocaleString()} / {r.records_written.toLocaleString()}
      </span>
    ),
  },
  {
    key: "error",
    header: "Error",
    cell: (r) =>
      r.error_class ? (
        <span className="rounded-sm bg-error/10 px-2 py-0.5 font-mono text-xs text-error">
          {r.error_class}
        </span>
      ) : (
        <span className="text-text-muted">—</span>
      ),
  },
];

export default function RunsPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const [rows, setRows] = useState<RunSummary[] | null>(null);

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;

    async function fetchOnce(workspaceId: string) {
      try {
        const list = await runsApi.list(workspaceId, { limit: 100 });
        if (!cancelled) setRows(list);
      } catch (err) {
        if (!cancelled) {
          toast.error(
            err instanceof ApiError ? err.message : "Couldn't load runs.",
          );
          setRows([]);
        }
      }
    }

    void fetchOnce(ws.id);
    // Poll every 5s so a running pipeline visibly progresses.
    const id = window.setInterval(() => {
      void fetchOnce(ws.id);
    }, 5_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [ws]);

  return (
    <>
      <Header
        title="Runs"
        subtitle={ws ? `Workspace ${ws.name}` : "Loading workspace…"}
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        <Card>
          {rows === null ? (
            <div className="py-12 text-center text-sm text-text-muted">
              Loading…
            </div>
          ) : (
            <DataTable
              columns={COLUMNS}
              rows={rows}
              emptyState={
                <EmptyState
                  icon={<ActivityIcon size={36} strokeWidth={1.5} />}
                  title="No runs yet"
                  description="Trigger a pipeline manually or wait for a scheduled run. This view updates every five seconds."
                />
              }
            />
          )}
        </Card>
      </main>
    </>
  );
}
