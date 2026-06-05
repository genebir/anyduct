/**
 * ERD designer model + SQL export (Phase AGX, 2026-06-05).
 *
 * The interactive ERD *designer* (draw-your-own, as opposed to the
 * connection schema *viewer* in lib/erd.ts) edits this model and renders
 * it on an @xyflow/react canvas. Pure helpers here so the editor stays
 * thin and the SQL export is testable/reasoned in isolation.
 *
 * Relationships are drag-to-create foreign keys: connecting table A → B
 * adds (or reuses) a ``<b>_id`` column on A that references B's primary
 * key. SQL export emits ``CREATE TABLE`` + PK + FK per the chosen
 * dialect's identifier quoting.
 */

export interface DesignColumn {
  name: string;
  type: string;
  pk: boolean;
}

export interface DesignTable {
  id: string;
  name: string;
  x: number;
  y: number;
  columns: DesignColumn[];
}

/** Cardinality at one end of a relationship. */
export type Cardinality = "one" | "many";

export interface DesignRelation {
  id: string;
  /** table id holding the FK column */
  from: string;
  /** FK column name on the ``from`` table */
  fromColumn: string;
  /** referenced table id */
  to: string;
  /** cardinality at the ``from`` (source) end — default "many" (FK side) */
  sourceCard?: Cardinality;
  /** cardinality at the ``to`` (target) end — default "one" (referenced) */
  targetCard?: Cardinality;
}

export interface ErdDesign {
  tables: DesignTable[];
  relations: DesignRelation[];
}

/** Canonical-ish SQL types offered in the column type dropdown. */
export const ERD_TYPES = [
  "BIGINT",
  "INTEGER",
  "SMALLINT",
  "NUMERIC(10,2)",
  "REAL",
  "DOUBLE PRECISION",
  "BOOLEAN",
  "VARCHAR(255)",
  "TEXT",
  "TIMESTAMP",
  "DATE",
  "JSON",
  "BLOB",
] as const;

export const EMPTY_DESIGN: ErdDesign = { tables: [], relations: [] };

// Vendor (DB) type → ERD vocabulary, for "import from connection". Keeps the
// designer's type dropdown meaningful instead of every column reading as the
// dropdown's first option. Base name (sans args) is looked up; length/precision
// are preserved for VARCHAR/NUMERIC.
const VENDOR_TYPE_MAP: Record<string, string> = {
  bigint: "BIGINT",
  int8: "BIGINT",
  bigserial: "BIGINT",
  integer: "INTEGER",
  int: "INTEGER",
  int4: "INTEGER",
  serial: "INTEGER",
  mediumint: "INTEGER",
  smallint: "SMALLINT",
  int2: "SMALLINT",
  tinyint: "SMALLINT",
  boolean: "BOOLEAN",
  bool: "BOOLEAN",
  bit: "BOOLEAN",
  real: "REAL",
  float4: "REAL",
  "double precision": "DOUBLE PRECISION",
  float8: "DOUBLE PRECISION",
  double: "DOUBLE PRECISION",
  float: "DOUBLE PRECISION",
  float64: "DOUBLE PRECISION",
  text: "TEXT",
  longtext: "TEXT",
  mediumtext: "TEXT",
  clob: "TEXT",
  "long varchar": "TEXT",
  date: "DATE",
  json: "JSON",
  jsonb: "JSON",
  variant: "JSON",
  super: "JSON",
  object: "JSON",
  bytea: "BLOB",
  blob: "BLOB",
  binary: "BLOB",
  varbinary: "BLOB",
  bytes: "BLOB",
  varbyte: "BLOB",
};

/** Map a connector's column type string to the ERD type vocabulary.
 *  Unknown types are returned unchanged (the dropdown tolerates them). */
export function normalizeImportType(raw: string): string {
  if (!raw || !raw.trim()) return "TEXT";
  const s = raw.trim();
  const lower = s.toLowerCase();
  const base = lower.replace(/\(.*\)$/, "").trim();

  if (
    base.includes("varchar") ||
    base.includes("char") ||
    base === "character varying" ||
    base === "string" ||
    base === "nvarchar" ||
    base === "fixedstring"
  ) {
    const m = lower.match(/\((\d+)\)/);
    return m ? `VARCHAR(${m[1]})` : "VARCHAR(255)";
  }
  if (base.includes("timestamp") || base === "datetime" || base === "datetime2" || base === "datetime64") {
    return "TIMESTAMP";
  }
  if (base.includes("numeric") || base.includes("decimal") || base.includes("number")) {
    const m = lower.match(/\((\d+)\s*,\s*(\d+)\)/);
    return m ? `NUMERIC(${m[1]},${m[2]})` : "NUMERIC(10,2)";
  }
  return VENDOR_TYPE_MAP[base] ?? s;
}

