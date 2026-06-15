"use client";

/**
 * Checkbox primitive (Step 10.8 polish, 2026-06-12 — user feedback: the
 * native `accent-color` control read as a foreign element on the dark
 * theme). A real `<input type="checkbox">` (form semantics, keyboard,
 * focus ring from the global focus-visible rule) with a custom
 * appearance defined in globals.css (`.checkbox`): token borders, accent
 * fill, navy check/dash marks. `indeterminate` covers the "some of the
 * visible rows are selected" header state.
 */

import { forwardRef, useEffect, useRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export interface CheckboxProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  indeterminate?: boolean;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  ({ indeterminate = false, className, ...rest }, forwardedRef) => {
    const innerRef = useRef<HTMLInputElement | null>(null);
    useEffect(() => {
      if (innerRef.current) innerRef.current.indeterminate = indeterminate;
    }, [indeterminate]);
    return (
      <input
        ref={(node) => {
          innerRef.current = node;
          if (typeof forwardedRef === "function") forwardedRef(node);
          else if (forwardedRef) forwardedRef.current = node;
        }}
        type="checkbox"
        className={cn("checkbox", className)}
        {...rest}
      />
    );
  },
);
Checkbox.displayName = "Checkbox";
