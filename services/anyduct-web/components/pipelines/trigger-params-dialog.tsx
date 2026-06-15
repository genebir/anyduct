"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, pipelinesApi, type PipelineSummary, type RunSummary } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useLocale } from "@/components/providers/locale-provider";

/**
 * Trigger-with-parameters dialog (자유도 1단계).
 *
 * When a pipeline declares ``params`` (defaults in its config), the
 * operator can override them per run before triggering — the values are
 * templated into the config via the ``{{ params.x }}`` layer at
 * execution. Pipelines with no declared params skip this dialog and
 * trigger in one click (the caller checks before opening).
 *
 * Each value is parsed as JSON when possible (so ``100`` → number,
 * ``["a"]`` → list), else kept as the raw string — matching the CLI's
 * ``--param`` semantics.
 */
export function TriggerParamsDialog({
  open,
  workspaceId,
  pipeline,
  onClose,
  onTriggered,
}: {
  open: boolean;
  workspaceId: string;
  pipeline: PipelineSummary | null;
  onClose: () => void;
  onTriggered?: (run: RunSummary) => void;
}) {
  const { t } = useLocale();
  const declared = ((pipeline?.current_config_json as { params?: Record<string, unknown> } | null)
    ?.params ?? {}) as Record<string, unknown>;
  const [values, setValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      // Seed each editable field with the declared default (stringified).
      const seed: Record<string, string> = {};
      for (const [k, v] of Object.entries(declared)) {
        seed[k] = typeof v === "string" ? v : JSON.stringify(v);
      }
      setValues(seed);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, pipeline?.id]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open || !pipeline) return null;

  function parseValue(raw: string): unknown {
    try {
      return JSON.parse(raw);
    } catch {
      return raw;
    }
  }

  async function submit() {
    if (!pipeline) return;
    setSubmitting(true);
    try {
      const params: Record<string, unknown> = {};
      for (const [k, raw] of Object.entries(values)) params[k] = parseValue(raw);
      const run = await pipelinesApi.trigger(workspaceId, pipeline.id, params);
      toast.success(t("pipelines.runQueued", { name: pipeline.name }));
      onTriggered?.(run);
      onClose();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("pipelines.triggerFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  const keys = Object.keys(declared);

  return (
    <div
      className={cn(
        "fixed inset-0 z-50 flex items-center justify-center px-4",
        "bg-[rgb(10_18_40_/_0.6)] backdrop-blur-md",
      )}
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="trigger-params-title"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-xl border border-border-subtle bg-surface p-6 shadow-lg"
      >
        <h2 id="trigger-params-title" className="text-lg font-semibold text-text">
          {t("triggerParams.title", { name: pipeline.name })}
        </h2>
        <p className="mt-2 text-sm text-text-secondary">{t("triggerParams.desc")}</p>
        <div className="mt-4 flex max-h-[50vh] flex-col gap-3 overflow-y-auto">
          {keys.map((k) => (
            <label key={k} className="flex flex-col gap-1">
              <span className="font-mono text-[12px] text-text-secondary">{k}</span>
              <Input
                value={values[k] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [k]: e.target.value }))}
              />
            </label>
          ))}
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" onClick={submit} loading={submitting}>
            {t("triggerParams.run")}
          </Button>
        </div>
      </div>
    </div>
  );
}
