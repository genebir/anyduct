/**
 * Best-effort parser for DA# / DA Modeler ``.damx`` ERD exports (Phase AHF).
 *
 * ``.damx`` is a proprietary .NET binary object graph. There is no public
 * schema, so this is a heuristic recovery validated against real exports:
 *
 * * Strings are framed as ``FF FE FF <len> <UTF-16LE * len>``.
 * * An **attribute (column)** record ends with ``PHYSICAL  TYPE  [LENGTH]``
 *   (e.g. ``DEPT_NO  VARCHAR  10``), preceded by its logical name / comment.
 * * Columns are stored contiguously per table; the **table name** is the
 *   first plain (non-GUID, non-type) string right after a column block,
 *   recognised by being followed by a run of GUID references and NOT by a
 *   column.
 * * A ``PK`` marker is followed by the primary-key column GUID(s).
 *
 * Recovered: tables, columns (+ normalized types), primary keys. Foreign
 * keys are then inferred from ``<x>_id`` naming (the binary FK graph isn't
 * reliably recoverable). Grouping is heuristic and may be imperfect for
 * unusual models — see ADR-0091.
 */

import {
  type DesignRelation,
  type DesignTable,
  type ErdDesign,
  inferRelationsByPk,
  newId,
  normalizeImportType,
  rawTablesToDesign,
} from "@/lib/erd-design";

const GUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const PHYS_RE = /^[A-Z][A-Z0-9_]+$/;
const SQL_TYPES = new Set([
  "VARCHAR", "CHAR", "NCHAR", "NVARCHAR", "INT", "INTEGER", "BIGINT", "SMALLINT",
  "TINYINT", "NUMERIC", "DECIMAL", "NUMBER", "DATETIME", "DATE", "TIME", "TIMESTAMP",
  "TEXT", "LONGTEXT", "MEDIUMTEXT", "BLOB", "BOOLEAN", "BOOL", "FLOAT", "DOUBLE",
  "REAL", "CLOB", "BIT", "BINARY", "VARBINARY",
]);

/** Decode the ``FF FE FF <len> <utf-16le>`` framed string stream. */
function frameStrings(buf: ArrayBuffer): string[] {
  const b = new Uint8Array(buf);
  const dec = new TextDecoder("utf-16le", { fatal: false });
  const out: string[] = [];
  let i = 0;
  const n = b.length;
  while (i < n - 4) {
    if (b[i] === 0xff && b[i + 1] === 0xfe && b[i + 2] === 0xff) {
      const len = b[i + 3];
      const start = i + 4;
      const end = start + len * 2;
      if (len > 0 && end <= n) {
        const s = dec.decode(b.subarray(start, end));
        // Accept cleanly-decoded frames; reject mis-aligned junk (replacement
        // char or C0 control bytes).
        if (s && !s.includes("\uFFFD") && !/[\u0000-\u0008\u000E-\u001F]/.test(s)) {
          out.push(s);
          i = end;
          continue;
        }
      }
    }
    i += 1;
  }
  return out;
}

interface ParsedColumn {
  name: string;
  type: string;
  guid: string | null;
  pk: boolean;
  table: string | null;
}
interface ParsedTable {
  name: string;
  columns: ParsedColumn[];
}

interface RawRelation {
  fromTable: string;
  fromColumn: string;
  toTable: string;
}

// Constraint / index pseudo-entity names (e.g. ``<table>_PK``) — DA# emits
// these and they must not be mistaken for real tables.
const CONSTRAINT_RE = /_(PK|FK|UK|UQ|IDX|IX|AK)\d*$/i;

function isEntityName(s: string): boolean {
  if (!s || s.length > 80) return false;
  if (GUID_RE.test(s) || s.startsWith("K_") || /^\d+$/.test(s)) return false;
  if (SQL_TYPES.has(s) || PHYS_RE.test(s)) return false;
  if (s.includes("--") || CONSTRAINT_RE.test(s)) return false;
  const c = s.trim()[0];
  return !(c === ":" || c === "," || c === "(" || c === ";" || c === "-");
}

