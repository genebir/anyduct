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
  notNull?: boolean;
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
 * Recover each table's PHYSICAL name (테이블명, e.g. TB_TSOBAS011) — Phase AJU.
 * DA# draws the table label as ``[PHYSICAL][LOGICAL]`` in the diagram (e.g.
 * ``TB_TSOBAS011`` then ``운행패턴역``). Returns both logical(Korean)→physical
 * (first per table, for the display-name flip) and physical→logical (EVERY
 * occurrence, last-wins, for canonicalizing relationship records — a table can
 * have several physical-name occurrences and all must map back). Models that use
 * the Korean name for both yield empty maps.
 */
function parsePhysicalTableNames(
  b: Uint8Array,
  koreanNames: Set<string>,
): { physByName: Map<string, string>; reversePhys: Map<string, string> } {
  const F = framesWithOffsets(b);
  const diaFrame = F.find((f) => f.s === "DIAGRAM_VERSION");
  const DIA = diaFrame ? diaFrame.off : 0;
  const physByName = new Map<string, string>();
  const reversePhys = new Map<string, string>();
  for (let i = 0; i < F.length - 1; i++) {
    if (F[i].off <= DIA) continue; // diagram section only
    const phys = F[i].s;
    const logical = F[i + 1].s;
    if (koreanNames.has(logical) && PHYS_RE.test(phys) && !phys.startsWith("K_") && !SQL_TYPES.has(phys)) {
      if (!physByName.has(logical)) physByName.set(logical, phys);
      reversePhys.set(phys, logical); // every physical occurrence → its Korean
    }
  }
  return { physByName, reversePhys };
}

/**
 * Build a robust physical-name → logical-name map (Phase AJS) by scanning EVERY
 * column record in the stream, independent of the main parser's token
 * advancement. DA# keeps logical & physical model areas separately, so a column
 * may be grouped from the physical area (no logical) while its logical name
 * lives in the logical area — this global map recovers it (e.g. COM_CD_GROUP_ID
 * → 공통코드그룹아이디). First non-empty logical per physical name wins.
 */
function parseLogicalByName(b: Uint8Array): Map<string, string> {
  const F = framesWithOffsets(b);
  const out = new Map<string, string>();
  for (let i = 0; i < F.length; i++) {
    if (!PHYS_RE.test(F[i].s) || F[i].s.startsWith("K_")) continue;
    if (i + 1 >= F.length || !SQL_TYPES.has(F[i + 1].s)) continue;
    if (out.has(F[i].s)) continue;
    // Walk back to the doubled GUID; the frame right after it is the logical.
    for (let j = i - 1; j >= Math.max(0, i - 8); j--) {
      if (GUID_RE.test(F[j].s) && j + 1 < F.length && F[j + 1].s === F[j].s) {
        const cand = F[j + 2]?.s;
        if (
          cand &&
          !GUID_RE.test(cand) &&
          !cand.startsWith("K_") &&
          !SQL_TYPES.has(cand) &&
          !/^\d+$/.test(cand) &&
          !cand.includes("--") &&
          !PHYS_RE.test(cand) &&
          cand.length <= 80
        ) {
          out.set(F[i].s, cand);
        }
        break;
      }
    }
  }
  return out;
}

/**
 * Recover the per-column **mandatory (NOT NULL)** flag (Phase AJQ). After a
 * column's ``PHYSICAL TYPE [LENGTH]`` frames there are two empty frames then an
 * int32: 1 = mandatory, 0 = nullable. Keyed by the column's doubled GUID.
 */
