"use client";

/**
 * MigrationForm — Phase AAN3 (2026-05-29).
 *
 * Migration-shaped, not ETL-shaped. The user picks a *table* to
 * copy (not a query), picks one of three humanised strategies (not
 * a sink mode + if-exists matrix), and sees a live source schema
 * preview so they know what's about to land on the destination.
 *
 * Layout reads left-to-right: SOURCE card → arrow → DESTINATION
 * card. Strategy and schema preview sit below. The visual
 * differentiation from the pipelines builder is the whole point —
 * a migration is "copy this table over there", not "extract,
 * transform, load".
 */

import type { FormEvent } from "react";
import { useEffect, useMemo, useState } from "react";
import { ArrowRightIcon } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useLocale } from "@/components/providers/locale-provider";
import { ApiError, connectionsApi, type ConnectionSummary } from "@/lib/api";
import {
  MIGRATION_SUPPORTED_TYPES,
  type MigrationFormData,
  type MigrationMode,
  type MigrationStrategy,
  parseQualifiedTable,
  validateMigrationForm,
} from "@/lib/migration-config";
import { translateType } from "@/lib/cross-db-type-mapping";

interface Props {
  workspaceId: string;
  name: string;
  onNameChange: (v: string) => void;
  /** ``null`` while the parent is loading. We render disabled
   *  controls instead of unmounting so the form layout doesn't
   *  jump when the data lands. */
  form: MigrationFormData | null;
  onChange: (next: MigrationFormData) => void;
  connections: ConnectionSummary[];
  nameLocked?: boolean;
  submitting: boolean;
  onSubmit: () => void;
  onCancel: () => void;
  submitLabel: string;
}

const STRATEGIES: MigrationStrategy[] = ["snapshot", "append", "mirror"];

function strategyLabel(
  s: MigrationStrategy,
  t: (k: never) => string,
): { label: string; desc: string } {
  const tx = t as unknown as (k: string) => string;
  if (s === "snapshot") {
    return {
      label: tx("migrations.strategySnapshot"),
      desc: tx("migrations.strategySnapshotDesc"),
    };
  }
  if (s === "append") {
    return {
      label: tx("migrations.strategyAppend"),
      desc: tx("migrations.strategyAppendDesc"),
    };
  }
  return {
    label: tx("migrations.strategyMirror"),
    desc: tx("migrations.strategyMirrorDesc"),
  };
}

