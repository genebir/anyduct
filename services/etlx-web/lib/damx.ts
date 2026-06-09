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

interface Frame {
  off: number;
  end: number;
  s: string;
}
/** Same framing as ``frameStrings`` but keeps byte offsets (for geometry). */
function framesWithOffsets(b: Uint8Array): Frame[] {
  // Strict (fatal) decode so framing is byte-identical to the reference
  // geometry parser: a frame that doesn't decode cleanly is rejected and we
  // advance one byte (exactly Python's .decode that raises on invalid bytes).
  const dec = new TextDecoder("utf-16le", { fatal: true });
  const out: Frame[] = [];
  let i = 0;
  const n = b.length;
  while (i < n - 4) {
    if (b[i] === 0xff && b[i + 1] === 0xfe && b[i + 2] === 0xff) {
      const len = b[i + 3];
      const start = i + 4;
      const end = start + len * 2;
      if (len > 0 && end <= n) {
        let s: string | null = null;
        try {
          s = dec.decode(b.subarray(start, end));
        } catch {
          s = null;
        }
        if (s) {
          out.push({ off: i, end, s });
          i = end;
          continue;
        }
      }
    }
    i += 1;
  }
  return out;
}

/**
 * Recover each table's diagram position from DA#'s canvas section (Phase AJG).
 *
 * Reverse-engineered + validated against a reference image (i.png): the
 * diagram section (after ``DIAGRAM_VERSION``) lists, per table, the entity
 * GUID followed by 16 bytes then the **canvas-item GUID**. At one occurrence
 * of that canvas-item GUID the next int32s are the bounds rectangle
 * ``[0, x, y, 0, 0, w, h]``. Returns entity-GUID → pixel bounds.
 */
function parseCanvasBounds(b: Uint8Array): Map<string, { x: number; y: number; w: number; h: number }> {
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  const F = framesWithOffsets(b);
  const diaFrame = F.find((f) => f.s === "DIAGRAM_VERSION");
  const DIA = diaFrame ? diaFrame.off : 0;
  const guidRe = GUID_RE;
  const i32 = (off: number) => (off + 4 <= b.length ? dv.getInt32(off, true) : 0);
  const strict = new TextDecoder("utf-16le", { fatal: true });
  const frameAt = (off: number): string | null => {
    if (off + 4 > b.length || b[off] !== 0xff || b[off + 1] !== 0xfe || b[off + 2] !== 0xff) return null;
    const len = b[off + 3];
    const end = off + 4 + len * 2;
    if (end > b.length) return null;
    try {
      return strict.decode(b.subarray(off + 4, end));
    } catch {
      return null;
    }
  };
  // entity GUID -> canvas-item GUID (entity frame end + 16 bytes -> framed guid)
  const entToCi = new Map<string, string>();
  for (const f of F) {
    if (f.off > DIA && guidRe.test(f.s) && !entToCi.has(f.s)) {
      const ci = frameAt(f.end + 16);
      if (ci && guidRe.test(ci)) entToCi.set(f.s, ci);
    }
  }
  // canvas-item GUID -> bounds (first occurrence matching the rect pattern)
  const boundsByCi = new Map<string, { x: number; y: number; w: number; h: number }>();
  const ciValues = new Set(entToCi.values());
  for (const f of F) {
    if (!guidRe.test(f.s) || boundsByCi.has(f.s)) continue;
    if (!ciValues.has(f.s)) continue;
    const v = [0, 1, 2, 3, 4, 5, 6].map((k) => i32(f.end + 4 * k));
    if (v[0] === 0 && v[3] === 0 && v[4] === 0 && v[5] > 50 && v[5] < 4000 && v[6] > 50 && v[6] < 6000 && v[1] >= 0 && v[1] < 60000 && v[2] >= 0 && v[2] < 60000) {
      boundsByCi.set(f.s, { x: v[1], y: v[2], w: v[5], h: v[6] });
    }
  }
  // Entity GUID -> table name, built from the MODEL section the same way the
  // diagram references it (name frame whose preceding token is an entity GUID
  // that the diagram links to a canvas item). Keying by name avoids relying on
  // the main parser's GUID map (which can disagree with the diagram's GUIDs).
  const guidToName = new Map<string, string>();
  for (let idx = 1; idx < F.length; idx++) {
    const f = F[idx];
    const prev = F[idx - 1].s;
    if (f.off < DIA && entToCi.has(prev) && !guidRe.test(f.s) && /^[가-힣A-Za-z]/.test(f.s) && !guidToName.has(prev)) {
      guidToName.set(prev, f.s);
    }
  }
  // A name can have several candidate entities (spurious mentions + the real
  // table definition). The real table entity is the LAST one in model order.
  const nameToEnt = new Map<string, string>();
  for (const [g, nm] of guidToName) nameToEnt.set(nm, g);
  const out = new Map<string, { x: number; y: number; w: number; h: number }>();
  for (const [nm, ent] of nameToEnt) {
    const ci = entToCi.get(ent);
    const bd = ci ? boundsByCi.get(ci) : undefined;
    if (bd) out.set(nm, bd);
  }
  return out;
}