function parseMandatory(b: Uint8Array): Map<string, boolean> {
  const F = framesWithOffsets(b);
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  const out = new Map<string, boolean>();
  for (let i = 0; i < F.length; i++) {
    if (!PHYS_RE.test(F[i].s) || F[i].s.startsWith("K_")) continue;
    if (i + 1 >= F.length || !SQL_TYPES.has(F[i + 1].s)) continue;
    // doubled GUID before the column identifies it (matches colByGuid).
    let guid: string | null = null;
    for (let j = i - 1; j >= Math.max(0, i - 8); j--) {
      if (GUID_RE.test(F[j].s) && j > 0 && F[j - 1].s === F[j].s) {
        guid = F[j].s;
        break;
      }
    }
    if (!guid) continue;
    let endFrame = F[i + 1]; // type
    let hasLen = false;
    if (i + 2 < F.length && /^\d+$/.test(F[i + 2].s)) {
      endFrame = F[i + 2]; // length
      hasLen = true;
    }
    // With a length there are 2 empty frames before the flag; without one there
    // are 3 (the extra empty frame stands in for the missing length).
    const off = endFrame.end + (hasLen ? 8 : 12);
    if (off + 4 <= b.length) out.set(guid, dv.getInt32(off, true) === 1);
  }
  return out;
}

/**
 * Parse the REAL relationships from DA#'s diagram (Phase AJM). Validated against
 * a reference image (i.png): relationship records list two **entity GUIDs in
 * [parent][child] order**, so binary order gives direction. The FK column is the
 * non-audit key column shared between the two tables (the child's FK = parent's
 * key). This replaces the old heuristic which mis-picked columns and missed
 * many links.
 */
function parseRelationships(
  b: Uint8Array,
  tables: ParsedTable[],
  colByGuid: Map<string, ParsedColumn>,
  reversePhys: Map<string, string>,
): RawRelation[] {
  // DEFINITIVE relationship extraction (Phase AKD) — reverse-engineered + count-
  // validated against DA# (TMS 265, 테이블표준화 16, exact). A relationship is a
  // record where a relationship GUID BRACKETS the entity pair:
  //   [relGUID] [parentEntity] [childEntity] [relGUID]  (F[i-1] === F[i+2])
  // followed by the parent's FK key attribute GUIDs. DA# stores each cross
  // relationship twice (logical area uses Korean names, physical area uses the
  // physical 테이블명) so we canonicalize TB_→Korean and dedupe. Self-loops are
  // kept only when the table actually has a UP_<ownPK> self-reference column
  // (filters entity-definition brackets, which are same-entity and have no UP_).
  const F = framesWithOffsets(b);
  const S = F.map((f) => f.s);
  const names = new Set(tables.map((t) => t.name));
  const colsByTable = new Map(tables.map((t) => [t.name, new Set(t.columns.map((c) => c.name))]));
  const pkByTable = new Map(tables.map((t) => [t.name, new Set(t.columns.filter((c) => c.pk).map((c) => c.name))]));
  const canon = (nm: string) => reversePhys.get(nm) ?? nm; // physical 테이블명 → Korean
  // The bracket record reliably gives the pair+direction, but its first attr is
  // NOT the FK; derive the FK column from the shared key (parent's PK preferred).
  const fkColumn = (parent: string, child: string): string => {
    const cp = colsByTable.get(parent);
    const cc = colsByTable.get(child);
    if (!cp || !cc) return "";
    const shared = [...cp].filter((x) => cc.has(x) && !REL_AUDIT.has(x) && !x.startsWith("UP_"));
    const keyish = shared.filter((x) => /(_ID|_NO|_CD)$/i.test(x));
    const pk = pkByTable.get(parent);
    const sharedPk = pk ? shared.filter((x) => pk.has(x)) : [];
    return sharedPk[0] ?? keyish[0] ?? shared[0] ?? "";
  };
  const upCol = (t: string): string | null => {
    const cs = colsByTable.get(t);
    if (!cs) return null;
    for (const c of cs) if (c.startsWith("UP_") && cs.has(c.slice(3))) return c;
    return null;
  };
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
  // Entity GUID → name (Korean logical-area name OR physical 테이블명), captured
  // from the model section. Accept both so logical AND physical records resolve.
  const g2n = new Map<string, string>();
  for (let i = 1; i < F.length; i++) {
    const f = F[i];
    const prev = F[i - 1].s;
    if (f.off >= DIA || !entset.has(prev) || GUID_RE.test(f.s)) continue;
    if (!/^[가-힣A-Za-z]/.test(f.s) || f.s.includes("고딕") || f.s === "Segoe UI") continue;
    g2n.set(prev, f.s); // accept any name; the canon + names filter applies per pair
  }
  // First pass: collect bracketed records from the MODEL section ONLY (count-
  // validated: the diagram section holds stale visual leftovers — e.g. CTC&ARS's
  // 시뮬레이터 link that DA# no longer renders).
  const out: RawRelation[] = [];
  const seen = new Set<string>();
  const selfCount = new Map<string, number>();
  for (let i = 1; i < F.length - 2; i++) {
    if (F[i].off >= DIA) continue; // MODEL section only
    const pn = g2n.get(S[i]);
    const cn = g2n.get(S[i + 1]);
    if (!pn || !cn) continue;
    if (!GUID_RE.test(S[i - 1]) || S[i - 1] !== S[i + 2]) continue; // relGUID bracket
    const parent = canon(pn);
    const child = canon(cn);
    if (!names.has(parent) || !names.has(child)) continue;
    if (parent === child) {
      // Self-loops are validated by multiplicity below (real ones appear exactly
      // twice — logical + physical area; entity-definition brackets appear once
      // or many times).
      selfCount.set(parent, (selfCount.get(parent) ?? 0) + 1);
      continue;
    }
    const k = `${child}->${parent}`;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push({ fromTable: child, fromColumn: fkColumn(parent, child), toTable: parent });
  }
  // Self-loops: a REAL self-relationship's bracket record appears exactly 2×
  // (count-validated on 4 real exports: 부서/메뉴 x2 everywhere = real, x1 or
  // x15+ = entity-definition noise like 공통코드그룹/권한).
  for (const [t, ct] of selfCount) {
    if (ct === 2) out.push({ fromTable: t, fromColumn: upCol(t) ?? "", toTable: t });
  }
  return out;
}
// Constraint / index pseudo-entity names (e.g. ``<table>_PK``) — DA# emits
// these and they must not be mistaken for real tables.
const CONSTRAINT_RE = /_(PK|FK|UK|UQ|IDX|IX|AK)\d*$/i;

