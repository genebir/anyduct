/**
 * Split-boundary suggestion for partitioned backfill (ADR-0095 follow-up).
 *
 * Given the cursor column's MIN/MAX (from `GET /cursor-stats`) propose
 * `windows` equal boundaries `[b0..bN]` — the dialog fills From/To/split
 * points with them and the operator edits before queuing. The first
 * boundary is nudged just below MIN because backfill windows are
 * half-open `(from, to]`: a boundary equal to MIN would silently drop
 * the oldest row.
 *
 * Supports numeric cursors (arithmetic split, integers stay integers)
 * and string date/datetime cursors (epoch interpolation, re-emitted in
 * the SAME shape as MIN — date-only stays date-only, the `T`/space
 * separator is preserved — so lexicographic ordering against stored
 * values survives). Anything else returns null ("can't suggest").
 */

const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;

export function suggestBoundaries(
  min: unknown,
  max: unknown,
  windows: number,
): (string | number)[] | null {
  if (!Number.isInteger(windows) || windows < 1 || windows > 64) return null;

  if (typeof min === "number" && typeof max === "number" && max > min) {
    const integers = Number.isInteger(min) && Number.isInteger(max);
    // Nudge below MIN: integer cursors step back by 1; floats by one
    // millionth of the range (cosmetically small, provably < MIN).
    const lower = integers ? min - 1 : min - (max - min) / 1_000_000;
    const points: number[] = [lower];
    for (let k = 1; k < windows; k++) {
      const v = min + (k * (max - min)) / windows;
      points.push(integers ? Math.round(v) : v);
    }
    points.push(max);
    return dedupeIncreasing(points);
  }

  if (typeof min === "string" && typeof max === "string") {
    const lo = Date.parse(min);
    const hi = Date.parse(max);
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) return null;
    const dateOnly = DATE_ONLY_RE.test(min);
    const sep = min.includes("T") ? "T" : " ";
    const fmt = (epoch: number): string => {
      const iso = new Date(epoch).toISOString(); // UTC — fine for suggestions
      return dateOnly ? iso.slice(0, 10) : iso.slice(0, 10) + sep + iso.slice(11, 19);
    };
    const day = 86_400_000;
    const points: string[] = [fmt(lo - (dateOnly ? day : 1000))];
    for (let k = 1; k < windows; k++) {
      points.push(fmt(lo + (k * (hi - lo)) / windows));
    }
    // MAX goes in verbatim: re-formatting it through Date would shift it
    // by the local timezone for space-separated datetimes, and a last
    // boundary BELOW the stored max silently drops the newest rows.
    points.push(max);
    return dedupeIncreasing(points);
  }

  return null;
}

/** Collapse rounding collisions — the server rejects non-increasing
 *  boundaries, so a tiny range (e.g. MIN..MAX one day apart split 4 ways)
 *  degrades to fewer windows instead of a 422. */
function dedupeIncreasing<T extends string | number>(points: T[]): T[] | null {
  const out: T[] = [];
  for (const p of points) {
    if (out.length === 0 || p > (out[out.length - 1] as T)) out.push(p);
  }
  return out.length >= 2 ? out : null;
}
