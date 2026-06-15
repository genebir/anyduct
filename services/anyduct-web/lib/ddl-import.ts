/**
 * DDL (CREATE TABLE) → ERD import (Phase AIP).
 *
 * The universal counterpart to ``.damx`` import: paste a schema's
 * ``CREATE TABLE`` (+ ``ALTER TABLE … ADD FOREIGN KEY``) from any RDBMS dump
 * and get an ERD with columns (type/NOT NULL/default), primary keys, and FK
 * relationships — then reuse layout / validation / docs.
 *
 * Pragmatic parser for the common ANSI / postgres / mysql / sqlite / mssql
 * shapes: quoted identifiers (`` " ` [ ] ``), multi-word types (DOUBLE
 * PRECISION, CHARACTER VARYING(255), TIMESTAMP WITH TIME ZONE), inline +
 * table-level PK/FK, and out-of-line ALTER ... ADD FOREIGN KEY. Exotic DDL
 * may be skipped — see ADR notes.
 */

import {
  newId,
  normalizeImportType,
  type DesignColumn,
  type DesignRelation,
  type DesignTable,
  type ErdDesign,
} from "@/lib/erd-design";

const FLAG_KEYWORDS = new Set([
  "NOT", "NULL", "DEFAULT", "PRIMARY", "REFERENCES", "UNIQUE", "CHECK",
  "GENERATED", "AUTO_INCREMENT", "AUTOINCREMENT", "IDENTITY", "COMMENT", "COLLATE",
]);

function stripComments(sql: string): string {
  return sql.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/--[^\n]*/g, " ");
}

