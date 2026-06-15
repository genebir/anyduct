/**
 * Builder skeleton — three-pane layout (palette + canvas + drawer) so the
 * user sees the builder shape immediately. The real page swaps in once the
 * pipeline / connections fetch returns + the lazy-loaded GraphEditor chunk
 * arrives. The previous behaviour was a blank white screen for the full
 * duration of those two awaits.
 */
export default function EditorLoading() {
  return (
    <div className="flex h-full flex-col" aria-busy aria-live="polite">
      <div className="flex h-14 shrink-0 items-center gap-3 border-b border-border-subtle bg-surface px-6">
        <div className="h-4 w-48 animate-pulse rounded bg-overlay" />
        <div className="ml-auto flex gap-2">
          <div className="h-8 w-20 animate-pulse rounded-md bg-overlay" />
          <div className="h-8 w-20 animate-pulse rounded-md bg-overlay" />
          <div className="h-8 w-16 animate-pulse rounded-md bg-overlay" />
        </div>
      </div>
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <aside className="flex w-64 shrink-0 flex-col gap-3 border-r border-border-subtle bg-surface p-4">
          <div className="h-4 w-24 animate-pulse rounded bg-overlay" />
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-10 animate-pulse rounded-md bg-overlay/60" />
          ))}
        </aside>
        <div className="flex-1 animate-pulse bg-bg" />
        <aside className="hidden w-80 shrink-0 border-l border-border-subtle bg-surface p-4 md:block">
          <div className="h-4 w-32 animate-pulse rounded bg-overlay" />
        </aside>
      </div>
    </div>
  );
}
