"use client";

import { cn } from "@/lib/cn";
import type { RunStatus } from "@/lib/api";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";

type AnyStatus = RunStatus | "queued" | "skipped";

const STATUS_CLASSES: Record<AnyStatus, string> = {
  pending: "bg-overlay text-text-secondary border-border-subtle",
  queued: "bg-overlay text-text-secondary border-border-subtle",
  running: "bg-info/10 text-info border-info/30",
  succeeded: "bg-success/10 text-success border-success/30",
  failed: "bg-error/10 text-error border-error/30",
  cancelled: "bg-warning/10 text-warning border-warning/30",
  skipped: "bg-overlay text-text-muted border-border-subtle",
};

export function StatusBadge({
  status,
  className,
}: {
  status: AnyStatus;
  className?: string;
}) {
  const { t } = useLocale();
  return (
    <span
      className={cn(
        "inline-flex h-[22px] items-center gap-1.5 rounded-sm border px-2 text-xs font-semibold uppercase tracking-wide",
        STATUS_CLASSES[status],
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "h-1.5 w-1.5 rounded-full bg-current",
          status === "running" && "pulse-dot",
        )}
      />
      {t(`status.${status}` as keyof Messages)}
    </span>
  );
}
