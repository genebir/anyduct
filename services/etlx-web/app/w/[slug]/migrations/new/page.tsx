"use client";

/**
 * /w/[slug]/migrations/new — Phase AAN2 (2026-05-29).
 *
 * Dedicated, builder-free migration creator. Saves a plain linear
 * pipeline (source + sink with ``auto_create_table=true``) through
 * the normal pipelines REST so the underlying entity is still a
 * pipeline — but the user never touches the graph builder.
 */

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { ApiError, connectionsApi, pipelinesApi } from "@/lib/api";
import type { ConnectionSummary } from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import { MigrationForm } from "@/components/migrations/migration-form";
import {
  DEFAULT_MIGRATION_FORM,
  buildMigrationConfig,
  validateMigrationForm,
  type MigrationFormData,
} from "@/lib/migration-config";

export default function NewMigrationPage() {
  const router = useRouter();
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();

  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [name, setName] = useState("");
  const [form, setForm] = useState<MigrationFormData>(DEFAULT_MIGRATION_FORM);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    (async () => {
      try {
        const list = await connectionsApi.list(ws.id);
        if (!cancelled) setConnections(list);
      } catch (err) {
        if (!cancelled) {
          toast.error(
            err instanceof ApiError ? err.message : t("connections.loadFailed"),
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws, t]);

  async function onSubmit() {
    if (!ws) return;
    if (!name.trim()) {
      toast.error(t("migrations.errRequired"));
      return;
    }
    const errs = validateMigrationForm(form);
    if (Object.keys(errs).length > 0) {
      toast.error(t("migrations.errRequired"));
      return;
    }
    setSubmitting(true);
    try {
      const config = buildMigrationConfig(name.trim(), form);
      const created = await pipelinesApi.create(ws.id, {
        name: name.trim(),
        config,
      });
      toast.success(t("migrations.saved"));
      router.push(`/w/${slug}/migrations/${created.id}`);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Header
        title={t("migrations.formTitleNew")}
        subtitle={t("migrations.formSubtitleNew")}
      />
      <main className="mx-auto w-full max-w-3xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        <MigrationForm
          name={name}
          onNameChange={setName}
          form={form}
          onChange={setForm}
          connections={connections}
          submitting={submitting}
          onSubmit={onSubmit}
          onCancel={() => router.push(`/w/${slug}/migrations`)}
          submitLabel={t("migrations.new")}
        />
      </main>
    </>
  );
}
