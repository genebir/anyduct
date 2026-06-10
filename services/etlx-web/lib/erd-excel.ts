/**
 * ERD → styled Excel workbook (Phase AKN).
 *
 * One .xlsx with the full spec split across sheets — 개요(cover), 테이블
 * 정의서, 컬럼 정의서, 관계/제약, 주제영역 — styled after
 * Apple's design language: near-black ink on white, a single restrained
 * accent, hairline rules instead of grids, generous row heights, frozen
 * semibold header rows with autofilter. ExcelJS is imported lazily so the
 * designer bundle doesn't carry it until the user actually exports.
 */

import type { ErdDesign } from "@/lib/erd-design";
import {
  columnDictionaryRows,
  constraintSpecRows,
  tableDefinitionRows,
} from "@/lib/erd-docs";

// Apple-ish palette (ARGB).
const INK = "FF1D1D1F"; // primary text
const INK2 = "FF6E6E73"; // secondary text
const HAIR = "FFE5E5EA"; // hairline rule
const HEADBG = "FFF5F5F7"; // header fill
const ZEBRA = "FFFAFAFC"; // alternate row fill
const ACCENT = "FF0A84FF"; // blue, used sparingly
const FONT = "Malgun Gothic"; // renders Korean cleanly on Windows; macOS falls back

type Sheet = import("exceljs").Worksheet;

function styleHeader(ws: Sheet, colCount: number): void {
  const row = ws.getRow(1);
  row.height = 24;
  for (let c = 1; c <= colCount; c++) {
    const cell = row.getCell(c);
    cell.font = { name: FONT, size: 9, bold: true, color: { argb: INK } };
    cell.fill = { type: "pattern", pattern: "solid", fgColor: { argb: HEADBG } };
    cell.alignment = { vertical: "middle", horizontal: "left" };
    cell.border = { bottom: { style: "thin", color: { argb: "FFD2D2D7" } } };
  }
  ws.views = [{ state: "frozen", ySplit: 1 }];
  ws.autoFilter = { from: { row: 1, column: 1 }, to: { row: 1, column: colCount } };
}

function styleBody(ws: Sheet, rowCount: number, colCount: number): void {
  for (let r = 2; r <= rowCount + 1; r++) {
    const row = ws.getRow(r);
    row.height = 19;
    for (let c = 1; c <= colCount; c++) {
      const cell = row.getCell(c);
      cell.font = { name: FONT, size: 9, color: { argb: INK }, ...(cell.font ?? {}) };
      cell.alignment = { vertical: "middle", horizontal: "left", wrapText: false };
      cell.border = { bottom: { style: "hair", color: { argb: HAIR } } };
      if (r % 2 === 0) {
        cell.fill = { type: "pattern", pattern: "solid", fgColor: { argb: ZEBRA } };
      }
    }
  }
}

/** Fit column widths to content (clamped) — narrow numerics, wide text. */
function fitColumns(ws: Sheet, headers: string[], rows: unknown[][]): void {
  headers.forEach((h, i) => {
    let w = h.length * 1.7 + 4;
    for (const r of rows) {
      const v = r[i];
      if (v == null) continue;
      const s = String(v);
      // Korean characters are double-width in Excel's metric.
      const len = [...s].reduce((acc, ch) => acc + (/[가-힣]/.test(ch) ? 2 : 1), 0);
      w = Math.max(w, Math.min(len + 3, 46));
    }
    ws.getColumn(i + 1).width = Math.max(6, Math.min(w, 48));
  });
}

function addDataSheet(
  wb: import("exceljs").Workbook,
  name: string,
  headers: string[],
  rows: unknown[][],
  accentCols: number[] = [],
): void {
  const ws = wb.addWorksheet(name);
  ws.addRow(headers);
  for (const r of rows) ws.addRow(r as (string | number)[]);
  styleHeader(ws, headers.length);
  styleBody(ws, rows.length, headers.length);
  // Accent: PK/FK flag columns get the blue ink for "Y" values only.
  for (const ci of accentCols) {
    for (let r = 2; r <= rows.length + 1; r++) {
      const cell = ws.getRow(r).getCell(ci);
      if (String(cell.value ?? "") === "Y") {
        cell.font = { name: FONT, size: 9, bold: true, color: { argb: ACCENT } };
      }
    }
  }
  fitColumns(ws, headers, rows);
}

