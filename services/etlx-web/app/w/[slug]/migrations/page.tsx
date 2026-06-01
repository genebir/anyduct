"use client";

/**
 * /w/[slug]/migrations — Phase AAN (2026-05-29) + AAN2 (dedicated form).
 *
 * Dedicated surface for cross-DB migration pipelines. A pipeline
 * counts as a migration here iff at least one of its sinks has
 * ``auto_create_table: true`` (ADR-0066 / 0071 / 0072 — the runtime
 * is on the hook for creating the destination table from the source
 * schema).
 *
 * Migrations don't open in the graph builder — their create / edit
 * lives at ``/migrations/new`` and ``/migrations/[id]`` (Phase AAN2)
 * so the surface stays focused: one source, one sink, four switches.
 * Pipelines builder is reserved for richer shapes (transforms, joins,
 * fan-out, etc.).
 */

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { ArrowRightLeftIcon, EditIcon, PlusIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Button } from "@/components/ui/button";
import { DataTable, type Column } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { ApiError, pipelinesApi, type PipelineSummary } from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";
import {
  type MigrationSummary,
  migrationSummaryOf,
} from "@/lib/migration-utils";

type Translate = (
  key: keyof Messages,
  vars?: Record<string, string | number>,
) => string;

type Row = PipelineSummary & { migration: MigrationSummary };

function ifExistsLabel(
  m: MigrationSummary,
  t: Translate,
): { label: string; tone: "muted" | "warn" | "error" } {
  if (m.ifExists === "drop") {
    return { label: t("migrations.ifExistsDrop"), tone: "warn" };
  }
  if (m.ifExists === "error") {
    return { label: t("migrations.ifExistsError"), tone: "error" };
  }
  return { label: t("migrations.ifExistsSkip"), tone: "muted" };
}

function buildColumns(t: Translate): Column<Row>[] {
  return [
    {
      key: "name",
      header: t("common.pipeline"),
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
      key: "sink",
      header: t("migrations.colSink"),
      cell: (r) => (
        <div className="font-mono text-xs text-text-secondary">
          {r.migration.sinkConnection ?? "—"}
          {r.migration.sinkTable ? ` / ${r.migration.sinkTable}` : ""}
        </div>
      ),
    },
    {
      key: "mode",
      header: t("migrations.colMode"),
      cell: (r) => (
        <span className="inline-flex h-5 items-center rounded-sm bg-overlay px-1.5 text-[11px] uppercase text-text-muted">
          {r.migration.sinkMode ?? "append"}
        </span>
      ),
    },
    {
      key: "if_exists",
      header: t("migrations.colIfExists"),
      cell: (r) => {
        const { label, tone } = ifExistsLabel(r.migration, t);
        const cls =
          tone === "warn"
            ? "text-warning"
            : tone === "error"
              ? "text-error"
              : "text-text-muted";
        return <span className={`text-xs ${cls}`}>{label}</span>;
      },
    },
  ];
}

export default function MigrationsPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [rows, setRows] = useState<PipelineSummary[] | null>(null);

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
            err instanceof ApiError ? err.message : t("pipelines.loadFailed"),
          );
          setRows([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws, t]);

  // Client-side filter — keeps the page a pure view of the pipelines
  // list. No server endpoint changes.
  const migrationRows = useMemo<Row[]>(() => {
    if (!rows) return [];
    const out: Row[] = [];
    for (const p of rows) {
      const migration = migrationSummaryOf(p.current_config_json);
      if (migration) out.push({ ...p, migration });
    }
    return out;
  }, [rows]);

  const columns = buildColumns(t);

  return (
    <>
      <Header
        title={t("migrations.title")}
        subtitle={
          ws ? t("common.workspaceSubtitle", { name: ws.name }) : undefined
        }
        actions={
          <Link href={`/w/${slug}/migrations/new`}>
            <Button size="sm" disabled={!ws}>
              <PlusIcon size={14} />
              {t("migrations.new")}
            </Button>
          </Link>
        }
      />
      <main className="mx-auto w-full max-w-5xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        <p className="text-sm text-text-muted">{t("migrations.desc")}</p>

        {rows === null ? null : migrationRows.length === 0 ? (
          <EmptyState
            icon={<ArrowRightLeftIcon size={28} />}
            title={t("migrations.title")}
            description={t("migrations.empty")}
            action={
              <Link href={`/w/${slug}/migrations/new`}>
                <Button disabled={!ws}>
                  <PlusIcon size={14} />
                  {t("migrations.new")}
                </Button>
              </Link>
            }
          />
        ) : (
          <DataTable
            columns={[
              ...columns,
              {
                key: "actions",
                header: "",
                className: "w-32 text-right",
                cell: (r) => (
                  <Link
                    href={`/w/${slug}/migrations/${r.id}`}
                    aria-label={t("common.edit")}
                  >
                    <Button size="sm" variant="secondary">
                      <EditIcon size={14} />
                      {t("common.edit")}
                    </Button>
                  </Link>
                ),
              },
            ]}
            rows={migrationRows}
          />
        )}
      </main>
    </>
  );
}
