"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { CableIcon, PlusIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import {
  ApiError,
  connectionsApi,
  type ConnectionSummary,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";

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

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    (async () => {
      try {
        const list = await connectionsApi.list(ws.id);
        if (!cancelled) setRows(list);
      } catch (err) {
        if (!cancelled) {
          toast.error(
            err instanceof ApiError ? err.message : "Couldn't load connections.",
          );
          setRows([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
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

  return (
    <>
      <Header
        title="Connections"
        subtitle={ws ? `Workspace ${ws.name}` : "Loading workspace…"}
        actions={
          <Button variant="primary" size="md" disabled>
            <PlusIcon size={16} />
            New connection
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
                  className: "w-32 text-right",
                  cell: (row) => (
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
                  ),
                },
              ]}
              rows={rows}
              emptyState={
                <EmptyState
                  icon={<CableIcon size={36} strokeWidth={1.5} />}
                  title="No connections yet"
                  description="Connections store the credentials and host metadata for sources and sinks. Add one through the API or YAML import to get started."
                />
              }
            />
          )}
        </Card>
      </main>
    </>
  );
}