export async function exportErdExcel(design: ErdDesign, docName: string): Promise<Blob> {
  const ExcelJS = (await import("exceljs")).default;
  const wb = new ExcelJS.Workbook();
  wb.creator = "etlx";
  wb.created = new Date();

  // ── 개요 (cover) ─────────────────────────────────────────────────────────
  const cover = wb.addWorksheet("개요");
  cover.getColumn(1).width = 3;
  cover.getColumn(2).width = 22;
  cover.getColumn(3).width = 60;
  const title = cover.getCell("B2");
  title.value = docName || "ERD";
  title.font = { name: FONT, size: 22, bold: true, color: { argb: INK } };
  cover.getRow(2).height = 34;
  const sub = cover.getCell("B3");
  sub.value = "데이터 정의서 (Data Specification)";
  sub.font = { name: FONT, size: 11, color: { argb: INK2 } };
  cover.getRow(3).height = 20;
  // Hairline accent rule under the title block.
  for (const c of [2, 3]) {
    cover.getCell(4, c).border = { bottom: { style: "medium", color: { argb: ACCENT } } };
  }
  const colCount = design.tables.reduce((s, t) => s + t.columns.length, 0);
  const stats: [string, string | number][] = [
    ["생성일", new Date().toISOString().slice(0, 10)],
    ["테이블 수", design.tables.length],
    ["컬럼 수", colCount],
    ["관계 수", design.relations.length],
  ];
  if (design.areas?.length) stats.push(["주제영역", design.areas.map((a) => a.name).join(", ")]);
  stats.push(["시트 구성", "테이블 정의서 · 컬럼 정의서 · 관계·제약" + (design.areas?.length ? " · 주제영역" : "")]);
  stats.forEach(([k, v], i) => {
    const r = 6 + i;
    cover.getRow(r).height = 21;
    const kc = cover.getCell(r, 2);
    kc.value = k;
    kc.font = { name: FONT, size: 10, color: { argb: INK2 } };
    kc.alignment = { vertical: "middle" };
    const vc = cover.getCell(r, 3);
    vc.value = v;
    vc.font = { name: FONT, size: 10, color: { argb: INK } };
    vc.alignment = { vertical: "middle", wrapText: true };
    kc.border = { bottom: { style: "hair", color: { argb: HAIR } } };
    vc.border = { bottom: { style: "hair", color: { argb: HAIR } } };
  });

  // ── data sheets (PK/FK columns accented) ─────────────────────────────────
  const td = tableDefinitionRows(design);
  addDataSheet(wb, "테이블 정의서", td.headers, td.rows);
  const cd = columnDictionaryRows(design);
  addDataSheet(wb, "컬럼 정의서", cd.headers, cd.rows, [
    cd.headers.indexOf("PK") + 1,
    cd.headers.indexOf("FK") + 1,
    cd.headers.indexOf("NOT NULL") + 1,
  ]);
  const cs = constraintSpecRows(design);
  addDataSheet(wb, "관계·제약", cs.headers, cs.rows);

  // ── 주제영역 ─────────────────────────────────────────────────────────────
  if (design.areas?.length) {
    const byId = new Map(design.tables.map((t) => [t.id, t]));
    const headers = ["No", "주제영역", "테이블 수", "테이블 목록"];
    const rows = design.areas.map((a, i) => [
      i + 1,
      a.name,
      a.tableIds.length,
      a.tableIds
        .map((id) => byId.get(id)?.name)
        .filter(Boolean)
        .join(", "),
    ]);
    addDataSheet(wb, "주제영역", headers, rows);
  }

  const buf = await wb.xlsx.writeBuffer();
  return new Blob([buf], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
}
