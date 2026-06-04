/**
 * Shared time formatting — Phase ACN (2026-06-04).
 *
 * Lists across the app render "how long ago" timestamps. The logic
 * was copy-pasted into the migrations list (``relativeTime``, no
 * suffix) and the migration detail page (``formatRelativeTime``, with
 * " ago"). Centralising it removes the duplication, keeps the buckets
 * (s / m / h / d) identical everywhere, and adds a NaN guard the
 * inline copies lacked.
 *
 * Pair ``relativeTime`` (scannable, in the cell) with ``absoluteTime``
 * (precise, in a ``title`` tooltip) so a list stays skimmable without
 * losing the exact instant on hover.
 */

/** Coarse relative age: ``"5m"`` / ``"5m ago"``. Returns ``"—"`` for
 *  null / unparseable input. */
export function relativeTime(
  iso: string | null | undefined,
  opts?: { ago?: boolean },
): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Math.max(0, Math.floor((Date.now() - then) / 1000));
  const s = opts?.ago ? " ago" : "";
  if (diff < 60) return `${diff}s${s}`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m${s}`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h${s}`;
  return `${Math.floor(diff / 86400)}d${s}`;
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
