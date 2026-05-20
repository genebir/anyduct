/**
 * Starter templates for the pipeline create flow.
 *
 * Each template builds a ready-to-edit BuilderState — operators are
 * pre-selected and laid out source → (transforms) → sink so a non-developer
 * starts from a working shape and only fills in connections + table names,
 * instead of facing a blank canvas. Connections are intentionally left empty
 * (the user picks their own); the builder flags those as incomplete.
 */

import {
  DEFAULT_DLQ,
  DEFAULT_RETRY,
  makeNode,
  type BuilderState,
} from "./pipeline-config";
import type { Messages } from "./i18n/messages";

export interface PipelineTemplate {
  id: string;
  labelKey: keyof Messages;
  descKey: keyof Messages;
  mode: "batch" | "stream";
  /** lucide icon name rendered by the picker (kept as a string to avoid a
   *  component import in this data module). */
  build: () => BuilderState;
}

function state(operatorIds: string[]): BuilderState {
  return {
    nodes: operatorIds.map((id) => makeNode(id)),
    retry: { ...DEFAULT_RETRY },
    dlq: { ...DEFAULT_DLQ },
  };
}

export const PIPELINE_TEMPLATES: PipelineTemplate[] = [
  {
    id: "blank",
    labelKey: "tpl.blank",
    descKey: "tpl.blankDesc",
    mode: "batch",
    build: () => state(["source:postgres", "sink:postgres"]),
  },
  {
    id: "db-copy",
    labelKey: "tpl.dbCopy",
    descKey: "tpl.dbCopyDesc",
    mode: "batch",
    build: () => state(["source:postgres", "sink:postgres"]),
  },
  {
    id: "db-filtered-copy",
    labelKey: "tpl.dbFiltered",
    descKey: "tpl.dbFilteredDesc",
    mode: "batch",
    build: () => state(["source:postgres", "transform:filter", "sink:postgres"]),
  },
  {
    id: "api-to-table",
    labelKey: "tpl.apiToTable",
    descKey: "tpl.apiToTableDesc",
    mode: "batch",
    build: () => state(["source:http", "sink:postgres"]),
  },
  {
    id: "db-to-s3",
    labelKey: "tpl.dbToS3",
    descKey: "tpl.dbToS3Desc",
    mode: "batch",
    build: () => state(["source:postgres", "sink:s3"]),
  },
  {
    id: "stream-load",
    labelKey: "tpl.streamLoad",
    descKey: "tpl.streamLoadDesc",
    mode: "stream",
    build: () => state(["source:kafka", "sink:postgres"]),
  },
];

export function findTemplate(id: string): PipelineTemplate | undefined {
  return PIPELINE_TEMPLATES.find((tmpl) => tmpl.id === id);
}
