"use client";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import type { ConnectionSummary } from "@/lib/api";
import type { DlqSettings, RetrySettings } from "@/lib/pipeline-config";

/**
 * Pipeline-level settings — retry policy + DLQ routing.
 *
 * Shown in the right-side panel when no node is selected. These map directly
 * to the core ``PipelineConfig.retry`` / ``PipelineConfig.dlq`` schemas
 * (etl_plugins.config.models). Toggles are explicit so a pipeline without
 * either knob serializes cleanly (no empty objects in the saved JSON).
 */
export function PipelineSettingsPanel({
  retry,
  dlq,
  connections,
  onChangeRetry,
  onChangeDlq,
}: {
  retry: RetrySettings;
  dlq: DlqSettings;
  connections: ConnectionSummary[];
  onChangeRetry: (next: RetrySettings) => void;
  onChangeDlq: (next: DlqSettings) => void;
}) {
  return (
    <aside className="flex w-80 shrink-0 flex-col gap-5 overflow-y-auto border-l border-border-subtle bg-surface px-4 py-5">
      <header>
        <div className="text-[11px] font-semibold uppercase tracking-widest text-text-muted">
          Pipeline settings
        </div>
        <p className="mt-1 text-xs text-text-secondary">
          Pipeline-wide policies. Click a node on the canvas to edit its fields
          instead.
        </p>
      </header>

      <Section
        title="Retry policy"
        enabled={retry.enabled}
        onToggle={(v) => onChangeRetry({ ...retry, enabled: v })}
        description="Wraps task execution with tenacity; on failure the task is re-attempted before the run is marked failed."
      >
        <FieldRow label="Max attempts">
          <Input
            type="number"
            min={1}
            max={20}
            value={retry.max_attempts}
            disabled={!retry.enabled}
            onChange={(e) =>
              onChangeRetry({
                ...retry,
                max_attempts: Number(e.target.value) || 1,
              })
            }
          />
        </FieldRow>
        <FieldRow label="Backoff">
          <select
            value={retry.backoff}
            disabled={!retry.enabled}
            onChange={(e) =>
              onChangeRetry({
                ...retry,
                backoff: e.target.value as RetrySettings["backoff"],
              })
            }
            className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none disabled:opacity-60"
          >
            <option value="fixed">fixed</option>
            <option value="exponential">exponential</option>
          </select>
        </FieldRow>
        <FieldRow
          label="Initial delay (s)"
          help="First attempt waits this long after failure; exponential doubles per attempt."
        >
          <Input
            type="number"
            min={0}
            step={0.5}
            value={retry.initial_delay_seconds}
            disabled={!retry.enabled}
            onChange={(e) =>
              onChangeRetry({
                ...retry,
                initial_delay_seconds: Number(e.target.value) || 0,
              })
            }
          />
        </FieldRow>
      </Section>

      <Section
        title="Dead-letter queue"
        enabled={dlq.enabled}
        onToggle={(v) => onChangeDlq({ ...dlq, enabled: v })}
        description="Records that fail transform are routed here instead of failing the whole run."
      >
        <FieldRow label="Connection">
          <select
            value={dlq.connection}
            disabled={!dlq.enabled}
            onChange={(e) =>
              onChangeDlq({ ...dlq, connection: e.target.value })
            }
            className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none disabled:opacity-60"
          >
            <option value="">— Select a connection —</option>
            {connections.map((c) => (
              <option key={c.id} value={c.name}>
                {c.name} ({c.type})
              </option>
            ))}
          </select>
        </FieldRow>
        <FieldRow
          label="Table"
          help="Batch sink target. Leave blank if using a stream-based DLQ."
        >
          <Input
            value={dlq.table}
            disabled={!dlq.enabled}
            placeholder="dlq.records_failed"
            onChange={(e) => onChangeDlq({ ...dlq, table: e.target.value })}
          />
        </FieldRow>
        <FieldRow
          label="Topic"
          help="Stream sink target. Leave blank if using a batch DLQ."
        >
          <Input
            value={dlq.topic}
            disabled={!dlq.enabled}
            placeholder="dlq.failed-records"
            onChange={(e) => onChangeDlq({ ...dlq, topic: e.target.value })}
          />
        </FieldRow>
        <FieldRow label="Mode">
          <select
            value={dlq.mode}
            disabled={!dlq.enabled}
            onChange={(e) =>
              onChangeDlq({
                ...dlq,
                mode: e.target.value as DlqSettings["mode"],
              })
            }
            className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none disabled:opacity-60"
          >
            <option value="append">append</option>
            <option value="overwrite">overwrite</option>
            <option value="upsert">upsert</option>
          </select>
        </FieldRow>
      </Section>
    </aside>
  );
}

function Section({
  title,
  description,
  enabled,
  onToggle,
  children,
}: {
  title: string;
  description?: string;
  enabled: boolean;
  onToggle: (v: boolean) => void;
  children: React.ReactNode;
}) {
  return (
    <section
      className={cn(
        "flex flex-col gap-3 rounded-md border p-3",
        enabled ? "border-border-default" : "border-border-subtle bg-elevated/40",
      )}
    >
      <header className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-text">{title}</h3>
        <label className="inline-flex items-center gap-2 text-xs text-text-secondary">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => onToggle(e.target.checked)}
            className="h-4 w-4 accent-[rgb(var(--accent))]"
          />
          Enable
        </label>
      </header>
      {description ? (
        <p className="text-[11px] text-text-muted">{description}</p>
      ) : null}
      <div className={cn("flex flex-col gap-3", !enabled && "opacity-70")}>
        {children}
      </div>
    </section>
  );
}

function FieldRow({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
        {label}
      </span>
      {children}
      {help ? <span className="text-[10px] text-text-muted">{help}</span> : null}
    </label>
  );
}
