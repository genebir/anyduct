"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useParams } from "next/navigation";
import { PlusIcon, Trash2Icon, UsersIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DataTable, type Column } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  membershipsApi,
  type MembershipSummary,
  type Role,
} from "@/lib/api";
import { useAuth } from "@/components/providers/auth-provider";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { cn } from "@/lib/cn";

const ROLES: { value: Role; label: string; description: string }[] = [
  {
    value: "owner",
    label: "Owner",
    description: "Full control + manage members.",
  },
  {
    value: "editor",
    label: "Editor",
    description: "Edit connections / pipelines / schedules.",
  },
  {
    value: "runner",
    label: "Runner",
    description: "Trigger runs and test connections.",
  },
  {
    value: "viewer",
    label: "Viewer",
    description: "Read-only access to everything.",
  },
];

const ROLE_BADGE: Record<Role, string> = {
  owner: "text-accent border-accent/40 bg-accent/10",
  editor: "text-info border-info/40 bg-info/10",
  runner: "text-warning border-warning/40 bg-warning/10",
  viewer: "text-text-secondary border-border-subtle bg-overlay",
};

export default function MembersPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { state: authState } = useAuth();
  const [rows, setRows] = useState<MembershipSummary[] | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [newRole, setNewRole] = useState<Role>("viewer");
  const [submitting, setSubmitting] = useState(false);
  const [pendingRemove, setPendingRemove] = useState<MembershipSummary | null>(
    null,
  );
  const [removing, setRemoving] = useState(false);
  const [updating, setUpdating] = useState<string | null>(null);

  const currentUserId =
    authState.kind === "signed-in" ? authState.user.id : null;
  const canManage = ws?.role === "owner" || ws?.role == null; // null = SuperAdmin bypass

  async function refresh(workspaceId: string) {
    try {
      const list = await membershipsApi.list(workspaceId);
      setRows(list);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't load members.",
      );
      setRows([]);
    }
  }

  useEffect(() => {
    if (!ws) return;
    void refresh(ws.id);
  }, [ws]);

  async function onAdd(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!ws || !email.trim()) return;
    setSubmitting(true);
    try {
      await membershipsApi.add(ws.id, { email: email.trim(), role: newRole });
      toast.success(`Added ${email.trim()}`);
      setEmail("");
      setNewRole("viewer");
      setAddOpen(false);
      await refresh(ws.id);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't add member.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  async function onRoleChange(row: MembershipSummary, role: Role) {
    if (!ws || row.role === role) return;
    setUpdating(row.user_id);
    try {
      await membershipsApi.updateRole(ws.id, row.user_id, role);
      toast.success(`${row.email} → ${role}`);
      await refresh(ws.id);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't change role.",
      );
    } finally {
      setUpdating(null);
    }
  }

  async function onConfirmRemove() {
    if (!ws || !pendingRemove) return;
    setRemoving(true);
    try {
      await membershipsApi.remove(ws.id, pendingRemove.user_id);
      toast.success(`Removed ${pendingRemove.email}`);
      setPendingRemove(null);
      await refresh(ws.id);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't remove member.",
      );
    } finally {
      setRemoving(false);
    }
  }

  const columns: Column<MembershipSummary>[] = [
    {
      key: "user",
      header: "User",
      cell: (r) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-text">{r.name}</div>
          <div className="truncate text-xs text-text-muted">{r.email}</div>
        </div>
      ),
    },
    {
      key: "role",
      header: "Role",
      className: "w-48",
      cell: (r) =>
        canManage && r.user_id !== currentUserId ? (
          <select
            value={r.role}
            disabled={updating === r.user_id}
            onChange={(e) => {
              void onRoleChange(r, e.target.value as Role);
            }}
            className="h-8 rounded-md border border-border-subtle bg-elevated px-2 text-xs text-text focus-visible:border-accent focus-visible:outline-none"
          >
            {ROLES.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        ) : (
          <span
            className={cn(
              "inline-flex h-[22px] items-center rounded-sm border px-2 text-xs font-semibold uppercase tracking-wide",
              ROLE_BADGE[r.role],
            )}
          >
            {r.role}
          </span>
        ),
    },
    ...(canManage
      ? [
          {
            key: "actions",
            header: "",
            className: "w-32 text-right",
            cell: (row) =>
              row.user_id === currentUserId ? (
                <span className="text-xs text-text-muted">You</span>
              ) : (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={(e) => {
                    e.stopPropagation();
                    setPendingRemove(row);
                  }}
                  aria-label={`Remove ${row.email}`}
                  className="hover:text-error"
                >
                  <Trash2Icon size={14} />
                </Button>
              ),
          } satisfies Column<MembershipSummary>,
        ]
      : []),
  ];

  return (
    <>
      <Header
        title="Members"
        subtitle={ws ? `Workspace ${ws.name}` : "Loading workspace…"}
        actions={
          canManage ? (
            <Button
              variant="primary"
              size="md"
              onClick={() => setAddOpen((v) => !v)}
            >
              <PlusIcon size={16} />
              Add member
            </Button>
          ) : null
        }
      />
      <main className="mx-auto w-full max-w-5xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {!canManage ? (
          <Card>
            <CardHeader
              title="Read-only"
              description="Only workspace Owners can add, change roles for, or remove members."
            />
          </Card>
        ) : null}

        {addOpen && canManage && ws ? (
          <Card>
            <CardHeader
              title="Add by email"
              description="The user must already have signed in once so their account exists in the metadata DB."
            />
            <form onSubmit={onAdd} className="grid gap-4 md:grid-cols-[2fr_1fr_auto]">
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  Email
                </span>
                <Input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="alice@example.com"
                  required
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  Role
                </span>
                <select
                  value={newRole}
                  onChange={(e) => setNewRole(e.target.value as Role)}
                  className="h-10 rounded-md border border-border-subtle bg-elevated px-3 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
                >
                  {ROLES.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="flex items-end gap-2">
                <Button
                  variant="ghost"
                  type="button"
                  onClick={() => setAddOpen(false)}
                  disabled={submitting}
                >
                  Cancel
                </Button>
                <Button type="submit" loading={submitting}>
                  Add
                </Button>
              </div>
            </form>
            <RoleLegend />
          </Card>
        ) : null}

        <Card>
          {rows === null ? (
            <div className="py-12 text-center text-sm text-text-muted">
              Loading…
            </div>
          ) : (
            <DataTable
              columns={columns}
              rows={rows}
              emptyState={
                <EmptyState
                  icon={<UsersIcon size={36} strokeWidth={1.5} />}
                  title="No members"
                  description="Invite teammates by email once they've signed in at least once."
                />
              }
            />
          )}
        </Card>
      </main>

      <ConfirmDialog
        open={pendingRemove !== null}
        title={
          pendingRemove
            ? `Remove ${pendingRemove.email}?`
            : "Remove member?"
        }
        description="They lose access to this workspace immediately. Re-add at any time if you change your mind."
        confirmLabel="Remove"
        destructive
        loading={removing}
        onConfirm={onConfirmRemove}
        onCancel={() => (removing ? undefined : setPendingRemove(null))}
      />
    </>
  );
}

function RoleLegend() {
  return (
    <dl className="mt-5 grid gap-2 border-t border-border-subtle pt-4 text-xs sm:grid-cols-2">
      {ROLES.map((r) => (
        <div key={r.value} className="flex gap-2">
          <dt
            className={cn(
              "h-fit shrink-0 rounded-sm border px-2 py-0.5 font-semibold uppercase tracking-wide",
              ROLE_BADGE[r.value],
            )}
          >
            {r.label}
          </dt>
          <dd className="text-text-muted">{r.description}</dd>
        </div>
      ))}
    </dl>
  );
}
