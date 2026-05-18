"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { PlusIcon, WorkflowIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { DataTable, type Column } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiError, pipelinesApi, type PipelineSummary } from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";

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
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const [rows, setRows] = useState<PipelineSummary[] | null>(null);
  const [triggering, setTriggering] = useState<string | null>(null);

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
          <Button variant="primary" size="md" disabled>
            <PlusIcon size={16} />
            New pipeline
          </Button>
        }
      />
      <main className="mx-auto w-full max-w-6xl space-y-6 px-6 py-8">
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
                  className: "w-48 text-right",
                  cell: (row) => (
                    <div className="flex justify-end gap-2">
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
                    <Button disabled>
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
    </>
  );
}
