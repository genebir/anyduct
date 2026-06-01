"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ActivityIcon,
  ArrowRightLeftIcon,
  BoxesIcon,
  BracesIcon,
  CableIcon,
  CalendarClockIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ChevronsUpDownIcon,
  GitBranchIcon,
  HomeIcon,
  LayersIcon,
  RadarIcon,
  ScrollTextIcon,
  SettingsIcon,
  SlidersHorizontalIcon,
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

interface NavGroup {
  id: string;
  labelKey: keyof Messages;
  icon: ReactNode;
  children: NavLink[];
}

type NavEntry = NavLink | NavGroup;

function isGroup(entry: NavEntry): entry is NavGroup {
  return "children" in entry;
}

const NAV: NavEntry[] = [
  {
    id: "overview",
    href: (s) => `/w/${s}`,
    labelKey: "nav.overview",
    icon: <HomeIcon size={18} />,
  },
  {
    id: "pipelines",
    href: (s) => `/w/${s}/pipelines`,
    labelKey: "nav.pipelines",
    icon: <WorkflowIcon size={18} />,
  },
  {
    // Phase AAN (2026-05-29): dedicated DB-migration surface.
    // Filtered view of pipelines whose at-least-one sink has
    // ``auto_create_table: true`` (ADR-0066 / 0071 / 0072). Kept
    // adjacent to ``pipelines`` so the user reads it as
    // "specialised pipelines", not a separate primitive.
    id: "migrations",
    href: (s) => `/w/${s}/migrations`,
    labelKey: "nav.migrations",
    icon: <ArrowRightLeftIcon size={18} />,
  },
  {
    id: "schedules",
    href: (s) => `/w/${s}/schedules`,
    labelKey: "nav.schedules",
    icon: <CalendarClockIcon size={18} />,
  },
  {
    id: "sensors",
    href: (s) => `/w/${s}/sensors`,
    labelKey: "nav.sensors",
    icon: <RadarIcon size={18} />,
  },
  {
    id: "runs",
    href: (s) => `/w/${s}/runs`,
    labelKey: "nav.runs",
    icon: <ActivityIcon size={18} />,
  },
  {
    id: "assets",
    href: (s) => `/w/${s}/assets`,
    labelKey: "nav.assets",
    icon: <LayersIcon size={18} />,
  },
  {
    id: "audit",
    href: (s) => `/w/${s}/audit`,
    labelKey: "nav.audit",
    icon: <ScrollTextIcon size={18} />,
  },
  {
    id: "settings",
    labelKey: "nav.settings",
    icon: <SettingsIcon size={18} />,
    children: [
      {
        id: "settings-general",
        href: (s) => `/w/${s}/settings`,
        labelKey: "nav.settingsGeneral",
        icon: <SlidersHorizontalIcon size={16} />,
      },
      {
        id: "connections",
        href: (s) => `/w/${s}/connections`,
        labelKey: "nav.connections",
        icon: <CableIcon size={16} />,
      },
      {
        id: "members",
        href: (s) => `/w/${s}/members`,
        labelKey: "nav.members",
        icon: <UsersIcon size={16} />,
      },
      {
        id: "variables",
        href: (s) => `/w/${s}/variables`,
        labelKey: "nav.variables",
        icon: <BracesIcon size={16} />,
      },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const { workspaces, current, setCurrent } = useWorkspaces();
  const { t } = useLocale();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

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
        {NAV.map((entry) => {
          if (!isGroup(entry)) {
            // Overview href is exactly /w/<slug>; every other route is nested,
            // so prefix-match everything except overview (which matches exactly).
            const href = slug ? entry.href(slug) : "/workspaces";
            const active =
              entry.id === "overview" ? pathname === href : pathname.startsWith(href);
            return <NavRow key={entry.id} href={href} active={active} icon={entry.icon} label={t(entry.labelKey)} />;
          }
          const childActive = entry.children.some(
            (c) => slug && pathname.startsWith(c.href(slug)),
          );
          const open = openGroups[entry.id] ?? childActive;
          return (
            <div key={entry.id} className="flex flex-col gap-0.5">
              <button
                type="button"
                onClick={() => setOpenGroups((g) => ({ ...g, [entry.id]: !open }))}
                aria-expanded={open}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition duration-200",
                  childActive
                    ? "font-semibold text-text"
                    : "text-text-secondary hover:bg-overlay hover:text-text",
                )}
              >
                <span className="shrink-0 text-text-muted">{entry.icon}</span>
                <span className="flex-1 text-left">{t(entry.labelKey)}</span>
                {open ? (
                  <ChevronDownIcon size={14} className="shrink-0 text-text-muted" />
                ) : (
                  <ChevronRightIcon size={14} className="shrink-0 text-text-muted" />
                )}
              </button>
              {open
                ? entry.children.map((c) => {
                    const href = slug ? c.href(slug) : "/workspaces";
                    return (
                      <NavRow
                        key={c.id}
                        href={href}
                        active={pathname.startsWith(href)}
                        icon={c.icon}
                        label={t(c.labelKey)}
                        nested
                      />
                    );
                  })
                : null}
            </div>
          );
        })}
      </nav>

      <div className="px-2 pt-2 text-[11px] text-text-muted">
        v0.1.0 · {workspaces.length}
      </div>
    </aside>
  );
}

function NavRow({
  href,
  active,
  icon,
  label,
  nested = false,
}: {
  href: string;
  active: boolean;
  icon: ReactNode;
  label: string;
  nested?: boolean;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "flex items-center gap-2.5 rounded-md py-2 text-sm transition duration-200",
        nested ? "pl-9 pr-2.5" : "px-2.5",
        active
          ? "bg-overlay font-semibold text-accent"
          : "text-text-secondary hover:bg-overlay hover:text-text",
      )}
    >
      <span className={cn(active ? "text-accent" : "text-text-muted", "shrink-0")}>{icon}</span>
      {label}
    </Link>
  );
}
