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
  type Cardinality,
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
}
interface ParsedTable {
  name: string;
  columns: ParsedColumn[];
}

function isEntityName(s: string): boolean {
  if (!s || s.length > 80) return false;
  if (GUID_RE.test(s) || s.startsWith("K_") || /^\d+$/.test(s)) return false;
  if (SQL_TYPES.has(s) || PHYS_RE.test(s)) return false;
  if (s.includes("--")) return false;
  const c = s.trim()[0];
  return !(c === ":" || c === "," || c === "(" || c === ";" || c === "-");
}

/** Parse a ``.damx`` buffer into an ERD design (best-effort). */
export function parseDamx(buf: ArrayBuffer): ErdDesign {
  const S = frameStrings(buf);
  const tables: ParsedTable[] = [];
  const colByGuid = new Map<string, ParsedColumn>();
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
    if (PHYS_RE.test(s) && i + 1 < N && SQL_TYPES.has(S[i + 1])) {
      const length = i + 2 < N && /^\d+$/.test(S[i + 2]) ? S[i + 2] : null;
      const col: ParsedColumn = {
        name: s,
        type: normalizeImportType(S[i + 1] + (length ? `(${length})` : "")),
        guid: doubledGuid,
        pk: false,
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
        const tbl: ParsedTable = { name: s, columns: cols };
        tables.push(tbl);
        cols = [];
        i += 1;
        continue;
      }
    }

    i += 1;
  }
  if (cols.length > 0) tables.push({ name: cols[0]?.name ?? "table", columns: cols });

  return toDesign(tables);
}

/** Convert parsed tables → ErdDesign (grid layout, PKs, inferred FKs). */
function toDesign(parsed: ParsedTable[]): ErdDesign {
  // Reuse the shared importer for layout + <x>_id FK inference, then overlay
  // the PKs recovered from the .damx PK markers.
  const base = rawTablesToDesign(
    parsed.map((t) => ({ table: t.name, columns: t.columns.map((c) => ({ name: c.name, type: c.type })) })),
  );
  const pkByTableCol = new Map<string, Set<string>>();
  parsed.forEach((t, idx) => {
    const pkset = new Set(t.columns.filter((c) => c.pk).map((c) => c.name));
    if (pkset.size > 0) pkByTableCol.set(base.tables[idx]?.id ?? `#${idx}`, pkset);
  });
  const tables: DesignTable[] = base.tables.map((t) => {
    const pkset = pkByTableCol.get(t.id);
    if (!pkset) return t;
    return { ...t, columns: t.columns.map((c) => ({ ...c, pk: pkset.has(c.name) || c.pk })) };
  });

  // FK edges: primary-key-name match (uses the recovered PKs — the main
  // signal for these models) plus the shared <x>_id inference, deduped.
  const pkRels = inferRelationsByPk(tables);
  const seen = new Set(pkRels.map((r) => `${r.from}.${r.fromColumn}->${r.to}`));
  const relations: DesignRelation[] = [...pkRels];
  for (const r of base.relations) {
    const key = `${r.from}.${r.fromColumn}->${r.to}`;
    if (seen.has(key)) continue;
    seen.add(key);
    relations.push({
      ...r,
      id: r.id || newId("rel"),
      sourceCard: (r.sourceCard ?? "many") as Cardinality,
      targetCard: (r.targetCard ?? "one") as Cardinality,
    });
  }
  return { tables, relations };
}
