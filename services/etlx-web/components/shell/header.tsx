"use client";

import { LogOutIcon, MoonIcon, SunIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useAuth } from "@/components/providers/auth-provider";
import { useTheme } from "@/components/providers/theme-provider";
import { cn } from "@/lib/cn";

export function Header({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  const { state, signOut } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const userInitial =
    state.kind === "signed-in" ? state.user.name.charAt(0).toUpperCase() : "?";

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-4 border-b border-border-subtle bg-bg/85 px-6 backdrop-blur-md">
      <div className="min-w-0">
        <h1 className="truncate text-base font-semibold text-text">{title}</h1>
        {subtitle ? (
          <div className="truncate text-xs text-text-secondary">
            {subtitle}
          </div>
        ) : null}
      </div>
      <div className="flex items-center gap-2">
        {actions}
        <button
          type="button"
          onClick={toggleTheme}
          aria-label={
            theme === "dark" ? "Switch to light theme" : "Switch to dark theme"
          }
          className={cn(
            "inline-flex h-9 w-9 items-center justify-center rounded-md text-text-secondary transition duration-200",
            "hover:bg-overlay hover:text-text",
          )}
        >
          {theme === "dark" ? <SunIcon size={16} /> : <MoonIcon size={16} />}
        </button>
        {state.kind === "signed-in" ? (
          <div className="flex items-center gap-2 rounded-md border border-border-subtle bg-elevated py-1 pl-1 pr-2">
            <span
              aria-hidden
              className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-accent-gradient text-xs font-semibold text-white"
            >
              {userInitial}
            </span>
            <span className="hidden text-sm text-text sm:inline">
              {state.user.name}
            </span>
            <button
              type="button"
              onClick={() => void signOut()}
              aria-label="Sign out"
              className="ml-1 inline-flex h-7 w-7 items-center justify-center rounded-sm text-text-muted transition duration-150 hover:bg-overlay hover:text-text"
            >
              <LogOutIcon size={14} />
            </button>
          </div>
        ) : null}
      </div>
    </header>
  );
}
