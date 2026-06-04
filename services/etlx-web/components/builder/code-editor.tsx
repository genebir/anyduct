"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

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
        className="min-h-32 w-full rounded-md border border-border-subtle bg-elevated px-3 py-2 font-mono text-xs text-text-muted"
        value="loading editor…"
      />
    ),
  },
);

/**
 * Generic inline code editor (Monaco) — Phase ADX (2026-06-04).
 *
 * Extracted from ``PythonCodeEditor`` so the SQL fields can share the exact
 * same editor (syntax highlighting, line numbers, theme sync, lazy-load)
 * the Python ``custom_python`` transform uses. ``language`` selects the
 * grammar ("python" | "sql" | …).
 *
 * **Uncontrolled by design** (inherited from the 2026-05-26 cursor-jump
 * fix). ``value`` is the *initial* buffer only — handed to Monaco as
 * ``defaultValue``; later prop changes do NOT mutate the editor. In
 * controlled mode every parent re-render flowed ``value`` back into Monaco
 * and any micro-mismatch triggered ``model.setValue()`` which reset the
 * cursor to position 0. To force a fresh buffer, remount the component (a
 * stable React ``key`` — e.g. ``PropertiesPanel key={node.id}`` — does it;
 * the SQL/visual mode toggle also remounts the editor).
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

  return (
    <div
      className="overflow-hidden rounded-md border border-border-subtle bg-elevated"
      style={{ height }}
    >
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
  );
}
