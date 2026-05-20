"use client";

import { useMemo } from "react";
import cronstrue from "cronstrue";
import { CronExpressionParser } from "cron-parser";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

const PRESETS: { labelKey: keyof Messages; expr: string }[] = [
  { labelKey: "cron.everyMinute", expr: "* * * * *" },
  { labelKey: "cron.every5", expr: "*/5 * * * *" },
  { labelKey: "cron.hourly", expr: "0 * * * *" },
  { labelKey: "cron.daily3", expr: "0 3 * * *" },
  { labelKey: "cron.weekdays9", expr: "0 9 * * 1-5" },
  { labelKey: "cron.monthly", expr: "0 0 1 * *" },
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
  const { t } = useLocale();
  const description = useMemo(
    () => describe(value, allowEmpty, t),
    [value, allowEmpty, t],
  );
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
        placeholder={allowEmpty ? t("cron.placeholderStream") : "0 3 * * *"}
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
            {t(preset.labelKey)}
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
            {t("cron.nextFirings")}
          </div>
          <ul className="mt-1 space-y-0.5 font-mono text-[11px] text-text-secondary">
            {upcoming.map((d, i) => (
              <li key={i} className="flex justify-between gap-3">
                <span>{d.toLocaleString()}</span>
                <span className="text-text-muted">{relative(d, t)}</span>
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

function relative(d: Date, t: Translate): string {
  const delta = d.getTime() - Date.now();
  if (delta < 0) return t("time.past");
  const seconds = Math.floor(delta / 1000);
  if (seconds < 60) return t("time.inSeconds", { n: seconds });
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t("time.inMinutes", { n: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return t("time.inHoursMinutes", { h: hours, m: minutes % 60 });
  const days = Math.floor(hours / 24);
  if (days < 30) return t("time.inDaysHours", { d: days, h: hours % 24 });
  const months = Math.floor(days / 30);
  return t("time.inMonths", { n: months });
}

function describe(
  expr: string,
  allowEmpty: boolean,
  t: Translate,
): { kind: "ok" | "empty" | "error"; text: string } {
  const trimmed = expr.trim();
  if (trimmed === "") {
    return {
      kind: "empty",
      text: allowEmpty ? t("cron.emptyStream") : t("cron.required"),
    };
  }
  try {
    return { kind: "ok", text: cronstrue.toString(trimmed, { verbose: true }) };
  } catch (err) {
    return {
      kind: "error",
      text: err instanceof Error ? err.message : t("cron.invalid"),
    };
  }
}