let _seq = 0;
/** Stable-ish id (crypto when available, else a counter). */
export function newId(prefix = "t"): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (c?.randomUUID) return `${prefix}_${c.randomUUID().slice(0, 8)}`;
  _seq += 1;
  return `${prefix}_${_seq}`;
}

/** Naive singularisation for FK column naming: ``customers`` → ``customer``. */
export function singular(name: string): string {
  const base = name.includes(".") ? name.slice(name.lastIndexOf(".") + 1) : name;
  if (base.endsWith("ies")) return `${base.slice(0, -3)}y`;
  if (base.endsWith("es")) return base.slice(0, -2);
  if (base.endsWith("s")) return base.slice(0, -1);
  return base;
}

/** Primary-key column name of a table (first pk column, else ``id``). */
export function pkColumn(table: DesignTable): string {
  return table.columns.find((c) => c.pk)?.name ?? "id";
}

function newTable(name: string, x: number, y: number): DesignTable {
  return {
    id: newId("tbl"),
    name,
    x,
    y,
    columns: [{ name: "id", type: "BIGINT", pk: true }],
  };
}

export function addTable(design: ErdDesign, name: string, x: number, y: number): ErdDesign {
  return { ...design, tables: [...design.tables, newTable(name, x, y)] };
}

/**
 * Create a FK relationship A → B. Adds a ``<b>_id`` column on A if it's
 * missing, then records the relation. No-op if the same FK already exists
 * or if either table is unknown / A === B.
 */
export function connect(design: ErdDesign, fromId: string, toId: string): ErdDesign {
  if (fromId === toId) return design;
  const from = design.tables.find((t) => t.id === fromId);
  const to = design.tables.find((t) => t.id === toId);
  if (!from || !to) return design;
  const colName = `${singular(to.name)}_id`;
  const tables = design.tables.map((t) => {
    if (t.id !== fromId) return t;
    if (t.columns.some((c) => c.name === colName)) return t;
    return { ...t, columns: [...t.columns, { name: colName, type: "BIGINT", pk: false }] };
  });
  const exists = design.relations.some(
    (r) => r.from === fromId && r.to === toId && r.fromColumn === colName,
  );
  const relations = exists
    ? design.relations
    : [
        ...design.relations,
        {
          id: newId("rel"),
          from: fromId,
          fromColumn: colName,
          to: toId,
          sourceCard: "many" as const,
          targetCard: "one" as const,
        },
      ];
  return { tables, relations };
}

/** Last dotted segment, lower-cased. */
function unqualified(name: string): string {
  const seg = name.includes(".") ? name.slice(name.lastIndexOf(".") + 1) : name;
  return seg.toLowerCase();
}

export interface ImportTable {
  table: string;
  columns: { name: string; type: string }[];
}

/**
 * Convert introspected connection tables into a design (grid layout, ``id``
 * as PK heuristic, FK relations inferred from ``<x>_id`` naming — same
 * convention as the schema viewer). Used by "Import from connection".
 */
export function rawTablesToDesign(raw: ImportTable[], offsetX = 0, offsetY = 0): ErdDesign {
  const perRow = Math.max(1, Math.ceil(Math.sqrt(raw.length)));
  const tables: DesignTable[] = raw.map((t, i) => ({
    id: newId("tbl"),
    name: t.table,
    x: offsetX + (i % perRow) * 280,
    y: offsetY + Math.floor(i / perRow) * 220,
    columns: t.columns.map((c) => ({
      name: c.name,
      type: normalizeImportType(c.type),
      pk: c.name.toLowerCase() === "id",
    })),
  }));
  // Map unqualified table name → id for FK inference.
  const byName = new Map(tables.map((t) => [unqualified(t.name), t.id]));
  const relations: DesignRelation[] = [];
  for (const t of tables) {
    for (const c of t.columns) {
      const lc = c.name.toLowerCase();
      if (lc === "id" || !lc.endsWith("_id")) continue;
      const base = lc.slice(0, -3);
      const targetId = [base, `${base}s`, `${base}es`].map((b) => byName.get(b)).find(Boolean);
      if (targetId && targetId !== t.id) {
        relations.push({
          id: newId("rel"),
          from: t.id,
          fromColumn: c.name,
          to: targetId,
          sourceCard: "many",
          targetCard: "one",
        });
      }
    }
  }
  return { tables, relations };
}

/** Merge an imported design into an existing one, skipping tables whose
 *  name already exists (case-insensitive). */
