/**
 * ERD model builder (Phase AGW, 2026-06-05).
 *
 * Turns a connection's introspected schema (tables + columns, from
 * ``connectionsApi.tables`` / ``connectionsApi.columns``) into an
 * entity-relationship model the ERD graph renders.
 *
 * Connectors don't expose foreign keys today (SchemaInspector only lists
 * columns), so relationships are **inferred by naming convention**: a
 * column ``<x>_id`` is treated as a reference to a table named ``x`` /
 * ``xs`` / ``xes`` when such a table exists. This is a heuristic — the UI
 * labels the edges as inferred. Real FK introspection is a future
 * SchemaInspector extension.
 */

export interface ErdColumn {
  name: string;
  type: string;
  /** Heuristic primary key (column literally named ``id``). */
  isKey?: boolean;
  /** Column that the FK-by-convention inference matched to another table. */
  isRef?: boolean;
}

export interface ErdEntity {
  /** Full (possibly schema-qualified) table name. */
  table: string;
  columns: ErdColumn[];
}

export interface ErdRelation {
  /** Full table name holding the ``<x>_id`` column. */
  from: string;
  /** Full table name the column is inferred to reference. */
  to: string;
  /** The referencing column name. */
  column: string;
}

export interface ErdModel {
  entities: ErdEntity[];
  relations: ErdRelation[];
}

export interface RawTable {
  table: string;
  columns: { name: string; type: string }[];
}

/** Last dotted segment, lower-cased — ``public.Customers`` → ``customers``. */
function unqualified(table: string): string {
  const seg = table.includes(".") ? table.slice(table.lastIndexOf(".") + 1) : table;
  return seg.toLowerCase();
}

/**
 * Build the ERD model. Relationships are inferred from ``<x>_id`` columns
 * (see module docstring). Pure + deterministic so it can be unit-reasoned
 * and story-driven.
 */
export function buildErdModel(tables: RawTable[]): ErdModel {
  // Map each table's unqualified lower name to its full name for lookup.
  const byName = new Map<string, string>();
  for (const t of tables) {
    byName.set(unqualified(t.table), t.table);
  }

  const relations: ErdRelation[] = [];
  const entities: ErdEntity[] = tables.map((t) => {
    const columns: ErdColumn[] = t.columns.map((c) => {
      const lc = c.name.toLowerCase();
      const col: ErdColumn = { name: c.name, type: c.type };
      if (lc === "id") col.isKey = true;
      if (lc !== "id" && lc.endsWith("_id")) {
        const base = lc.slice(0, -3); // strip "_id"
        const candidates = [base, `${base}s`, `${base}es`];
        const targetKey = candidates.find((cand) => byName.has(cand));
        if (targetKey) {
          const to = byName.get(targetKey)!;
          if (to !== t.table) {
            col.isRef = true;
            relations.push({ from: t.table, to, column: c.name });
          }
        }
      }
      return col;
    });
    return { table: t.table, columns };
  });

  return { entities, relations };
}
