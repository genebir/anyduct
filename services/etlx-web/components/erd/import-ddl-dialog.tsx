"use client";

/**
 * Paste-DDL import dialog (Phase AIP). Paste CREATE TABLE / ALTER TABLE DDL
 * from any RDBMS and import it into the ERD designer.
 */

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { parseDdl } from "@/lib/ddl-import";
import type { ErdDesign } from "@/lib/erd-design";
import { useLocale } from "@/components/providers/locale-provider";

const PLACEHOLDER = `CREATE TABLE dept (
  dept_no  VARCHAR(10) PRIMARY KEY,
  dept_nm  VARCHAR(200) NOT NULL
);`;

export function ImportDdlDialog({
  open,
  onImport,
  onClose,
}: {
  open: boolean;
  onImport: (design: ErdDesign) => void;
  onClose: () => void;
}) {
  const { t } = useLocale();
  const [text, setText] = useState("");
  if (!open) return null;

  const preview = (() => {
    if (!text.trim()) return null;
    try {
      return parseDdl(text);
    } catch {
      return null;
    }
  })();

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgb(10_18_40_/_0.6)] p-4 backdrop-blur-md"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[80vh] w-full max-w-2xl flex-col gap-3 rounded-lg border border-border-subtle bg-surface p-4"
      >
        <div className="text-sm font-semibold text-text">{t("erdDdl.title")}</div>
        <p className="text-xs text-text-muted">{t("erdDdl.desc")}</p>
        <textarea
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={PLACEHOLDER}
          spellCheck={false}
          className="h-64 w-full resize-none rounded-md border border-border-subtle bg-bg p-2 font-mono text-xs text-text focus-visible:border-accent focus-visible:outline-none"
        />
        <div className="flex items-center justify-between">
          <span className="text-xs text-text-muted">
            {preview
              ? t("erdDdl.preview", {
                  tables: preview.tables.length,
                  rels: preview.relations.length,
                })
              : ""}
          </span>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={onClose}>
              {t("common.cancel")}
            </Button>
            <Button
              size="sm"
              disabled={!preview || preview.tables.length === 0}
              onClick={() => {
                if (preview) onImport(preview);
              }}
            >
              {t("erdDdl.import")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
