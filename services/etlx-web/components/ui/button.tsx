"use client";

import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "destructive" | "outline";
type Size = "sm" | "md" | "lg";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
}

const VARIANT_CLASSES: Record<Variant, string> = {
  primary:
    "bg-accent-gradient text-white hover:brightness-110 active:brightness-95",
  secondary:
    "bg-elevated text-text border border-border-subtle hover:bg-overlay",
  ghost: "bg-transparent text-text-secondary hover:bg-overlay hover:text-text",
  destructive: "bg-error text-white hover:brightness-110",
  outline:
    "bg-transparent border border-border text-text hover:bg-overlay",
};

const SIZE_CLASSES: Record<Size, string> = {
  sm: "h-8 px-3 text-sm rounded-md gap-1.5",
  md: "h-10 px-4 text-sm rounded-md gap-2",
  lg: "h-12 px-5 text-base rounded-md gap-2",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      className,
      variant = "primary",
      size = "md",
      loading = false,
      disabled,
      children,
      ...rest
    },
    ref,
  ) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        // ``whitespace-nowrap`` prevents the row-button collapse where the
        // spinner pushed CJK labels onto two lines (user report 2026-05-26:
        // "실행 → 실/행"). ``relative`` so the spinner can overlay the
        // children without affecting layout.
        "relative inline-flex items-center justify-center whitespace-nowrap font-medium transition duration-200 ease-out",
        "disabled:cursor-not-allowed disabled:opacity-60",
        "focus-visible:outline-none",
        VARIANT_CLASSES[variant],
        SIZE_CLASSES[size],
        className,
      )}
      {...rest}
    >
      {/* Spinner overlays the centre of the button while loading; children
          stay in the flow but get visually muted via ``invisible`` so the
          button keeps its idle width (no jiggle when entering / leaving
          the loading state). */}
      {loading ? (
        <span
          className="pointer-events-none absolute inset-0 inline-flex items-center justify-center"
          aria-hidden
        >
          <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
        </span>
      ) : null}
      <span className={cn("inline-flex items-center gap-[inherit]", loading && "invisible")}>
        {children}
      </span>
    </button>
  ),
);
Button.displayName = "Button";