export function mergeDesign(base: ErdDesign, incoming: ErdDesign): ErdDesign {
  const existing = new Set(base.tables.map((t) => unqualified(t.name)));
  const addTables = incoming.tables.filter((t) => !existing.has(unqualified(t.name)));
  const addedIds = new Set(addTables.map((t) => t.id));
  // Keep relations whose endpoints are both present after the merge.
  const keepIds = new Set([...base.tables.map((t) => t.id), ...addedIds]);
  const addRelations = incoming.relations.filter(
    (r) => addedIds.has(r.from) && keepIds.has(r.to),
  );
  return {
    tables: [...base.tables, ...addTables],
    relations: [...base.relations, ...addRelations],
  };
}

// Columns that are commonly part of composite keys / boilerplate and would
// create false FK edges if treated as a parent key.
const FK_IGNORE_PK_NAMES = new Set([
  "RGTR_ID", "REG_DT", "MDFR_ID", "MDFCN_DT", "USE_YN", "RMRK_CN", "SORT_SEQ", "VER_NO",
]);

/**
 * Infer FK relationships from primary keys (Phase AHG, refined AHH against a
 * real DA# diagram). The parent of a column is the table whose **primary key
 * is exactly that single column** (a dimension table) — junction tables with
 * composite PKs are not parents, which disambiguates shared key names like
 * ``AUTHRT_ID`` (sole PK of 권한, but composite in 권한별기능). A child column
 * matching such a sole PK → FK; ``UP_<pk>`` is a self-reference. Requires
 * ``pk`` flags (recovered from a .damx import).
 */
export function inferRelationsByPk(tables: DesignTable[]): DesignRelation[] {
  // colName → table id, only for tables whose PK is exactly that one column,
  // and only when that name is the sole PK of exactly one table (else skip).
  const soleByName = new Map<string, string[]>();
  for (const t of tables) {
    const pks = t.columns.filter((c) => c.pk).map((c) => c.name);
    if (pks.length === 1 && !FK_IGNORE_PK_NAMES.has(pks[0])) {
      const arr = soleByName.get(pks[0]) ?? [];
      arr.push(t.id);
      soleByName.set(pks[0], arr);
    }
  }
  const parentOf = new Map<string, string>();
  for (const [name, ids] of soleByName) if (ids.length === 1) parentOf.set(name, ids[0]);

  const relations: DesignRelation[] = [];
  const seen = new Set<string>();
  const add = (from: string, fromColumn: string, to: string) => {
    const key = `${from}.${fromColumn}->${to}`;
    if (seen.has(key)) return;
    seen.add(key);
    relations.push({ id: newId("rel"), from, fromColumn, to, sourceCard: "many", targetCard: "one" });
  };
  for (const t of tables) {
    for (const c of t.columns) {
      const direct = parentOf.get(c.name);
      if (direct && direct !== t.id) {
        add(t.id, c.name, direct);
        continue;
      }
      // UP_<pk> hierarchical self/parent reference (e.g. UP_DEPT_NO → 부서.DEPT_NO).
      if (c.name.startsWith("UP_")) {
        const up = parentOf.get(c.name.slice(3));
        if (up) add(t.id, c.name, up);
      }
    }
  }
  return relations;
}

function quoteIdent(ident: string, dialect: string): string {
  return dialect === "mysql" ? `\`${ident}\`` : `"${ident}"`;
}

/**
 * Render the design to ``CREATE TABLE`` DDL. ``dialect`` only affects
 * identifier quoting (the column types are emitted verbatim — the user
 * picked SQL types). FKs come from the relations.
 */
export function toSql(design: ErdDesign, dialect: string): string {
  const q = (s: string) => quoteIdent(s, dialect);
  const byId = new Map(design.tables.map((t) => [t.id, t]));
  const out: string[] = [];
  for (const t of design.tables) {
    const lines: string[] = [];
    for (const c of t.columns) {
      lines.push(`  ${q(c.name)} ${c.type}`);
    }
    const pks = t.columns.filter((c) => c.pk).map((c) => q(c.name));
    if (pks.length > 0) {
      lines.push(`  PRIMARY KEY (${pks.join(", ")})`);
    }
    for (const r of design.relations) {
      if (r.from !== t.id) continue;
      const target = byId.get(r.to);
      if (!target) continue;
      lines.push(
        `  FOREIGN KEY (${q(r.fromColumn)}) REFERENCES ${q(target.name)} (${q(pkColumn(target))})`,
      );
    }
    out.push(`CREATE TABLE ${q(t.name)} (\n${lines.join(",\n")}\n);`);
  }
  return out.join("\n\n");
}
