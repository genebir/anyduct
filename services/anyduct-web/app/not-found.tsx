"use client";

/**
 * App-wide 404 (Step 10.7, 2026-06-12) — rendered inside the root
 * layout (providers alive), so it speaks the app's language and offers
 * the obvious next step instead of Next's bare default.
 */

import Link from "next/link";
import { CompassIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { useLocale } from "@/components/providers/locale-provider";

export default function NotFound() {
  const { t } = useLocale();
  return (
    <div className="flex h-full min-h-[60vh] items-center justify-center px-6">
      <EmptyState
        icon={<CompassIcon size={36} strokeWidth={1.5} />}
        title={t("notFound.title")}
        description={t("notFound.desc")}
        action={
          <Link href="/workspaces">
            <Button>{t("notFound.home")}</Button>
          </Link>
        }
      />
    </div>
  );
}
