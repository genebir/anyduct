/**
 * Variable type inference + display badge — Phase AAL (2026-05-29).
 *
 * The builder's pipeline-settings panel (Phase L1) and the
 * ``/w/[slug]/variables`` page render the same shape (a small list of
 * key/value/description rows), so they should share the type-badge
 * vocabulary. Lifted out of the builder so both surfaces agree on
 * what "JSON" vs "number" means at a glance.
 */

export type VarType = "string" | "number" | "boolean" | "json";

export function inferType(value: unknown): VarType {
  if (typeof value === "string") return "string";
  if (typeof value === "number") return "number";
  if (typeof value === "boolean") return "boolean";
  return "json";
}