/** Parse a ``.damx`` buffer into an ERD design (best-effort). */
export function parseDamx(buf: ArrayBuffer): ErdDesign {
  const S = frameStrings(buf);
  const tables: ParsedTable[] = [];
  const colByGuid = new Map<string, ParsedColumn>();
  const entGuidToName = new Map<string, string>();
  const nameSeen = new Set<string>();
  let cols: ParsedColumn[] = [];
  let doubledGuid: string | null = null;
  const N = S.length;
  let i = 0;

  while (i < N) {
    const s = S[i];

    // Track the doubled GUID that precedes each column record.
    if (GUID_RE.test(s) && i + 1 < N && S[i + 1] === s) {
      doubledGuid = s;
    }

    // Column: PHYSICAL then a SQL TYPE, optional numeric length.
    // Exclude ``K_*`` property keys (e.g. K_ATTR_PRIVACY_TYPE) — not columns.
    if (PHYS_RE.test(s) && !s.startsWith("K_") && i + 1 < N && SQL_TYPES.has(S[i + 1])) {
      const length = i + 2 < N && /^\d+$/.test(S[i + 2]) ? S[i + 2] : null;
      const col: ParsedColumn = {
        name: s,
        type: normalizeImportType(S[i + 1] + (length ? `(${length})` : "")),
        guid: doubledGuid,
        pk: false,
        table: null,
      };
      cols.push(col);
      if (doubledGuid) colByGuid.set(doubledGuid, col);
      doubledGuid = null;
      i += length ? 3 : 2;
      continue;
    }

    // PK marker: following GUIDs are primary-key column refs.
    if (s === "PK") {
      let j = i + 1;
      while (j < N && GUID_RE.test(S[j])) {
        const c = colByGuid.get(S[j]);
        if (c) c.pk = true;
        j += 1;
      }
      i = j;
      continue;
    }

    // Table name: plain string after a column block, followed by a GUID run
    // and NOT by a column (no TYPE in the look-ahead window).
    if (cols.length > 0 && isEntityName(s)) {
      const win = S.slice(i + 1, i + 5);
      const guidCount = win.filter((w) => GUID_RE.test(w)).length;
      const hasColumnAhead = S.slice(i + 1, i + 7).some((w) => SQL_TYPES.has(w));
      if (guidCount >= 2 && !hasColumnAhead) {
        for (const c of cols) c.table = s;
        tables.push({ name: s, columns: cols });
        // Entity GUID = the token just before the table name at its first
        // (definition) occurrence; relationship records reference it.
        const eg = i > 0 && GUID_RE.test(S[i - 1]) ? S[i - 1] : null;
        if (eg && !nameSeen.has(s) && !/clipboard|before_?id/i.test(s)) {
          entGuidToName.set(eg, s);
          nameSeen.add(s);
        }
        cols = [];
        i += 1;
        continue;
      }
    }

    i += 1;
  }
  if (cols.length > 0) {
    const name = cols[0]?.name ?? "table";
    for (const c of cols) c.table = name;
    tables.push({ name, columns: cols });
  }

  const deduped = dedupeTables(tables);
  const rels = extractRelationships(S, entGuidToName, colByGuid, deduped);
  return toDesign(deduped, rels);
}

/**
 * Parse the **real** FK relationships from DA#'s relationship section
 * (Phase AHM). Each relationship is stored as two consecutive entity GUIDs
 * — ``[parent][child]`` — followed by the parent's key attribute(s), e.g.
 * ``[E:부서][E:사용자별부서] · · (a:부서.DEPT_NO)``. The parent is the entity
 * whose key attribute follows; the FK is child → parent. This is the actual
 * link data DA# draws the diagram from (not name/domain guessing).
 *
 * Filtering: the key attribute is preferred to be the parent's primary key
 * (disambiguates direction + skips the junk self-pairs in later sections);
 * self-references are only kept when the child table has an ``UP_*`` column
 * (a real recursive FK like 상위부서번호).
 */
