/**
 * Skeleton shimmer (Step 10.7, DESIGN.md §6.2) — `bg-elevated` →
 * `bg-overlay` over 1.5s (static under prefers-reduced-motion). Replaces
 * the bare "Loading…" text on list pages so the layout doesn't jump when
 * rows land.
 */

import { cn } from "@/lib/cn";

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn("skeleton", className)} />;
}

/** Table-shaped placeholder for the list pages: a header bar + N rows
 *  of varying widths (uniform widths read as a glitch, not loading). */
export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  const widths = ["w-2/3", "w-1/2", "w-3/4", "w-2/5", "w-3/5"];
  return (
    <div className="space-y-3 py-2" role="status" aria-label="Loading">
      <Skeleton className="h-4 w-1/3" />
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={`h-8 ${widths[i % widths.length]}`} />
      ))}
    </div>
  );
}
