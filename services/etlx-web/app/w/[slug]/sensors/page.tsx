"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import {
  CheckCircle2Icon,
  EditIcon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  RadarIcon,
  Trash2Icon,
  XCircleIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Card, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DataTable, type Column } from "@/components/ui/data-table";
import {
  ContextMenu,
  ContextMenuItem,
  ContextMenuSeparator,
  useContextMenu,
} from "@/components/ui/context-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  pipelinesApi,
  sensorsApi,
  type PipelineSummary,
  type SensorCheckResponse,
  type SensorCreateBody,
  type SensorSummary,
  type SensorUpdateBody,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";
import { cn } from "@/lib/cn";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

/** UI-known sensor types. Each entry mirrors a registered core/service
 *  builder so the select stays the dispatch SSoT.
 *  - ``http`` (core)            : polls a URL, fires on status / contains
 *  - ``asset_freshness`` (svc)  : fires when ``assets.last_materialized_at``
 *                                  is older than ``max_age_minutes`` or
 *                                  the asset has never materialised.
 *  ``configHint`` is shown under the JSON editor so the operator has a
 *  starting shape without leaving the page.
 */
const SENSOR_TYPES: { value: string; label: string; configHint: string }[] = [
  {
    value: "http",
    label: "HTTP (poll URL)",
    configHint: '{"url": "https://example.com/healthz", "expect_status": 200}',
  },
  {
    value: "asset_freshness",
    label: "Asset freshness (catalog stale-watch)",
    configHint: '{"asset_key": "postgres://prod/main/users", "max_age_minutes": 30}',
  },
  {
    value: "lineage_arrival",
    label: "Lineage arrival (upstream materialisation)",
    configHint:
      '{"upstream_asset_keys": ["postgres://prod/main/orders","postgres://prod/main/users"], "window_minutes": 60, "require_all": true}',
  },
];

type FormState =
  | { kind: "closed" }
  | { kind: "create" }
  | { kind: "edit"; sensor: SensorSummary };

interface FormValues {
  name: string;
  type: string;
  configText: string; // raw JSON the user edits
  targetPipelineId: string;
  pollIntervalSeconds: number;
  isActive: boolean;
}

function defaultValuesFor(sensor: SensorSummary | null): FormValues {
  return {
    name: sensor?.name ?? "",
    type: sensor?.type ?? "http",
    // pretty-print so the operator can read what's stored without
    // mentally unfolding it.
    configText: JSON.stringify(sensor?.config_json ?? {}, null, 2),
    targetPipelineId: sensor?.target_pipeline_id ?? "",
    pollIntervalSeconds: sensor?.poll_interval_seconds ?? 60,
    isActive: sensor?.is_active ?? true,
  };
}

function buildColumns(
  t: Translate,
  onCheck: (s: SensorSummary) => void,
  onEdit: (s: SensorSummary) => void,
  onDelete: (s: SensorSummary) => void,
  checking: string | null,
  pipelineNameById: Map<string, string>,
): Column<SensorSummary>[] {
  return [
    { key: "name", header: t("sensors.colName"), cell: (s) => s.name },
    {
      key: "type",
      header: t("sensors.colType"),
      cell: (s) => (
        <span className="rounded-sm bg-overlay px-2 py-0.5 font-mono text-xs text-text-secondary">
          {s.type}
        </span>
      ),
    },
    {
      key: "target",
      header: t("sensors.colTarget"),
      cell: (s) =>
        s.target_pipeline_id ? (
          <span className="text-text-secondary">
            {pipelineNameById.get(s.target_pipeline_id) ?? s.target_pipeline_id.slice(0, 8)}
          </span>
        ) : (
          <span className="text-warning">{t("sensors.orphaned")}</span>
        ),
    },
    {
      key: "interval",
      header: t("sensors.colInterval"),
      cell: (s) => (
        <span className="font-mono text-xs text-text-secondary">{s.poll_interval_seconds}s</span>
      ),
    },
    {
      key: "status",
      header: t("common.status"),
      cell: (s) =>
        s.is_active ? (
          <span className="text-success">{t("common.active")}</span>
        ) : (
          <span className="text-text-muted">{t("common.paused")}</span>
        ),
    },
    {
      key: "lastCheck",
      header: t("sensors.colLastCheck"),
      cell: (s) =>
        s.last_check_at ? (
          <LastCheckCell sensor={s} t={t} />
        ) : (
          <span className="text-text-muted">—</span>
        ),
    },
    {
      key: "actions",
      header: "",
      cell: (s) => (
        <div className="flex items-center justify-end gap-1.5">
          <button
            type="button"
            onClick={() => onCheck(s)}
            disabled={checking === s.id}
            title={t("sensors.checkNow")}
            aria-label={t("sensors.checkNowAria", { name: s.name })}
            className="inline-flex h-7 w-7 items-center justify-center rounded-sm text-text-muted transition duration-150 hover:bg-overlay hover:text-text disabled:opacity-50"
          >
            <PlayIcon size={14} />
          </button>
          <button
            type="button"
            onClick={() => onEdit(s)}
            title={t("common.edit")}
            aria-label={t("sensors.editAria", { name: s.name })}
            className="inline-flex h-7 w-7 items-center justify-center rounded-sm text-text-muted transition duration-150 hover:bg-overlay hover:text-text"
          >
            <PencilIcon size={14} />
          </button>
          <button
            type="button"
            onClick={() => onDelete(s)}
            title={t("common.delete")}
            aria-label={t("sensors.deleteAria", { name: s.name })}
            className="inline-flex h-7 w-7 items-center justify-center rounded-sm text-text-muted transition duration-150 hover:bg-overlay hover:text-error"
          >
            <Trash2Icon size={14} />
          </button>
        </div>
      ),
    },
  ];
}

