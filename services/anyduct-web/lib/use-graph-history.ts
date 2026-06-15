"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GraphBuilderState } from "@/lib/pipeline-config";

/**
 * History-tracked graph state for the pipeline builder (Phase L1).
 *
 * The builder used to expose ``[state, setState]`` directly through
 * ``useState`` — fine until the user wanted to recover from a stray
 * delete or a runaway drag. This hook wraps the same shape but pushes
 * every committed change onto an undo stack capped at ``capacity``,
 * giving the builder Cmd+Z / Cmd+Shift+Z + toolbar buttons without
 * scattering history logic across the editor.
 *
 * Design choices worth noting:
 *  * **One snapshot per *committed* change.** Drag-in-progress (every
 *    ``onNodeDrag`` tick) would explode the stack; the caller is
 *    expected to ``commit()`` at meaningful boundaries — ``onChange``
 *    from the GraphEditor is already that boundary because the editor
 *    only fires it on user-completed actions (drop, connect, edit).
 *  * **Initial load is *not* an undo target.** ``setInitial`` clears
 *    the stack so the first thing the user can undo is their first
 *    edit, not the network fetch that loaded the pipeline.
 *  * **Equality short-circuit.** If ``commit`` receives a state
 *    identical to the current top (same reference *and* same shape),
 *    the push is skipped — prevents redundant snapshots from React
 *    re-renders triggered by parent state.
 *  * **capacity** caps memory (default 100). The oldest snapshots get
 *    dropped when the stack overflows.
 */
export interface GraphHistory {
  state: GraphBuilderState | null;
  commit: (next: GraphBuilderState) => void;
  setInitial: (next: GraphBuilderState | null) => void;
  undo: () => void;
  redo: () => void;
  canUndo: boolean;
  canRedo: boolean;
  /** Position within the stack of the currently-visible snapshot. Useful
   *  for tying "saved" state to the history — callers stash the index
   *  at save time and compare against this on every render to decide
   *  whether the graph differs from the last persisted version. ``-1``
   *  while the stack is empty (no initial loaded yet). */
  index: number;
}

const DEFAULT_CAPACITY = 100;

export function useGraphHistory(capacity = DEFAULT_CAPACITY): GraphHistory {
  // We store the entire stack in a single useState so undo/redo trigger
  // a single re-render. ``index`` points at the *current* snapshot;
  // undo decrements, redo increments.
  const [stack, setStack] = useState<GraphBuilderState[]>([]);
  const [index, setIndex] = useState<number>(-1);

  const state = index >= 0 ? stack[index] ?? null : null;

  const setInitial = useCallback((next: GraphBuilderState | null) => {
    // Clear the stack so the load itself isn't an undo target. A null
    // initial (still loading) leaves us at index=-1.
    if (next === null) {
      setStack([]);
      setIndex(-1);
    } else {
      setStack([next]);
      setIndex(0);
    }
  }, []);

  const commit = useCallback(
    (next: GraphBuilderState) => {
      setStack((cur) => {
        // Identity short-circuit — caller passes the same object back.
        if (cur[index] === next) return cur;
        // Drop the redo tail: a new edit invalidates any branch we'd
        // jumped back from.
        const truncated = cur.slice(0, index + 1);
        const pushed = [...truncated, next];
        // Capacity cap from the *front* so the most recent snapshots
        // win — old history falls off but Cmd+Z still works for the
        // last ``capacity`` actions.
        if (pushed.length > capacity) {
          return pushed.slice(pushed.length - capacity);
        }
        return pushed;
      });
      setIndex((cur) => {
        const projected = cur + 1;
        // Mirror the slice-from-front behaviour above: if we capped,
        // the new index sits at the *new* top, not at cur+1.
        return Math.min(projected, capacity - 1);
      });
    },
    [capacity, index],
  );

  const undo = useCallback(() => {
    setIndex((cur) => (cur > 0 ? cur - 1 : cur));
  }, []);

  const redo = useCallback(() => {
    setIndex((cur) => (cur < stack.length - 1 ? cur + 1 : cur));
  }, [stack.length]);

  return useMemo(
    () => ({
      state,
      commit,
      setInitial,
      undo,
      redo,
      canUndo: index > 0,
      canRedo: index >= 0 && index < stack.length - 1,
      index,
    }),
    [state, commit, setInitial, undo, redo, index, stack.length],
  );
}

/**
 * Wires Cmd+Z / Cmd+Shift+Z (and Ctrl variants for non-Mac) onto the
 * given history. Intentionally global on ``window`` so the user can
 * undo from anywhere in the builder, not just when the canvas has
 * focus — typing in a side-panel field shouldn't intercept Cmd+Z
 * though, so the handler bails when the event target is an editable
 * element. Browser-native undo on inputs / textareas / Monaco still
 * works because we never preventDefault unless the keystroke is the
 * graph-undo combo *outside* an editable surface.
 */
export function useGraphHistoryShortcuts(history: GraphHistory): void {
  // The handler closes over the latest history via a ref so we don't
  // re-attach the listener on every commit (which would lose any
  // keystrokes that arrived during the swap).
  const ref = useRef(history);
  useEffect(() => {
    ref.current = history;
  }, [history]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      // Skip if the user is typing into something — Monaco, inputs,
      // textareas, contenteditable. ``isEditableElement`` covers them.
      if (isEditableElement(e.target)) return;
      const key = e.key.toLowerCase();
      if (key === "z" && !e.shiftKey) {
        e.preventDefault();
        ref.current.undo();
      } else if ((key === "z" && e.shiftKey) || key === "y") {
        // Cmd+Shift+Z is the Mac convention; Ctrl+Y is the Windows
        // convention. Bind both so muscle memory just works.
        e.preventDefault();
        ref.current.redo();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

function isEditableElement(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  // Monaco renders into a div with role="textbox" + a hidden textarea
  // child; the focus actually lives on the textarea so the INPUT check
  // above catches it. The role check is a belt-and-suspenders fallback
  // for any other editor we drop in later.
  if (target.getAttribute("role") === "textbox") return true;
  return false;
}