function isEntityName(s: string): boolean {
  if (!s || s.length > 80) return false;
  if (GUID_RE.test(s) || s.startsWith("K_") || /^\d+$/.test(s)) return false;
  if (SQL_TYPES.has(s) || PHYS_RE.test(s)) return false;
  if (s.includes("--") || CONSTRAINT_RE.test(s)) return false;
  // Real table names are single tokens; a name with whitespace is a description
  // text mis-captured as a name (DA# stores a desc sentence after the name).
  if (/\s/.test(s)) return false;
  const c = s.trim()[0];
  return !(c === ":" || c === "," || c === "(" || c === ";" || c === "-");
}

/** Parse a ``.damx`` buffer into an ERD design (best-effort). */
export function parseDamx(buf: ArrayBuffer): ErdDesign {
  const S = frameStrings(buf);
  const mandatoryByGuid = parseMandatory(new Uint8Array(buf));
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
        notNull: doubledGuid ? mandatoryByGuid.get(doubledGuid) : undefined,
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
    // Accept Korean names (isEntityName) AND ALL-CAPS English *word* table names
    // (PIT, CROSSING, …) which isEntityName rejects as column-like. Require NO
    // digits and no ``TB_`` prefix so we don't pick up DA#'s separate physical-
    // area table definitions (TB_TSOBAS011 etc., which are digit-bearing codes
    // duplicating the real Korean tables). The GUID-run + no-type-ahead guard
    // below further distinguishes a table from a column.
    const nameOk =
      isEntityName(s) ||
      (PHYS_RE.test(s) &&
        !/\d/.test(s) &&
        !s.startsWith("TB_") &&
        !SQL_TYPES.has(s) &&
        !s.startsWith("K_") &&
        !CONSTRAINT_RE.test(s));
    if (cols.length > 0 && nameOk && !(S[i + 1] ?? "").startsWith("K_")) {
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
  const { physByName, reversePhys } = parsePhysicalTableNames(
    new Uint8Array(buf),
    new Set(deduped.map((t) => t.name)),
  );
  // Drop duplicate columns within a table (the grouping heuristic can pick the
  // same physical column twice) — a table can't have two identical column names,
  // and duplicates break React keys downstream. Keep the first (richer) one.
  for (const t of deduped) {
    const seenCol = new Set<string>();
    t.columns = t.columns.filter((c) => (seenCol.has(c.name) ? false : (seenCol.add(c.name), true)));
  }
  // Fill any column logical names missed by the main loop (DA# logical/physical
  // areas are separate) from a global raw scan — e.g. COM_CD_GROUP_ID.
  const rawLogical = parseLogicalByName(new Uint8Array(buf));
  for (const t of deduped) {
    for (const c of t.columns) {
      if (!c.logical && rawLogical.has(c.name)) c.logical = rawLogical.get(c.name);
    }
  }
  const rels = parseRelationships(new Uint8Array(buf), deduped, colByGuid, reversePhys);
  const design = toDesign(deduped, rels, physByName);

  // Apply DA#'s real diagram positions (Phase AJG) so the imported ERD matches
  // the original layout instead of a generated grid. Scale 0.6 ≈ our node size
  // vs DA#'s, which keeps relative spacing without overlap.
  const bounds = parseCanvasBounds(new Uint8Array(buf));
  // Only trust the recovered positions when they're (almost) all distinct —
  // a guard so we never ship an overlapping layout if geometry recovery is
  // partial; the caller then falls back to auto-layout.
  // bounds are keyed by the Korean (logical) name; the table's display name may
  // now be the physical 테이블명, so resolve via logical.
  const boundsKey = (t: DesignTable) => bounds.get(t.logical ?? t.name);
  const matched = design.tables.filter((t) => boundsKey(t));
  const distinct = new Set(matched.map((t) => `${boundsKey(t)!.x},${boundsKey(t)!.y}`));
  const reliable = matched.length >= 2 && distinct.size >= matched.length - 1;
  if (reliable) {
    const SCALE = 0.6;
    for (const t of design.tables) {
      const bd = boundsKey(t);
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
function toDesign(
  parsed: ParsedTable[],
  rawRels: RawRelation[],
  physByName: Map<string, string> = new Map(),
): ErdDesign {
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
    const nnByName = new Map(pcols.filter((c) => c.notNull).map((c) => [c.name, true]));
    const korean = t.name; // DA# entity name = logical (논리명)
    const physical = physByName.get(korean); // 테이블명 (e.g. TB_TSOBAS011)
    return {
      ...t,
      // Physical 테이블명 becomes the table name when present; the Korean entity
      // name is the logical name. Models without a separate physical name keep
      // the Korean for both.
      name: physical ?? korean,
      logical: korean,
      columns: t.columns.map((c) => ({
        ...c,
        pk: pkset.has(c.name) || c.pk,
        logical: logicalByName.get(c.name) ?? c.logical ?? stdLogical.get(c.name),
        comment: commentByName.get(c.name) ?? c.comment,
        notNull: nnByName.get(c.name) || c.notNull,
      })),
    };
  });

  // Index by BOTH physical name and logical (Korean) name — rawRels and the
  // PK-inference reference tables by their Korean name, but t.name may now be
  // the physical 테이블명.
  const idByName = new Map<string, string>();
  for (const t of tables) {
    idByName.set(t.name, t.id);
    if (t.logical) idByName.set(t.logical, t.id);
  }
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
  const nameToTable = new Map<string, DesignTable>();
  for (const t of tables) {
    nameToTable.set(t.name, t);
    if (t.logical) nameToTable.set(t.logical, t);
  }
  for (const r of rawRels) {
    if (r.fromTable === r.toTable || r.fromTable.includes(r.toTable)) continue;
    const col = nameToTable.get(r.toTable)?.columns.find((c) => c.name === r.fromColumn);
    if (col) col.pk = true;
  }
  // Primary-key columns are implicitly NOT NULL.
  for (const t of tables) for (const c of t.columns) if (c.pk) c.notNull = true;
  return { tables, relations };
}

/* ── Subject areas (주제영역) ─────────────────────────────────────────────
   DA# stores multiple diagram panes — one per subject area (plus often a
   full-model pane). Each pane is delimited by a K_PANE_PRINT_DEV record; the
   pane's NAME sits right before its first marker, and a pane's content spans
   from its marker until the next named header (logical pane + physical pane).
   Membership = entities with a canvas-item link inside the pane's frames.
   Count-validated: 안전지원 8 areas / TMS 13 / CTC&ARS 10 / 단일파일 1. */

/**
 * Parse a ``.damx`` into ONE ErdDesign whose subject areas become TABS
 * (``design.areas``) — Phase AKH, replacing the one-diagram-per-area split
 * (AKF) per user feedback. Tables/relations are global; each area records its
 * member table ids + that pane's DA# positions. Files with fewer than 2 named
 * panes return the plain whole-model design (no tabs).
 */
export function parseDamxWithAreas(buf: ArrayBuffer): ErdDesign {
  const full = parseDamx(buf);
  const b = new Uint8Array(buf);
  const F = framesWithOffsets(b);
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  const diaIdx = F.findIndex((f) => f.s === "DIAGRAM_VERSION");
  if (diaIdx < 0) return full;
  const DIA = F[diaIdx].off;
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
  // entity GUID → table name (model section; same recipe as parseRelationships)
  const entHasCi = new Set<string>();
  for (const f of F) {
    if (f.off > DIA && GUID_RE.test(f.s) && !entHasCi.has(f.s)) {
      const ci = frameAt(f.end + 16);
      if (ci && GUID_RE.test(ci)) entHasCi.add(f.s);
    }
  }
  const g2n = new Map<string, string>();
  for (let i = 1; i < F.length; i++) {
    const f = F[i];
    const prev = F[i - 1].s;
    if (f.off >= DIA || !entHasCi.has(prev) || GUID_RE.test(f.s)) continue;
    if (!/^[가-힣A-Za-z]/.test(f.s) || f.s.includes("고딕") || f.s === "Segoe UI") continue;
    g2n.set(prev, f.s);
  }
  const tn = new Set(g2n.values());
  const rev = new Map<string, string>();
  for (let i = 0; i < F.length - 1; i++) {
    if (F[i].off > DIA && tn.has(F[i + 1].s) && PHYS_RE.test(F[i].s) && !F[i].s.startsWith("K_")) {
      rev.set(F[i].s, F[i + 1].s);
    }
  }
  const canon = (nm: string) => rev.get(nm) ?? nm;
  // pane markers + header names (a name right before a marker, preceded by a
  // GUID; paper sizes like A4 from the page-setup record are noise).
  const marks: number[] = [];
  for (let i = 0; i < F.length; i++) if (F[i].s === "K_PANE_PRINT_DEV") marks.push(i);
  if (marks.length < 2) return full;
  const headers = new Map<number, string>();
  for (let mi = 0; mi < marks.length; mi++) {
    const m = marks[mi];
    const lo = mi > 0 ? marks[mi - 1] : diaIdx;
    for (let j = m - 1; j > Math.max(lo, m - 12); j--) {
      const s = F[j].s;
      if (GUID_RE.test(s) || s.includes("고딕") || s === "Segoe UI" || s.includes("^")) continue;
      if (!/^[가-힣A-Za-z0-9]/.test(s) || /^[AB]\d+$/.test(s) || s.length > 60) continue;
      if (j > 0 && GUID_RE.test(F[j - 1].s)) {
        headers.set(mi, s);
        break;
      }
    }
  }
  if (headers.size < 2) return full;
  // membership + per-pane POSITIONS. The bounds rect lives at one of the
  // canvas-item GUID's occurrences WITHIN the same pane segment (a ci string
  // can appear many times; only the in-pane instance carries the rect) — so
  // both the ci link and its rect are resolved segment-locally. Validated:
  // 안전지원 8 tabs all fully positioned, multi-tab tables get per-tab coords.
  const i32 = (off: number) => (off + 4 <= b.length ? dv.getInt32(off, true) : 0);
  const rectAt = (end: number): { x: number; y: number } | null => {
    const v = [0, 1, 2, 3, 4, 5, 6].map((k) => i32(end + 4 * k));
    if (v[0] === 0 && v[3] === 0 && v[4] === 0 && v[5] > 50 && v[5] < 4000 && v[6] > 50 && v[6] < 6000 && v[1] >= 0 && v[1] < 60000 && v[2] >= 0 && v[2] < 60000) {
      return { x: v[1], y: v[2] };
    }
    return null;
  };
  // GUID → frame indices in the diagram section (for in-segment rect search).
  const idxByGuid = new Map<string, number[]>();
  for (let i = diaIdx; i < F.length; i++) {
    if (!GUID_RE.test(F[i].s)) continue;
    let arr = idxByGuid.get(F[i].s);
    if (!arr) {
      arr = [];
      idxByGuid.set(F[i].s, arr);
    }
    arr.push(i);
  }
  const areaMembers = new Map<string, Set<string>>();
  const areaPos = new Map<string, Map<string, { x: number; y: number }>>(); // area → Korean name → rect
  let cur: string | null = null;
  for (let mi = 0; mi < marks.length; mi++) {
    const named = headers.get(mi);
    if (named) cur = named;
    if (!cur) continue;
    const lo = marks[mi];
    const hi = mi + 1 < marks.length ? marks[mi + 1] : F.length;
    let mem = areaMembers.get(cur);
    let pos = areaPos.get(cur);
    if (!mem) {
      mem = new Set();
      areaMembers.set(cur, mem);
      pos = new Map();
      areaPos.set(cur, pos);
    }
    for (let j = lo; j < hi; j++) {
      const s = F[j].s;
      if (!g2n.has(s)) continue;
      const ci = frameAt(F[j].end + 16);
      if (!ci || !GUID_RE.test(ci)) continue;
      const nm = canon(g2n.get(s)!);
      mem.add(nm);
      if (!pos!.has(nm)) {
        // rect = this ci's occurrence inside THIS segment that carries bounds
        for (const k of idxByGuid.get(ci) ?? []) {
          if (k < lo || k >= hi) continue;
          const r = rectAt(F[k].end);
          if (r) {
            pos!.set(nm, r);
            break;
          }
        }
      }
    }
  }
  // assemble subject-area TABS over the full design (membership + positions)
  const byKorean = new Map<string, DesignTable>();
  for (const t of full.tables) byKorean.set(t.logical ?? t.name, t);
  const areas: NonNullable<ErdDesign["areas"]> = [];
  const SCALE = 0.6;
  for (const [name, members] of areaMembers) {
    const pos = areaPos.get(name)!;
    const tableIds: string[] = [];
    const positions: Record<string, { x: number; y: number }> = {};
    let placed = 0;
    const distinct = new Set<string>();
    for (const m of members) {
      const t = byKorean.get(m);
      if (!t) continue;
      tableIds.push(t.id);
      const bd = pos.get(m);
      if (bd) {
        positions[t.id] = { x: Math.round(bd.x * SCALE), y: Math.round(bd.y * SCALE) };
        placed += 1;
        distinct.add(`${bd.x},${bd.y}`);
      }
    }
    if (tableIds.length === 0) continue;
    // Only keep DA# positions when they're reliable; otherwise leave the area
    // unpositioned so the import handler can auto-layout that tab.
    const reliable = placed >= 2 && distinct.size >= placed - 1 && placed >= tableIds.length * 0.6;
    areas.push({ id: newId("area"), name, tableIds, ...(reliable ? { positions } : {}) });
  }
  if (areas.length < 2) return full;
  return { ...full, areas };
}
