/**
 * Route-level loading skeleton (Next 15 app router). Streams in the second
 * a workspace route is requested so the user sees a minimal page chrome
 * instead of a blank screen while the per-page useEffect data fetches
 * resolve. One file under ``/w/[slug]`` covers every nested route that
 * doesn't ship its own ``loading.tsx``.
 */
export default function WorkspaceLoading() {
  return (
    <div className="flex h-full flex-1 flex-col" aria-busy aria-live="polite">
      {/* Header strip — same height as <Header> so the layout doesn't jump
          when the real page swaps in. */}
      <div className="flex h-14 shrink-0 items-center gap-3 border-b border-border-subtle bg-surface px-6">
        <div className="h-4 w-40 animate-pulse rounded bg-overlay" />
        <div className="ml-auto h-7 w-24 animate-pulse rounded bg-overlay" />
      </div>
      <div className="flex-1 space-y-4 overflow-hidden px-6 py-8">
        <div className="h-6 w-56 animate-pulse rounded bg-overlay" />
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <div
              key={i}
              className="h-28 animate-pulse rounded-lg border border-border-subtle bg-overlay/40"
            />
          ))}
        </div>
      </div>
    </div>
  );
}
