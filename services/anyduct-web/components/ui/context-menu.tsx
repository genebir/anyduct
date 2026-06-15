"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { cn } from "@/lib/cn";

/**
 * Lightweight right-click context-menu primitive (2026-05-26 user request:
 * '웹화면 자체에서 모든 영역에 대해 우클릭 액션을 추가해줘'). Used everywhere
 * the user might want quick row / surface actions without hunting for the
 * button: the builder canvas, nodes + edges, list rows, etc.
 *
 * Why not Radix / Headless UI: bringing a context-menu dependency for what
 * is effectively a floating ``<ul>`` + a few keyboard handlers wasn't worth
 * the bundle hit. This impl handles the 95% case (open at pointer, close on
 * outside / escape / item click, viewport-edge clamp, optional submenu).
 *
 * Usage::
 *
 *   const menu = useContextMenu();
 *   <div onContextMenu={menu.openOnEvent}>...</div>
 *   <ContextMenu menu={menu}>
 *     <ContextMenuItem onSelect={...}>Open</ContextMenuItem>
 *     <ContextMenuSeparator />
 *     <ContextMenuItem onSelect={...} destructive>Delete</ContextMenuItem>
 *   </ContextMenu>
 */

interface MenuState {
  open: boolean;
  x: number;
  y: number;
}

export interface ContextMenuController {
  state: MenuState;
  openAt: (x: number, y: number) => void;
  /** Bound directly to ``onContextMenu`` — preventDefault + position. */
  openOnEvent: (e: { preventDefault: () => void; clientX: number; clientY: number }) => void;
  close: () => void;
}

/** Hook owning the menu's open + position state. One per surface. */
export function useContextMenu(): ContextMenuController {
  const [state, setState] = useState<MenuState>({ open: false, x: 0, y: 0 });
  const openAt = useCallback((x: number, y: number) => {
    setState({ open: true, x, y });
  }, []);
  const openOnEvent = useCallback(
    (e: { preventDefault: () => void; clientX: number; clientY: number }) => {
      e.preventDefault();
      setState({ open: true, x: e.clientX, y: e.clientY });
    },
    [],
  );
  const close = useCallback(() => setState((s) => ({ ...s, open: false })), []);
  return { state, openAt, openOnEvent, close };
}

// Provided so ContextMenuItem can call close() without prop drilling.
const _CloseCtx = createContext<() => void>(() => {});

export function ContextMenu({
  menu,
  children,
  minWidth = 200,
}: {
  menu: ContextMenuController;
  children: ReactNode;
  minWidth?: number;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  // Outside click + Escape close. Attach only while open so we don't pay
  // the event-listener cost on every surface that has a menu wired up.
  useEffect(() => {
    if (!menu.state.open) return;
    const onPointerDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) menu.close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") menu.close();
    };
    // pointerdown (not click) so the menu dismisses before any click
    // handler under the cursor fires — feels snappier on touch + mouse.
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  if (!menu.state.open) return null;

  // Clamp to viewport — flip if the menu would overflow on the right /
  // bottom. ``minWidth`` is a hint; real overflow flip uses the rendered
  // rect once we know the size.
  const vw = typeof window !== "undefined" ? window.innerWidth : 1024;
  const vh = typeof window !== "undefined" ? window.innerHeight : 768;
  const left = Math.min(menu.state.x, vw - minWidth - 8);
  const top = Math.min(menu.state.y, vh - 40);

  return (
    <_CloseCtx.Provider value={menu.close}>
      <div
        ref={ref}
        role="menu"
        aria-orientation="vertical"
        className="fixed z-50 flex flex-col rounded-md border border-border-subtle bg-elevated py-1 text-sm text-text shadow-lg"
        style={{ left, top, minWidth }}
      >
        {children}
      </div>
    </_CloseCtx.Provider>
  );
}

export function ContextMenuItem({
  onSelect,
  disabled,
  destructive,
  icon,
  shortcut,
  children,
}: {
  onSelect?: () => void;
  disabled?: boolean;
  destructive?: boolean;
  icon?: ReactNode;
  /** Shortcut hint shown right-aligned (e.g. ``⌘C``). Display only. */
  shortcut?: string;
  children: ReactNode;
}) {
  const close = useContext(_CloseCtx);
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={() => {
        if (disabled) return;
        onSelect?.();
        close();
      }}
      className={cn(
        "flex items-center gap-2 px-3 py-1.5 text-left transition-colors duration-100",
        disabled
          ? "cursor-not-allowed text-text-muted opacity-60"
          : destructive
            ? "text-error hover:bg-error/10"
            : "text-text hover:bg-overlay",
      )}
    >
      {icon ? <span className="inline-flex h-4 w-4 items-center justify-center">{icon}</span> : null}
      <span className="flex-1">{children}</span>
      {shortcut ? <span className="text-[10px] text-text-muted">{shortcut}</span> : null}
    </button>
  );
}

export function ContextMenuSeparator() {
  return <div role="separator" className="my-1 h-px bg-border-subtle" />;
}

export function ContextMenuLabel({ children }: { children: ReactNode }) {
  return (
    <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-text-muted">
      {children}
    </div>
  );
}

/**
 * Submenu: hover the trigger row, the child menu slides out to the right.
 * Simple CSS-driven — no portal, parent stays open while submenu is hovered.
 * For deeply nested cases (3+ levels) use multiple separate menus instead.
 */
export function ContextMenuSubmenu({
  label,
  icon,
  children,
}: {
  label: string;
  icon?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="group relative">
      <div
        role="menuitem"
        className="flex cursor-default items-center gap-2 px-3 py-1.5 text-left text-text transition-colors duration-100 hover:bg-overlay"
      >
        {icon ? (
          <span className="inline-flex h-4 w-4 items-center justify-center">{icon}</span>
        ) : null}
        <span className="flex-1">{label}</span>
        <span className="text-text-muted">›</span>
      </div>
      <div
        role="menu"
        className="absolute left-full top-0 z-50 hidden min-w-[200px] -translate-y-1 flex-col rounded-md border border-border-subtle bg-elevated py-1 shadow-lg group-hover:flex"
      >
        {children}
      </div>
    </div>
  );
}
