"use client";

import { useState } from "react";
import { Trash2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import type { ConnectionSummary, PipelineSummary } from "@/lib/api";
import type { DlqSettings, RetrySettings } from "@/lib/pipeline-config";
import { useLocale } from "@/components/providers/locale-provider";
import { type VarType, inferType } from "@/lib/variable-types";
import { Checkbox } from "@/components/ui/checkbox";

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
  triggers,
  pipelines,
  onChangeRetry,
  onChangeDlq,
  onChangeVariables,
  onChangeTriggers,
}: {
  retry: RetrySettings;
  dlq: DlqSettings;
  connections: ConnectionSummary[];
  variables: Record<string, unknown>;
  /** Downstream pipeline ids (ADR-0029 call-pipeline). Surfaced here instead
   *  of as canvas nodes since they're orchestration metadata, not dataflow. */
  triggers?: string[];
  /** Other pipelines in the workspace (the current one is filtered out by
   *  the caller). Used to populate the downstream-trigger dropdown. */
  pipelines?: PipelineSummary[];
  onChangeRetry: (next: RetrySettings) => void;
  onChangeDlq: (next: DlqSettings) => void;
  onChangeVariables: (next: Record<string, unknown>) => void;
  onChangeTriggers?: (next: string[]) => void;
}) {
  const { t } = useLocale();
  const enableLabel = t("builder.enable");
  // Phase AFQ (2026-06-04) — a DLQ sink uses ``table`` (BatchSink) OR
  // ``topic`` (StreamSink), never both. Show only the field that matches
  // the selected connection's type so a sqlite DLQ doesn't display an
  // irrelevant "topic" box (and vice versa for Kafka). Stream connector
  // types per operators.ts (currently kafka). Default to table when no
  // connection is picked yet — RDBMS DLQs are the common case.
  const dlqConnType = connections.find((c) => c.name === dlq.connection)?.type;
  const dlqIsStream = dlqConnType === "kafka";
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
        {/* Phase AFQ — table (BatchSink) vs topic (StreamSink) by type. */}
        {dlqIsStream ? (
          <FieldRow label={t("common.topic")} help={t("builder.dlqTopicHelp")}>
            <Input
              value={dlq.topic}
              disabled={!dlq.enabled}
              placeholder={t("builder.dlqTopicPlaceholder")}
              onChange={(e) => onChangeDlq({ ...dlq, topic: e.target.value })}
            />
          </FieldRow>
        ) : (
          <FieldRow label={t("common.table")} help={t("builder.dlqTableHelp")}>
            <Input
              value={dlq.table}
              disabled={!dlq.enabled}
              placeholder={t("builder.dlqTablePlaceholder")}
              onChange={(e) => onChangeDlq({ ...dlq, table: e.target.value })}
            />
          </FieldRow>
        )}
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

      {onChangeTriggers && pipelines ? (
        <TriggersEditor
          triggers={triggers ?? []}
          pipelines={pipelines}
          onChange={onChangeTriggers}
        />
      ) : null}
    </aside>
  );
}

/** Downstream pipeline triggers — ADR-0029 call-pipeline lives here in
 *  graph-only mode (no canvas node). Each entry queues a run of the
 *  targeted pipeline when this one succeeds. */
