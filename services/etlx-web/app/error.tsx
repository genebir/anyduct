"use client";

/**
 * Route-segment error boundary (Step 10.7, 2026-06-12).
 *
 * Catches render/runtime errors below the root layout — the providers
 * (locale/theme) are still alive here, so the page stays in the app's
 * visual language instead of Next's unstyled default. `reset()`
 * re-renders the segment; a transient fetch hiccup usually recovers.
 */

import { useEffect } from "react";
import Link from "next/link";
import { AlertTriangleIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { useLocale } from "@/components/providers/locale-provider";

export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const { t } = useLocale();
  useEffect(() => {
    // Surface in the console for bug reports — the UI shows a friendly
    // card, but the stack matters when someone files an issue.
    console.error(error);
  }, [error]);

  return (
    <div className="flex h-full min-h-[60vh] items-center justify-center px-6">
      <EmptyState
        icon={<AlertTriangleIcon size={36} strokeWidth={1.5} />}
        title={t("appError.title")}
        description={
          error.digest
            ? t("appError.descWithDigest", { digest: error.digest })
            : t("appError.desc")
        }
        action={
          <div className="flex items-center gap-2">
            <Button onClick={reset}>{t("appError.retry")}</Button>
            <Link href="/workspaces">
              <Button variant="ghost">{t("appError.home")}</Button>
            </Link>
          </div>
        }
      />
    </div>
  );
}
