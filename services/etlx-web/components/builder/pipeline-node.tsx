"use client";

import { Handle, Position, type NodeProps } from "@xyflow/react";
import { AlertTriangleIcon, Trash2Icon } from "lucide-react";
import { findOperator, getOperatorLabel } from "@/lib/operators";
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
  const label = getOperatorLabel(op, t);

  // Compute missing-required-fields up-front: drives both the node-card
  // summary text ("Set: connection, table") AND the warning chrome, so
  // the analyst sees exactly what's blocking the node without opening
  // the properties drawer (Phase L1 audit fix 2026-05-26).
  //
  // Phase AAF (2026-05-29): also honour ``showWhen`` — a hidden field
  // can't possibly block the user, so don't shout "incomplete" at them
  // for something they can't see.
  const missingRequired = op.fields
    .filter((f) => f.required)
    .filter((f) => !f.showWhen || d.values[f.showWhen.field] === f.showWhen.equals)
    .filter((f) => {
      const v = d.values[f.key];
      return v === undefined || v === null || v === "";
    })
    .map((f) => f.label);
  const summary = describeNode(op, d.values, t, missingRequired);
  // A node is "incomplete" iff a required field is empty. Source/sink
  // ``connection`` always counts as required so legacy nodes still
  // render the warning; transforms inherit the same rule via
  // ``required: true`` in their FieldDef catalogue.
  const incomplete = missingRequired.length > 0;

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
          <div
            className="truncate text-xs uppercase tracking-wider text-text-muted underline decoration-dotted decoration-text-muted/40 underline-offset-2"
            title={glossaryTooltip(op.kind, t)}
          >
            {op.kind}
          </div>
          <div className="truncate text-sm font-semibold text-text">
            {label}
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
            aria-label={t("builder.removeAria", { label })}
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
          "truncate px-3 py-2 text-xs",
          incomplete ? "text-warning" : "text-text-secondary",
        )}
        title={summary}
      >
        {summary}
      </div>
    </div>
  );
}

/** Map an operator ``kind`` to a plain-language definition for the
 *  dotted-underline tooltip on the node card / properties drawer.
 *  Phase L1 audit: analysts didn't know "source / sink / transform"
 *  meant — giving them a one-line definition on hover removes that
 *  bar with no new screen real estate. */
function glossaryTooltip(
  kind: string,
  t: Translate,
): string {
  if (kind === "source") return t("glossary.source");
  if (kind === "sink") return t("glossary.sink");
  if (kind === "transform") return t("glossary.transform");
  return "";
}

function describeNode(
  op: ReturnType<typeof findOperator>,
  values: Record<string, unknown>,
  t: Translate,
  missingRequired: string[],
): string {
  if (!op) return "";
  // If anything required is missing, lead with that — it's the most
  // important thing the user can act on. Truncate the list at two so
  // the card doesn't overflow; the property drawer shows the rest.
  if (missingRequired.length > 0) {
    const head = missingRequired.slice(0, 2).join(", ");
    const tail =
      missingRequired.length > 2 ? ` +${missingRequired.length - 2}` : "";
    return t("builder.needsFields", { fields: head + tail });
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
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 40 ? `${s.slice(0, 40)}…` : s;
}
