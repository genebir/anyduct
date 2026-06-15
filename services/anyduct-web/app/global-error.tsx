"use client";

/**
 * Root error boundary (Step 10.7, 2026-06-12) — fires only when the
 * ROOT LAYOUT itself throws (providers included), so nothing of the app
 * shell survives: we must render our own <html>/<body> and can't use
 * the locale provider. Bilingual static copy instead.
 */

import "./globals.css";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body className="flex min-h-screen items-center justify-center bg-base font-sans text-text antialiased">
        <div className="mx-6 max-w-md rounded-xl border border-border-subtle bg-surface p-8 text-center shadow-lg">
          <h1 className="text-lg font-semibold">
            Something went wrong · 문제가 발생했습니다
          </h1>
          <p className="mt-2 text-sm text-text-secondary">
            The app shell failed to render. Reloading usually fixes it.
            <br />앱 셸 렌더링에 실패했습니다. 새로고침하면 대부분 해결됩니다.
          </p>
          {error.digest ? (
            <p className="mt-2 font-mono text-xs text-text-muted">digest: {error.digest}</p>
          ) : null}
          <button
            type="button"
            onClick={reset}
            className="mt-6 inline-flex h-9 cursor-pointer items-center rounded-lg bg-accent px-4 text-sm font-semibold text-on-accent hover:opacity-90"
          >
            Reload · 다시 시도
          </button>
        </div>
      </body>
    </html>
  );
}