function TriggersEditor({
  triggers,
  pipelines,
  onChange,
}: {
  triggers: string[];
  pipelines: PipelineSummary[];
  onChange: (next: string[]) => void;
}) {
  const { t } = useLocale();
  const [pick, setPick] = useState("");
  const byId = new Map(pipelines.map((p) => [p.id, p]));
  const available = pipelines.filter((p) => !triggers.includes(p.id));

  function add() {
    if (!pick || triggers.includes(pick)) return;
    onChange([...triggers, pick]);
    setPick("");
  }

  function remove(pid: string) {
    onChange(triggers.filter((t) => t !== pid));
  }

  return (
    <section className="flex flex-col gap-3 rounded-md border border-border-subtle p-3">
      <header>
        <h3 className="text-sm font-semibold text-text">{t("triggers.title")}</h3>
        <p className="mt-1 text-[11px] text-text-muted">{t("triggers.desc")}</p>
      </header>
      {triggers.length > 0 ? (
        <ul className="flex flex-col gap-1.5">
          {triggers.map((pid) => {
            const p = byId.get(pid);
            return (
              <li key={pid} className="flex items-center gap-2 text-sm">
                <span className="min-w-0 flex-1 truncate text-text">
                  {p?.name ?? pid}
                </span>
                <button
                  type="button"
                  aria-label={t("triggers.removeAria", { name: p?.name ?? pid })}
                  onClick={() => remove(pid)}
                  className="text-text-muted hover:text-error"
                >
                  <Trash2Icon size={14} />
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
      <div className="flex gap-2">
        <select
          value={pick}
          onChange={(e) => setPick(e.target.value)}
          className="h-10 min-w-0 flex-1 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
        >
          <option value="">{t("triggers.pick")}</option>
          {available.length === 0 ? (
            <option disabled value="">
              {t("triggers.empty")}
            </option>
          ) : null}
          {available.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={add}
          disabled={!pick}
        >
          {t("common.add")}
        </Button>
      </div>
    </section>
  );
}

/** Variable value placeholder + label table. The ``VarType`` enum
 *  + ``inferType`` are imported from ``lib/variable-types`` so the
 *  workspace variables page (Phase AAL) and this panel agree on the
 *  badge vocabulary. */
const VAR_TYPE_OPTIONS: { value: VarType; label: string; placeholder: string }[] = [
  { value: "string", label: "string", placeholder: "hello" },
  { value: "number", label: "number", placeholder: "100" },
  { value: "boolean", label: "boolean", placeholder: "true" },
  { value: "json", label: "JSON", placeholder: '{"key":"value"}' },
];

/** Convert the user's raw text into the declared type. Returns
 *  ``{ ok: false, error }`` so the caller can surface a real error
 *  instead of silently coercing — the silent-fallback behaviour was
 *  the audit finding being fixed. */
function parseTyped(
  text: string,
  type: VarType,
): { ok: true; value: unknown } | { ok: false; error: string } {
  if (type === "string") return { ok: true, value: text };
  if (type === "number") {
    if (text.trim() === "") return { ok: false, error: "enter a number" };
    const n = Number(text);
    if (Number.isNaN(n)) return { ok: false, error: `"${text}" is not a number` };
    return { ok: true, value: n };
  }
  if (type === "boolean") {
    const t = text.trim().toLowerCase();
    if (t === "true") return { ok: true, value: true };
    if (t === "false") return { ok: true, value: false };
    return { ok: false, error: 'enter "true" or "false"' };
  }
  // json
  try {
    return { ok: true, value: JSON.parse(text) };
  } catch (e) {
    const msg = e instanceof Error ? e.message : "invalid JSON";
    return { ok: false, error: msg };
  }
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
  const [type, setType] = useState<VarType>("string");
  const [valueText, setValueText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const entries = Object.entries(variables);

  function add() {
    const key = name.trim();
    if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(key)) {
      setError(t("variables.nameError"));
      return;
    }
    const parsed = parseTyped(valueText, type);
    if (!parsed.ok) {
      setError(parsed.error);
      return;
    }
    onChange({ ...variables, [key]: parsed.value });
    setName("");
    setValueText("");
    setError(null);
  }

  function remove(key: string) {
    const next = { ...variables };
    delete next[key];
    onChange(next);
  }

  const placeholder =
    VAR_TYPE_OPTIONS.find((o) => o.value === type)?.placeholder ?? "";

  return (
    <section className="flex flex-col gap-3 rounded-md border border-border-subtle p-3">
      <header>
        <h3 className="text-sm font-semibold text-text">{t("builder.variables")}</h3>
        <p className="mt-1 text-[11px] text-text-muted">{t("builder.variablesDesc")}</p>
      </header>
      {entries.length > 0 ? (
        <ul className="flex flex-col gap-1.5">
          {entries.map(([key, value]) => {
            const inferred = inferType(value);
            return (
              <li key={key} className="flex items-center gap-2 text-sm">
                <code className="text-text">{`\${var.${key}}`}</code>
                <span
                  className="inline-flex h-4 items-center rounded-sm bg-overlay px-1 text-[10px] font-semibold uppercase text-text-muted"
                  title={t("variables.typeBadge", { type: inferred })}
                >
                  {inferred}
                </span>
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
            );
          })}
        </ul>
      ) : null}
      <div className="flex flex-col gap-2">
        <div className="grid grid-cols-[1fr_auto] gap-2">
          <FieldRow label={t("variables.name")}>
            <Input
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setError(null);
              }}
              placeholder="min_id"
            />
          </FieldRow>
          <FieldRow label={t("variables.type")}>
            <select
              value={type}
              onChange={(e) => {
                setType(e.target.value as VarType);
                setError(null);
              }}
              className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
            >
              {VAR_TYPE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </FieldRow>
        </div>
        <FieldRow label={t("variables.value")}>
          <Input
            value={valueText}
            onChange={(e) => {
              setValueText(e.target.value);
              setError(null);
            }}
            placeholder={placeholder}
          />
        </FieldRow>
        {error ? (
          <p className="text-[11px] text-error" role="alert">
            {error}
          </p>
        ) : null}
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
          <Checkbox

            checked={enabled}
            onChange={(e) => onToggle(e.target.checked)}
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
