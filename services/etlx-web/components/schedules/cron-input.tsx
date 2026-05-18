"use client";

import { useMemo } from "react";
import cronstrue from "cronstrue";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";

const PRESETS: { label: string; expr: string }[] = [
  { label: "Every minute", expr: "* * * * *" },
  { label: "Every 5 min", expr: "*/5 * * * *" },
  { label: "Hourly", expr: "0 * * * *" },
  { label: "Daily 03:00", expr: "0 3 * * *" },
  { label: "Weekdays 09:00", expr: "0 9 * * 1-5" },
  { label: "Monthly", expr: "0 0 1 * *" },
];

/**
 * 5-field cron input with preset chips + human-readable description.
 *
 * The server validates with croniter (Step 8.5e), so we only need to give
 * the user immediate feedback while typing. ``cronstrue`` throws on invalid
 * input — we catch and show a muted hint instead of red error styling so a
 * mid-edit "0 * *" doesn't yell at the user before they finish.
 */
export function CronInput({
  value,
  onChange,
  disabled,
  allowEmpty = false,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  allowEmpty?: boolean;
}) {
  const description = useMemo(() => describe(value, allowEmpty), [value, allowEmpty]);

  return (
    <div className="flex flex-col gap-2">
      <Input
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        placeholder={allowEmpty ? "(leave blank for stream)" : "0 3 * * *"}
        className="font-mono"
        invalid={description.kind === "error"}
      />
      <div className="flex flex-wrap gap-1.5">
        {PRESETS.map((preset) => (
          <button
            key={preset.expr}
            type="button"
            onClick={() => onChange(preset.expr)}
            disabled={disabled}
            className={cn(
              "rounded-sm border border-border-subtle px-2 py-0.5 text-[11px] text-text-secondary transition duration-150",
              "hover:border-border-strong hover:bg-overlay hover:text-text",
              value === preset.expr && "border-accent text-accent",
            )}
          >
            {preset.label}
          </button>
        ))}
      </div>
      <p
        className={cn(
          "text-[11px]",
          description.kind === "ok"
            ? "text-text-secondary"
            : description.kind === "empty"
              ? "text-text-muted"
              : "text-error",
        )}
      >
        {description.text}
      </p>
    </div>
  );
}

function describe(
  expr: string,
  allowEmpty: boolean,
): { kind: "ok" | "empty" | "error"; text: string } {
  const trimmed = expr.trim();
  if (trimmed === "") {
    return {
      kind: "empty",
      text: allowEmpty
        ? "Empty cron: stream pipelines run continuously, the scheduler ignores this row."
        : "Cron expression is required for batch schedules.",
    };
  }
  try {
    return { kind: "ok", text: cronstrue.toString(trimmed, { verbose: true }) };
  } catch (err) {
    return {
      kind: "error",
      text: err instanceof Error ? err.message : "Invalid cron expression.",
    };
  }
}