interface ParsedColumn {
  name: string;
  type: string;
  guid: string | null;
  pk: boolean;
  table: string | null;
  logical?: string;
  comment?: string;
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

const REL_AUDIT = new Set([
  "RGTR_ID", "REG_DT", "MDFR_ID", "MDFCN_DT", "USE_YN", "RMRK_CN", "SORT_SEQ", "VER_NO",
]);

/**
 * Parse the REAL relationships from DA#'s diagram (Phase AJM). Validated against
 * a reference image (i.png): relationship records list two **entity GUIDs in
 * [parent][child] order**, so binary order gives direction. The FK column is the
 * non-audit key column shared between the two tables (the child's FK = parent's
 * key). This replaces the old heuristic which mis-picked columns and missed
 * many links.
 */
function parseRelationships(b: Uint8Array, tables: ParsedTable[]): RawRelation[] {
  const F = framesWithOffsets(b);
  const S = F.map((f) => f.s);
  const names = new Set(tables.map((t) => t.name));
  const colsByTable = new Map(tables.map((t) => [t.name, new Set(t.columns.map((c) => c.name))]));
  const upByTable = new Map<string, string>();
  for (const t of tables) {
    const up = t.columns.find((c) => c.name.startsWith("UP_"));
    if (up) upByTable.set(t.name, up.name);
  }
  const diaFrame = F.find((f) => f.s === "DIAGRAM_VERSION");
  const DIA = diaFrame ? diaFrame.off : 0;
  const strict = new TextDecoder("utf-16le", { fatal: true });
  const frameAt = (off: number): string | null => {
    if (off + 4 > b.length || b[off] !== 0xff || b[off + 1] !== 0xfe || b[off + 2] !== 0xff) return null;
    const len = b[off + 3];
    const end = off + 4 + len * 2;
    if (end > b.length) return null;
    try {
      return strict.decode(b.subarray(off + 4, end));
    } catch {
      return null;
    }
  };
  // Entity (table) GUIDs = diagram GUIDs whose +16 frame is a canvas-item GUID.
  const entset = new Set<string>();
  for (const f of F) {
    if (f.off > DIA && GUID_RE.test(f.s) && !entset.has(f.s)) {
      const ci = frameAt(f.end + 16);
      if (ci && GUID_RE.test(ci)) entset.add(f.s);
    }
  }
  // Entity GUID -> table name (last model occurrence; must be a real table).
  const g2n = new Map<string, string>();
  for (let i = 1; i < F.length; i++) {
    const f = F[i];
    const prev = F[i - 1].s;
    if (f.off < DIA && entset.has(prev) && !GUID_RE.test(f.s) && /^[가-힣A-Za-z]/.test(f.s) && names.has(f.s)) {
      g2n.set(prev, f.s);
    }
  }
  const out: RawRelation[] = [];
  const seen = new Set<string>();
  const add = (child: string, col: string, parent: string) => {
    const k = [child, parent].sort().join("|") + "|" + col;
    if (seen.has(k)) return;
    seen.add(k);
    out.push({ fromTable: child, fromColumn: col, toTable: parent });
  };
  for (let i = 0; i < S.length - 1; i++) {
    const parent = g2n.get(S[i]);
    const child = g2n.get(S[i + 1]);
    if (!parent || !child) continue;
    if (parent === child) {
      const up = upByTable.get(parent);
      if (up) add(parent, up, parent);
      continue;
    }
    const cp = colsByTable.get(parent);
    const cc = colsByTable.get(child);
    if (!cp || !cc) continue;
    const shared = [...cp].filter((x) => cc.has(x) && !REL_AUDIT.has(x) && !x.startsWith("UP_"));
    const keyish = shared.filter((x) => /(_ID|_NO|_CD)$/i.test(x));
    const key = keyish[0] ?? shared[0];
    if (key) add(child, key, parent); // binary order: [parent][child]
  }
  // Drop sibling-adjacency noise: if the chosen parent is ITSELF a FK-child via
  // the same key (so it doesn't own that key), the link is only real when it's
  // a history→base reference (child name contains the parent name).
  const childKeys = new Set(out.map((r) => `${r.fromTable}|${r.fromColumn}`));
  return out.filter((r) => {
    if (r.fromTable === r.toTable) return true; // self-ref
    if (!childKeys.has(`${r.toTable}|${r.fromColumn}`)) return true; // parent owns the key
    return r.fromTable.includes(r.toTable); // ambiguous → keep only history→base
  });
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
  let pendingLogical: string | null = null;
  let pendingComment: string | null = null;
  const N = S.length;
  let i = 0;

  while (i < N) {
    const s = S[i];

    // Track the doubled GUID that precedes each column record, and the
    // logical name that follows it (e.g. "부서번호" before DEPT_NO).
    if (GUID_RE.test(s) && i + 1 < N && S[i + 1] === s) {
      doubledGuid = s;
      const cand = S[i + 2];
      pendingLogical =
        cand &&
        !GUID_RE.test(cand) &&
        !cand.startsWith("K_") &&
        !SQL_TYPES.has(cand) &&
        !/^\d+$/.test(cand) &&
        !cand.includes("--") &&
        // Exclude markers (PK/FK/...) and physical-style ALL-CAPS tokens —
        // real logical names here are Korean/free text, not identifiers.
        !PHYS_RE.test(cand) &&
        cand.length <= 80
          ? cand
          : null;
      // The string after the logical name is the column description / standard
      // term (e.g. "기관, 기업, … 식별 번호 -- [공공용어: …]"). Keep the clean
      // part before the "-- [meta]" tail.
      const cand2 = S[i + 3];
      pendingComment =
        cand2 &&
        cand2 !== pendingLogical &&
        !GUID_RE.test(cand2) &&
        !cand2.startsWith("K_") &&
        !SQL_TYPES.has(cand2) &&
        !PHYS_RE.test(cand2) &&
        !/^\d+$/.test(cand2)
          ? cand2.split(" -- ")[0].trim() || null
          : null;
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
        logical: pendingLogical ?? undefined,
        comment: pendingComment ?? undefined,
      };
      cols.push(col);
      if (doubledGuid) colByGuid.set(doubledGuid, col);
      doubledGuid = null;
      pendingLogical = null;
      pendingComment = null;
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
    // and NOT by a column (no TYPE in the look-ahead window). A name followed
    // immediately by a ``K_*`` property key is a COLUMN's logical name (e.g.
    // 세션아이디 → K_ATTR_DATA_OWNER), not a table — excluding it prevents a
    // phantom table that would steal the real table's leading columns.
    if (cols.length > 0 && isEntityName(s) && !(S[i + 1] ?? "").startsWith("K_")) {
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
  const rels = parseRelationships(new Uint8Array(buf), deduped);
  const design = toDesign(deduped, rels);

  // Apply DA#'s real diagram positions (Phase AJG) so the imported ERD matches
  // the original layout instead of a generated grid. Scale 0.6 ≈ our node size
  // vs DA#'s, which keeps relative spacing without overlap.
  const bounds = parseCanvasBounds(new Uint8Array(buf));
  // Only trust the recovered positions when they're (almost) all distinct —
  // a guard so we never ship an overlapping layout if geometry recovery is
  // partial; the caller then falls back to auto-layout.
  const matched = design.tables.filter((t) => bounds.has(t.name));
  const distinct = new Set(matched.map((t) => `${bounds.get(t.name)!.x},${bounds.get(t.name)!.y}`));
  const reliable = matched.length >= 2 && distinct.size >= matched.length - 1;
  if (reliable) {
    const SCALE = 0.6;
    for (const t of design.tables) {
      const bd = bounds.get(t.name);
      if (bd) {
        t.x = Math.round(bd.x * SCALE);
        t.y = Math.round(bd.y * SCALE);
      }
    }
  }
  (design as ErdDesign & { __damxPositioned?: boolean }).__damxPositioned = reliable;
  return design;
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
  // Global standard-term map: a physical column name → its logical name,
  // learned from any table that has it. Standardized models reuse one term
  // (USER_ID → 사용자아이디 everywhere), so this fills columns whose own
  // record missed the logical (e.g. dimension PKs).
  const stdLogical = new Map<string, string>();
  for (const t of parsed) {
    for (const c of t.columns) {
      if (c.logical && !stdLogical.has(c.name)) stdLogical.set(c.name, c.logical);
    }
  }
  // Overlay the recovered PKs + logical names onto the design tables.
  const tables: DesignTable[] = base.tables.map((t, idx) => {
    const pcols = parsed[idx]?.columns ?? [];
    const pkset = new Set(pcols.filter((c) => c.pk).map((c) => c.name));
    const logicalByName = new Map(pcols.filter((c) => c.logical).map((c) => [c.name, c.logical!]));
    const commentByName = new Map(pcols.filter((c) => c.comment).map((c) => [c.name, c.comment!]));
    return {
      ...t,
      columns: t.columns.map((c) => ({
        ...c,
        pk: pkset.has(c.name) || c.pk,
        logical: logicalByName.get(c.name) ?? c.logical ?? stdLogical.get(c.name),
        comment: commentByName.get(c.name) ?? c.comment,
      })),
    };
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
  // Infer missing PKs from relationships: a key referenced by a non-history
  // child is the parent's primary key (recovers dimension PKs the 'PK' marker
  // misses). Self-refs (UP_* col) and history→base links are skipped.
  const nameToTable = new Map(tables.map((t) => [t.name, t]));
  for (const r of rawRels) {
    if (r.fromTable === r.toTable || r.fromTable.includes(r.toTable)) continue;
    const col = nameToTable.get(r.toTable)?.columns.find((c) => c.name === r.fromColumn);
    if (col) col.pk = true;
  }
  // Primary-key columns are implicitly NOT NULL.
  for (const t of tables) for (const c of t.columns) if (c.pk) c.notNull = true;
  return { tables, relations };
}
