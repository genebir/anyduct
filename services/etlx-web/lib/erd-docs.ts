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
 *   - **매핑 정의서 (Source→Target Mapping)** — target side filled from the
 *     ERD, source/transform/load columns left blank — the S2T template.
 *   - **전체 정의서 (Markdown)** — a human-readable all-in-one for wiki/PR.
 *
 * CSVs are UTF-8 **with BOM + CRLF** so Excel opens Korean correctly.
 */

import type { ErdDesign, DesignTable } from "@/lib/erd-design";

const BOM = "﻿";

function esc(v: unknown): string {
  const s = v == null ? "" : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
function row(cells: unknown[]): string {
  return cells.map(esc).join(",");
}
function csv(headers: string[], rows: unknown[][]): string {
  return BOM + [row(headers), ...rows.map(row)].join("\r\n");
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

export function columnDictionaryCsv(design: ErdDesign): string {
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
      rows.push([
        no, t.name, "", i + 1, c.name, "",
        base, length, scale, c.pk ? "Y" : "", c.pk ? "Y" : "N", fk ? "Y" : "N",
        fk?.targetTable ?? "", fk?.targetCol ?? "", "", "", "",
      ]);
    });
  }
  return csv(headers, rows);
}

// --- 테이블 정의서 (Table Definition) ---------------------------------------

export function tableDefinitionCsv(design: ErdDesign): string {
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
    i + 1, t.name, "", t.columns.length, pkColumnsOf(t).join(", "),
    [...(refsTo.get(t.id) ?? [])].join(", "), [...(refBy.get(t.id) ?? [])].join(", "), "", "",
  ]);
  return csv(headers, rows);
}

// --- 매핑 정의서 (Source → Target Mapping) ----------------------------------

export function mappingSpecCsv(design: ErdDesign): string {
  const headers = [
    "No", "대상 테이블", "대상 컬럼", "대상 데이터타입", "대상 NOT NULL", "PK",
    "매핑 유형", "변환 규칙", "원본 시스템", "원본 스키마", "원본 테이블",
    "원본 컬럼", "원본 데이터타입", "적재 방식", "검증 규칙", "비고",
  ];
  const rows: unknown[][] = [];
  let no = 0;
  for (const t of design.tables) {
    for (const c of t.columns) {
      no += 1;
      rows.push([
        no, t.name, c.name, c.type, c.pk ? "Y" : "", c.pk ? "Y" : "",
        "", "", "", "", "", "", "", "", "", "",
      ]);
    }
  }
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
    lines.push(`## ${t.name}`);
    lines.push("");
    lines.push(`- PK: ${pkColumnsOf(t).join(", ") || "—"}`);
    lines.push("");
    lines.push("| # | 컬럼 | 타입 | PK | NOT NULL | FK → |");
    lines.push("|---|------|------|----|----------|------|");
    t.columns.forEach((c, i) => {
      const fk = fks.get(`${t.id}::${c.name}`);
      lines.push(
        `| ${i + 1} | ${c.name} | ${c.type} | ${c.pk ? "✔" : ""} | ${c.pk ? "✔" : ""} | ${fk ? `${fk.targetTable}.${fk.targetCol}` : ""} |`,
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
    lines.push(`| ${from.name} | ${r.fromColumn} | → | ${to.name} | ${pkColumnOf(to)} | ${card} |`);
  }
  lines.push("");
  return lines.join("\n");
}
