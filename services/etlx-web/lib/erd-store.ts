/**
 * Named ERD diagram store (Phase AHB).
 *
 * Lets the designer save/list/open/rename/delete *multiple* named ERDs
 * (manage them, not just one autosave). Client-side (localStorage) for
 * now; a server-backed store (shared across users, like pipelines) is the
 * planned follow-up — this module is the single seam to swap later.
 */

import { EMPTY_DESIGN, newId, type ErdDesign } from "@/lib/erd-design";

export interface ErdDoc {
  id: string;
  name: string;
  updatedAt: number;
}

const listKey = (slug: string) => `etlx:erd:list:${slug}`;
const docKey = (slug: string, id: string) => `etlx:erd:doc:${slug}:${id}`;

function read<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota / private mode — non-fatal */
  }
}

/** Diagrams for a workspace, newest first. */
export function listDocs(slug: string): ErdDoc[] {
  return read<ErdDoc[]>(listKey(slug), []).sort((a, b) => b.updatedAt - a.updatedAt);
}

export function loadDesign(slug: string, id: string): ErdDesign {
  return read<ErdDesign>(docKey(slug, id), EMPTY_DESIGN);
}

/** Upsert a diagram's name + design, stamping updatedAt. */
export function saveDoc(slug: string, id: string, name: string, design: ErdDesign): void {
  write(docKey(slug, id), design);
  const list = read<ErdDoc[]>(listKey(slug), []).filter((d) => d.id !== id);
  list.push({ id, name, updatedAt: Date.now() });
  write(listKey(slug), list);
}

export function renameDoc(slug: string, id: string, name: string): void {
  const list = read<ErdDoc[]>(listKey(slug), []).map((d) =>
    d.id === id ? { ...d, name, updatedAt: Date.now() } : d,
  );
  write(listKey(slug), list);
}

export function deleteDoc(slug: string, id: string): void {
  try {
    localStorage.removeItem(docKey(slug, id));
  } catch {
    /* ignore */
  }
  write(
    listKey(slug),
    read<ErdDoc[]>(listKey(slug), []).filter((d) => d.id !== id),
  );
}

/** Create a new (empty) diagram, returning its id. */
export function createDoc(slug: string, name: string): string {
  const id = newId("erd");
  saveDoc(slug, id, name, EMPTY_DESIGN);
  return id;
}