function LastCheckCell({ sensor, t }: { sensor: SensorSummary; t: Translate }) {
  const result = sensor.last_result_json;
  const triggered = result?.triggered ?? false;
  const when = sensor.last_check_at
    ? new Date(sensor.last_check_at).toLocaleString()
    : "—";
  return (
    <div className="flex items-center gap-1.5">
      {triggered ? (
        <CheckCircle2Icon size={14} className="text-success" />
      ) : (
        <XCircleIcon size={14} className="text-text-muted" />
      )}
      <span
        className="text-xs text-text-secondary"
        title={result?.message ?? t("sensors.noMessage")}
      >
        {when}
      </span>
    </div>
  );
}

export default function SensorsPage() {
  const { slug } = useParams<{ slug: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();
  const [pipelines, setPipelines] = useState<PipelineSummary[]>([]);
  const [rows, setRows] = useState<SensorSummary[] | null>(null);
  const [form, setForm] = useState<FormState>({ kind: "closed" });
  const [pendingDelete, setPendingDelete] = useState<SensorSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [checking, setChecking] = useState<string | null>(null);
  const rowMenu = useContextMenu();
  const rowMenuTargetRef = useRef<SensorSummary | null>(null);

  async function refresh(workspaceId: string) {
    try {
      const [ps, rs] = await Promise.all([
        pipelinesApi.list(workspaceId),
        sensorsApi.list(workspaceId),
      ]);
      setPipelines(ps);
      setRows(rs);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("sensors.loadFailed"));
      setRows([]);
    }
  }

  useEffect(() => {
    if (!ws) return;
    void refresh(ws.id);
  }, [ws]);

  async function onCheck(sensor: SensorSummary) {
    if (!ws) return;
    setChecking(sensor.id);
    try {
      const result: SensorCheckResponse = await sensorsApi.check(ws.id, sensor.id);
      toast[result.triggered ? "success" : "info"](
        result.triggered
          ? t("sensors.checkTriggered", { msg: result.message ?? "—" })
          : t("sensors.checkQuiet", { msg: result.message ?? "—" }),
      );
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("sensors.checkFailed"));
    } finally {
      setChecking(null);
    }
  }

  async function onConfirmDelete() {
    if (!ws || !pendingDelete) return;
    setDeleting(true);
    try {
      await sensorsApi.delete(ws.id, pendingDelete.id);
      toast.success(t("sensors.deleted", { name: pendingDelete.name }));
      setPendingDelete(null);
      await refresh(ws.id);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("sensors.deleteFailed"));
    } finally {
      setDeleting(false);
    }
  }

  async function onSaveCreate(values: FormValues) {
    if (!ws) return;
    const body = buildBodyOrThrow(values);
    if (body === null) return;
    try {
      await sensorsApi.create(ws.id, body);
      toast.success(t("sensors.created", { name: values.name }));
      setForm({ kind: "closed" });
      await refresh(ws.id);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("sensors.saveFailed"));
    }
  }

  async function onSaveUpdate(sensor: SensorSummary, values: FormValues) {
    if (!ws) return;
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(values.configText);
      if (typeof config !== "object" || config === null || Array.isArray(config)) {
        throw new Error("config must be a JSON object");
      }
    } catch (e) {
      toast.error(t("sensors.invalidJson", { error: String(e) }));
      return;
    }
    const body: SensorUpdateBody = {
      name: values.name,
      config_json: config,
      target_pipeline_id: values.targetPipelineId || null,
      poll_interval_seconds: values.pollIntervalSeconds,
      is_active: values.isActive,
    };
    try {
      await sensorsApi.update(ws.id, sensor.id, body);
      toast.success(t("sensors.updated", { name: values.name }));
      setForm({ kind: "closed" });
      await refresh(ws.id);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("sensors.saveFailed"));
    }
  }

  function buildBodyOrThrow(values: FormValues): SensorCreateBody | null {
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(values.configText);
      if (typeof config !== "object" || config === null || Array.isArray(config)) {
        throw new Error("config must be a JSON object");
      }
    } catch (e) {
      toast.error(t("sensors.invalidJson", { error: String(e) }));
      return null;
    }
    return {
      name: values.name,
      type: values.type,
      config_json: config,
      target_pipeline_id: values.targetPipelineId || null,
      poll_interval_seconds: values.pollIntervalSeconds,
      is_active: values.isActive,
    };
  }

  const pipelineNameById = new Map<string, string>(pipelines.map((p) => [p.id, p.name]));

  return (
    <>
      <Header
        title={t("nav.sensors")}
        subtitle={
          ws
            ? t("common.workspaceSubtitle", { name: ws.name })
            : t("common.loadingWorkspace")
        }
        actions={
          <Button
            variant="primary"
            size="md"
            onClick={() =>
              setForm((f) => (f.kind === "create" ? { kind: "closed" } : { kind: "create" }))
            }
          >
            <PlusIcon size={16} />
            {t("sensors.new")}
          </Button>
        }
      />
      <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 overflow-y-auto px-6 py-8">
        {form.kind === "create" && ws ? (
          <Card>
            <CardHeader title={t("sensors.newTitle")} description={t("sensors.newDesc")} />
            <SensorForm
              mode="create"
              pipelines={pipelines}
              initial={defaultValuesFor(null)}
              onCancel={() => setForm({ kind: "closed" })}
              onSubmit={onSaveCreate}
              t={t}
            />
          </Card>
        ) : null}

        {form.kind === "edit" && ws ? (
          <Card>
            <CardHeader
              title={t("sensors.editTitle", { name: form.sensor.name })}
              description={t("sensors.editDesc")}
            />
            <SensorForm
              mode="edit"
              pipelines={pipelines}
              initial={defaultValuesFor(form.sensor)}
              onCancel={() => setForm({ kind: "closed" })}
              onSubmit={(v) => onSaveUpdate(form.sensor, v)}
              t={t}
            />
          </Card>
        ) : null}

        <Card>
          <CardHeader title={t("sensors.listTitle")} description={t("sensors.listDesc")} />
          {rows === null ? (
            <p className="px-1 text-sm text-text-muted">{t("common.loading")}</p>
          ) : rows.length === 0 ? (
            <EmptyState
              icon={<RadarIcon size={28} />}
              title={t("sensors.emptyTitle")}
              description={t("sensors.emptyDesc")}
            />
          ) : (
            <DataTable<SensorSummary>
              rows={rows}
              columns={buildColumns(
                t,
                onCheck,
                (s) => setForm({ kind: "edit", sensor: s }),
                (s) => setPendingDelete(s),
                checking,
                pipelineNameById,
              )}
              onRowContextMenu={(row, e) => {
                rowMenuTargetRef.current = row;
                rowMenu.openOnEvent(e);
              }}
            />
          )}
        </Card>
      </main>

      <ConfirmDialog
        open={pendingDelete !== null}
        title={t("sensors.deleteTitle")}
        description={
          pendingDelete ? t("sensors.deleteDesc", { name: pendingDelete.name }) : ""
        }
        confirmLabel={t("common.delete")}
        cancelLabel={t("common.cancel")}
        loading={deleting}
        destructive
        onConfirm={onConfirmDelete}
        onCancel={() => setPendingDelete(null)}
      />

      {/* Row right-click — mirrors per-row buttons. */}
      <ContextMenu menu={rowMenu}>
        <ContextMenuItem
          icon={<PlayIcon size={14} />}
          onSelect={() => {
            const s = rowMenuTargetRef.current;
            if (s) void onCheck(s);
          }}
        >
          {t("sensors.checkNow")}
        </ContextMenuItem>
        <ContextMenuItem
          icon={<EditIcon size={14} />}
          onSelect={() => {
            const s = rowMenuTargetRef.current;
            if (s) setForm({ kind: "edit", sensor: s });
          }}
        >
          {t("common.edit")}
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          icon={<Trash2Icon size={14} />}
          destructive
          onSelect={() => {
            const s = rowMenuTargetRef.current;
            if (s) setPendingDelete(s);
          }}
        >
          {t("common.delete")}
        </ContextMenuItem>
      </ContextMenu>
    </>
  );
}

