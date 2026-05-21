"use client";

import { useState } from "react";
import { Trash2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import type { ConnectionSummary } from "@/lib/api";
import type { DlqSettings, RetrySettings } from "@/lib/pipeline-config";
import { useLocale } from "@/components/providers/locale-provider";

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
  variables,
  onChangeRetry,
  onChangeDlq,
  onChangeVariables,
}: {
  retry: RetrySettings;
  dlq: DlqSettings;
  connections: ConnectionSummary[];
  variables: Record<string, unknown>;
  onChangeRetry: (next: RetrySettings) => void;
  onChangeDlq: (next: DlqSettings) => void;
  onChangeVariables: (next: Record<string, unknown>) => void;
}) {
  const { t } = useLocale();
  const enableLabel = t("builder.enable");
  return (
    <aside className="flex w-80 shrink-0 flex-col gap-5 overflow-y-auto border-l border-border-subtle bg-surface px-4 py-5">
      <header>
        <div className="text-[11px] font-semibold uppercase tracking-widest text-text-muted">
          {t("builder.settingsTitle")}
        </div>
        <p className="mt-1 text-xs text-text-secondary">
          {t("builder.settingsDesc")}
        </p>
      </header>

      <Section
        title={t("builder.retryPolicy")}
        enableLabel={enableLabel}
        enabled={retry.enabled}
        onToggle={(v) => onChangeRetry({ ...retry, enabled: v })}
        description={t("builder.retryDesc")}
      >
        <FieldRow label={t("builder.maxAttempts")}>
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
        <FieldRow label={t("builder.backoff")}>
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
            <option value="fixed">{t("builder.fixed")}</option>
            <option value="exponential">{t("builder.exponential")}</option>
          </select>
        </FieldRow>
        <FieldRow
          label={t("builder.initialDelay")}
          help={t("builder.initialDelayHelp")}
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
        title={t("builder.dlq")}
        enableLabel={enableLabel}
        enabled={dlq.enabled}
        onToggle={(v) => onChangeDlq({ ...dlq, enabled: v })}
        description={t("builder.dlqDesc")}
      >
        <FieldRow label={t("common.connection")}>
          <select
            value={dlq.connection}
            disabled={!dlq.enabled}
            onChange={(e) =>
              onChangeDlq({ ...dlq, connection: e.target.value })
            }
            className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none disabled:opacity-60"
          >
            <option value="">{t("builder.selectConnection")}</option>
            {connections.map((c) => (
              <option key={c.id} value={c.name}>
                {c.name} ({c.type})
              </option>
            ))}
          </select>
        </FieldRow>
        <FieldRow label={t("common.table")} help={t("builder.dlqTableHelp")}>
          <Input
            value={dlq.table}
            disabled={!dlq.enabled}
            placeholder={t("builder.dlqTablePlaceholder")}
            onChange={(e) => onChangeDlq({ ...dlq, table: e.target.value })}
          />
        </FieldRow>
        <FieldRow label={t("common.topic")} help={t("builder.dlqTopicHelp")}>
          <Input
            value={dlq.topic}
            disabled={!dlq.enabled}
            placeholder={t("builder.dlqTopicPlaceholder")}
            onChange={(e) => onChangeDlq({ ...dlq, topic: e.target.value })}
          />
        </FieldRow>
        <FieldRow label={t("common.mode")}>
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
            <option value="append">{t("builder.append")}</option>
            <option value="overwrite">{t("builder.overwrite")}</option>
            <option value="upsert">{t("builder.upsert")}</option>
          </select>
        </FieldRow>
      </Section>

      <VariablesEditor variables={variables} onChange={onChangeVariables} />
    </aside>
  );
}

function VariablesEditor({
  variables,
  onChange,
}: {
  variables: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const { t } = useLocale();
  const [name, setName] = useState("");
  const [valueText, setValueText] = useState("");
  const entries = Object.entries(variables);

  function add() {
    const key = name.trim();
    if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(key)) return;
    let value: unknown;
    try {
      value = JSON.parse(valueText);
    } catch {
      value = valueText;
    }
    onChange({ ...variables, [key]: value });
    setName("");
    setValueText("");
  }

  function remove(key: string) {
    const next = { ...variables };
    delete next[key];
    onChange(next);
  }

  return (
    <section className="flex flex-col gap-3 rounded-md border border-border-subtle p-3">
      <header>
        <h3 className="text-sm font-semibold text-text">{t("builder.variables")}</h3>
        <p className="mt-1 text-[11px] text-text-muted">{t("builder.variablesDesc")}</p>
      </header>
      {entries.length > 0 ? (
        <ul className="flex flex-col gap-1.5">
          {entries.map(([key, value]) => (
            <li key={key} className="flex items-center gap-2 text-sm">
              <code className="text-text">{`\${var.${key}}`}</code>
              <span className="min-w-0 flex-1 truncate text-text-secondary">
                {JSON.stringify(value)}
              </span>
              <button
                type="button"
                aria-label={t("variables.deleteAria", { name: key })}
                onClick={() => remove(key)}
                className="text-text-muted hover:text-error"
              >
                <Trash2Icon size={14} />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-col gap-2">
        <div className="grid grid-cols-2 gap-2">
          <FieldRow label={t("variables.name")}>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="min_id" />
          </FieldRow>
          <FieldRow label={t("variables.value")}>
            <Input
              value={valueText}
              onChange={(e) => setValueText(e.target.value)}
              placeholder="100"
            />
          </FieldRow>
        </div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={add}
          className="w-full whitespace-nowrap"
        >
          {t("common.add")}
        </Button>
      </div>
    </section>
  );
}

function Section({
  title,
  description,
  enabled,
  onToggle,
  enableLabel,
  children,
}: {
  title: string;
  description?: string;
  enabled: boolean;
  onToggle: (v: boolean) => void;
  enableLabel: string;
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
          {enableLabel}
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
