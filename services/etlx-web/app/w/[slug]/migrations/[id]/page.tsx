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
import { Trash2Icon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  connectionsApi,
  pipelinesApi,
  type ConnectionSummary,
  type PipelineSummary,
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
          pipeline ? (
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
