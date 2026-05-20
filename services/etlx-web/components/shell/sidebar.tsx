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
  HomeIcon,
  ScrollTextIcon,
  SettingsIcon,
  UsersIcon,
  WorkflowIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { useLocale } from "@/components/providers/locale-provider";
import { useWorkspaces } from "@/components/providers/workspace-provider";
import type { Messages } from "@/lib/i18n/messages";

interface NavLink {
  id: string;
  href: (slug: string) => string;
  labelKey: keyof Messages;
  icon: ReactNode;
}

const NAV: NavLink[] = [
  {
    id: "overview",
    href: (s) => `/w/${s}`,
    labelKey: "nav.overview",
    icon: <HomeIcon size={18} />,
  },
  {
    id: "connections",
    href: (s) => `/w/${s}/connections`,
    labelKey: "nav.connections",
    icon: <CableIcon size={18} />,
  },
  {
    id: "pipelines",
    href: (s) => `/w/${s}/pipelines`,
    labelKey: "nav.pipelines",
    icon: <WorkflowIcon size={18} />,
  },
  {
    id: "schedules",
    href: (s) => `/w/${s}/schedules`,
    labelKey: "nav.schedules",
    icon: <CalendarClockIcon size={18} />,
  },
  {
    id: "runs",
    href: (s) => `/w/${s}/runs`,
    labelKey: "nav.runs",
    icon: <ActivityIcon size={18} />,
  },
  {
    id: "members",
    href: (s) => `/w/${s}/members`,
    labelKey: "nav.members",
    icon: <UsersIcon size={18} />,
  },
  {
    id: "audit",
    href: (s) => `/w/${s}/audit`,
    labelKey: "nav.audit",
    icon: <ScrollTextIcon size={18} />,
  },
  {
    id: "settings",
    href: (s) => `/w/${s}/settings`,
    labelKey: "nav.settings",
    icon: <SettingsIcon size={18} />,
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const { workspaces, current, setCurrent } = useWorkspaces();
  const { t } = useLocale();
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
              {current?.name ?? t("nav.selectWorkspace")}
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
                {t("nav.noWorkspaces")}
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
        {NAV.map((link) => {
          const href = slug ? link.href(slug) : "/workspaces";
          // Overview href is exactly /w/<slug> — every other workspace route
          // is /w/<slug>/<segment>, so "starts with overview's href" would
          // also match every nested page. Match exactly for the overview
          // entry, prefix-match for the rest.
          const active =
            link.id === "overview"
              ? pathname === href
              : pathname.startsWith(href);
          return (
            <Link
              key={link.id}
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
              {t(link.labelKey)}
            </Link>
          );
        })}
      </nav>

      <div className="px-2 pt-2 text-[11px] text-text-muted">
        v0.1.0 · {workspaces.length}
      </div>
    </aside>
  );
}
