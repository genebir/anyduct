"use client";

import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  invalid?: boolean;
};

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, invalid, ...rest }, ref) => (
    <input
      ref={ref}
      aria-invalid={invalid || undefined}
      className={cn(
        "h-10 w-full rounded-md bg-elevated px-3 text-sm text-text",
        "border border-border-subtle placeholder:text-text-muted",
        "transition duration-200",
        "focus-visible:outline-none focus-visible:border-accent focus-visible:ring-accent",
        invalid && "border-error",
        className,
      )}
      {...rest}
    />
  ),
);
Input.displayName = "Input";
