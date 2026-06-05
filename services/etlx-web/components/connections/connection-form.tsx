"use client";

import { useMemo, useState, type FormEvent } from "react";
import { EyeIcon, EyeOffIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import {
  ApiError,
  connectionsApi,
  type ConnectionCreateBody,
  type ConnectionSummary,
} from "@/lib/api";
import {
  CONNECTOR_SCHEMAS,
  findSchema,
  type ConnectorField,
} from "@/lib/connector-schemas";
import { useLocale } from "@/components/providers/locale-provider";

type FieldValue = string | number | boolean | undefined;

export interface ConnectionFormProps {
  workspaceId: string;
  mode: "create" | "edit";
  /** Required in edit mode. */
  existing?: ConnectionSummary;
  /** Phase ADE (2026-06-04) — number of pipelines referencing this
   *  connection by name. In edit mode, renaming breaks them (configs
   *  reference by name), so we warn when the name is changed. */
  usageCount?: number;
  onSaved: (c: ConnectionSummary) => void;
  onCancel: () => void;
}

export function ConnectionForm(props: ConnectionFormProps) {
  if (props.mode === "edit") return <EditForm {...props} existing={props.existing!} />;
  return <CreateForm {...props} />;
}

function CreateForm({ workspaceId, onSaved, onCancel }: ConnectionFormProps) {
  const { t } = useLocale();
  const [type, setType] = useState(CONNECTOR_SCHEMAS[0].type);
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, FieldValue>>({});
  const [submitting, setSubmitting] = useState(false);

  const schema = useMemo(() => findSchema(type)!, [type]);

  function setField(key: string, value: FieldValue) {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  function changeType(next: string) {
    setType(next);
    // Reset values so a stale field from a previous type doesn't leak into
    // the new connector's config.
    setValues({});
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!name.trim()) {
      toast.error(t("connForm.nameRequired"));
      return;
    }

    setSubmitting(true);
    try {
      const body = buildCreateBody({ name: name.trim(), schema, values });
      const created = await connectionsApi.create(workspaceId, body);
      toast.success(t("connForm.created", { name: created.name }));
      onSaved(created);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("connForm.createFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={t("connForm.newTitle")}
        description={t("connForm.newDesc")}
      />
      <form onSubmit={onSubmit} className="grid gap-4 md:grid-cols-2">
        <FieldRow label={t("connForm.name")} className="md:col-span-2">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={t("connForm.namePlaceholder")}
            required
          />
        </FieldRow>
        <FieldRow label={t("connForm.type")} className="md:col-span-2">
          <ConnectorTypeRadio value={type} onChange={changeType} />
        </FieldRow>
        {schema.fields.map((field) => (
          <FieldRow
            key={field.key}
            label={field.label}
            help={field.help}
            className={fieldSpan(field)}
          >
            <FieldInput
              field={field}
              value={values[field.key] ?? field.defaultValue}
              onChange={(v) => setField(field.key, v)}
            />
          </FieldRow>
        ))}
        <div className="flex justify-end gap-2 pt-2 md:col-span-2">
          <Button variant="ghost" type="button" onClick={onCancel} disabled={submitting}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" loading={submitting}>
            {t("connForm.create")}
          </Button>
        </div>
      </form>
    </Card>
  );
}

function EditForm({
  workspaceId,
  existing,
  usageCount = 0,
  onSaved,
  onCancel,
}: ConnectionFormProps & { existing: ConnectionSummary }) {
  const { t } = useLocale();
  const [name, setName] = useState(existing.name);
  const [submitting, setSubmitting] = useState(false);
  const renaming = name.trim() !== existing.name && name.trim().length > 0;

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (name.trim() === existing.name) {
      onCancel();
      return;
    }
    setSubmitting(true);
    try {
      const updated = await connectionsApi.update(workspaceId, existing.id, {
        name: name.trim(),
      });
      toast.success(t("connForm.renamed", { name: updated.name }));
      onSaved(updated);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("connForm.renameFailed"),
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title={t("connForm.editTitle", { name: existing.name })}
        description={t("connForm.editDesc")}
      />
      <form onSubmit={onSubmit} className="flex flex-col gap-3 md:flex-row md:items-end">
        <FieldRow label={t("connForm.name")} className="flex-1">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          {/* Phase ADE (2026-06-04) — renaming a referenced connection
              breaks the pipelines that name it (configs aren't rewritten
              automatically). Warn so the operator updates them too. */}
          {renaming && usageCount > 0 ? (
            <span className="mt-1 text-xs text-warning">
              {t("connForm.renameInUseWarn", { count: usageCount })}
            </span>
          ) : null}
        </FieldRow>
        <div className="flex gap-2">
          <Button variant="ghost" type="button" onClick={onCancel} disabled={submitting}>
            {t("common.cancel")}
          </Button>
          <Button type="submit" loading={submitting}>
            {t("common.save")}
          </Button>
        </div>
      </form>
    </Card>
  );
}

function FieldRow({
  label,
  help,
  className,
  children,
}: {
  label: string;
  help?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={cn("flex flex-col gap-1.5", className)}>
      <span className="text-xs font-semibold uppercase tracking-wider text-text-secondary">
        {label}
      </span>
      {children}
      {help ? <span className="text-[11px] text-text-muted">{help}</span> : null}
    </label>
  );
}

function FieldInput({
  field,
  value,
  onChange,
}: {
  field: ConnectorField;
  value: FieldValue;
  onChange: (v: FieldValue) => void;
}) {
  // Phase AAQ post-mortem (2026-05-29) — keep the user's cleared
  // value as an empty string instead of collapsing to ``undefined``.
  // The form's render reads ``values[field.key] ?? defaultValue``,
  // so an ``undefined`` from a cleared input re-applied the schema
  // default ("host" snapping back to "localhost") on every keystroke
  // — surprising and annoying. An empty string is *defined*, so the
  // ``??`` keeps it; ``buildCreateBody`` still treats empty as
  // "omit the key" so the runtime falls back to its connector
  // default at construction time. Same effect on the wire, much
  // less surprise in the UI.
  const { t } = useLocale();
  const [reveal, setReveal] = useState(false);
  if (field.type === "number") {
    return (
      <Input
        type="number"
        value={value == null ? "" : String(value)}
        placeholder={field.placeholder}
        required={field.required}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v === "" ? "" : Number(v));
        }}
      />
    );
  }
  if (field.type === "boolean") {
    // Phase AAQ post-mortem 3 (2026-05-29) — Vertica's ``ssl`` is a
    // real bool on the wire. A free-text input let users type "false"
    // and the driver bounced it with "ssl should be a bool or
    // ssl.SSLContext". A checkbox makes the type-on-the-wire match
    // what the user sees.
    const checked =
      typeof value === "boolean"
        ? value
        : typeof field.defaultValue === "boolean"
          ? field.defaultValue
          : false;
    return (
      <label className="flex cursor-pointer items-center gap-2 text-sm text-text">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          className="h-4 w-4 cursor-pointer accent-accent"
        />
        <span className="select-none text-text-secondary">
          {checked ? "Enabled" : "Disabled"}
        </span>
      </label>
    );
  }
  if (field.type === "password") {
    return (
      <div className="relative">
        <Input
          type={reveal ? "text" : "password"}
          value={(value as string) ?? ""}
          placeholder={field.placeholder ?? "••••••••"}
          required={field.required}
          autoComplete="new-password"
          onChange={(e) => onChange(e.target.value)}
          className="pr-9"
        />
        <button
          type="button"
          onClick={() => setReveal((v) => !v)}
          aria-label={reveal ? t("connForm.concealValue") : t("connForm.revealValue")}
          title={reveal ? t("connForm.concealValue") : t("connForm.revealValue")}
          className="absolute inset-y-0 right-0 flex items-center px-2.5 text-text-muted hover:text-text"
        >
          {reveal ? <EyeOffIcon size={15} /> : <EyeIcon size={15} />}
        </button>
      </div>
    );
  }
  return (
    <Input
      value={(value as string) ?? ""}
      placeholder={field.placeholder}
      required={field.required}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function ConnectorTypeRadio({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {CONNECTOR_SCHEMAS.map((s) => (
        <button
          key={s.type}
          type="button"
          onClick={() => onChange(s.type)}
          className={cn(
            "flex flex-col items-start gap-0.5 rounded-md border px-3 py-2 text-left transition duration-150",
            value === s.type
              ? "border-accent bg-overlay text-text"
              : "border-border-subtle text-text-secondary hover:border-border-strong hover:text-text",
          )}
        >
          <span className="text-sm font-medium">{s.label}</span>
          <span className="text-[11px] text-text-muted">{s.type}</span>
        </button>
      ))}
    </div>
  );
}

function fieldSpan(field: ConnectorField): string {
  // Long-form fields (URLs, paths, expressions) take both columns; everything
  // else is comfortable in one.
  if (["endpoint_url", "database", "bootstrap_servers"].includes(field.key))
    return "md:col-span-2";
  return "";
}

/* ─────────────────────────────────────────────────────────────────────────
   Wire-format builder — translates the flat values map into the
   ``config`` + ``secrets`` shape the server expects.
   ─────────────────────────────────────────────────────────────────────── */

function buildCreateBody({
  name,
  schema,
  values,
}: {
  name: string;
  schema: { type: string; fields: ConnectorField[] };
  values: Record<string, FieldValue>;
}): ConnectionCreateBody {
  const config: Record<string, unknown> = {};
  const secrets: Record<string, string> = {};

  for (const field of schema.fields) {
    const raw = values[field.key];
    const value = raw === undefined ? field.defaultValue : raw;

    if (value === undefined || value === "") {
      if (field.required) {
        throw new Error(`Field "${field.label}" is required.`);
      }
      continue;
    }

    if (field.isSecret) {
      // Always allocate a logical key derived from the field name. The marker
      // tells the server "look up this key in secrets and stash it in the
      // configured backend". The plaintext only travels over the wire (TLS
      // assumed at the API gateway) and never lands in metadata DB.
      secrets[field.key] = String(value);
      config[field.key] = { $secret: field.key };
      continue;
    }

    config[field.key] = value;
  }

  return {
    name,
    type: schema.type,
    config,
    secrets,
  };
}
