"use client";

import { useEffect } from "react";
import { KeyboardIcon, XIcon } from "lucide-react";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

/**
 * Builder keyboard-shortcut cheat-sheet (Phase L1, 2026-05-26).
 *
 * Opens via the ``?`` key or the keyboard icon in the builder header.
 * The shape mirrors :class:`ConfirmDialog` — fixed backdrop + centred
 * card — so we stay free of a primitive-library dependency.
 *
 * Two-column grid so each row is visually a "key chord → action".
 * Mac/Windows split: we show ``⌘`` on macOS (UA sniff is cheap and
 * correct here — keyboard convention not browser feature). Other
 * platforms see ``Ctrl``. The same key combo binding works in both
 * because :func:`useGraphHistoryShortcuts` checks both ``metaKey``
 * and ``ctrlKey``.
 */
export function ShortcutsDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useLocale();
  // Esc to close — same UX as ConfirmDialog.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  // UA-based key glyph — keyboard convention is platform-bound, not
  // browser-feature-detectable. Server render falls back to "Ctrl" so
  // the HTML doesn't flash an empty span pre-hydration.
  const isMac =
    typeof navigator !== "undefined" && /Mac|iPod|iPhone|iPad/.test(navigator.platform);
  const mod = isMac ? "⌘" : "Ctrl";

  const rows: Array<{ keys: string[]; action: string }> = [
    { keys: [mod, "Z"], action: t("shortcuts.undo") },
    { keys: [mod, "⇧", "Z"], action: t("shortcuts.redo") },
    { keys: [mod, "S"], action: t("shortcuts.save") },
    { keys: [mod, "D"], action: t("shortcuts.duplicate") },
    { keys: [mod, "L"], action: t("shortcuts.autoLayout") },
    { keys: ["Delete"], action: t("shortcuts.delete") },
    { keys: ["⇧", t("shortcuts.click")], action: t("shortcuts.multiSelect") },
    { keys: [t("shortcuts.rightClick")], action: t("shortcuts.contextMenu") },
    { keys: [t("shortcuts.drag")], action: t("shortcuts.addNode") },
    { keys: ["?"], action: t("shortcuts.openHelp") },
    { keys: ["Esc"], action: t("shortcuts.deselect") },
  ];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-bg/80 px-4 py-8 backdrop-blur-sm"
      onClick={onClose}
      role="dialog"
      aria-modal
      aria-labelledby="shortcuts-title"
    >
      <div
        className="relative w-full max-w-md rounded-lg border border-border-subtle bg-surface p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="mb-4 flex items-start justify-between">
          <div className="flex items-center gap-2">
            <KeyboardIcon size={18} className="text-text-secondary" />
            <h2 id="shortcuts-title" className="text-base font-semibold text-text">
              {t("shortcuts.title")}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("common.close")}
            className="rounded-sm p-1 text-text-muted hover:bg-overlay hover:text-text"
          >
            <XIcon size={16} />
          </button>
        </header>
        <p className="mb-4 text-xs text-text-secondary">{t("shortcuts.intro")}</p>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          {rows.map((row, i) => (
            <Row key={i} keys={row.keys} action={row.action} />
          ))}
        </dl>
      </div>
    </div>
  );
}

function Row({ keys, action }: { keys: string[]; action: string }) {
  return (
    <>
      <dt className="flex items-center gap-1 whitespace-nowrap text-right">
        {keys.map((k, i) => (
          <Kbd key={i}>{k}</Kbd>
        ))}
      </dt>
      <dd className="self-center text-text-secondary">{action}</dd>
    </>
  );
}

function Kbd({ children }: { children: string }) {
  return (
    <kbd className="inline-flex h-6 min-w-6 items-center justify-center rounded border border-border-subtle bg-elevated px-1.5 font-mono text-[11px] font-medium text-text shadow-sm">
      {children}
    </kbd>
  );
}
