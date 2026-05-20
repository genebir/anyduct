"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { AlertTriangleIcon, Trash2Icon } from "lucide-react";
import { findOperator } from "@/lib/operators";
import { cn } from "@/lib/cn";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

export interface PipelineNodeData extends Record<string, unknown> {
  operatorId: string;
  values: Record<string, unknown>;
  selected?: boolean;
  onSelect: (id: string) => void;
  onRemove: (id: string) => void;
  canRemove: boolean;
}

export function PipelineNode({ id, data }: NodeProps) {
  const { t } = useLocale();
  const d = data as PipelineNodeData;
  const op = findOperator(d.operatorId);
  if (!op) return null;
  const Icon = op.icon;

  const summary = describeNode(op, d.values, t);
  // A source/sink with no connection (or a call with no target) can't run —
  // flag it on the canvas so the gap is visible without opening the node.
  const incomplete =
    ((op.kind === "source" || op.kind === "sink") && !d.values.connection) ||
    (op.kind === "call" && !d.values.pipeline_id);

  return (
    <div
      onClick={() => d.onSelect(id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          d.onSelect(id);
        }
      }}
      className={cn(
        "group relative w-60 cursor-pointer rounded-lg border bg-elevated text-left text-sm shadow-md transition duration-200",
        d.selected
          ? "border-accent ring-accent"
          : incomplete
            ? "border-warning/60 hover:border-warning"
            : "border-border-subtle hover:border-border-strong",
      )}
    >
      {op.kind !== "source" ? (
        <Handle
          type="target"
          position={Position.Left}
          className="!h-2.5 !w-2.5 !rounded-full !border-2 !border-bg !bg-accent"
        />
      ) : null}
      {op.kind === "source" || op.kind === "transform" ? (
        <Handle
          type="source"
          position={Position.Right}
          className="!h-2.5 !w-2.5 !rounded-full !border-2 !border-bg !bg-accent"
        />
      ) : null}

      <div className="flex items-center gap-2 border-b border-border-subtle px-3 py-2">
        <span
          aria-hidden
          className="inline-flex h-6 w-6 items-center justify-center rounded-sm text-white"
          style={{ background: op.accent }}
        >
          <Icon size={14} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs uppercase tracking-wider text-text-muted">
            {op.kind}
          </div>
          <div className="truncate text-sm font-semibold text-text">
            {op.label}
          </div>
        </div>
        {incomplete ? (
          <AlertTriangleIcon
            size={14}
            className="shrink-0 text-warning"
            aria-label={t("builder.noConnection")}
          />
        ) : null}
        {d.canRemove ? (
          <button
            type="button"
            aria-label={t("builder.removeAria", { label: op.label })}
            onClick={(e) => {
              e.stopPropagation();
              d.onRemove(id);
            }}
            className="rounded-sm p-1 text-text-muted opacity-0 transition duration-150 hover:bg-overlay hover:text-error group-hover:opacity-100"
          >
            <Trash2Icon size={14} />
          </button>
        ) : null}
      </div>
      <div
        className={cn(
          "px-3 py-2 text-xs",
          incomplete ? "text-warning" : "text-text-secondary",
        )}
      >
        {summary}
      </div>
    </div>
  );
}

function describeNode(
  op: ReturnType<typeof findOperator>,
  values: Record<string, unknown>,
  t: Translate,
): string {
  if (!op) return "";
  if (op.kind === "call") {
    const target = values.pipeline_id as string | undefined;
    return target ? `→ ${target.slice(0, 8)}…` : t("builder.notConfigured");
  }
  if (op.kind === "source" || op.kind === "sink") {
    const conn = (values.connection as string) || t("builder.noConnection");
    const target =
      (values.table as string) ||
      (values.topic as string) ||
      (values.key as string) ||
      "";
    return target ? `${conn} · ${target}` : conn;
  }
  // transform — show first non-empty field as a hint
  const first = op.fields[0]?.key;
  const v = first ? values[first] : undefined;
  if (v === undefined || v === null || v === "") return t("builder.notConfigured");
  if (typeof v === "string") return v.length > 40 ? `${v.slice(0, 40)}…` : v;
  return JSON.stringify(v);
}
