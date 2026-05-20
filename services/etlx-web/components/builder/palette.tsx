"use client";

import { PlusIcon } from "lucide-react";
import { cn } from "@/lib/cn";
import { OPERATOR_GROUPS } from "@/lib/operators";
import { useLocale } from "@/components/providers/locale-provider";

export function Palette({
  onAdd,
}: {
  onAdd: (operatorId: string) => void;
}) {
  const { t } = useLocale();
  return (
    <aside className="flex w-64 shrink-0 flex-col gap-4 overflow-y-auto border-r border-border-subtle bg-surface px-3 py-4">
      <div>
        <div className="px-1 text-[11px] font-semibold uppercase tracking-widest text-text-muted">
          {t("builder.operators")}
        </div>
        <p className="mt-1 px-1 text-xs text-text-secondary">
          {t("builder.operatorsHint")}
        </p>
      </div>
      {OPERATOR_GROUPS.map((group) => (
        <div key={group.kind} className="flex flex-col gap-1">
          <div className="px-1 text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
            {group.label}
          </div>
          {group.specs.map((spec) => {
            const Icon = spec.icon;
            return (
              <button
                key={spec.id}
                type="button"
                onClick={() => onAdd(spec.id)}
                className={cn(
                  "group flex items-start gap-2 rounded-md border border-transparent px-2 py-2 text-left transition duration-150",
                  "hover:border-border-subtle hover:bg-overlay",
                )}
              >
                <span
                  aria-hidden
                  className="mt-0.5 inline-flex h-6 w-6 items-center justify-center rounded-sm text-white"
                  style={{ background: spec.accent }}
                >
                  <Icon size={14} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-text">
                    {spec.label}
                  </span>
                  <span className="block truncate text-[11px] text-text-muted">
                    {spec.description}
                  </span>
                </span>
                <PlusIcon
                  size={14}
                  className="mt-1 text-text-muted opacity-0 transition duration-150 group-hover:opacity-100"
                />
              </button>
            );
          })}
        </div>
      ))}
    </aside>
  );
}
