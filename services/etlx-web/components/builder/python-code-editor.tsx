"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

// Monaco is heavy (~500 KB from CDN). Lazy-load with SSR disabled so the
// initial pipeline-builder bundle stays cheap; the editor only spins up the
// first time a custom_python operator's panel opens.
const MonacoEditor = dynamic(
  () => import("@monaco-editor/react").then((m) => m.Editor),
  {
    ssr: false,
    loading: () => (
      <textarea
        readOnly
        aria-busy
        className="min-h-48 w-full rounded-md border border-border-subtle bg-elevated px-3 py-2 font-mono text-xs text-text-muted"
        value="// loading editor…"
      />
    ),
  },
);

// Drawer (520px wide, 2026-05-26) gives Monaco real room — 480px tall keeps
// roughly ~25 lines visible without overwhelming the rest of the panel.
const DEFAULT_HEIGHT = 480;

/**
 * Inline Python code editor for the ``custom_python`` transform
 * (ADR-0041 I2). Monospace + line numbers + Python syntax highlighting via
 * Monaco; falls back to a plain ``<textarea>`` during lazy-load.
 *
 * The user's source must define a top-level ``transform(record)`` function;
 * the editor seeds an empty buffer with that signature so first-time users
 * have something runnable.
 */
export function PythonCodeEditor({
  value,
  onChange,
  height = DEFAULT_HEIGHT,
}: {
  value: string;
  onChange: (next: string) => void;
  height?: number;
}) {
  // Detect dark / light from the `data-theme` attribute set by ThemeProvider.
  // Monaco needs a string ("vs-dark" | "light") so we re-evaluate on mount
  // and on `data-theme` mutations (the toggle in the header).
  const [theme, setTheme] = useState<"vs-dark" | "light">("vs-dark");
  useEffect(() => {
    const read = () =>
      setTheme(document.documentElement.dataset.theme === "light" ? "light" : "vs-dark");
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
        language="python"
        theme={theme}
        value={value}
        onChange={(v) => onChange(v ?? "")}
        options={{
          minimap: { enabled: false },
          fontSize: 12,
          tabSize: 4,
          insertSpaces: true,
          scrollBeyondLastLine: false,
          renderLineHighlight: "line",
          // No telemetry / outbound calls beyond the loader fetching Monaco.
          automaticLayout: true,
          wordWrap: "on",
        }}
      />
    </div>
  );
}

/** Starter code seeded into a fresh ``custom_python`` node — gives the user
 *  a runnable shape so they don't stare at an empty editor. */
export const PYTHON_CODE_STARTER = `def transform(record):
    # \`record.data\` is a dict; return a (possibly new) Record or None to drop.
    return record.__class__(
        data=dict(record.data),
        metadata=record.metadata,
        schema_version=record.schema_version,
    )
`;
