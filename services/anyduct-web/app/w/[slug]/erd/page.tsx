"use client";

/**
 * /w/[slug]/erd — Phase AHD. Server-backed list of ERD diagrams (like the
 * pipelines / migrations list). Pick one to open the designer, or create
 * a new one. Persisted via the REST API (ADR-0090), shared across users.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { BoxesIcon, PencilIcon, PlusIcon, Trash2Icon, UploadIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiError, erdApi, type ErdDiagramSummary } from "@/lib/api";
import { EMPTY_DESIGN, type ErdDesign } from "@/lib/erd-design";
import { parseDamxWithAreas } from "@/lib/damx";
import { autoLayout, layoutAreas, removeOverlaps } from "@/lib/erd-layout";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import { absoluteTime, relativeTime } from "@/lib/format-time";

export default function ErdListPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const router = useRouter();
  const { t } = useLocale();
  const [rows, setRows] = useState<ErdDiagramSummary[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState("");

  const refresh = useCallback(
    async (wsId: string) => {
      try {
        setRows(await erdApi.list(wsId));
      } catch (e) {
        // Surface the failure instead of silently showing the empty state —
        // an operator must be able to tell "load failed" from "no diagrams".
        toast.error(e instanceof ApiError ? e.message : t("common.loadFailed"));
        setRows([]);
      }
    },
    [t],
  );

  useEffect(() => {
    if (ws?.id) void refresh(ws.id);
  }, [ws?.id, refresh]);

  const fileRef = useRef<HTMLInputElement | null>(null);
  const [importing, setImporting] = useState(false);

  // Import a .damx as ONE diagram whose subject areas (주제영역) become tabs
  // inside the designer — like DA#'s pane list. Files without multiple named
  // panes import as a plain single-canvas diagram.
  const onDamxFile = async (file: File) => {
    if (!ws?.id || importing) return;
    setImporting(true);
    try {
      const buf = await file.arrayBuffer();
      let design = parseDamxWithAreas(buf);
      if (design.tables.length === 0) {
        toast.error(t("erdList.importEmpty"));
        return;
      }
      const positioned = (design as ErdDesign & { __damxPositioned?: boolean }).__damxPositioned;
      design = positioned ? removeOverlaps(design) : autoLayout(design);
      design = layoutAreas(design); // fill per-tab positions where DA#'s weren't recoverable
      const created = await erdApi.create(ws.id, {
        name: file.name.replace(/\.damx$/i, ""),
        design_json: design,
      });
      toast.success(
        design.areas?.length
          ? t("erdList.importedAreas", { n: design.areas.length })
          : t("erdList.importedSingle"),
      );
      router.push(`/w/${slug}/erd/${created.id}`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : t("erdList.importError"));
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const onNew = async () => {
    if (!ws?.id || creating) return;
    setCreating(true);
    try {
      const created = await erdApi.create(ws.id, {
        name: `Untitled ${(rows?.length ?? 0) + 1}`,
        design_json: EMPTY_DESIGN,
      });
      router.push(`/w/${slug}/erd/${created.id}`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : t("erdList.createError"));
      setCreating(false);
    }
  };

  // Deleting a diagram is irreversible (server-backed, no trash) — confirm.
  const [pendingDelete, setPendingDelete] = useState<ErdDiagramSummary | null>(null);
  const onDelete = async () => {
    if (!ws?.id || !pendingDelete) return;
    const target = pendingDelete;
    setPendingDelete(null);
    try {
      await erdApi.delete(ws.id, target.id);
      await refresh(ws.id);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : t("common.error"));
    }
  };

  const onRename = async (id: string) => {
    const name = renameVal.trim();
    setRenamingId(null);
    if (!ws?.id || !name) return;
    try {
      await erdApi.update(ws.id, id, { name });
      await refresh(ws.id);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : t("common.error"));
    }
  };

  const filtered = (rows ?? []).filter((d) =>
    d.name.toLowerCase().includes(query.trim().toLowerCase()),
  );

  return (
    <div>
      <Header
        title={t("nav.erd")}
        subtitle={ws ? t("common.workspaceSubtitle", { name: ws.name }) : t("common.loadingWorkspace")}
        actions={
          <div className="flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".damx"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void onDamxFile(f);
              }}
            />
            <Button
              size="sm"
              variant="ghost"
              onClick={() => fileRef.current?.click()}
              disabled={importing || !ws?.id}
              title={t("erdList.importHint")}
            >
              <UploadIcon size={14} />
              {importing ? t("erdList.importing") : t("erdList.import")}
            </Button>
            <Button size="sm" variant="secondary" onClick={() => void onNew()} disabled={creating || !ws?.id}>
              <PlusIcon size={14} />
              {t("erdList.new")}
            </Button>
          </div>
        }
      />
      <div className="p-4">
        {rows === null ? (
          <Card className="p-8 text-center text-sm text-text-muted">{t("common.loading")}</Card>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<BoxesIcon size={28} />}
            title={t("erdList.emptyTitle")}
            description={t("erdList.emptyDesc")}
          />
        ) : (
          <>
            {rows.length > 6 ? (
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("erdList.search")}
                className="mb-3 max-w-xs"
              />
            ) : null}
            {filtered.length === 0 ? (
              <p className="py-6 text-center text-sm text-text-muted">{t("common.noResults")}</p>
            ) : (
              <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {filtered.map((d) => (
                  <Card key={d.id} className="flex items-center justify-between gap-2 p-3">
                    {renamingId === d.id ? (
                      <Input
                        autoFocus
                        value={renameVal}
                        onChange={(e) => setRenameVal(e.target.value)}
                        onBlur={() => void onRename(d.id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                          if (e.key === "Escape") setRenamingId(null);
                        }}
                        className="h-8 flex-1 text-sm"
                      />
                    ) : (
                      <Link href={`/w/${slug}/erd/${d.id}`} className="min-w-0 flex-1">
                        <div className="truncate font-medium text-text">{d.name}</div>
                        <div className="mt-0.5 text-xs text-text-muted">
                          {t("erdList.tableCount", { n: d.table_count })} ·{" "}
                          <span title={absoluteTime(d.updated_at)}>
                            {relativeTime(d.updated_at, t)}
                          </span>
                        </div>
                      </Link>
                    )}
                    <div className="flex shrink-0 items-center">
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={t("erdList.renameAria", { name: d.name })}
                        onClick={() => {
                          setRenameVal(d.name);
                          setRenamingId(d.id);
                        }}
                      >
                        <PencilIcon size={14} />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={t("erdList.deleteAria", { name: d.name })}
                        className="hover:text-error"
                        onClick={() => setPendingDelete(d)}
                      >
                        <Trash2Icon size={14} />
                      </Button>
                    </div>
                  </Card>
                ))}
              </div>
            )}
          </>
        )}
      </div>
      <ConfirmDialog
        open={pendingDelete !== null}
        title={t("erdList.deleteTitle", { name: pendingDelete?.name ?? "" })}
        description={t("erdList.deleteDesc", { n: pendingDelete?.table_count ?? 0 })}
        confirmLabel={t("common.delete")}
        destructive
        onConfirm={() => void onDelete()}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