function unquote(id: string): string {
  return id.trim().replace(/^[`"[]|[`"\]]$/g, "").replace(/^"|"$/g, "").trim();
}

/** Split on top-level commas (ignoring parens). */
function splitTopLevel(s: string, sep = ","): string[] {
  const out: string[] = [];
  let depth = 0;
  let cur = "";
  for (const ch of s) {
    if (ch === "(") depth++;
    else if (ch === ")") depth--;
    if (ch === sep && depth === 0) {
      out.push(cur);
      cur = "";
    } else cur += ch;
  }
  if (cur.trim()) out.push(cur);
  return out;
}

/** Split a DDL script into statements on top-level semicolons. */
function statements(sql: string): string[] {
  return splitTopLevel(sql, ";").map((s) => s.trim()).filter(Boolean);
}

interface PItem {
  tables: Map<string, DesignColumn[]>;
  pk: Map<string, string[]>;
  rels: { from: string; col: string; to: string }[];
}

function parseColumn(def: string, rels: PItem["rels"], table: string): DesignColumn | null {
  const tokens = def.trim().split(/\s+/);
  if (tokens.length < 2) return null;
  const name = unquote(tokens[0]);
  if (!name) return null;
  // Type = tokens until a flag keyword (kept with any (...) args).
  const typeParts: string[] = [];
  let i = 1;
  for (; i < tokens.length; i++) {
    const up = tokens[i].replace(/\(.*$/, "").toUpperCase();
    if (FLAG_KEYWORDS.has(up)) break;
    typeParts.push(tokens[i]);
  }
  const rawType = typeParts.join(" ") || "TEXT";
  const rest = tokens.slice(i).join(" ");
  const upper = rest.toUpperCase();
  const notNull = /\bNOT\s+NULL\b/.test(upper);
  const pkInline = /\bPRIMARY\s+KEY\b/.test(upper);
  let defaultValue: string | undefined;
  const dm = rest.match(/\bDEFAULT\s+('[^']*'|"[^"]*"|[^\s,]+)/i);
  if (dm) defaultValue = dm[1];
  // Inline REFERENCES other(col)
  const rm = rest.match(/\bREFERENCES\s+([`"[\]\w.]+)\s*\(\s*([`"[\]\w]+)\s*\)/i);
  if (rm) rels.push({ from: table, col: name, to: unquote(rm[1]) });
  return {
    name,
    type: normalizeImportType(rawType),
    pk: pkInline,
    notNull: notNull || pkInline,
    defaultValue,
  };
}

export function parseDdl(sql: string): ErdDesign {
  const tables = new Map<string, DesignColumn[]>();
  const pkByTable = new Map<string, string[]>();
  const rels: PItem["rels"] = [];

  const lastIdent = (s: string) => unquote(s.split(".").pop() ?? s);

  for (const stmt of statements(stripComments(sql))) {
    const create = stmt.match(/^CREATE\s+(?:GLOBAL\s+|LOCAL\s+|TEMP(?:ORARY)?\s+)*TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([`"[\]\w.]+)\s*\(([\s\S]*)\)\s*[^)]*$/i);
    if (create) {
      const table = lastIdent(create[1]);
      const cols: DesignColumn[] = [];
      const pks: string[] = [];
      for (const rawItem of splitTopLevel(create[2])) {
        const item = rawItem.trim();
        if (!item) continue;
        const head = item.replace(/\(.*$/, "").toUpperCase().trim();
        if (/^(CONSTRAINT\b.*\bPRIMARY\s+KEY|PRIMARY\s+KEY)/i.test(item)) {
          const m = item.match(/PRIMARY\s+KEY\s*\(([^)]*)\)/i);
          if (m) splitTopLevel(m[1]).forEach((c) => pks.push(unquote(c)));
          continue;
        }
        if (/\bFOREIGN\s+KEY\b/i.test(item)) {
          const m = item.match(/FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+([`"[\]\w.]+)\s*\(([^)]*)\)/i);
          if (m) {
            const fromCols = splitTopLevel(m[1]).map(unquote);
            const toT = lastIdent(m[2]);
            fromCols.forEach((c) => rels.push({ from: table, col: c, to: toT }));
          }
          continue;
        }
        if (/^(UNIQUE|CHECK|KEY|INDEX)\b/i.test(head)) continue; // skip other constraints
        const col = parseColumn(item, rels, table);
        if (col) cols.push(col);
      }
      // Apply table-level PKs.
      for (const c of cols) if (pks.includes(c.name)) (c.pk = true), (c.notNull = true);
      tables.set(table, cols);
      pkByTable.set(table, pks);
      continue;
    }
    // ALTER TABLE x ADD [CONSTRAINT n] FOREIGN KEY (col) REFERENCES y (col)
    const alter = stmt.match(/^ALTER\s+TABLE\s+([`"[\]\w.]+)[\s\S]*?FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+([`"[\]\w.]+)\s*\(([^)]*)\)/i);
    if (alter) {
      const from = lastIdent(alter[1]);
      const toT = lastIdent(alter[3]);
      splitTopLevel(alter[2]).map(unquote).forEach((c) => rels.push({ from, col: c, to: toT }));
    }
  }

  // Build the design (grid layout; user runs auto-layout afterwards).
  const names = [...tables.keys()];
  const perRow = Math.max(1, Math.ceil(Math.sqrt(names.length)));
  const idByName = new Map<string, string>();
  const designTables: DesignTable[] = names.map((name, i) => {
    const id = newId("tbl");
    idByName.set(name, id);
    return {
      id,
      name,
      x: (i % perRow) * 300,
      y: Math.floor(i / perRow) * 240,
      columns: tables.get(name) ?? [],
    };
  });
  const seen = new Set<string>();
  const relations: DesignRelation[] = [];
  for (const r of rels) {
    const from = idByName.get(r.from);
    const to = idByName.get(r.to);
    if (!from || !to) continue;
    const key = `${from}.${r.col}->${to}`;
    if (seen.has(key)) continue;
    seen.add(key);
    relations.push({ id: newId("rel"), from, fromColumn: r.col, to, sourceCard: "many", targetCard: "one" });
  }
  return { tables: designTables, relations };
}
