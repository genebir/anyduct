"use client";

/**
 * Required-field marker (2026-06-12, user request) — a tiny red asterisk
 * at the label's top-right instead of a "Required" text chip. The
 * tooltip + aria-label keep the meaning available to hover and screen
 * readers (a bare colored glyph alone would convey by color only).
 */

import { useLocale } from "@/components/providers/locale-provider";

export function RequiredMark() {
  const { t } = useLocale();
  return (
    <span
      // self-start/-mt position it top-right in flex labels; align-super
      // does the same job in plain inline label spans.
      className="-mt-0.5 ml-0.5 self-start align-super text-[10px] font-bold leading-none text-error"
      title={t("common.requiredField")}
      aria-label={t("common.requiredField")}
      role="img"
    >
      *
    </span>
  );
}
