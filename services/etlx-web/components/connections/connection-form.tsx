"use client";

import { useMemo, useState, type FormEvent } from "react";
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

type FieldValue = string | number | undefined;

export interface ConnectionFormProps {
  workspaceId: string;
  mode: "create" | "edit";
  /** Required in edit mode. */
  existing?: ConnectionSummary;
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
  onSaved,
  onCancel,
}: ConnectionFormProps & { existing: ConnectionSummary }) {
  const { t } = useLocale();
  const [name, setName] = useState(existing.name);
  const [submitting, setSubmitting] = useState(false);

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
  if (field.type === "number") {
    return (
      <Input
        type="number"
        value={value == null ? "" : String(value)}
        placeholder={field.placeholder}
        required={field.required}
        onChange={(e) => {
          const v = e.target.value;
          onChange(v === "" ? undefined : Number(v));
        }}
      />
    );
  }
  if (field.type === "password") {
    return (
      <Input
        type="password"
        value={(value as string) ?? ""}
        placeholder={field.placeholder ?? "••••••••"}
        required={field.required}
        autoComplete="new-password"
        onChange={(e) => onChange(e.target.value || undefined)}
      />
    );
  }
  return (
    <Input
      value={(value as string) ?? ""}
      placeholder={field.placeholder}
      required={field.required}
      onChange={(e) => onChange(e.target.value || undefined)}
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