export function MigrationForm({
  workspaceId,
  name,
  onNameChange,
  form,
  onChange,
  connections,
  nameLocked,
  submitting,
  onSubmit,
  onCancel,
  submitLabel,
}: Props) {
  const { t } = useLocale();

  // Only RDBMS connections — the ones whose connector implements
  // ``SchemaWriter`` (``ensure_table``).
  const supportedConnections = useMemo(
    () =>
      connections.filter((c) => MIGRATION_SUPPORTED_TYPES.has(c.type)),
    [connections],
  );

  const connByName = useMemo(() => {
    const m = new Map<string, ConnectionSummary>();
    for (const c of supportedConnections) m.set(c.name, c);
    return m;
  }, [supportedConnections]);

  // Source-tables introspection (Phase AAN3) — populate the table
  // picker so the user picks rather than types. ADR-0033.
  const sourceConnRow = form
    ? connByName.get(form.sourceConnection) ?? null
    : null;
  // Phase ABF (2026-06-01) — destination dialect for the schema
  // preview's "→ dest type" column. ``null`` when the user hasn't
  // picked a destination yet; the preview hides the column then.
  const sinkConnRow = form
    ? connByName.get(form.sinkConnection) ?? null
    : null;
  const sinkDialect = sinkConnRow?.type ?? "";
  const [sourceTables, setSourceTables] = useState<string[]>([]);
  const [tablesLoading, setTablesLoading] = useState(false);
  useEffect(() => {
    if (!sourceConnRow) {
      setSourceTables([]);
      return;
    }
    let cancelled = false;
    setTablesLoading(true);
    (async () => {
      try {
        const resp = await connectionsApi.tables(workspaceId, sourceConnRow.id);
        if (!cancelled) setSourceTables(resp.tables);
      } catch {
        if (!cancelled) setSourceTables([]); // soft-fail; users can type anyway
      } finally {
        if (!cancelled) setTablesLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sourceConnRow, workspaceId]);

  // Source-columns preview (Phase AAN3) — show the user what's
  // about to land on the destination. Soft-fail: an unknown schema
  // doesn't block the form.
  const [sourceColumns, setSourceColumns] = useState<
    { name: string; type: string }[]
  >([]);
  const [columnsState, setColumnsState] =
    useState<"idle" | "loading" | "ok" | "fail">("idle");
  useEffect(() => {
    if (!sourceConnRow || !form?.sourceTable) {
      setSourceColumns([]);
      setColumnsState("idle");
      return;
    }
    let cancelled = false;
    setColumnsState("loading");
    (async () => {
      try {
        const resp = await connectionsApi.columns(
          workspaceId,
          sourceConnRow.id,
          form.sourceTable,
        );
        if (cancelled) return;
        setSourceColumns(resp.columns);
        setColumnsState("ok");
      } catch (err) {
        if (cancelled) return;
        setSourceColumns([]);
        setColumnsState(err instanceof ApiError ? "fail" : "fail");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sourceConnRow, form?.sourceTable, workspaceId]);

  const errors = form ? validateMigrationForm(form) : {};

  function field<K extends keyof MigrationFormData>(
    key: K,
    value: MigrationFormData[K],
  ): void {
    if (!form) return;
    onChange({ ...form, [key]: value });
  }

  function handleSubmit(e: FormEvent<HTMLFormElement>): void {
    e.preventDefault();
    if (!form) return;
    onSubmit();
  }

  const disabled = !form || submitting;
  const f = form ?? {
    mode: "single" as MigrationMode,
    sourceConnection: "",
    sourceTable: "",
    sourceSchema: "",
    selectedTables: [] as string[],
    sinkConnection: "",
    sinkTable: "",
    sinkSchema: "",
    strategy: "snapshot" as MigrationStrategy,
    keyColumns: "",
    cursorColumn: "",
  };

  // Phase AAS (2026-06-01) — schema-mode helpers.
  // In schema mode the user types ``sourceSchema`` and we show every
  // ``<schema>.<table>`` that ``connectionsApi.tables`` returned for
  // that schema. Tables with no schema prefix (sqlite) all live under
  // the implicit "" bucket, which the form treats as a wildcard so a
  // bare ``orders`` shows up when ``sourceSchema === ""``.
  const tablesInSchema = useMemo(() => {
    if (!form || form.mode !== "schema") return [];
    const wanted = form.sourceSchema.trim();
    return sourceTables
      .map((q) => ({ qualified: q, parsed: parseQualifiedTable(q) }))
      .filter(({ parsed }) =>
        wanted === "" ? parsed.schema === null : parsed.schema === wanted,
      );
  }, [form, sourceTables]);

  const allSchemas = useMemo(() => {
    const s = new Set<string>();
    for (const q of sourceTables) {
      const p = parseQualifiedTable(q);
      if (p.schema) s.add(p.schema);
    }
    return [...s].sort();
  }, [sourceTables]);

  function toggleTable(qualified: string): void {
    if (!form) return;
    const has = form.selectedTables.includes(qualified);
    onChange({
      ...form,
      selectedTables: has
        ? form.selectedTables.filter((t) => t !== qualified)
        : [...form.selectedTables, qualified],
    });
  }

  function toggleAllTables(): void {
    if (!form) return;
    const all = tablesInSchema.map((t) => t.qualified);
    const allSelected =
      all.length > 0 && all.every((t) => form.selectedTables.includes(t));
    onChange({
      ...form,
      selectedTables: allSelected
        ? form.selectedTables.filter((t) => !all.includes(t))
        : Array.from(new Set([...form.selectedTables, ...all])),
    });
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-6">
      {/* Name */}
      <Card>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
            {t("common.name")}
          </span>
          <Input
            value={name}
            placeholder={
              f.mode === "schema" ? "ods_replication" : "orders_replication"
            }
            onChange={(e) => onNameChange(e.target.value)}
            disabled={disabled || !!nameLocked}
          />
          {f.mode === "schema" ? (
            <span className="text-xs text-text-muted">
              {t("migrations.schemaNameHint")}
            </span>
          ) : null}
        </label>

        {/* Phase AAS (2026-06-01) — mode toggle. ``nameLocked`` is
            set on the edit page so a saved single-table migration
            doesn't accidentally promote to schema mode (the page
            renders only one detail at a time). */}
        {!nameLocked ? (
          <div className="mt-4 flex gap-2 rounded-md bg-overlay p-1">
            {(["single", "schema"] as MigrationMode[]).map((m) => {
              const active = f.mode === m;
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => {
                    if (!form) return;
                    onChange({ ...form, mode: m });
                  }}
                  className={`flex-1 rounded px-3 py-1.5 text-xs font-medium transition ${
                    active
                      ? "bg-bg text-text shadow-sm"
                      : "text-text-muted hover:text-text"
                  }`}
                  disabled={disabled}
                >
                  {m === "single"
                    ? t("migrations.modeSingle")
                    : t("migrations.modeSchema")}
                </button>
              );
            })}
          </div>
        ) : null}
      </Card>

      {/* Direction — Source → Destination cards side by side */}
      <div className="grid items-stretch gap-3 sm:grid-cols-[1fr_auto_1fr]">
        {/* SOURCE */}
        <Card>
          <div className="text-[11px] font-semibold uppercase tracking-wider text-accent">
            {t("migrations.from")}
          </div>
          <div className="mt-3 flex flex-col gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-text-secondary">
                {t("migrations.fromConnection")}
              </span>
              <select
                value={f.sourceConnection}
                onChange={(e) => {
                  if (!form) return;
                  // Reset table / schema when the connection changes —
                  // both lookups depend on the connection.
                  onChange({
                    ...form,
                    sourceConnection: e.target.value,
                    sourceTable: "",
                    sourceSchema: "",
                    selectedTables: [],
                  });
                }}
                disabled={disabled}
                className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
              >
                <option value="">{t("builder.selectConnection")}</option>
                {supportedConnections.map((c) => (
                  <option key={c.id} value={c.name}>
                    {c.name} ({c.type})
                  </option>
                ))}
              </select>
              {errors.sourceConnection ? (
                <span className="text-xs text-error">
                  {t("migrations.errRequired")}
                </span>
              ) : null}
            </label>

            {f.mode === "single" ? (
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-secondary">
                  {t("migrations.pickTable")}
                </span>
                {/* Phase AAS follow-up (2026-06-01) — user request
                    "직접 테이블명 입력 말고 드롭다운으로 선택할 수
                    있도록". Free-text + datalist let the user type
                    typos that don't exist on the source; a real
                    ``<select>`` constrains them to the introspected
                    list. Loaded source tables (``sourceTables``)
                    already come from ``connectionsApi.tables`` which
                    is the runtime's source of truth. */}
                <select
                  value={f.sourceTable}
                  onChange={(e) => field("sourceTable", e.target.value)}
                  disabled={disabled || !sourceConnRow || tablesLoading}
                  className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
                >
                  <option value="">
                    {!sourceConnRow
                      ? t("migrations.pickConnFirst")
                      : tablesLoading
                        ? t("migrations.schemaLoading")
                        : sourceTables.length === 0
                          ? t("migrations.noTablesFound")
                          : t("migrations.pickTable")}
                  </option>
                  {sourceTables.map((tab) => (
                    <option key={tab} value={tab}>
                      {tab}
                    </option>
                  ))}
                </select>
                {errors.sourceTable ? (
                  <span className="text-xs text-error">
                    {t("migrations.errRequired")}
                  </span>
                ) : null}
              </label>
            ) : (
              <>
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-text-secondary">
                    {t("migrations.sourceSchema")}
                  </span>
                  <select
                    value={f.sourceSchema}
                    onChange={(e) => {
                      if (!form) return;
                      onChange({
                        ...form,
                        sourceSchema: e.target.value,
                        // Reset selections when the schema changes —
                        // the previous picks belonged to a different
                        // schema.
                        selectedTables: [],
                        // Mirror to destination by default so identity
                        // round-trips are the default.
                        sinkSchema: form.sinkSchema || e.target.value,
                      });
                    }}
                    disabled={disabled || !sourceConnRow || tablesLoading}
                    className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
                  >
                    <option value="">
                      {!sourceConnRow
                        ? t("migrations.pickConnFirst")
                        : tablesLoading
                          ? t("migrations.schemaLoading")
                          : allSchemas.length === 0
                            ? t("migrations.noSchemasFound")
                            : t("migrations.pickSchema")}
                    </option>
                    {allSchemas.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                  {errors.sourceSchema ? (
                    <span className="text-xs text-error">
                      {t("migrations.errRequired")}
                    </span>
                  ) : null}
                </label>
                <div className="flex flex-col gap-1">
                  <div className="flex items-baseline justify-between">
                    <span className="text-xs text-text-secondary">
                      {t("migrations.tablesToReplicate")}
                    </span>
                    <button
                      type="button"
                      onClick={toggleAllTables}
                      disabled={disabled || tablesInSchema.length === 0}
                      className="text-xs text-accent hover:underline disabled:text-text-muted disabled:no-underline"
                    >
                      {tablesInSchema.length > 0 &&
                      tablesInSchema.every((t) =>
                        f.selectedTables.includes(t.qualified),
                      )
                        ? t("migrations.deselectAll")
                        : t("migrations.selectAll")}
                    </button>
                  </div>
                  <div className="max-h-56 overflow-y-auto rounded-md border border-border-subtle">
                    {!sourceConnRow ? (
                      <p className="px-3 py-3 text-xs text-text-muted">
                        {t("migrations.pickConnFirst")}
                      </p>
                    ) : tablesLoading ? (
                      <p className="px-3 py-3 text-xs text-text-muted">
                        {t("migrations.schemaLoading")}
                      </p>
                    ) : tablesInSchema.length === 0 ? (
                      <p className="px-3 py-3 text-xs text-text-muted">
                        {f.sourceSchema
                          ? t("migrations.schemaNoTables", {
                              schema: f.sourceSchema,
                            })
                          : t("migrations.typeSchemaFirst")}
                      </p>
                    ) : (
                      <ul className="divide-y divide-border-subtle">
                        {tablesInSchema.map(({ qualified, parsed }) => {
                          const checked = f.selectedTables.includes(qualified);
                          return (
                            <li key={qualified}>
                              <label className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs hover:bg-overlay">
                                <input
                                  type="checkbox"
                                  className="h-3.5 w-3.5 cursor-pointer accent-accent"
                                  checked={checked}
                                  onChange={() => toggleTable(qualified)}
                                  disabled={disabled}
                                />
                                <span className="font-mono text-text">
                                  {parsed.name}
                                </span>
                              </label>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </div>
                  {errors.selectedTables ? (
                    <span className="text-xs text-error">
                      {t("migrations.selectAtLeastOne")}
                    </span>
                  ) : (
                    <span className="text-xs text-text-muted">
                      {t("migrations.selectedTablesCount", {
                        n: f.selectedTables.length,
                      })}
                    </span>
                  )}
                </div>
              </>
            )}
          </div>
        </Card>

        {/* arrow */}
        <div className="flex items-center justify-center px-1 text-accent">
          <ArrowRightIcon size={28} aria-hidden />
          <span className="sr-only">{t("migrations.directionArrow")}</span>
        </div>

        {/* DESTINATION */}
        <Card>
          <div className="text-[11px] font-semibold uppercase tracking-wider text-accent">
            {t("migrations.to")}
          </div>
          <div className="mt-3 flex flex-col gap-3">
            <label className="flex flex-col gap-1">
              <span className="text-xs text-text-secondary">
                {t("migrations.toConnection")}
              </span>
              <select
                value={f.sinkConnection}
                onChange={(e) => field("sinkConnection", e.target.value)}
                disabled={disabled}
                className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
              >
                <option value="">{t("builder.selectConnection")}</option>
                {supportedConnections.map((c) => (
                  <option key={c.id} value={c.name}>
                    {c.name} ({c.type})
                  </option>
                ))}
              </select>
              {errors.sinkConnection ? (
                <span className="text-xs text-error">
                  {t("migrations.errRequired")}
                </span>
              ) : null}
            </label>
            {f.mode === "single" ? (
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-secondary">
                  {t("migrations.destTable")}
                </span>
                <Input
                  value={f.sinkTable}
                  onChange={(e) => field("sinkTable", e.target.value)}
                  disabled={disabled}
                  placeholder={f.sourceTable || "orders_copy"}
                />
                <span className="text-xs text-text-muted">
                  {t("migrations.destTableHelp")}
                </span>
                {errors.sinkTable ? (
                  <span className="text-xs text-error">
                    {t("migrations.errRequired")}
                  </span>
                ) : null}
              </label>
            ) : (
              <label className="flex flex-col gap-1">
                <span className="text-xs text-text-secondary">
                  {t("migrations.destSchema")}
                </span>
                <Input
                  value={f.sinkSchema}
                  onChange={(e) => field("sinkSchema", e.target.value)}
                  disabled={disabled}
                  placeholder={f.sourceSchema || "public"}
                />
                <span className="text-xs text-text-muted">
                  {t("migrations.destSchemaHelp")}
                </span>
                {errors.sinkSchema ? (
                  <span className="text-xs text-error">
                    {t("migrations.errRequired")}
                  </span>
                ) : null}
              </label>
            )}
          </div>
        </Card>
      </div>

      {/* STRATEGY — humanised radio */}
      <Card>
        <div className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {t("migrations.strategy")}
        </div>
        <div className="mt-3 flex flex-col gap-2">
          {STRATEGIES.map((s) => {
            const { label, desc } = strategyLabel(s, t as never);
            const checked = f.strategy === s;
            return (
              <label
                key={s}
                className={`flex cursor-pointer items-start gap-3 rounded-md border p-3 transition ${
                  checked
                    ? "border-accent bg-overlay"
                    : "border-border-subtle hover:border-border-strong"
                }`}
              >
                <input
                  type="radio"
                  name="strategy"
                  className="mt-1 h-4 w-4 cursor-pointer accent-accent"
                  checked={checked}
                  onChange={() => field("strategy", s)}
                  disabled={disabled}
                />
                <div className="flex flex-col gap-0.5">
                  <span className="text-sm font-medium text-text">{label}</span>
                  <span className="text-xs text-text-muted">{desc}</span>
                </div>
              </label>
            );
          })}
        </div>

        {f.strategy === "append" ? (
          <label className="mt-4 flex flex-col gap-1">
            <span className="text-xs text-text-secondary">
              {t("migrations.cursorColumn")}
            </span>
            <Input
              value={f.cursorColumn}
              onChange={(e) => field("cursorColumn", e.target.value)}
              disabled={disabled}
              placeholder="updated_at"
            />
            <span className="text-xs text-text-muted">
              {t("migrations.cursorColumnHelp")}
            </span>
            {errors.cursorColumn ? (
              <span className="text-xs text-error">
                {t("migrations.errRequired")}
              </span>
            ) : null}
          </label>
        ) : null}

        {f.strategy === "mirror" ? (
          <label className="mt-4 flex flex-col gap-1">
            <span className="text-xs text-text-secondary">
              {t("migrations.keyColumnsLabel")}
            </span>
            <Input
              value={f.keyColumns}
              onChange={(e) => field("keyColumns", e.target.value)}
              disabled={disabled}
              placeholder="id"
            />
            <span className="text-xs text-text-muted">
              {t("migrations.keyColumnsHelp")}
            </span>
            {errors.keyColumns ? (
              <span className="text-xs text-error">
                {t("migrations.errRequired")}
              </span>
            ) : null}
          </label>
        ) : null}
      </Card>

      {/* SCHEMA PREVIEW — only when we have a source connection + table */}
      {sourceConnRow && f.sourceTable ? (
        <Card>
          <div className="flex items-baseline justify-between">
            <div className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
              {t("migrations.schemaPreview")}
            </div>
            {columnsState === "ok" ? (
              <span className="text-xs text-text-muted">
                {t("migrations.schemaPreviewHint", {
                  n: sourceColumns.length,
                })}
              </span>
            ) : null}
          </div>
          <div className="mt-3 max-h-56 overflow-y-auto rounded-md border border-border-subtle">
            {columnsState === "loading" ? (
              <p className="px-3 py-4 text-xs text-text-muted">
                {t("migrations.schemaLoading")}
              </p>
            ) : columnsState === "fail" ? (
              <p className="px-3 py-4 text-xs text-warning">
                {t("migrations.schemaLoadFailed")}
              </p>
            ) : columnsState === "ok" && sourceColumns.length > 0 ? (
              <table className="w-full text-xs">
                {sinkDialect ? (
                  <thead>
                    <tr className="border-b border-border-subtle">
                      <th className="px-3 py-1 text-left text-[10px] font-semibold uppercase text-text-muted">
                        {t("migrations.colColumn")}
                      </th>
                      <th className="px-3 py-1 text-left text-[10px] font-semibold uppercase text-text-muted">
                        {t("migrations.colSourceType")}
                      </th>
                      <th className="px-3 py-1 text-left text-[10px] font-semibold uppercase text-text-muted">
                        {t("migrations.colDestType", { dialect: sinkDialect })}
                      </th>
                    </tr>
                  </thead>
                ) : null}
                <tbody>
                  {sourceColumns.map((c) => {
                    const isPk =
                      f.strategy === "mirror" &&
                      f.keyColumns
                        .split(",")
                        .map((s) => s.trim())
                        .includes(c.name);
                    const destType = sinkDialect
                      ? translateType(c.type, sinkDialect)
                      : null;
                    return (
                      <tr
                        key={c.name}
                        className="border-b border-border-subtle last:border-0"
                      >
                        <td className="px-3 py-1.5 font-mono text-text">
                          {c.name}
                          {isPk ? (
                            <span className="ml-2 inline-flex h-4 items-center rounded-sm bg-accent/15 px-1 text-[10px] font-semibold uppercase text-accent">
                              PK
                            </span>
                          ) : null}
                        </td>
                        <td className="px-3 py-1.5 font-mono text-text-muted">
                          {c.type}
                        </td>
                        {sinkDialect ? (
                          <td className="px-3 py-1.5 font-mono text-accent">
                            {destType}
                          </td>
                        ) : null}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : null}
          </div>
        </Card>
      ) : null}

      <div className="flex justify-end gap-2">
        <Button
          type="button"
          variant="ghost"
          onClick={onCancel}
          disabled={submitting}
        >
          {t("common.cancel")}
        </Button>
        <Button type="submit" loading={submitting} disabled={disabled}>
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
