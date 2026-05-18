"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { BoxesIcon, PlusIcon } from "lucide-react";
import { Header } from "@/components/shell/header";
import { Card, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { useWorkspaces } from "@/components/providers/workspace-provider";
import { ApiError, workspacesApi, type WorkspaceSummary } from "@/lib/api";
import { Input } from "@/components/ui/input";

const PRESET_COLORS = [
  "#FF3D8B",
  "#6366F1",
  "#10B981",
  "#F59E0B",
  "#EC4899",
  "#06B6D4",
  "#8B5CF6",
  "#14B8A6",
];

export default function WorkspacesPage() {
  const { workspaces, setCurrent, refresh } = useWorkspaces();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [color, setColor] = useState(PRESET_COLORS[0]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    // Refresh once on mount in case workspace list changed in another tab.
    void refresh();
  }, [refresh]);

  async function onCreate() {
    if (!name.trim() || !slug.trim()) return;
    setSubmitting(true);
    try {
      const ws = await workspacesApi.create({
        name: name.trim(),
        slug: slug.trim(),
        color_hex: color,
      });
      toast.success(`Created workspace ${ws.name}`);
      await refresh();
      setCurrent(ws.id);
      setCreating(false);
      setName("");
      setSlug("");
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : "Couldn't create workspace.";
      toast.error(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Header
        title="Workspaces"
        subtitle="Choose a workspace to manage its connections, pipelines, and runs."
        actions={
          <Button
            type="button"
            variant="primary"
            size="md"
            onClick={() => setCreating((v) => !v)}
          >
            <PlusIcon size={16} />
            New workspace
          </Button>
        }
      />
      <main className="mx-auto w-full max-w-5xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {creating ? (
          <Card>
            <CardHeader
              title="Create workspace"
              description="Workspaces isolate connections, pipelines, and runs. You become its first owner."
            />
            <div className="grid gap-4 md:grid-cols-2">
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  Name
                </span>
                <Input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Marketing"
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  Slug
                </span>
                <Input
                  value={slug}
                  onChange={(e) => setSlug(e.target.value.toLowerCase())}
                  placeholder="marketing"
                />
              </label>
              <div className="md:col-span-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  Accent color
                </span>
                <div className="mt-2 flex flex-wrap gap-2">
                  {PRESET_COLORS.map((c) => (
                    <button
                      key={c}
                      type="button"
                      aria-label={`Pick color ${c}`}
                      onClick={() => setColor(c)}
                      className="h-8 w-8 rounded-md ring-offset-2 ring-offset-elevated transition duration-150 hover:scale-105"
                      style={{
                        background: c,
                        boxShadow:
                          c === color
                            ? "0 0 0 2px rgb(var(--accent))"
                            : undefined,
                      }}
                    />
                  ))}
                </div>
              </div>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <Button
                variant="ghost"
                onClick={() => setCreating(false)}
                disabled={submitting}
              >
                Cancel
              </Button>
              <Button onClick={onCreate} loading={submitting}>
                Create workspace
              </Button>
            </div>
          </Card>
        ) : null}

        {workspaces.length === 0 ? (
          <Card>
            <EmptyState
              icon={<BoxesIcon size={40} strokeWidth={1.5} />}
              title="No workspaces yet"
              description="Create your first workspace to start building pipelines."
              action={
                <Button onClick={() => setCreating(true)}>
                  <PlusIcon size={16} />
                  New workspace
                </Button>
              }
            />
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {workspaces.map((w) => (
              <WorkspaceCard key={w.id} workspace={w} onSelect={setCurrent} />
            ))}
          </div>
        )}
      </main>
    </>
  );
}

function WorkspaceCard({
  workspace,
  onSelect,
}: {
  workspace: WorkspaceSummary;
  onSelect: (id: string) => void;
}) {
  return (
    <Link
      href={`/w/${workspace.slug}/connections`}
      onClick={() => onSelect(workspace.id)}
      className="group block"
    >
      <Card className="transition duration-200 group-hover:border-border-strong">
        <div className="flex items-center gap-3">
          <span
            className="inline-flex h-10 w-10 items-center justify-center rounded-md text-white"
            style={{ background: workspace.color_hex }}
            aria-hidden
          >
            <BoxesIcon size={18} />
          </span>
          <div className="min-w-0">
            <div className="truncate text-base font-semibold text-text">
              {workspace.name}
            </div>
            <div className="truncate text-xs text-text-secondary">
              {workspace.slug} ·{" "}
              {workspace.role
                ? workspace.role
                : "SuperAdmin bypass"}
            </div>
          </div>
        </div>
      </Card>
    </Link>
  );
}