function extractRelationships(
  S: string[],
  entGuidToName: Map<string, string>,
  colByGuid: Map<string, ParsedColumn>,
  tables: ParsedTable[],
): RawRelation[] {
  const valid = new Set(tables.map((t) => t.name));
  const upColByTable = new Map<string, string>();
  for (const t of tables) {
    const up = t.columns.find((c) => c.name.startsWith("UP_"));
    if (up) upColByTable.set(t.name, up.name);
  }

  const out: RawRelation[] = [];
  const seen = new Set<string>();
  const add = (fromTable: string, fromColumn: string, toTable: string) => {
    const key = `${fromTable}.${fromColumn}->${toTable}`;
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ fromTable, fromColumn, toTable });
  };

  const N = S.length;
  let k = 0;
  while (k < N - 1) {
    const e1 = entGuidToName.get(S[k]);
    const e2 = entGuidToName.get(S[k + 1]);
    if (!e1 || !e2) {
      k += 1;
      continue;
    }
    // Key attribute in the look-ahead window; prefer the one that is a PK.
    let keyAttr: ParsedColumn | null = null;
    let firstAttr: ParsedColumn | null = null;
    for (let j = k + 2; j < Math.min(k + 8, N); j++) {
      const a = colByGuid.get(S[j]);
      if (a) {
        if (!firstAttr) firstAttr = a;
        if (a.pk) {
          keyAttr = a;
          break;
        }
      }
    }
    keyAttr = keyAttr ?? firstAttr;
    if (!keyAttr) {
      k += 1;
      continue;
    }
    const parent = keyAttr.table === e1 ? e1 : keyAttr.table === e2 ? e2 : e1;
    const child = parent === e1 ? e2 : e1;
    if (valid.has(child) && valid.has(parent)) {
      if (child !== parent) {
        add(child, keyAttr.name, parent);
      } else {
        const up = upColByTable.get(child);
        if (up) add(child, up, parent);
      }
    }
    k += 2;
  }
  return out;
}

/**
 * DA# files have the real table definitions first, then later sections
 * (diagram/clipboard) that re-list leaked key columns under duplicate or junk
 * names — these create phantom tables and false FK edges. Drop junk-named
 * tables and, for duplicate names, keep the real one (most PK columns, then
 * first seen).
 */
function dedupeTables(tables: ParsedTable[]): ParsedTable[] {
  const JUNK_NAME = /clipboard|before_?id/i;
  const pkCount = (t: ParsedTable) => t.columns.filter((c) => c.pk).length;
  const byName = new Map<string, ParsedTable>();
  const order: string[] = [];
  for (const t of tables) {
    const name = t.name.trim();
    if (!name || JUNK_NAME.test(name) || t.columns.length === 0) continue;
    const cur = byName.get(name);
    if (!cur) {
      byName.set(name, t);
      order.push(name);
    } else if (pkCount(t) > pkCount(cur)) {
      byName.set(name, t); // prefer the real, PK-bearing definition
    }
  }
  return order.map((n) => byName.get(n)!);
}

/** Convert parsed tables + real key-group relations → ErdDesign. */
function toDesign(parsed: ParsedTable[], rawRels: RawRelation[]): ErdDesign {
  const base = rawTablesToDesign(
    parsed.map((t) => ({ table: t.name, columns: t.columns.map((c) => ({ name: c.name, type: c.type })) })),
  );
  // Overlay the recovered PKs onto the design tables.
  const tables: DesignTable[] = base.tables.map((t, idx) => {
    const pkset = new Set(parsed[idx]?.columns.filter((c) => c.pk).map((c) => c.name) ?? []);
    if (pkset.size === 0) return t;
    return { ...t, columns: t.columns.map((c) => ({ ...c, pk: pkset.has(c.name) || c.pk })) };
  });

  const idByName = new Map(tables.map((t) => [t.name, t.id]));
  const seen = new Set<string>();
  const relations: DesignRelation[] = [];
  // Primary signal: the real FK relationships parsed from the .damx key groups.
  for (const r of rawRels) {
    const from = idByName.get(r.fromTable);
    const to = idByName.get(r.toTable);
    if (!from || !to) continue;
    const key = `${from}.${r.fromColumn}->${to}`;
    if (seen.has(key)) continue;
    seen.add(key);
    relations.push({
      id: newId("rel"),
      from,
      fromColumn: r.fromColumn,
      to,
      sourceCard: "many",
      targetCard: "one",
    });
  }
  // Fallback only if the file had no parseable key groups (older DA# exports).
  if (relations.length === 0) {
    relations.push(...inferRelationsByPk(tables));
  }
  return { tables, relations };
}
