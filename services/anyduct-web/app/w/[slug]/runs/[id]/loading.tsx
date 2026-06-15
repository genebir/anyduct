/**
 * Run detail skeleton — header + two-column layout (logs / summary). Shown
 * while the run + logs + metrics + node-runs fetches resolve, instead of
 * the previous indefinite loading state.
 */
export default function RunDetailLoading() {
  return (
    <div className="flex h-full flex-col" aria-busy aria-live="polite">
      <div className="flex h-14 shrink-0 items-center gap-3 border-b border-border-subtle bg-surface px-6">
        <div className="h-7 w-7 animate-pulse rounded-md bg-overlay" />
        <div className="h-4 w-44 animate-pulse rounded bg-overlay" />
        <div className="ml-auto flex gap-2">
          <div className="h-8 w-20 animate-pulse rounded-md bg-overlay" />
        </div>
      </div>
      <main className="flex-1 overflow-y-auto px-6 py-8">
        <div className="mx-auto grid w-full max-w-6xl gap-6 lg:grid-cols-[2fr_1fr]">
          <div className="h-96 animate-pulse rounded-lg border border-border-subtle bg-overlay/30" />
          <div className="space-y-4">
            <div className="h-64 animate-pulse rounded-lg border border-border-subtle bg-overlay/30" />
            <div className="h-40 animate-pulse rounded-lg border border-border-subtle bg-overlay/30" />
          </div>
        </div>
      </main>
    </div>
  );
}
