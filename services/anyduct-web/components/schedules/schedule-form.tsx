"use client";

import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import { RequiredMark } from "@/components/ui/required-mark";
import { CronInput } from "./cron-input";
import {
  ApiError,
  schedulesApi,
  type PipelineMode,
  type ScheduleSummary,
} from "@/lib/api";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";
import { Checkbox } from "@/components/ui/checkbox";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

type Mode = PipelineMode;

interface CommonProps {
  workspaceId: string;
  pipelineId: string;
  onSaved: (s: ScheduleSummary) => void;
  onCancel: () => void;
}

export function ScheduleCreateForm({
  workspaceId,
  pipelineId,
  onSaved,
  onCancel,
}: CommonProps) {
  const { t } = useLocale();
  const [name, setName] = useState("");
  const [mode, setMode] = useState<Mode>("batch");
  const [cron, setCron] = useState("0 3 * * *");
  const [active, setActive] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!name.trim()) {
      toast.error(t("schedForm.nameRequired"));
      return;
    }
    const cronExpr = mode === "batch" ? cron.trim() : cron.trim() || null;
    if (mode === "batch" && !cronExpr) {
      toast.error(t("schedForm.cronRequired"));
      return;
    }
    setSubmitting(true);
    try {
      const created = await schedulesApi.create(workspaceId, pipelineId, {
        name: name.trim(),
        mode,
        cron_expr: cronExpr,
        is_active: active,
      });
      toast.success(t("schedForm.created", { name: created.name }));
      onSaved(created);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("schedForm.createFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={t("schedForm.newTitle")}
        description={t("schedForm.newDesc")}
      />
      <form onSubmit={onSubmit} className="grid gap-4 md:grid-cols-2">
        <FieldRow label={t("common.name")} required className="md:col-span-2">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("schedForm.namePlaceholder")}
            required
          />
        </FieldRow>
        <FieldRow label={t("common.mode")} className="md:col-span-2">
          <ModePicker value={mode} onChange={setMode} t={t} />
        </FieldRow>
        <FieldRow
          label={t("schedForm.cron")}
          required
          help={
            mode === "stream"
              ? t("schedForm.cronHelpStream")
              : t("schedForm.cronHelpBatch")
          }
          className="md:col-span-2"
        >
          <CronInput value={cron} onChange={setCron} allowEmpty={mode === "stream"} />
        </FieldRow>
        <FieldRow label={t("common.active")} className="md:col-span-2">
          <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-text">
            <Checkbox

              checked={active}
              onChange={(e) => setActive(e.target.checked)}
            />
            {t("schedForm.activeDesc")}
          </label>
        </FieldRow>
        <div className="flex justify-end gap-2 pt-2 md:col-span-2">
          <Button variant="ghost" type="button" onClick={onCancel} disabled={submitting}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" loading={submitting}>
            {t("schedForm.create")}
          </Button>
        </div>
      </form>
    </Card>
  );
}

export function ScheduleEditForm({
  workspaceId,
  pipelineId,
  existing,
  onSaved,
  onCancel,
}: CommonProps & { existing: ScheduleSummary }) {
  const { t } = useLocale();
  const [name, setName] = useState(existing.name);
  const [cron, setCron] = useState(existing.cron_expr ?? "");
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSubmitting(true);
    try {
      const body: Record<string, unknown> = {};
      if (name.trim() !== existing.name) body.name = name.trim();
      const nextCron =
        existing.mode === "batch" ? cron.trim() : cron.trim() || null;
      if (nextCron !== (existing.cron_expr ?? "")) body.cron_expr = nextCron;
      if (Object.keys(body).length === 0) {
        onCancel();
        return;
      }
      const updated = await schedulesApi.update(
        workspaceId,
        pipelineId,
        existing.id,
        body,
      );
      toast.success(t("schedForm.updated", { name: updated.name }));
      onSaved(updated);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("schedForm.updateFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={t("schedForm.editTitle", { name: existing.name })}
        description={t("schedForm.editDesc", { mode: existing.mode })}
      />
      <form onSubmit={onSubmit} className="grid gap-4 md:grid-cols-2">
        <FieldRow label={t("common.name")} required className="md:col-span-2">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </FieldRow>
        <FieldRow
          label={t("schedForm.cron")}
          required
          help={t("schedForm.cronHelpEdit")}
          className="md:col-span-2"
        >
          <CronInput
            value={cron}
            onChange={setCron}
            allowEmpty={existing.mode === "stream"}
          />
        </FieldRow>
        <div className="flex justify-end gap-2 pt-2 md:col-span-2">
          <Button variant="ghost" type="button" onClick={onCancel} disabled={submitting}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" loading={submitting}>
            {t("common.save")}
          </Button>
        </div>
      </form>
    </Card>
  );
}

function FieldRow({
  label,
  help,
  required,
  className,
  children,
}: {
  label: string;
  help?: string;
  required?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={cn("flex flex-col gap-1.5", className)}>
      <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
        {label}
        {required ? <RequiredMark /> : null}
      </span>
      {children}
      {help ? <span className="text-[11px] text-text-muted">{help}</span> : null}
    </label>
  );
}

function ModePicker({
  value,
  onChange,
  t,
}: {
  value: Mode;
  onChange: (m: Mode) => void;
  t: Translate;
}) {
  return (
    <div className="flex gap-2">
      {(["batch", "stream"] as const).map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => onChange(m)}
          className={cn(
            "rounded-md border px-3 py-2 text-sm transition duration-150",
            value === m
              ? "border-accent bg-overlay text-text"
              : "border-border-subtle text-text-secondary hover:border-border-strong hover:text-text",
          )}
        >
          <div className="font-medium">{m}</div>
          <div className="text-[11px] text-text-muted">
            {m === "batch"
              ? t("schedForm.modeBatch")
              : t("schedForm.modeStream")}
          </div>
        </button>
      ))}
    </div>
  );
}
