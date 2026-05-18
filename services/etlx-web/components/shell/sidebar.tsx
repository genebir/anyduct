"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ActivityIcon,
  BoxesIcon,
  CableIcon,
  CalendarClockIcon,
  ChevronsUpDownIcon,
  GitBranchIcon,
  SettingsIcon,
  WorkflowIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { useWorkspaces } from "@/components/providers/workspace-provider";

interface NavLink {
  href: (slug: string) => string;
  label: string;
  icon: ReactNode;
}

const NAV: NavLink[] = [
  {
    href: (s) => `/w/${s}/connections`,
    label: "Connections",
    icon: <CableIcon size={18} />,
  },
  {
    href: (s) => `/w/${s}/pipelines`,
    label: "Pipelines",
    icon: <WorkflowIcon size={18} />,
  },
  {
    href: (s) => `/w/${s}/schedules`,
    label: "Schedules",
    icon: <CalendarClockIcon size={18} />,
  },
  {
    href: (s) => `/w/${s}/runs`,
    label: "Runs",
    icon: <ActivityIcon size={18} />,
  },
  {
    href: (s) => `/w/${s}/settings`,
    label: "Settings",
    icon: <SettingsIcon size={18} />,
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const { workspaces, current, setCurrent } = useWorkspaces();
  const [pickerOpen, setPickerOpen] = useState(false);

  const slug = current?.slug ?? "";
  const accent = current?.color_hex ?? "#FF3D8B";

  return (
    <aside
      className="relative flex w-60 shrink-0 flex-col gap-4 border-r border-border-subtle bg-surface px-3 py-4"
      style={{ boxShadow: `inset 4px 0 0 0 ${accent}` }}
    >
      <Link
        href="/workspaces"
        className="flex items-center gap-2 px-2 py-1 text-sm font-semibold text-text"
      >
        <span
          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-white"
          style={{ background: accent }}
          aria-hidden
        >
          <GitBranchIcon size={16} />
        </span>
        etlx
      </Link>

      <div className="relative">
        <button
          type="button"
          onClick={() => setPickerOpen((v) => !v)}
          className={cn(
            "flex w-full items-center justify-between gap-2 rounded-md border border-border-subtle bg-elevated px-3 py-2 text-left text-sm",
            "transition duration-200 hover:bg-overlay",
          )}
          aria-haspopup="listbox"
          aria-expanded={pickerOpen}
        >
          <span className="flex items-center gap-2 truncate">
            <BoxesIcon size={16} className="text-text-muted" />
            <span className="truncate text-text">
              {current?.name ?? "Select workspace"}
            </span>
          </span>
          <ChevronsUpDownIcon size={14} className="text-text-muted" />
        </button>

        {pickerOpen ? (
          <ul
            role="listbox"
            className="absolute left-0 right-0 top-full z-40 mt-1 max-h-64 overflow-auto rounded-md border border-border-subtle bg-elevated p-1 shadow-md"
          >
            {workspaces.length === 0 ? (
              <li className="px-2 py-1.5 text-sm text-text-muted">
                No workspaces yet.
              </li>
            ) : (
              workspaces.map((w) => (
                <li key={w.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setCurrent(w.id);
                      setPickerOpen(false);
                    }}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-sm transition duration-150",
                      "hover:bg-overlay",
                      w.id === current?.id && "bg-overlay",
                    )}
                  >
                    <span
                      className="inline-block h-2 w-2 rounded-full"
                      style={{ background: w.color_hex }}
                      aria-hidden
                    />
                    <span className="truncate">{w.name}</span>
                  </button>
                </li>
              ))
            )}
          </ul>
        ) : null}
      </div>

      <nav className="flex flex-1 flex-col gap-0.5">
        <div className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-text-muted">
          Navigate
        </div>
        {NAV.map((link) => {
          const href = slug ? link.href(slug) : "/workspaces";
          const active = pathname.startsWith(`/w/${slug}/${link.label.toLowerCase()}`);
          return (
            <Link
              key={link.label}
              href={href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition duration-200",
                active
                  ? "bg-overlay font-semibold text-accent"
                  : "text-text-secondary hover:bg-overlay hover:text-text",
              )}
            >
              <span
                className={cn(
                  active ? "text-accent" : "text-text-muted",
                  "shrink-0",
                )}
              >
                {link.icon}
              </span>
              {link.label}
            </Link>
          );
        })}
      </nav>

      <div className="px-2 pt-2 text-[11px] text-text-muted">
        v0.1.0 · {workspaces.length} workspace
        {workspaces.length === 1 ? "" : "s"}
      </div>
    </aside>
  );
}
