"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";
import { Maximize2Icon, Minimize2Icon } from "lucide-react";
import { useLocale } from "@/components/providers/locale-provider";

// Monaco is heavy (~500 KB from CDN). Lazy-load with SSR disabled so the
// initial pipeline-builder bundle stays cheap; the editor only spins up the
// first time a code/SQL field's panel opens.
const MonacoEditor = dynamic(
  () => import("@monaco-editor/react").then((m) => m.Editor),
  {
    ssr: false,
    loading: () => (
      <textarea
        readOnly
        aria-busy
        className="h-full min-h-32 w-full resize-none border-0 bg-elevated px-3 py-2 font-mono text-xs text-text-muted outline-none"
        value="loading editor…"
      />
    ),
  },
);

/**
 * Generic inline code editor (Monaco) — Phase ADX (2026-06-04),
 * fullscreen toggle in ADZ.
 *
 * Extracted from ``PythonCodeEditor`` so the SQL fields can share the exact
 * same editor (syntax highlighting, line numbers, theme sync, lazy-load)
 * the Python ``custom_python`` transform uses. ``language`` selects the
 * grammar ("python" | "sql").
 *
 * **Uncontrolled by design** (inherited from the 2026-05-26 cursor-jump
 * fix). ``value`` is the *initial* buffer only — handed to Monaco as
 * ``defaultValue``; later prop changes do NOT mutate the editor. To force a
 * fresh buffer, remount the component (a stable React ``key`` does it).
 *
 * **Fullscreen (ADZ)** only swaps the *container* className (inline box ↔
 * ``fixed inset-0`` overlay); the ``<MonacoEditor>`` element stays at the
 * same tree position so it is **not** remounted — the buffer and cursor
 * survive the toggle, and ``automaticLayout`` reflows it to the new size.
 */
export function CodeEditor({
  language,
  value,
  onChange,
  height = 480,
  tabSize = 4,
}: {
  language: "python" | "sql";
  /** Initial buffer — read once at mount (Monaco ``defaultValue``). */
  value: string;
  onChange: (next: string) => void;
  height?: number;
  tabSize?: number;
}) {
  const { t } = useLocale();
  // Detect dark / light from the `data-theme` attribute set by ThemeProvider.
  const [theme, setTheme] = useState<"vs-dark" | "light">("vs-dark");
  useEffect(() => {
    const read = () =>
      setTheme(
        document.documentElement.dataset.theme === "light" ? "light" : "vs-dark",
      );
    read();
    const obs = new MutationObserver(read);
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => obs.disconnect();
  }, []);

  const [fullscreen, setFullscreen] = useState(false);
  // Esc exits fullscreen — matches the dialog convention elsewhere.
  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  return (
    <div
      className={
        fullscreen
          ? "fixed inset-0 z-50 flex flex-col bg-bg"
          : "relative flex flex-col overflow-hidden rounded-md border border-border-subtle bg-elevated"
      }
      style={fullscreen ? undefined : { height }}
    >
      {/* Toggle control — a header bar in fullscreen, a corner button inline.
          Index-0 child either way; React replaces just this node on toggle,
          leaving the editor (index-1) mounted. */}
      {fullscreen ? (
        <div className="flex items-center justify-between border-b border-border-subtle px-4 py-2">
          <span className="font-mono text-[11px] uppercase tracking-wider text-text-muted">
            {language}
          </span>
          <button
            type="button"
            onClick={() => setFullscreen(false)}
            aria-label={t("builder.collapseEditor")}
            className="inline-flex items-center gap-1 rounded-sm px-2 py-1 text-xs text-text-secondary transition hover:bg-overlay hover:text-text"
          >
            <Minimize2Icon size={13} />
            {t("builder.collapseEditor")}
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setFullscreen(true)}
          aria-label={t("builder.expandEditor")}
          title={t("builder.expandEditor")}
          className="absolute right-1.5 top-1.5 z-10 inline-flex h-6 w-6 items-center justify-center rounded-sm bg-bg/70 text-text-muted backdrop-blur transition hover:bg-overlay hover:text-text"
        >
          <Maximize2Icon size={13} />
        </button>
      )}
      <div className={fullscreen ? "min-h-0 flex-1" : "min-h-0 flex-1"}>
        <MonacoEditor
          language={language}
          theme={theme}
          defaultValue={value}
          onChange={(v) => onChange(v ?? "")}
          options={{
            minimap: { enabled: false },
            fontSize: 12,
            tabSize,
            insertSpaces: true,
            scrollBeyondLastLine: false,
            renderLineHighlight: "line",
            automaticLayout: true,
            wordWrap: "on",
          }}
        />
      </div>
    </div>
  );
}
