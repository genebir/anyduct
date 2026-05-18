"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { CalendarClockIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import {
  ApiError,
  pipelinesApi,
  schedulesApi,
  type PipelineSummary,
  type ScheduleSummary,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";

interface ScheduleRow extends ScheduleSummary {
  pipeline_name: string;
}

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
  const [rows, setRows] = useState<ScheduleRow[] | null>(null);

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    (async () => {
      try {
        const pipelines: PipelineSummary[] = await pipelinesApi.list(ws.id);
        const groups = await Promise.all(
          pipelines.map(async (p) => {
            const list = await schedulesApi.list(ws.id, p.id);
            return list.map((s) => ({ ...s, pipeline_name: p.name }));
          }),
        );
        if (!cancelled) setRows(groups.flat());
      } catch (err) {
        if (!cancelled) {
          toast.error(
            err instanceof ApiError ? err.message : "Couldn't load schedules.",
          );
          setRows([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws]);

  return (
    <>
      <Header
        title="Schedules"
        subtitle={ws ? `Workspace ${ws.name}` : "Loading workspace…"}
      />
      <main className="mx-auto w-full max-w-6xl space-y-6 px-6 py-8">
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
                  icon={<CalendarClockIcon size={36} strokeWidth={1.5} />}
                  title="No schedules yet"
                  description="Attach a cron schedule to a pipeline to have it run automatically. The scheduler enqueues a Run as soon as the next firing time arrives."
                />
              }
            />
          )}
        </Card>
      </main>
    </>
  );
}
