/**
 * ERD data-quality validation (Phase AIO).
 *
 * Surfaces the model gaps that matter before handing off definition docs —
 * most importantly **tables with no primary key** (the .damx importer can't
 * always recover a dimension table's sole PK, so the modeler should set it).
 * Pure + exported so the panel and any future doc-lint share one source.
 */

import type { ErdDesign } from "@/lib/erd-design";

export type ErdIssueKind = "no_pk" | "no_columns" | "dup_column" | "untyped_column";

export interface ErdIssue {
  kind: ErdIssueKind;
  tableId: string;
  tableName: string;
  column?: string;
  severity: "warning" | "info";
}

export function validateErd(design: ErdDesign): ErdIssue[] {
  const issues: ErdIssue[] = [];
  for (const t of design.tables) {
    if (t.columns.length === 0) {
      issues.push({ kind: "no_columns", tableId: t.id, tableName: t.name, severity: "warning" });
      continue;
    }
    if (!t.columns.some((c) => c.pk)) {
      issues.push({ kind: "no_pk", tableId: t.id, tableName: t.name, severity: "warning" });
    }
    const seen = new Set<string>();
    for (const c of t.columns) {
      if (c.name && seen.has(c.name)) {
        issues.push({ kind: "dup_column", tableId: t.id, tableName: t.name, column: c.name, severity: "warning" });
      }
      seen.add(c.name);
      if (!c.type || !c.type.trim()) {
        issues.push({ kind: "untyped_column", tableId: t.id, tableName: t.name, column: c.name, severity: "info" });
      }
    }
  }
  return issues;
}
