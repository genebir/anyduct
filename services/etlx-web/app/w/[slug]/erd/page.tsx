"use client";

/**
 * /w/[slug]/erd — Phase AHC. List of saved ERD diagrams (like the
 * pipelines / migrations list). Pick one to open the designer, or create
 * a new one. Client-side store (localStorage) for now (ADR-0089).
 */

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { BoxesIcon, PlusIcon, Trash2Icon } from "lucide-react";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { createDoc, deleteDoc, listDocs, loadDesign, type ErdDoc } from "@/lib/erd-store";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import { relativeTime, absoluteTime } from "@/lib/format-time";

export default function ErdListPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const router = useRouter();
  const { t } = useLocale();
  const [docs, setDocs] = useState<ErdDoc[] | null>(null);

  useEffect(() => {
    setDocs(listDocs(slug));
  }, [slug]);

  const onNew = () => {
    const id = createDoc(slug, `Untitled ${listDocs(slug).length + 1}`);
    router.push(`/w/${slug}/erd/${id}`);
  };

  const onDelete = (id: string) => {
    deleteDoc(slug, id);
    setDocs(listDocs(slug));
  };

  return (
    <div>
      <Header
        title={t("nav.erd")}
        subtitle={ws ? t("common.workspaceSubtitle", { name: ws.name }) : t("common.loadingWorkspace")}
        actions={
          <Button size="sm" variant="secondary" onClick={onNew}>
            <PlusIcon size={14} />
            {t("erdList.new")}
          </Button>
        }
      />
      <div className="p-4">
        {docs === null ? (
          <Card className="p-8 text-center text-sm text-text-muted">{t("common.loading")}</Card>
        ) : docs.length === 0 ? (
          <EmptyState
            icon={<BoxesIcon size={28} />}
            title={t("erdList.emptyTitle")}
            description={t("erdList.emptyDesc")}
          />
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {docs.map((d) => {
              const design = loadDesign(slug, d.id);
              return (
                <Card key={d.id} className="flex items-center justify-between gap-2 p-3">
                  <Link href={`/w/${slug}/erd/${d.id}`} className="min-w-0 flex-1">
                    <div className="truncate font-medium text-text">{d.name}</div>
                    <div className="mt-0.5 text-xs text-text-muted">
                      {t("erdList.tableCount", { n: design.tables.length })} ·{" "}
                      <span title={absoluteTime(new Date(d.updatedAt).toISOString())}>
                        {relativeTime(new Date(d.updatedAt).toISOString(), t)}
                      </span>
                    </div>
                  </Link>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={t("erdList.deleteAria", { name: d.name })}
                    className="hover:text-error"
                    onClick={() => onDelete(d.id)}
                  >
                    <Trash2Icon size={14} />
                  </Button>
                </Card>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
