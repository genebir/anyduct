"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface Column<Row> {
  key: string;
  header: ReactNode;
  cell: (row: Row) => ReactNode;
  className?: string;
}

export function DataTable<Row extends { id: string }>({
  columns,
  rows,
  emptyState,
  onRowClick,
}: {
  columns: Column<Row>[];
  rows: Row[];
  emptyState?: ReactNode;
  onRowClick?: (row: Row) => void;
}) {
  if (rows.length === 0 && emptyState) {
    return <div className="py-8">{emptyState}</div>;
  }
  return (
    <div className="overflow-hidden rounded-lg border border-border-subtle">
      <table className="w-full border-collapse text-sm">
        <thead className="sticky top-0 bg-surface">
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                className={cn(
                  "border-b border-border-subtle px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-text-secondary",
                  col.className,
                )}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.id}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={cn(
                "border-b border-border-subtle bg-bg transition duration-150",
                onRowClick &&
                  "cursor-pointer hover:bg-overlay focus-within:bg-overlay",
              )}
            >
              {columns.map((col) => (
                <td
                  key={col.key}
                  className={cn(
                    "px-4 py-3 align-middle text-text",
                    col.className,
                  )}
                >
                  {col.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
