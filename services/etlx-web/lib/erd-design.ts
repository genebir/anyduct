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
      type: c.type || "TEXT",
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
