"use client";

/**
 * /w/[slug]/migrations/[id] — Phase AAN2 (2026-05-29).
 *
 * Dedicated edit/detail page for a migration pipeline. Loads the
 * pipeline through ``pipelinesApi.get``, parses the config into
 * form state via ``parseMigrationConfig``, and saves back as a
 * fresh ``PipelineConfig`` JSON.
 *
 * If the parsing fails (the pipeline turns out to be a graph-mode
 * or fan-out pipeline that someone migrated by hand), we bail with
 * a friendly notice that routes the user to the generic pipelines
 * builder instead — we'd rather opt them out than silently lose
 * data through round-trip.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { PlayIcon, Trash2Icon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  ApiError,
  connectionsApi,
  pipelinesApi,
  runsApi,
  type ConnectionSummary,
  type PipelineSummary,
  type RunSummary,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import { MigrationForm } from "@/components/migrations/migration-form";
import {
  buildMigrationConfig,
  parseMigrationConfig,
  validateMigrationForm,
  type MigrationFormData,
} from "@/lib/migration-config";

const RUNS_POLL_MS = 5_000;
const RUNS_LIMIT = 5;

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function formatRelativeTime(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  const now = Date.now();
  const diff = Math.max(0, Math.floor((now - then) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function MigrationDetailPage() {
  const router = useRouter();
  const { slug, id } = useParams<{ slug: string; id: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();

  const [pipeline, setPipeline] = useState<PipelineSummary | null>(null);
  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [form, setForm] = useState<MigrationFormData | null>(null);
  /** ``true`` only when the loaded config wasn't a migration shape —
   *  we render the bail-out card and don't show the form. */
  const [outsideMigrationShape, setOutsideMigrationShape] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // Recent runs panel (Phase AAN4) — close the loop so the user
  // sees the migration in motion without leaving the page.
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [triggering, setTriggering] = useState(false);

  useEffect(() => {
    if (!ws || !id) return;
    let cancelled = false;
    (async () => {
      try {
        const [p, cs] = await Promise.all([
          pipelinesApi.get(ws.id, id),
          connectionsApi.list(ws.id),
        ]);
        if (cancelled) return;
        setPipeline(p);
        setConnections(cs);
        const parsed = parseMigrationConfig(p.current_config_json);
        if (!parsed) {
          setOutsideMigrationShape(true);
          return;
        }
        setForm(parsed);
      } catch (err) {
        if (!cancelled) {
          toast.error(
            err instanceof ApiError ? err.message : t("pipelines.loadFailed"),
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws, id, t]);

  // Recent runs poller (Phase AAN4). Pipeline-scoped + small limit
  // matches the operator's mental model: "what did this migration do
  // recently?". Polls every 5s so a triggered run lands without a
  // page reload — mirrors the runs page's cadence.
  useEffect(() => {
    if (!ws || !id) return;
    let cancelled = false;
    const fetchRuns = async () => {
      try {
        const list = await runsApi.list(ws.id, {
          pipeline_id: id,
          limit: RUNS_LIMIT,
        });
        if (!cancelled) setRuns(list);
      } catch {
        // Soft-fail — don't toast on every poll tick if the network
        // wobbles. The page still shows whatever last landed.
        if (!cancelled && runs === null) setRuns([]);
      }
    };
    void fetchRuns();
    const handle = setInterval(() => void fetchRuns(), RUNS_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ws, id]);

  async function onSubmit() {
    if (!ws || !pipeline || !form) return;
    const errs = validateMigrationForm(form);
    if (Object.keys(errs).length > 0) {
      toast.error(t("migrations.errRequired"));
      return;
    }
    setSubmitting(true);
    try {
      const config = buildMigrationConfig(pipeline.name, form);
      const updated = await pipelinesApi.update(ws.id, pipeline.id, { config });
      setPipeline(updated);
      toast.success(t("migrations.saved"));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function onRunNow() {
    if (!ws || !pipeline) return;
    setTriggering(true);
    try {
      const r = await pipelinesApi.trigger(ws.id, pipeline.id);
      toast.success(t("migrations.runQueued"));
      // Optimistic insert so the user sees the run row immediately;
      // the next poll tick reconciles with the server truth.
      setRuns((prev) => (prev ? [r, ...prev].slice(0, RUNS_LIMIT) : [r]));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    } finally {
      setTriggering(false);
    }
  }

  async function onDelete() {
    if (!ws || !pipeline) return;
    setDeleting(true);
    try {
      await pipelinesApi.delete(ws.id, pipeline.id);
      toast.success(t("migrations.deleted"));
      router.push(`/w/${slug}/migrations`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  }

  return (
    <>
      <Header
        title={pipeline?.name ?? t("migrations.formTitleEdit")}
        subtitle={t("migrations.formSubtitleEdit")}
        actions={
          pipeline && !outsideMigrationShape ? (
            <>
              <Button
                size="sm"
                loading={triggering}
                disabled={!pipeline.current_version}
                onClick={() => void onRunNow()}
                title={
                  pipeline.current_version
                    ? undefined
                    : t("migrations.saveBeforeRun")
                }
              >
                <PlayIcon size={14} />
                {t("migrations.runNow")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2Icon size={14} />
                {t("migrations.delete")}
              </Button>
            </>
          ) : pipeline ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2Icon size={14} />
              {t("migrations.delete")}
            </Button>
          ) : null
        }
      />
      <main className="mx-auto w-full max-w-4xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {outsideMigrationShape && pipeline ? (
          <Card>
            <p className="text-sm text-text-secondary">
              {t("migrations.notMigration")}
            </p>
            <div className="mt-3">
              <Link href={`/w/${slug}/pipelines/${pipeline.id}/edit`}>
                <Button size="sm" variant="secondary">
                  {t("migrations.openInPipelines")}
                </Button>
              </Link>
            </div>
          </Card>
        ) : (
          <>
            <MigrationForm
              workspaceId={ws?.id ?? ""}
              name={pipeline?.name ?? ""}
              onNameChange={() => {
                /* Name is locked on edit — rename lives on the
                 * pipelines page (a migration-specific rename would
                 * just duplicate that surface). */
              }}
              form={form}
              onChange={setForm}
              connections={connections}
              nameLocked
              submitting={submitting}
              onSubmit={onSubmit}
              onCancel={() => router.push(`/w/${slug}/migrations`)}
              submitLabel={t("common.save")}
            />
            <RecentRunsCard
              runs={runs}
              slug={slug}
              t={t}
              emptyHint={t("migrations.runsEmpty")}
              title={t("migrations.recentRuns")}
            />
          </>
        )}
      </main>
      <ConfirmDialog
        open={confirmDelete}
        title={t("migrations.delete")}
        description={t("migrations.deleteConfirm")}
        confirmLabel={t("common.delete")}
        destructive
        loading={deleting}
        onConfirm={() => void onDelete()}
        onCancel={() => setConfirmDelete(false)}
      />
    </>
  );
}

function RecentRunsCard({
  runs,
  slug,
  t,
  title,
  emptyHint,
}: {
  runs: RunSummary[] | null;
  slug: string;
  t: (k: never) => string;
  title: string;
  emptyHint: string;
}) {
  const tx = t as unknown as (k: string) => string;
  return (
    <Card>
      <div className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
        {title}
      </div>
      <div className="mt-3">
        {runs === null ? (
          <p className="text-xs text-text-muted">{tx("common.loading")}</p>
        ) : runs.length === 0 ? (
          <p className="text-xs text-text-muted">{emptyHint}</p>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {runs.map((r) => (
              <li
                key={r.id}
                className="flex items-center gap-3 py-2 text-sm"
              >
                <StatusBadge status={r.status} />
                <Link
                  href={`/w/${slug}/runs/${r.id}`}
                  className="flex-1 truncate font-mono text-xs text-text-secondary hover:text-accent"
                >
                  {r.id.slice(0, 8)}
                </Link>
                <span className="text-xs tabular-nums text-text-muted">
                  {r.records_written.toLocaleString()} {tx("migrations.runRowsWritten")}
                </span>
                <span className="text-xs tabular-nums text-text-muted">
                  {formatDuration(r.duration_seconds)}
                </span>
                <span className="w-20 text-right text-xs text-text-muted">
                  {formatRelativeTime(r.finished_at ?? r.started_at ?? r.created_at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}
