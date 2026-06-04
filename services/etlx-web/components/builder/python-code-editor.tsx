"use client";

import { CodeEditor } from "./code-editor";

// Drawer (520px wide, 2026-05-26) gives Monaco real room — 480px tall keeps
// roughly ~25 lines visible without overwhelming the rest of the panel.
const DEFAULT_HEIGHT = 480;

/**
 * Inline Python code editor for the ``custom_python`` transform
 * (ADR-0041 I2). Thin wrapper over the shared {@link CodeEditor} (Phase
 * ADX) pinned to ``language="python"``; the uncontrolled-by-design and
 * lazy-load notes live there.
 *
 * The starter ``transform(record)`` shape is seeded by the caller when
 * the saved value is empty, so first-time users don't stare at a blank
 * editor.
 */
export function PythonCodeEditor({
  value,
  onChange,
  height = DEFAULT_HEIGHT,
}: {
  /** Initial buffer. Read once at mount (uncontrolled — remount to reset). */
  value: string;
  onChange: (next: string) => void;
  height?: number;
}) {
  return (
    <CodeEditor
      language="python"
      value={value}
      onChange={onChange}
      height={height}
      tabSize={4}
    />
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
