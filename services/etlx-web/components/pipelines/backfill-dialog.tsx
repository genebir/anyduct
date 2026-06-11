"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError, pipelinesApi, type PipelineSummary } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useLocale } from "@/components/providers/locale-provider";

/**
 * Backfill dialog (ADR-0039): enqueues a run over a cursor range on the
 * pipeline's source cursor_column. Both bounds optional (open range). Records
 * with value > from and <= to are read. The server 400s if the pipeline has no
 * cursor_column.
 */
export function BackfillDialog({
  open,
  workspaceId,
  pipeline,
  onClose,
}: {
  open: boolean;
  workspaceId: string;
  pipeline: PipelineSummary | null;
  onClose: () => void;
}) {
  const { t } = useLocale();
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [splits, setSplits] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      setFrom("");
      setTo("");
      setSplits("");
    }
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

  async function submit() {
    if (!pipeline) return;
    // Phase P3b (ADR-0095): interior split points turn the range into N
    // parallel sub-runs — boundaries = [from, ...splits, to], one half-open
    // window per consecutive pair. Splitting needs both outer bounds.
    const splitTokens = splits
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (splitTokens.length > 0 && (!from.trim() || !to.trim())) {
      toast.error(t("backfill.splitNeedsBounds"));
      return;
    }
    setSubmitting(true);
    try {
      if (splitTokens.length > 0) {
        const raw = [from.trim(), ...splitTokens, to.trim()];
        // The server requires same-typed boundaries; numeric cursors must go
        // as numbers or "9" < "10" fails the lexicographic increase check.
        const allNumeric = raw.every((v) => /^-?\d+(\.\d+)?$/.test(v));
        const boundaries = allNumeric ? raw.map(Number) : raw;
        const runs = await pipelinesApi.partitionedBackfill(workspaceId, pipeline.id, {
          boundaries,
        });
        toast.success(
          t("backfill.queuedMany", { count: String(runs.length), name: pipeline.name }),
        );
      } else {
        await pipelinesApi.backfill(workspaceId, pipeline.id, {
          cursor_from: from.trim() || null,
          cursor_to: to.trim() || null,
        });
        toast.success(t("backfill.queued", { name: pipeline.name }));
      }
      onClose();
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("backfill.failed"));
    } finally {
      setSubmitting(false);
    }
  }

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
        aria-labelledby="backfill-dialog-title"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-xl border border-border-subtle bg-surface p-6 shadow-lg"
      >
        <h2 id="backfill-dialog-title" className="text-lg font-semibold text-text">
          {t("backfill.title", { name: pipeline.name })}
        </h2>
        <p className="mt-2 text-sm text-text-secondary">{t("backfill.desc")}</p>
        <div className="mt-4 flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
              {t("backfill.from")}
            </span>
            <Input
              value={from}
              placeholder={t("backfill.fromPlaceholder")}
              onChange={(e) => setFrom(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
              {t("backfill.to")}
            </span>
            <Input
              value={to}
              placeholder={t("backfill.toPlaceholder")}
              onChange={(e) => setTo(e.target.value)}
            />
          </label>
          <p className="text-[11px] text-text-muted">{t("backfill.rangeHint")}</p>
          <label className="flex flex-col gap-1">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
              {t("backfill.split")}
            </span>
            <Input
              value={splits}
              placeholder={t("backfill.splitPlaceholder")}
              onChange={(e) => setSplits(e.target.value)}
            />
          </label>
          <p className="text-[11px] text-text-muted">{t("backfill.splitHint")}</p>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            {t("common.cancel")}
          </Button>
          <Button variant="primary" onClick={submit} loading={submitting}>
            {t("backfill.run")}
          </Button>
        </div>
      </div>
    </div>
  );
}
