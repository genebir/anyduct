"use client";

import { useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import { useWorkspaces } from "@/components/providers/workspace-provider";
import type { WorkspaceSummary } from "./api";

/**
 * Resolve a `WorkspaceSummary` from the URL slug.
 *
 * - Selects the workspace as "current" once loaded so the sidebar's accent
 *   bar reflects the page the user landed on.
 * - Bounces to `/workspaces` if the slug doesn't match anything the caller
 *   is a member of (covers stale bookmarks).
 */
export function useWorkspaceFromSlug(slug: string): WorkspaceSummary | null {
  const { workspaces, setCurrent } = useWorkspaces();
  const router = useRouter();

  const ws = useMemo(
    () => workspaces.find((w) => w.slug === slug) ?? null,
    [workspaces, slug],
  );

  useEffect(() => {
    if (workspaces.length > 0 && !ws) {
      router.replace("/workspaces");
    }
  }, [workspaces, ws, router]);

  useEffect(() => {
    if (ws) setCurrent(ws.id);
  }, [ws, setCurrent]);

  return ws;
}
