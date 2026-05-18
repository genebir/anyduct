"use client";

import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import { CronInput } from "./cron-input";
import {
  ApiError,
  schedulesApi,
  type PipelineMode,
  type ScheduleSummary,
} from "@/lib/api";

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
  const [name, setName] = useState("");
  const [mode, setMode] = useState<Mode>("batch");
  const [cron, setCron] = useState("0 3 * * *");
  const [active, setActive] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!name.trim()) {
      toast.error("Schedule name is required.");
      return;
    }
    const cronExpr = mode === "batch" ? cron.trim() : cron.trim() || null;
    if (mode === "batch" && !cronExpr) {
      toast.error("Batch schedules require a cron expression.");
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
      toast.success(`Created ${created.name}`);
      onSaved(created);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't create schedule.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="New schedule"
        description="The cron scheduler enqueues a fresh Run row whenever the next firing time elapses (no catchup)."
      />
      <form onSubmit={onSubmit} className="grid gap-4 md:grid-cols-2">
        <FieldRow label="Name" className="md:col-span-2">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="nightly-refresh"
            required
          />
        </FieldRow>
        <FieldRow label="Mode" className="md:col-span-2">
          <ModePicker value={mode} onChange={setMode} />
        </FieldRow>
        <FieldRow
          label="Cron expression"
          help={
            mode === "stream"
              ? "Stream pipelines run continuously — leave blank unless you want a re-arm cron."
              : "Standard 5-field cron (min hour dom mon dow). UTC."
          }
          className="md:col-span-2"
        >
          <CronInput value={cron} onChange={setCron} allowEmpty={mode === "stream"} />
        </FieldRow>
        <FieldRow label="Active" className="md:col-span-2">
          <label className="inline-flex cursor-pointer items-center gap-2 text-sm text-text">
            <input
              type="checkbox"
              checked={active}
              onChange={(e) => setActive(e.target.checked)}
              className="h-4 w-4 accent-[rgb(var(--accent))]"
            />
            Schedule is active and the scheduler will enqueue Run rows for it.
          </label>
        </FieldRow>
        <div className="flex justify-end gap-2 pt-2 md:col-span-2">
          <Button variant="ghost" type="button" onClick={onCancel} disabled={submitting}>
            Cancel
          </Button>
          <Button type="submit" loading={submitting}>
            Create schedule
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
      toast.success(`Updated ${updated.name}`);
      onSaved(updated);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't update schedule.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={`Edit ${existing.name}`}
        description={`Mode is ${existing.mode} — immutable. To switch, delete + recreate.`}
      />
      <form onSubmit={onSubmit} className="grid gap-4 md:grid-cols-2">
        <FieldRow label="Name" className="md:col-span-2">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </FieldRow>
        <FieldRow
          label="Cron expression"
          help="The next firing time is recomputed from this expression on the next scheduler tick."
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
            Cancel
          </Button>
          <Button type="submit" loading={submitting}>
            Save
          </Button>
        </div>
      </form>
    </Card>
  );
}

function FieldRow({
  label,
  help,
  className,
  children,
}: {
  label: string;
  help?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={cn("flex flex-col gap-1.5", className)}>
      <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
        {label}
      </span>
      {children}
      {help ? <span className="text-[11px] text-text-muted">{help}</span> : null}
    </label>
  );
}

function ModePicker({
  value,
  onChange,
}: {
  value: Mode;
  onChange: (m: Mode) => void;
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
              ? "Cron-driven discrete Runs"
              : "Continuous worker keep-alive"}
          </div>
        </button>
      ))}
    </div>
  );
}
