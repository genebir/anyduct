/**
 * No-code filter ⇄ Python expression bridge.
 *
 * The core filter transform evaluates a Python expression with `data` (the
 * record dict) and `metadata` in scope and no builtins. This module turns a
 * list of simple AND-joined conditions into that expression and parses our
 * own output back, so the visual builder can re-hydrate a saved filter.
 * Anything we can't parse (hand-written / OR / functions) falls back to the
 * raw "advanced" editor — generation is the only path that must be correct.
 */

export type FilterOp =
  | "eq"
  | "ne"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "contains"
  | "empty"
  | "notEmpty";

export interface Condition {
  field: string;
  op: FilterOp;
  value: string;
}

export const FILTER_OPS: FilterOp[] = [
  "eq",
  "ne",
  "gt",
  "gte",
  "lt",
  "lte",
  "contains",
  "empty",
  "notEmpty",
];

export function opNeedsValue(op: FilterOp): boolean {
  return op !== "empty" && op !== "notEmpty";
}

const CMP: Record<string, string> = {
  eq: "==",
  ne: "!=",
  gt: ">",
  gte: ">=",
  lt: "<",
  lte: "<=",
};

/** Render a raw string as a Python literal (number / bool / None / string). */
function pyLiteral(raw: string): string {
  const s = raw.trim();
  if (s === "") return '""';
  if (/^-?\d+(\.\d+)?$/.test(s)) return s;
  if (s === "true" || s === "True") return "True";
  if (s === "false" || s === "False") return "False";
  if (s === "null" || s === "None") return "None";
  // JSON.stringify gives a safely-escaped double-quoted string that is also a
  // valid Python string literal.
  return JSON.stringify(s);
}

/** Best-effort inverse of pyLiteral for editing. */
function decodeLiteral(lit: string): string {
  const s = lit.trim();
  if (s === "True") return "true";
  if (s === "False") return "false";
  if (s === "None") return "null";
  if (
    (s.startsWith('"') && s.endsWith('"')) ||
    (s.startsWith("'") && s.endsWith("'"))
  ) {
    if (s.startsWith('"')) {
      try {
        return JSON.parse(s) as string;
      } catch {
        /* fall through */
      }
    }
    return s.slice(1, -1);
  }
  return s;
}

export function buildExpr(conds: Condition[]): string {
  const clauses: string[] = [];
  for (const c of conds) {
    const field = c.field.trim();
    if (!field) continue;
    const acc = `data[${JSON.stringify(field)}]`;
    if (c.op === "empty") clauses.push(`not ${acc}`);
    else if (c.op === "notEmpty") clauses.push(acc);
    else if (c.op === "contains") clauses.push(`${pyLiteral(c.value)} in ${acc}`);
    else clauses.push(`${acc} ${CMP[c.op]} ${pyLiteral(c.value)}`);
  }
  return clauses.join(" and ");
}

// Single capture group = the dict key (keys with quotes aren't supported).
const KEY = "data\\[\\s*['\"]([^'\"]+)['\"]\\s*\\]";

function parseClause(p: string): Condition | null {
  let m: RegExpExecArray | null;
  if ((m = new RegExp(`^not\\s+${KEY}$`).exec(p))) {
    return { field: m[1], op: "empty", value: "" };
  }
  if ((m = new RegExp(`^${KEY}$`).exec(p))) {
    return { field: m[1], op: "notEmpty", value: "" };
  }
  if ((m = new RegExp(`^(.+?)\\s+in\\s+${KEY}$`).exec(p))) {
    return { field: m[2], op: "contains", value: decodeLiteral(m[1]) };
  }
  if ((m = new RegExp(`^${KEY}\\s*(==|!=|>=|<=|>|<)\\s*(.+)$`).exec(p))) {
    const opMap: Record<string, FilterOp> = {
      "==": "eq",
      "!=": "ne",
      ">": "gt",
      ">=": "gte",
      "<": "lt",
      "<=": "lte",
    };
    return { field: m[1], op: opMap[m[2]], value: decodeLiteral(m[3]) };
  }
  return null;
}

/**
 * Parse an expression back into conditions, or null if it isn't a plain
 * AND-join of clauses we recognise (caller should show the raw editor).
 */
export function parseExpr(expr: string): Condition[] | null {
  const trimmed = expr.trim();
  if (trimmed === "") return [];
  const conds: Condition[] = [];
  for (const part of trimmed.split(" and ")) {
    const c = parseClause(part.trim());
    if (!c) return null;
    conds.push(c);
  }
  return conds;
}
