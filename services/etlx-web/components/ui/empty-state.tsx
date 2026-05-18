"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mx-auto flex max-w-md flex-col items-center gap-4 py-16 text-center",
        className,
      )}
    >
      {icon ? <div className="text-text-muted">{icon}</div> : null}
      <div>
        <div className="text-lg font-semibold text-text">{title}</div>
        {description ? (
          <div className="mt-2 text-sm text-text-secondary">{description}</div>
        ) : null}
      </div>
      {action ? <div className="pt-2">{action}</div> : null}
    </div>
  );
}
