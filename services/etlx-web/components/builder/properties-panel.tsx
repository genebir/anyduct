"use client";

import { useEffect, useState } from "react";
import { ArrowLeftIcon, ArrowRightIcon } from "lucide-react";
import { Input } from "@/components/ui/input";
import { findOperator, type FieldDef } from "@/lib/operators";
import type { ConnectionSummary } from "@/lib/api";
import type { BuilderNode } from "@/lib/pipeline-config";
import { cn } from "@/lib/cn";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

export function PropertiesPanel({
  node,
  connections,
  onChange,
  transformIndex = -1,
  transformCount = 0,
  onMove,
}: {
  node: BuilderNode | null;
  connections: ConnectionSummary[];
  onChange: (id: string, values: Record<string, unknown>) => void;
  /** Index of this node within the transform run, or -1 if not a transform. */
  transformIndex?: number;
  transformCount?: number;
  onMove?: (id: string, dir: -1 | 1) => void;
}) {
  const { t } = useLocale();
  if (!node) {
    return (
      <aside className="flex w-80 shrink-0 flex-col border-l border-border-subtle bg-surface px-4 py-6 text-sm text-text-muted">
        {t("builder.selectNode")}
      </aside>
    );
  }
  const op = findOperator(node.operatorId);
  if (!op) return null;

  const matchingConnections =
    op.kind === "source" || op.kind === "sink"
      ? connections.filter((c) => c.type === op.connectorType)
      : [];

  return (
    <aside className="flex w-80 shrink-0 flex-col gap-4 overflow-y-auto border-l border-border-subtle bg-surface px-4 py-5">
      <header>
        <div className="text-[11px] font-semibold uppercase tracking-widest text-text-muted">
          {op.kind}
        </div>
        <div className="mt-1 text-base font-semibold text-text">
          {op.label}
        </div>
        <p className="mt-1 text-xs text-text-secondary">{op.description}</p>
        {op.kind === "transform" && onMove && transformCount > 1 ? (
          <div className="mt-3 flex items-center gap-2">
            <button
              type="button"
              onClick={() => onMove(node.id, -1)}
              disabled={transformIndex <= 0}
              aria-label={t("builder.moveLeft")}
              title={t("builder.moveLeft")}
              className="inline-flex h-7 w-7 items-center justify-center rounded-sm border border-border-subtle text-text-secondary transition duration-150 hover:bg-overlay hover:text-text disabled:opacity-40"
            >
              <ArrowLeftIcon size={14} />
            </button>
            <button
              type="button"
              onClick={() => onMove(node.id, 1)}
              disabled={transformIndex >= transformCount - 1}
              aria-label={t("builder.moveRight")}
              title={t("builder.moveRight")}
              className="inline-flex h-7 w-7 items-center justify-center rounded-sm border border-border-subtle text-text-secondary transition duration-150 hover:bg-overlay hover:text-text disabled:opacity-40"
            >
              <ArrowRightIcon size={14} />
            </button>
            <span className="text-[11px] text-text-muted">
              {t("builder.stepLabel", {
                n: transformIndex + 1,
                total: transformCount,
              })}
            </span>
          </div>
        ) : null}
      </header>

      <div className="flex flex-col gap-4">
        {op.fields.map((field) => (
          <FieldEditor
            key={field.key}
            field={field}
            value={node.data[field.key]}
            connections={matchingConnections}
            t={t}
            onChange={(v) =>
              onChange(node.id, { ...node.data, [field.key]: v })
            }
          />
        ))}
      </div>
    </aside>
  );
}

function FieldEditor({
  field,
  value,
  connections,
  onChange,
  t,
}: {
  field: FieldDef;
  value: unknown;
  connections: ConnectionSummary[];
  onChange: (v: unknown) => void;
  t: Translate;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
        {field.label}
      </span>
      <FieldInput
        field={field}
        value={value}
        connections={connections}
        onChange={onChange}
        t={t}
      />
      {"help" in field && field.help ? (
        <span className="text-[11px] text-text-muted">{field.help}</span>
      ) : null}
    </label>
  );
}

function FieldInput({
  field,
  value,
  connections,
  onChange,
  t,
}: {
  field: FieldDef;
  value: unknown;
  connections: ConnectionSummary[];
  onChange: (v: unknown) => void;
  t: Translate;
}) {
  if (field.kind === "connection") {
    return (
      <select
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value || undefined)}
        className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
      >
        <option value="">{t("builder.selectConnection")}</option>
        {connections.length === 0 ? (
          <option disabled value="">
            {t("builder.noConnectionsOfType")}
          </option>
        ) : null}
        {connections.map((c) => (
          <option key={c.id} value={c.name}>
            {c.name}
          </option>
        ))}
      </select>
    );
  }
  if (field.kind === "select") {
    return (
      <select
        value={(value as string) ?? ""}
        onChange={(e) => onChange(e.target.value || undefined)}
        className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
      >
        <option value="">—</option>
        {field.options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  if (field.kind === "number") {
    return (
      <Input
        type="number"
        value={value == null ? "" : String(value)}
        placeholder={field.placeholder}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "") onChange(undefined);
          else onChange(Number(v));
        }}
      />
    );
  }
  if (field.kind === "json") {
    return <JsonInput value={value} onChange={onChange} field={field} t={t} />;
  }
  if (field.multiline) {
    return (
      <textarea
        rows={4}
        value={(value as string) ?? ""}
        placeholder={field.placeholder}
        onChange={(e) => onChange(e.target.value || undefined)}
        className={cn(
          "min-h-20 w-full rounded-md border border-border-subtle bg-elevated px-3 py-2 font-mono text-xs text-text",
          "transition duration-200 focus-visible:border-accent focus-visible:outline-none",
        )}
      />
    );
  }
  return (
    <Input
      value={(value as string) ?? ""}
      placeholder={field.placeholder}
      onChange={(e) => onChange(e.target.value || undefined)}
    />
  );
}

function JsonInput({
  value,
  onChange,
  field,
  t,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
  field: Extract<FieldDef, { kind: "json" }>;
  t: Translate;
}) {
  const [text, setText] = useState<string>(() =>
    value === undefined ? "" : JSON.stringify(value, null, 2),
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setText(value === undefined ? "" : JSON.stringify(value, null, 2));
  }, [value]);

  return (
    <div className="flex flex-col gap-1">
      <textarea
        rows={4}
        value={text}
        placeholder={field.placeholder}
        onChange={(e) => {
          const txt = e.target.value;
          setText(txt);
          if (txt.trim() === "") {
            setError(null);
            onChange(undefined);
            return;
          }
          try {
            onChange(JSON.parse(txt));
            setError(null);
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
          }
        }}
        className={cn(
          "min-h-20 w-full rounded-md border border-border-subtle bg-elevated px-3 py-2 font-mono text-xs text-text",
          "transition duration-200 focus-visible:border-accent focus-visible:outline-none",
          error && "border-error",
        )}
      />
      {error ? (
        <span className="text-[11px] text-error">
          {t("builder.jsonError", { error })}
        </span>
      ) : null}
    </div>
  );
}
