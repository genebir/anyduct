/**
 * Shared time formatting — Phase ACN (2026-06-04), localised in ACP.
 *
 * Lists across the app render "how long ago" timestamps. The logic
 * had drifted into four copies: the migrations list (no suffix), the
 * migration detail page (" ago"), and the dashboard (``fmtTime``,
 * localised via i18n). ACN/ACO centralised the first three but on a
 * hardcoded English string — a regression for the Korean locale. ACP
 * makes the shared helper localised by taking the ``t`` translator,
 * so every surface speaks the user's language from one implementation.
 *
 * Pair ``relativeTime`` (scannable, in the cell) with ``absoluteTime``
 * (precise, in a ``title`` tooltip) so a list stays skimmable without
 * losing the exact instant on hover.
 */

/** Minimal translator shape — narrowed to exactly the keys this module
 *  uses. Keeping the key union small (rather than ``string``) means a
 *  page's ``t: (key: keyof Messages) => string`` is assignable here:
 *  a translator that accepts the whole keyspace trivially handles this
 *  subset (contravariant parameters). */
type Translate = (
  key: "time.justNow" | "time.minutesAgo" | "time.hoursAgo" | "time.daysAgo",
  vars?: Record<string, string | number>,
) => string;

/** Localised coarse relative age: ``"just now"`` / ``"5m ago"`` /
 *  ``"3h ago"`` / ``"2d ago"`` (and their Korean equivalents). Returns
 *  ``"—"`` for null / unparseable input. */
export function relativeTime(
  iso: string | null | undefined,
  t: Translate,
): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (diff < 60) return t("time.justNow");
  if (diff < 3600) return t("time.minutesAgo", { n: Math.floor(diff / 60) });
  if (diff < 86400) return t("time.hoursAgo", { n: Math.floor(diff / 3600) });
  return t("time.daysAgo", { n: Math.floor(diff / 86400) });
}

/** Full locale timestamp for a ``title`` tooltip. Returns ``undefined``
 *  (not a string) for null / unparseable input so it can be spread
 *  straight onto a ``title`` attribute without painting "—". */
export function absoluteTime(iso: string | null | undefined): string | undefined {
  if (!iso) return undefined;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return undefined;
  return d.toLocaleString();
}
