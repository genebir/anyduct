"use client";

import { useMemo } from "react";
import cronstrue from "cronstrue";
import { CronExpressionParser } from "cron-parser";
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
  const upcoming = useMemo(
    () => (description.kind === "ok" ? nextFirings(value, 3) : []),
    [value, description.kind],
  );

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
      {upcoming.length > 0 ? (
        <div className="rounded-md border border-border-subtle bg-elevated/40 p-2">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-text-muted">
            Next firings (your timezone)
          </div>
          <ul className="mt-1 space-y-0.5 font-mono text-[11px] text-text-secondary">
            {upcoming.map((d, i) => (
              <li key={i} className="flex justify-between gap-3">
                <span>{d.toLocaleString()}</span>
                <span className="text-text-muted">{relative(d)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function nextFirings(expr: string, n: number): Date[] {
  const out: Date[] = [];
  try {
    const it = CronExpressionParser.parse(expr.trim(), { currentDate: new Date() });
    for (let i = 0; i < n; i++) out.push(it.next().toDate());
  } catch {
    // Mid-edit invalid input — silently return what we have.
  }
  return out;
}

function relative(d: Date): string {
  const delta = d.getTime() - Date.now();
  if (delta < 0) return "past";
  const seconds = Math.floor(delta / 1000);
  if (seconds < 60) return `in ${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `in ${hours}h ${minutes % 60}m`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `in ${days}d ${hours % 24}h`;
  const months = Math.floor(days / 30);
  return `in ~${months}mo`;
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
