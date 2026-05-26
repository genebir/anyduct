"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ActivityIcon, PlayIcon, SaveIcon, XCircleIcon, ZapIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Button } from "@/components/ui/button";
import { PipelineSettingsPanel } from "@/components/builder/pipeline-settings-panel";
import { GraphEditor } from "@/components/builder/graph-editor";
import {
  ApiError,
  connectionsApi,
  pipelinesApi,
  type ConnectionSummary,
  type DryRunResponse,
  type PipelineSummary,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";
import {
  blankGraph,
  deserialize,
  deserializeGraph,
  extractPipelineMeta,
  isGraphConfig,
  linearToGraph,
  serializeGraph,
  validateGraph,
  DEFAULT_DLQ,
  DEFAULT_RETRY,
  type DlqSettings,
  type GraphBuilderState,
  type PipelineConfigJson,
  type RetrySettings,
} from "@/lib/pipeline-config";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

/**
 * Pipeline editor — **graph-only** since 2026-05-26 (user request).
 *
 * Every pipeline is composed on the dataflow canvas. Loading a config that
 * was saved by the old linear builder transparently converts to a graph
 * (``linearToGraph``) plus extracts retry / dlq / variables / triggers as
 * separate state. Saving always emits ``graph: { nodes, edges }`` — the
 * linear ``source / transforms / sink`` shape isn't written back.
 *
 * Right-side panels are now (a) the per-node ``PropertiesPanel`` when a
 * node is selected, (b) the per-edge branch-condition editor when an edge
 * is selected, (c) the ``PipelineSettingsPanel`` (retry, dlq, variables,
 * downstream triggers) when nothing is selected.
 */
export default function PipelineEditorPage() {
  const { slug, id } = useParams<{ slug: string; id: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();

  const [pipeline, setPipeline] = useState<PipelineSummary | null>(null);
  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [allPipelines, setAllPipelines] = useState<PipelineSummary[]>([]);
  const [graphState, setGraphState] = useState<GraphBuilderState>(() => blankGraph());
  const [retry, setRetry] = useState<RetrySettings>({ ...DEFAULT_RETRY });
  const [dlq, setDlq] = useState<DlqSettings>({ ...DEFAULT_DLQ });
  const [variables, setVariables] = useState<Record<string, unknown>>({});
  const [triggers, setTriggers] = useState<string[]>([]);
  const [autoMaterialize, setAutoMaterialize] = useState(false);
  const [freshnessSla, setFreshnessSla] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [dryRunning, setDryRunning] = useState(false);
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null);
  const [dirty, setDirty] = useState(false);
  const loadedRef = useRef(false);

  // Load pipeline + connections + triggers.
  useEffect(() => {
    if (!ws) return;
    let cancelled = false;
    (async () => {
      try {
        const [p, conns, pipelines] = await Promise.all([
          pipelinesApi.get(ws.id, id),
          connectionsApi.list(ws.id),
          pipelinesApi.list(ws.id),
        ]);
        if (cancelled) return;
        setPipeline(p);
        setConnections(conns);
        setAllPipelines(pipelines.filter((x) => x.id !== id));
        const cfg = p.current_config_json as PipelineConfigJson | null;
        const meta = extractPipelineMeta(cfg);
        setVariables(meta.variables);
        setAutoMaterialize(meta.auto_materialize);
        setFreshnessSla(meta.freshness_sla_minutes);
        setRetry(meta.retry);
        setDlq(meta.dlq);
        // Graph configs load directly; legacy linear configs convert in-place
        // so the user sees the same DAG without a migration prompt.
        if (isGraphConfig(cfg)) {
          setGraphState(deserializeGraph(cfg, conns));
        } else if (cfg) {
          setGraphState(linearToGraph(deserialize(cfg, conns)));
        } else {
          setGraphState(blankGraph());
        }
        // Downstream triggers — best-effort; a pending `0003` migration
        // must not break the editor.
        try {
          const t = await pipelinesApi.getTriggers(ws.id, id);
          if (!cancelled) setTriggers(t.target_pipeline_ids);
        } catch {
          /* triggers table may not exist yet — ignore */
        }
        if (cancelled) return;
        setDirty(false);
        loadedRef.current = true;
      } catch (err) {
        toast.error(err instanceof ApiError ? err.message : t("builder.loadFailed"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws, id, t]);

  const updateGraph = useCallback((next: GraphBuilderState) => {
    setGraphState(next);
    setDirty(true);
  }, []);

  const updateRetry = useCallback((next: RetrySettings) => {
    setRetry(next);
    setDirty(true);
  }, []);
  const updateDlq = useCallback((next: DlqSettings) => {
    setDlq(next);
    setDirty(true);
  }, []);
  const updateVariables = useCallback((next: Record<string, unknown>) => {
    setVariables(next);
    setDirty(true);
  }, []);
  const updateTriggers = useCallback((next: string[]) => {
    setTriggers(next);
    setDirty(true);
  }, []);

  // Pipeline data mode (batch | stream) drives the palette's connector
  // filtering. The mode itself is fixed at creation time and not editable
  // here — it lives in the saved config.
  const dataMode: "batch" | "stream" =
    (pipeline?.current_config_json as { mode?: string } | null)?.mode === "stream"
      ? "stream"
      : "batch";

  // Structural problems that would make the save fail server validation,
  // surfaced inline before the user clicks save.
  const graphIssues = useMemo(() => validateGraph(graphState), [graphState]);

  const onSave = useCallback(async () => {
    if (!ws || !pipeline) return;
    if (graphIssues.length > 0) {
      toast.error(graphIssues[0]);
      return;
    }
    setSaving(true);
    try {
      const config = serializeGraph(graphState, {
        name: pipeline.name,
        mode: dataMode,
        variables,
        auto_materialize: autoMaterialize,
        freshness_sla_minutes: freshnessSla,
        retry,
        dlq,
      });
      const updated = await pipelinesApi.update(ws.id, pipeline.id, { config });
      // Downstream triggers live outside config_json (ADR-0029) — persist
      // them on every save. Best-effort so a pending migration doesn't
      // fail the whole save.
      try {
        await pipelinesApi.setTriggers(ws.id, pipeline.id, triggers);
      } catch {
        /* triggers table may not exist yet */
      }
      setPipeline(updated);
      setDirty(false);
      toast.success(t("builder.saved", { version: updated.current_version ?? "?" }));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("builder.saveFailed"));
    } finally {
      setSaving(false);
    }
  }, [
    ws,
    pipeline,
    graphState,
    graphIssues,
    dataMode,
    variables,
    autoMaterialize,
    freshnessSla,
    retry,
    dlq,
    triggers,
    t,
  ]);

  const onDryRun = useCallback(async () => {
    if (!ws || !pipeline) return;
    if (dirty) {
      toast.warning(t("builder.unsavedRun"));
      return;
    }
    setDryRunning(true);
    setDryRunResult(null);
    try {
      const result = await pipelinesApi.dryRun(ws.id, pipeline.id);
      setDryRunResult(result);
      if (result.ok) toast.success(t("builder.dryRunPassed"));
      else toast.error(t("builder.dryRunIssues"));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("builder.dryRunFailedToast"));
    } finally {
      setDryRunning(false);
    }
  }, [ws, pipeline, dirty, t]);

  const onTrigger = useCallback(async () => {
    if (!ws || !pipeline) return;
    if (dirty) {
      toast.warning(t("builder.unsavedRun"));
      return;
    }
    try {
      await pipelinesApi.trigger(ws.id, pipeline.id);
      toast.success(t("pipelines.runQueued", { name: pipeline.name }));
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : t("pipelines.triggerFailed"));
    }
  }, [ws, pipeline, dirty, t]);

  const settingsPanel = (
    <PipelineSettingsPanel
      retry={retry}
      dlq={dlq}
      connections={connections}
      variables={variables}
      triggers={triggers}
      pipelines={allPipelines}
      onChangeRetry={updateRetry}
      onChangeDlq={updateDlq}
      onChangeVariables={updateVariables}
      onChangeTriggers={updateTriggers}
    />
  );

  return (
    <>
      <Header
        title={pipeline?.name ?? t("builder.title")}
        subtitle={
          pipeline
            ? `${ws?.name ?? ""} · v${pipeline.current_version ?? t("builder.draft")}`
            : t("common.loading")
        }
        actions={
          <div className="flex items-center gap-2">
            {dirty ? (
              <span
                className="inline-flex items-center gap-1.5 rounded-sm border border-warning/40 bg-warning/10 px-2 py-1 text-xs font-medium text-warning"
                title={t("builder.unsavedRun")}
              >
                <span className="h-1.5 w-1.5 rounded-full bg-warning" aria-hidden />
                {t("builder.unsaved")}
              </span>
            ) : null}
            <label
              className="flex items-center gap-1.5 text-xs text-text-secondary"
              title={t("autoMat.help")}
            >
              <input
                type="checkbox"
                checked={autoMaterialize}
                onChange={(e) => {
                  setAutoMaterialize(e.target.checked);
                  setDirty(true);
                }}
                className="accent-[rgb(var(--accent))]"
              />
              <span className="text-text-muted">{t("autoMat.label")}</span>
            </label>
            <label
              className="flex items-center gap-1.5 text-xs text-text-secondary"
              title={t("freshness.help")}
            >
              <span className="text-text-muted">{t("freshness.label")}</span>
              <input
                type="number"
                min={0}
                value={freshnessSla ?? ""}
                placeholder={t("freshness.placeholder")}
                onChange={(e) => {
                  const v = e.target.value;
                  setFreshnessSla(v === "" ? null : Math.max(0, Number(v)));
                  setDirty(true);
                }}
                className="h-8 w-20 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
              />
            </label>
            <Button
              variant="ghost"
              onClick={onDryRun}
              loading={dryRunning}
              disabled={!pipeline?.current_version}
              title={!pipeline?.current_version ? t("builder.saveFirst") : undefined}
            >
              <ZapIcon size={16} />
              {t("builder.dryRun")}
            </Button>
            <Button
              variant="ghost"
              onClick={onTrigger}
              disabled={!pipeline?.current_version}
              title={!pipeline?.current_version ? t("builder.saveFirst") : undefined}
            >
              <PlayIcon size={16} />
              {t("common.trigger")}
            </Button>
            {/* Quick nav to the runs list pre-filtered to THIS pipeline.
                The runs page reads ``?pipeline=<id>`` and shows a banner
                so the user can tell they're not seeing the workspace-wide
                list. */}
            {pipeline ? (
              <Link
                href={`/w/${slug}/runs?pipeline=${pipeline.id}`}
                className="inline-flex h-9 items-center gap-1.5 rounded-md px-3 text-sm text-text-secondary transition duration-150 hover:bg-overlay hover:text-text"
                title={t("builder.viewRunsTitle")}
              >
                <ActivityIcon size={16} />
                {t("builder.viewRuns")}
              </Link>
            ) : null}
            <Button onClick={onSave} loading={saving} disabled={!pipeline}>
              <SaveIcon size={16} />
              {t("common.save")}
            </Button>
          </div>
        }
      />
      {graphIssues.length > 0 ? (
        <div className="flex shrink-0 items-center gap-2 border-b border-warning/40 bg-warning/10 px-4 py-2 text-sm text-warning">
          <XCircleIcon size={16} className="shrink-0" />
          <span>{t("graph.invalid", { issue: graphIssues[0] })}</span>
        </div>
      ) : null}
      <GraphEditor
        state={graphState}
        connections={connections}
        mode={dataMode}
        onChange={updateGraph}
        workspaceId={ws?.id}
        settingsPanel={settingsPanel}
        dryRunPanel={
          dryRunResult ? (
            <DryRunPanel result={dryRunResult} onDismiss={() => setDryRunResult(null)} t={t} />
          ) : null
        }
      />
    </>
  );
}

// --- Dry run panel (graph-only mode reuses the same component) -------------

function DryRunPanel({
  result,
  onDismiss,
  t,
}: {
  result: DryRunResponse;
  onDismiss: () => void;
  t: Translate;
}) {
  const checkedCount = result.connectors.length;
  return (
    <div
      className="shrink-0 border-t border-border-subtle bg-elevated px-4 py-3 text-sm"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {result.ok ? (
            <span className="inline-flex items-center gap-1 text-success">
              ✓ {t("builder.dryRunPassedHeader")}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 text-error">
              ✗ {t("builder.dryRunFailedHeader")}
            </span>
          )}
          <span className="text-text-muted">
            {t("builder.connectorsChecked", { count: checkedCount })}
          </span>
        </div>
        <button
          type="button"
          aria-label="dismiss"
          onClick={onDismiss}
          className="text-text-muted hover:text-text"
        >
          ×
        </button>
      </div>
      {result.errors.length > 0 ? (
        <ul className="mt-2 list-inside list-disc text-error">
          {result.errors.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      ) : null}
      {result.connectors.some((c) => !c.ok) ? (
        <ul className="mt-2 list-inside list-disc text-error">
          {result.connectors
            .filter((c) => !c.ok)
            .map((c, i) => (
              <li key={i}>
                <strong>{c.name}</strong>: {c.error ?? t("builder.dryRunFailedHeader")}
              </li>
            ))}
        </ul>
      ) : null}
    </div>
  );
}
