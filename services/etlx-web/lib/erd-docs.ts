/**
 * ERD → data-engineering documents (Phase AIK).
 *
 * Generates the deliverables a data engineer actually hands off, derived
 * from the ERD model (tables / columns / types / PK / FK):
 *
 *   - **컬럼 정의서 (Data Dictionary)** — every column with type/length/
 *     scale/PK/FK/참조/NOT-NULL, plus blank logical-name/description/default
 *     columns to fill in.
 *   - **테이블 정의서 (Table Definition)** — per table: column count, PK,
 *     FK-referenced and referenced-by tables.
 *   - **전체 정의서 (Markdown)** — a human-readable all-in-one for wiki/PR.
 *
 * CSVs are UTF-8 **with BOM + CRLF** so Excel opens Korean correctly.
 */

import type { ErdDesign, DesignTable } from "@/lib/erd-design";

const BOM = "﻿";

function esc(v: unknown): string {
  let s = v == null ? "" : String(v);
  // Neutralize CSV formula injection (OWASP): a cell beginning with = + - @
  // or a tab/CR is run as a formula by Excel/Sheets. Prefix with a single
  // quote so spreadsheets treat it as text.
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
function row(cells: unknown[]): string {
  return cells.map(esc).join(",");
}
function csv(headers: string[], rows: unknown[][]): string {
  return BOM + [row(headers), ...rows.map(row)].join("\r\n");
}

/** Escape a value for a Markdown table cell (pipes break the table; newlines
 *  split the row). */
function mdCell(v: unknown): string {
  return (v == null ? "" : String(v)).replace(/\|/g, "\\|").replace(/\r?\n/g, "<br>");
}

/** Split a SQL type into base + length + scale: VARCHAR(255) → {VARCHAR,255,""},
 *  NUMERIC(10,2) → {NUMERIC,10,2}, DATETIME → {DATETIME,"",""}. */
function parseType(type: string): { base: string; length: string; scale: string } {
  const m = type.match(/^([^(]+)\(([^)]*)\)\s*$/);
  if (!m) return { base: type.trim(), length: "", scale: "" };
  const args = m[2].split(",").map((s) => s.trim());
  return { base: m[1].trim(), length: args[0] ?? "", scale: args[1] ?? "" };
}

function pkColumnsOf(t: DesignTable): string[] {
  return t.columns.filter((c) => c.pk).map((c) => c.name);
}
function pkColumnOf(t: DesignTable): string {
  return pkColumnsOf(t)[0] ?? t.columns[0]?.name ?? "";
}

interface Fk {
  targetTable: string;
  targetCol: string;
}
/** ``"<tableId>::<colName>" → { targetTable, targetCol }`` for FK columns. */
function fkIndex(design: ErdDesign): Map<string, Fk> {
  const byId = new Map(design.tables.map((t) => [t.id, t]));
  const out = new Map<string, Fk>();
  for (const r of design.relations) {
    const to = byId.get(r.to);
    if (!to) continue;
    out.set(`${r.from}::${r.fromColumn}`, { targetTable: to.name, targetCol: pkColumnOf(to) });
  }
  return out;
}

// --- 컬럼 정의서 (Data Dictionary) ------------------------------------------

export function columnDictionaryRows(design: ErdDesign): { headers: string[]; rows: unknown[][] } {
  const fks = fkIndex(design);
  const headers = [
    "No", "테이블 물리명", "테이블 논리명", "컬럼 순번", "컬럼 물리명", "컬럼 논리명",
    "데이터 타입", "길이", "소수 자릿수", "NOT NULL", "PK", "FK",
    "참조 테이블", "참조 컬럼", "기본값", "컬럼 설명", "비고",
  ];
  const rows: unknown[][] = [];
  let no = 0;
  for (const t of design.tables) {
    t.columns.forEach((c, i) => {
      no += 1;
      const { base, length, scale } = parseType(c.type);
      const fk = fks.get(`${t.id}::${c.name}`);
      const notNull = c.pk || c.notNull ? "Y" : "";
      rows.push([
        no, t.name, t.logical ?? "", i + 1, c.name, c.logical ?? "",
        base, length, scale, notNull, c.pk ? "Y" : "N", fk ? "Y" : "N",
        fk?.targetTable ?? "", fk?.targetCol ?? "", c.defaultValue ?? "", c.comment ?? "", "",
      ]);
    });
  }
  return { headers, rows };
}

export function columnDictionaryCsv(design: ErdDesign): string {
  const { headers, rows } = columnDictionaryRows(design);
  return csv(headers, rows);
}

// --- 테이블 정의서 (Table Definition) ---------------------------------------

export function tableDefinitionRows(design: ErdDesign): { headers: string[]; rows: unknown[][] } {
  const byId = new Map(design.tables.map((t) => [t.id, t]));
  const refsTo = new Map<string, Set<string>>(); // tableId → names it references (FK→)
  const refBy = new Map<string, Set<string>>(); // tableId → names that reference it
  const push = (m: Map<string, Set<string>>, k: string, v: string) => {
    const s = m.get(k) ?? new Set<string>();
    s.add(v);
    m.set(k, s);
  };
  for (const r of design.relations) {
    const from = byId.get(r.from);
    const to = byId.get(r.to);
    if (!from || !to) continue;
    if (from.id !== to.id) {
      push(refsTo, from.id, to.name);
      push(refBy, to.id, from.name);
    } else {
      push(refsTo, from.id, `${to.name} (self)`);
    }
  }
  const headers = [
    "No", "테이블 물리명", "테이블 논리명", "컬럼 수", "PK 컬럼",
    "참조(FK→) 테이블", "피참조 테이블", "테이블 설명", "비고",
  ];
  const rows = design.tables.map((t, i) => [
    i + 1, t.name, t.logical ?? "", t.columns.length, pkColumnsOf(t).join(", "),
    [...(refsTo.get(t.id) ?? [])].join(", "), [...(refBy.get(t.id) ?? [])].join(", "), t.comment ?? "", "",
  ]);
  return { headers, rows };
}

export function tableDefinitionCsv(design: ErdDesign): string {
  const { headers, rows } = tableDefinitionRows(design);
  return csv(headers, rows);
}

// --- 제약조건 / 인덱스 정의서 (Constraints & Indexes) -----------------------

export function constraintSpecRows(design: ErdDesign): { headers: string[]; rows: unknown[][] } {
  const byId = new Map(design.tables.map((t) => [t.id, t]));
  const headers = [
    "No", "테이블", "제약 유형", "제약/인덱스명", "컬럼", "참조 테이블", "참조 컬럼", "설명",
  ];
  const rows: unknown[][] = [];
  let no = 0;
  // PK constraints.
  for (const t of design.tables) {
    const pks = pkColumnsOf(t);
    if (pks.length > 0) {
      no += 1;
      rows.push([no, t.name, "PK", `PK_${t.name}`, pks.join(", "), "", "", "기본키"]);
    }
  }
  // FK constraints + a recommended index on each FK column (join performance).
  for (const r of design.relations) {
    const from = byId.get(r.from);
    const to = byId.get(r.to);
    if (!from || !to) continue;
    no += 1;
    rows.push([
      no, from.name, "FK", `FK_${from.name}_${to.name}`, r.fromColumn, to.name, pkColumnOf(to), "외래키",
    ]);
    no += 1;
    rows.push([
      no, from.name, "INDEX(권장)", `IX_${from.name}_${r.fromColumn}`, r.fromColumn, "", "",
      "FK 조인 성능용 권장 인덱스",
    ]);
  }
  return { headers, rows };
}

export function constraintSpecCsv(design: ErdDesign): string {
  const { headers, rows } = constraintSpecRows(design);
  return csv(headers, rows);
}

// --- 전체 정의서 (Markdown) -------------------------------------------------

export function fullSpecMarkdown(design: ErdDesign, diagramName: string, dateStr: string): string {
  const fks = fkIndex(design);
  const colCount = design.tables.reduce((s, t) => s + t.columns.length, 0);
  const lines: string[] = [];
  lines.push(`# ${diagramName} — 데이터 정의서`);
  lines.push("");
  lines.push(`> 생성일: ${dateStr} · 테이블 ${design.tables.length}개 · 컬럼 ${colCount}개 · 관계 ${design.relations.length}개`);
  lines.push("");
  lines.push("## 목차");
  design.tables.forEach((t) => lines.push(`- [${t.name}](#${t.name.toLowerCase().replace(/\s+/g, "-")})`));
  lines.push("");
  for (const t of design.tables) {
    lines.push(`## ${t.name}${t.logical ? ` (${t.logical})` : ""}`);
    lines.push("");
    if (t.comment) lines.push(`${t.comment}`, "");
    lines.push(`- PK: ${pkColumnsOf(t).join(", ") || "—"}`);
    lines.push("");
    lines.push("| # | 컬럼 | 논리명 | 타입 | PK | NOT NULL | FK → | 기본값 | 설명 |");
    lines.push("|---|------|--------|------|----|----------|------|--------|------|");
    t.columns.forEach((c, i) => {
      const fk = fks.get(`${t.id}::${c.name}`);
      const nn = c.pk || c.notNull ? "✔" : "";
      lines.push(
        `| ${i + 1} | ${mdCell(c.name)} | ${mdCell(c.logical)} | ${mdCell(c.type)} | ${c.pk ? "✔" : ""} | ${nn} | ${fk ? mdCell(`${fk.targetTable}.${fk.targetCol}`) : ""} | ${mdCell(c.defaultValue)} | ${mdCell(c.comment)} |`,
      );
    });
    lines.push("");
  }
  // Relationships appendix.
  const byId = new Map(design.tables.map((t) => [t.id, t]));
  lines.push("## 관계 (Foreign Keys)");
  lines.push("");
  lines.push("| 자식 테이블 | 자식 컬럼 | → | 부모 테이블 | 부모 컬럼 | 카디널리티 |");
  lines.push("|-------------|-----------|---|-------------|-----------|------------|");
  for (const r of design.relations) {
    const from = byId.get(r.from);
    const to = byId.get(r.to);
    if (!from || !to) continue;
    const card = `${r.sourceCard ?? "many"}:${r.targetCard ?? "one"}`;
    lines.push(`| ${mdCell(from.name)} | ${mdCell(r.fromColumn)} | → | ${mdCell(to.name)} | ${mdCell(pkColumnOf(to))} | ${card} |`);
  }
  lines.push("");
  return lines.join("\n");
}
