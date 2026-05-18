"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { useAuth } from "@/components/providers/auth-provider";
import { Sidebar } from "./sidebar";

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { state } = useAuth();
  const isPublic = pathname === "/login";

  if (isPublic || state.kind !== "signed-in") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg">
        {state.kind === "loading" ? null : children}
      </div>
    );
  }

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {children}
      </div>
    </div>
  );
}
