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
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";
import { cn } from "@/lib/cn";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

const ROLES: { value: Role; labelKey: keyof Messages; descKey: keyof Messages }[] = [
  { value: "owner", labelKey: "roles.owner", descKey: "roles.ownerDesc" },
  { value: "editor", labelKey: "roles.editor", descKey: "roles.editorDesc" },
  { value: "runner", labelKey: "roles.runner", descKey: "roles.runnerDesc" },
  { value: "viewer", labelKey: "roles.viewer", descKey: "roles.viewerDesc" },
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
  const { t } = useLocale();
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
        err instanceof ApiError ? err.message : t("members.loadFailed"),
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
      toast.success(t("members.added", { email: email.trim() }));
      setEmail("");
      setNewRole("viewer");
      setAddOpen(false);
      await refresh(ws.id);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("members.addFailed"),
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
      toast.success(
        t("members.roleChanged", {
          email: row.email,
          role: t(`roles.${role}` as keyof Messages),
        }),
      );
      await refresh(ws.id);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("members.roleChangeFailed"),
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
      toast.success(t("members.removed", { email: pendingRemove.email }));
      setPendingRemove(null);
      await refresh(ws.id);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("members.removeFailed"),
      );
    } finally {
      setRemoving(false);
    }
  }

  const columns: Column<MembershipSummary>[] = [
    {
      key: "user",
      header: t("members.colUser"),
      cell: (r) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-text">
            {r.name}
            {/* Phase ADO (2026-06-04) — mark the current user, matching
                the friendly-self pattern on audit (ABT) and runs (ABU)
                so an admin spots their own row at a glance. */}
            {r.user_id === currentUserId ? (
              <span className="ml-2 inline-flex h-4 items-center rounded-sm bg-accent/15 px-1 text-[10px] font-semibold uppercase text-accent">
                {t("members.you")}
              </span>
            ) : null}
          </div>
          <div className="truncate text-xs text-text-muted">{r.email}</div>
        </div>
      ),
    },
    {
      key: "role",
      header: t("common.role"),
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
                {t(opt.labelKey)}
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
            {t(`roles.${r.role}` as keyof Messages)}
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
                <span className="text-xs text-text-muted">{t("members.you")}</span>
              ) : (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={(e) => {
                    e.stopPropagation();
                    setPendingRemove(row);
                  }}
                  aria-label={t("members.removeAria", { email: row.email })}
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
        title={t("nav.members")}
        subtitle={
          ws
            ? t("common.workspaceSubtitle", { name: ws.name })
            : t("common.loadingWorkspace")
        }
        actions={
          canManage ? (
            <Button
              variant="primary"
              size="md"
              onClick={() => setAddOpen((v) => !v)}
            >
              <PlusIcon size={16} />
              {t("members.add")}
            </Button>
          ) : null
        }
      />
      <main className="mx-auto w-full max-w-5xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {!canManage ? (
          <Card>
            <CardHeader
              title={t("members.readOnly")}
              description={t("members.readOnlyDesc")}
            />
          </Card>
        ) : null}

        {addOpen && canManage && ws ? (
          <Card>
            <CardHeader
              title={t("members.addByEmail")}
              description={t("members.addByEmailDesc")}
            />
            <form onSubmit={onAdd} className="grid gap-4 md:grid-cols-[2fr_1fr_auto]">
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  {t("common.email")}
                </span>
                <Input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder={t("members.emailPlaceholder")}
                  required
                />
              </label>
              <label className="flex flex-col gap-1.5">
                <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
                  {t("common.role")}
                </span>
                <select
                  value={newRole}
                  onChange={(e) => setNewRole(e.target.value as Role)}
                  className="h-10 rounded-md border border-border-subtle bg-elevated px-3 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
                >
                  {ROLES.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {t(opt.labelKey)}
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
                  {t("common.cancel")}
                </Button>
                <Button type="submit" loading={submitting}>
                  {t("common.add")}
                </Button>
              </div>
            </form>
            <RoleLegend t={t} />
          </Card>
        ) : null}

        <Card>
          {rows === null ? (
            <div className="py-12 text-center text-sm text-text-muted">
              {t("common.loading")}
            </div>
          ) : (
            <DataTable
              columns={columns}
              rows={rows}
              emptyState={
                <EmptyState
                  icon={<UsersIcon size={36} strokeWidth={1.5} />}
                  title={t("members.emptyTitle")}
                  description={t("members.emptyDesc")}
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
            ? t("members.removeTitle", { email: pendingRemove.email })
            : t("members.removeTitleFallback")
        }
        description={t("members.removeDesc")}
        confirmLabel={t("common.remove")}
        destructive
        loading={removing}
        onConfirm={onConfirmRemove}
        onCancel={() => (removing ? undefined : setPendingRemove(null))}
      />
    </>
  );
}

function RoleLegend({ t }: { t: Translate }) {
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
            {t(r.labelKey)}
          </dt>
          <dd className="text-text-muted">{t(r.descKey)}</dd>
        </div>
      ))}
    </dl>
  );
}
