"use client";

import { useEffect, type ReactNode } from "react";
import { Button } from "./button";
import { cn } from "@/lib/cn";

/**
 * Small token-aligned confirmation dialog.
 *
 * Renders via a fixed backdrop + center card so we don't take a dependency
 * on a full primitive library this slice (DESIGN.md §7.9). Closes on Esc /
 * backdrop click; the destructive button is auto-focused so the keyboard
 * default path is "cancel".
 */
export function ConfirmDialog({
  open,
  title,
  description,
  body,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  destructive = false,
  loading = false,
  confirmDisabled = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  /** Optional rich content rendered between the description and the
   *  action buttons — e.g. an input or a CronInput when the dialog
   *  collects a value (Phase AAY). */
  body?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  loading?: boolean;
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className={cn(
        "fixed inset-0 z-50 flex items-center justify-center px-4",
        "bg-[rgb(10_18_40_/_0.6)] backdrop-blur-md",
      )}
      onClick={onCancel}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        onClick={(e) => e.stopPropagation()}
        className={cn(
          "w-full max-w-md rounded-xl border border-border-subtle bg-surface p-6 shadow-lg",
        )}
      >
        <h2
          id="confirm-dialog-title"
          className="text-lg font-semibold text-text"
        >
          {title}
        </h2>
        {description ? (
          <p className="mt-2 text-sm text-text-secondary">{description}</p>
        ) : null}
        {body ? <div className="mt-4">{body}</div> : null}
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="ghost" onClick={onCancel} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? "destructive" : "primary"}
            onClick={onConfirm}
            loading={loading}
            disabled={confirmDisabled}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
