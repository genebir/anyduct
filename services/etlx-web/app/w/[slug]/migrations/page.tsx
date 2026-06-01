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
import { StatusBadge } from "@/components/ui/status-badge";
import {
  ApiError,
  pipelinesApi,
  runsApi,
  type PipelineSummary,
  type RunSummary,
} from "@/lib/api";
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

type Row = PipelineSummary & {
  migration: MigrationSummary;
  lastRun: RunSummary | null;
};

const RUNS_POLL_MS = 5_000;

function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diff = Math.max(0, Math.floor((now - then) / 1000));
  if (diff < 60) return `${diff}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  return `${Math.floor(diff / 86400)}d`;
}

function strategyChip(
  s: MigrationSummary["strategy"],
  t: Translate,
): { label: string; cls: string } {
  if (s === "snapshot") {
    return {
      label: t("migrations.strategySnapshot"),
      cls: "bg-warning/15 text-warning",
    };
  }
  if (s === "append") {
    return {
      label: t("migrations.strategyAppend"),
      cls: "bg-info/15 text-info",
    };
  }
  if (s === "mirror") {
    return {
      label: t("migrations.strategyMirror"),
      cls: "bg-accent/15 text-accent",
    };
  }
  return { label: "custom", cls: "bg-overlay text-text-muted" };
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
      // Phase AAN3 — direction column reads as "src → dst" so the
      // migration intent is the first thing the operator sees.
      key: "direction",
      header: `${t("migrations.from")} → ${t("migrations.to")}`,
      cell: (r) => (
        <div className="flex items-center gap-1.5 text-xs">
          <span className="font-mono text-text-secondary">
            {r.migration.sourceConnection ?? "—"}
          </span>
          <span className="text-accent">→</span>
          <span className="font-mono text-text-secondary">
            {r.migration.sinkConnection ?? "—"}
            {r.migration.sinkTable ? ` / ${r.migration.sinkTable}` : ""}
          </span>
        </div>
      ),
    },
    {
      key: "strategy",
      header: t("migrations.colStrategy"),
      cell: (r) => {
        const { label, cls } = strategyChip(r.migration.strategy, t);
        return (
          <span
            className={`inline-flex h-5 items-center rounded-sm px-1.5 text-[11px] font-medium ${cls}`}
          >
            {label}
          </span>
        );
      },
    },
    {
      // Phase AAP — health at a glance. "Last run" surfaces both the
      // status (badge color) and how long ago, so the operator can
      // skim the list and spot a stale or failed migration without
      // opening it.
      key: "last_run",
      header: t("migrations.colLastRun"),
      cell: (r) =>
        r.lastRun ? (
          <div className="flex items-center gap-2 text-xs">
            <StatusBadge status={r.lastRun.status} />
            <span className="text-text-muted">
              {relativeTime(
                r.lastRun.finished_at ??
                  r.lastRun.started_at ??
                  r.lastRun.created_at,
              )}
            </span>
          </div>
        ) : (
          <span className="text-xs text-text-muted">
            {t("migrations.neverRun")}
          </span>
        ),
    },
  ];
}

export default function MigrationsPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [rows, setRows] = useState<PipelineSummary[] | null>(null);
  /** Most recent run per pipeline_id. Refreshed every ``RUNS_POLL_MS``
   *  so a Trigger from elsewhere lands on this list without a page
   *  reload. */
  const [lastRunByPipeline, setLastRunByPipeline] = useState<
    Map<string, RunSummary>
  >(new Map());

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

  // Recent runs across the whole workspace, indexed by pipeline_id.
  // Phase AAP: gives every migration row a "last status + when" chip
  // so the operator sees health at a glance without clicking in. We
  // fetch a *workspace-wide* page of runs (single request, no N+1)
  // and let the dict picker pick the most recent per pipeline.
  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    const fetchRuns = async () => {
      try {
        const list = await runsApi.list(ws.id, { limit: 200 });
        if (cancelled) return;
        const m = new Map<string, RunSummary>();
        for (const r of list) {
          const prev = m.get(r.pipeline_id);
          // ``runsApi.list`` orders by created_at desc — the first one
          // we see per pipeline is already the most recent.
          if (!prev) m.set(r.pipeline_id, r);
        }
        setLastRunByPipeline(m);
      } catch {
        // Soft-fail. The list still renders without the status chip.
      }
    };
    void fetchRuns();
    const handle = setInterval(() => void fetchRuns(), RUNS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [ws]);

  // Client-side filter — keeps the page a pure view of the pipelines
  // list. No server endpoint changes.
  const migrationRows = useMemo<Row[]>(() => {
    if (!rows) return [];
    const out: Row[] = [];
    for (const p of rows) {
      const migration = migrationSummaryOf(p.current_config_json);
      if (migration) {
        out.push({
          ...p,
          migration,
          lastRun: lastRunByPipeline.get(p.id) ?? null,
        });
      }
    }
    return out;
  }, [rows, lastRunByPipeline]);

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
