"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useParams, useRouter } from "next/navigation";
import { Trash2Icon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  workspacesApi,
  type WorkspaceSummary,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useWorkspaces } from "@/components/providers/workspace-provider";

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

export default function SettingsPage() {
  const router = useRouter();
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { refresh } = useWorkspaces();
  const isOwner = ws?.role === "owner" || ws?.role == null;

  return (
    <>
      <Header
        title="Settings"
        subtitle={ws ? `Workspace ${ws.name}` : "Loading workspace…"}
      />
      <main className="mx-auto w-full max-w-3xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {ws ? (
          <>
            <ProfileForm
              workspace={ws}
              disabled={!isOwner}
              onSaved={async (updated) => {
                await refresh();
                if (updated.slug !== slug) {
                  router.replace(`/w/${updated.slug}/settings`);
                }
              }}
            />
            <DangerZone workspace={ws} disabled={!isOwner} />
          </>
        ) : (
          <Card>Loading…</Card>
        )}
      </main>
    </>
  );
}

function ProfileForm({
  workspace,
  disabled,
  onSaved,
}: {
  workspace: WorkspaceSummary;
  disabled: boolean;
  onSaved: (updated: WorkspaceSummary) => void;
}) {
  const [name, setName] = useState(workspace.name);
  const [workspaceSlug, setWorkspaceSlug] = useState(workspace.slug);
  const [color, setColor] = useState(workspace.color_hex);
  const [submitting, setSubmitting] = useState(false);

  // Sync when navigating between workspaces while the page stays mounted.
  useEffect(() => {
    setName(workspace.name);
    setWorkspaceSlug(workspace.slug);
    setColor(workspace.color_hex);
  }, [workspace.id]);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (disabled) return;
    const body: Record<string, string> = {};
    if (name.trim() !== workspace.name) body.name = name.trim();
    if (workspaceSlug.trim() !== workspace.slug) body.slug = workspaceSlug.trim();
    if (color !== workspace.color_hex) body.color_hex = color;
    if (Object.keys(body).length === 0) {
      toast.info("Nothing to save.");
      return;
    }
    setSubmitting(true);
    try {
      const updated = await workspacesApi.update(workspace.id, body);
      toast.success(`Saved ${updated.name}`);
      onSaved(updated);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't save workspace.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="Workspace"
        description={
          disabled
            ? "Read-only — only Owners can rename or recolor the workspace."
            : "Renaming changes the URL slug; existing bookmarks will redirect."
        }
      />
      <form onSubmit={onSubmit} className="grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
            Name
          </span>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={disabled}
            required
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
            Slug
          </span>
          <Input
            value={workspaceSlug}
            onChange={(e) => setWorkspaceSlug(e.target.value.toLowerCase())}
            disabled={disabled}
            pattern="^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
            required
          />
        </label>
        <div className="sm:col-span-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
            Accent color
          </span>
          <div className="mt-2 flex flex-wrap gap-2">
            {PRESET_COLORS.map((c) => (
              <button
                key={c}
                type="button"
                aria-label={`Pick color ${c}`}
                onClick={() => !disabled && setColor(c)}
                disabled={disabled}
                className="h-8 w-8 rounded-md ring-offset-2 ring-offset-elevated transition duration-150 hover:scale-105 disabled:cursor-not-allowed disabled:opacity-60"
                style={{
                  background: c,
                  boxShadow:
                    c === color ? "0 0 0 2px rgb(var(--accent))" : undefined,
                }}
              />
            ))}
          </div>
        </div>
        <div className="sm:col-span-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
            Your role
          </span>
          <div className="mt-1 text-sm capitalize text-text">
            {workspace.role ?? "SuperAdmin bypass"}
          </div>
        </div>
        {!disabled ? (
          <div className="flex justify-end gap-2 pt-2 sm:col-span-2">
            <Button type="submit" loading={submitting}>
              Save changes
            </Button>
          </div>
        ) : null}
      </form>
    </Card>
  );
}

function DangerZone({
  workspace,
  disabled,
}: {
  workspace: WorkspaceSummary;
  disabled: boolean;
}) {
  const router = useRouter();
  const { refresh } = useWorkspaces();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  if (disabled) return null;

  async function onDelete() {
    setSubmitting(true);
    try {
      await workspacesApi.delete(workspace.id);
      toast.success(`Deleted ${workspace.name}`);
      await refresh();
      router.replace("/workspaces");
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't delete workspace.",
      );
      setSubmitting(false);
    }
  }

  return (
    <>
      <Card className="border-error/40">
        <CardHeader
          title="Danger zone"
          description="Deleting a workspace removes connections, pipelines, schedules, runs, and audit rows. There's no undo."
        />
        <div className="flex justify-end">
          <Button variant="destructive" onClick={() => setConfirmOpen(true)}>
            <Trash2Icon size={16} />
            Delete workspace
          </Button>
        </div>
      </Card>
      <ConfirmDialog
        open={confirmOpen}
        title={`Delete ${workspace.name}?`}
        description="Everything in this workspace is removed (connections, pipelines, schedules, runs, audit). Confirm you have a backup if you'll need this data later."
        confirmLabel="Delete forever"
        destructive
        loading={submitting}
        onConfirm={onDelete}
        onCancel={() => (submitting ? undefined : setConfirmOpen(false))}
      />
    </>
  );
}