// --- form ------------------------------------------------------------------

function SensorForm({
  mode,
  pipelines,
  initial,
  onCancel,
  onSubmit,
  t,
}: {
  mode: "create" | "edit";
  pipelines: PipelineSummary[];
  initial: FormValues;
  onCancel: () => void;
  onSubmit: (values: FormValues) => void | Promise<void>;
  t: Translate;
}) {
  const [values, setValues] = useState<FormValues>(initial);
  const [submitting, setSubmitting] = useState(false);

  function update<K extends keyof FormValues>(k: K, v: FormValues[K]) {
    setValues((prev) => ({ ...prev, [k]: v }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await onSubmit(values);
    } finally {
      setSubmitting(false);
    }
  }

  // ``config_json`` parse error surfaced inline so the user sees feedback
  // before clicking save (matches the JSON field UX in the builder).
  let configError: string | null = null;
  try {
    const parsed = JSON.parse(values.configText);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      configError = t("sensors.configMustBeObject");
    }
  } catch (e) {
    configError = String(e);
  }

  const nameValid = values.name.trim().length > 0;
  const canSubmit = nameValid && configError === null && !submitting;

  return (
    <form className="flex flex-col gap-4" onSubmit={handleSubmit}>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label={t("sensors.fieldName")} required>
          <Input
            value={values.name}
            onChange={(e) => update("name", e.target.value)}
            placeholder="wait-for-upstream"
            disabled={mode === "edit"}
          />
        </Field>
        <Field label={t("sensors.fieldType")} required>
          <select
            value={values.type}
            onChange={(e) => update("type", e.target.value)}
            disabled={mode === "edit"}
            className="h-10 w-full rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none disabled:opacity-60"
          >
            {SENSOR_TYPES.map((spec) => (
              <option key={spec.value} value={spec.value}>
                {spec.label}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <Field
        label={t("sensors.fieldConfig")}
        required
        help={t("sensors.fieldConfigHelp")}
      >
        <textarea
          value={values.configText}
          onChange={(e) => update("configText", e.target.value)}
          rows={8}
          spellCheck={false}
          className={cn(
            "min-h-32 w-full rounded-md border bg-elevated px-3 py-2 font-mono text-xs text-text",
            "focus-visible:outline-none",
            configError
              ? "border-error focus-visible:border-error"
              : "border-border-subtle focus-visible:border-accent",
          )}
        />
        {configError ? (
          <p className="mt-1 text-xs text-error">{configError}</p>
        ) : (() => {
          // Surface the per-type sample config so the operator has the
          // shape in front of them without leaving the page. Hidden once
          // they start producing a parse error so the screen doesn't
          // double-stack diagnostic text.
          const hint = SENSOR_TYPES.find((s) => s.value === values.type)?.configHint;
          return hint ? (
            <p className="mt-1 font-mono text-[11px] text-text-secondary">
              {t("sensors.configExamplePrefix")}: {hint}
            </p>
          ) : null;
        })()}
      </Field>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label={t("sensors.fieldTarget")} help={t("sensors.fieldTargetHelp")}>
          <select
            value={values.targetPipelineId}
            onChange={(e) => update("targetPipelineId", e.target.value)}
            className="h-10 w-full rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
          >
            <option value="">{t("sensors.targetNone")}</option>
            {pipelines.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("sensors.fieldInterval")} required>
          <Input
            type="number"
            min={5}
            max={86400}
            value={values.pollIntervalSeconds}
            onChange={(e) =>
              update(
                "pollIntervalSeconds",
                Math.max(5, Math.min(86400, Number(e.target.value) || 60)),
              )
            }
          />
        </Field>
      </div>

      <label className="inline-flex items-center gap-2 text-sm text-text-secondary">
        <input
          type="checkbox"
          checked={values.isActive}
          onChange={(e) => update("isActive", e.target.checked)}
          className="h-4 w-4 accent-[rgb(var(--accent))]"
        />
        {t("sensors.fieldActive")}
      </label>

      <div className="flex items-center justify-end gap-2 pt-2">
        <Button type="button" variant="ghost" onClick={onCancel}>
          {t("common.cancel")}
        </Button>
        <Button type="submit" variant="primary" disabled={!canSubmit} loading={submitting}>
          {mode === "create" ? t("common.create") : t("common.save")}
        </Button>
      </div>
    </form>
  );
}

function Field({
  label,
  required,
  help,
  children,
}: {
  label: string;
  required?: boolean;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-text-secondary">
        {label}
        {required ? <span className="ml-1 text-error">*</span> : null}
      </span>
      {children}
      {help ? <span className="text-[10px] text-text-muted">{help}</span> : null}
    </label>
  );
}
