"use client";

/**
 * MigrationForm — dedicated, builder-free form for cross-DB
 * migrations (Phase AAN2, 2026-05-29).
 *
 * The migration surface is intentionally narrower than the graph
 * builder: one source connection, one source query, one sink
 * connection, one sink table, and the few knobs that make
 * cross-DB replication safe (mode, key_columns when upserting,
 * auto_create_if_exists). No transforms, no fan-out, no joins —
 * users who need those graduate to the pipelines builder.
 *
 * The form is presentational only: it doesn't load or save, it
 * just renders the data + validation and emits ``onChange`` /
 * ``onSubmit``. The new + edit pages own the lifecycle.
 */

import type { FormEvent } from "react";
import { useMemo } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useLocale } from "@/components/providers/locale-provider";
import type { ConnectionSummary } from "@/lib/api";
import {
  MIGRATION_SUPPORTED_TYPES,
  type IfExists,
  type MigrationFormData,
  type SinkMode,
  validateMigrationForm,
} from "@/lib/migration-config";

interface Props {
  name: string;
  onNameChange: (v: string) => void;
  /** ``null`` while the parent is loading. We render disabled
   *  controls instead of unmounting so the form layout doesn't
   *  jump when the data lands. */
  form: MigrationFormData | null;
  onChange: (next: MigrationFormData) => void;
  connections: ConnectionSummary[];
  /** Pass ``true`` to disable the name input (e.g. on edit page —
   *  rename is a separate concern; keep this form focused on the
   *  migration contract itself). */
  nameLocked?: boolean;
  submitting: boolean;
  onSubmit: () => void;
  onCancel: () => void;
  submitLabel: string;
}

const SINK_MODES: SinkMode[] = ["append", "overwrite", "upsert"];

export function MigrationForm({
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
  // ``SchemaWriter`` so ``auto_create_table`` actually does
  // something.
  const supportedConnections = useMemo(
    () =>
      connections.filter((c) => MIGRATION_SUPPORTED_TYPES.has(c.type)),
    [connections],
  );

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
    sourceConnection: "",
    sourceQuery: "",
    sinkConnection: "",
    sinkTable: "",
    sinkMode: "overwrite" as SinkMode,
    keyColumns: "",
    autoCreateTable: true,
    ifExists: "skip" as IfExists,
  };

  return (
    <Card>
      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
            {t("common.name")}
          </span>
          <Input
            value={name}
            placeholder="orders_replication"
            onChange={(e) => onNameChange(e.target.value)}
            disabled={disabled || !!nameLocked}
          />
        </label>

        <fieldset className="flex flex-col gap-3 rounded-md border border-border-subtle p-4">
          <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-text-secondary">
            {t("migrations.formSource")}
          </legend>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs text-text-secondary">
              {t("migrations.formConnection")}
            </span>
            <select
              value={f.sourceConnection}
              onChange={(e) => field("sourceConnection", e.target.value)}
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
          <label className="flex flex-col gap-1.5">
            <span className="text-xs text-text-secondary">
              {t("migrations.formQuery")}
            </span>
            <textarea
              value={f.sourceQuery}
              onChange={(e) => field("sourceQuery", e.target.value)}
              disabled={disabled}
              rows={3}
              placeholder="SELECT id, amount, created_at FROM orders"
              className="rounded-md border border-border-subtle bg-elevated px-3 py-2 font-mono text-sm text-text focus-visible:border-accent focus-visible:outline-none"
            />
            {errors.sourceQuery ? (
              <span className="text-xs text-error">
                {t("migrations.errRequired")}
              </span>
            ) : null}
          </label>
        </fieldset>

        <fieldset className="flex flex-col gap-3 rounded-md border border-border-subtle p-4">
          <legend className="px-1 text-xs font-semibold uppercase tracking-wider text-text-secondary">
            {t("migrations.formSink")}
          </legend>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5">
              <span className="text-xs text-text-secondary">
                {t("migrations.formConnection")}
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
            <label className="flex flex-col gap-1.5">
              <span className="text-xs text-text-secondary">
                {t("migrations.formTable")}
              </span>
              <Input
                value={f.sinkTable}
                onChange={(e) => field("sinkTable", e.target.value)}
                disabled={disabled}
                placeholder="orders_copy"
              />
              {errors.sinkTable ? (
                <span className="text-xs text-error">
                  {t("migrations.errRequired")}
                </span>
              ) : null}
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs text-text-secondary">
                {t("migrations.formMode")}
              </span>
              <select
                value={f.sinkMode}
                onChange={(e) =>
                  field("sinkMode", e.target.value as SinkMode)
                }
                disabled={disabled}
                className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
              >
                {SINK_MODES.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs text-text-secondary">
                {t("migrations.formIfExists")}
              </span>
              <select
                value={f.ifExists}
                onChange={(e) =>
                  field("ifExists", e.target.value as IfExists)
                }
                disabled={disabled}
                className="h-10 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
              >
                <option value="skip">{t("migrations.ifExistsSkip")}</option>
                <option value="drop">{t("migrations.ifExistsDrop")}</option>
                <option value="error">{t("migrations.ifExistsError")}</option>
              </select>
            </label>
          </div>
          {f.sinkMode === "upsert" ? (
            <label className="flex flex-col gap-1.5">
              <span className="text-xs text-text-secondary">
                {t("migrations.formKeyColumns")}
              </span>
              <Input
                value={f.keyColumns}
                onChange={(e) => field("keyColumns", e.target.value)}
                disabled={disabled}
                placeholder="id"
              />
              <span className="text-xs text-text-muted">
                {t("migrations.formKeyColumnsHelp")}
              </span>
              {errors.keyColumns ? (
                <span className="text-xs text-error">
                  {t("migrations.errRequired")}
                </span>
              ) : null}
            </label>
          ) : null}
        </fieldset>

        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="ghost"
            onClick={onCancel}
            disabled={submitting}
          >
            {t("common.cancel")}
          </Button>
          <Button
            type="submit"
            loading={submitting}
            disabled={disabled}
          >
            {submitLabel}
          </Button>
        </div>
      </form>
    </Card>
  );
}
