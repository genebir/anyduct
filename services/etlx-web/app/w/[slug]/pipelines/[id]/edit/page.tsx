"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import {
  CheckCircle2Icon,
  ChevronRightIcon,
  PlayIcon,
  SaveIcon,
  XCircleIcon,
  ZapIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Button } from "@/components/ui/button";
import { Palette } from "@/components/builder/palette";
import { PropertiesPanel } from "@/components/builder/properties-panel";
import { BuilderCanvas } from "@/components/builder/builder-canvas";
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
import { cn } from "@/lib/cn";
import { findOperator, OPERATOR_KIND_ACCENT } from "@/lib/operators";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { useLocale } from "@/components/providers/locale-provider";
import type { Messages } from "@/lib/i18n/messages";
import {
  blankBuilder,
  callTargets,
  deserialize,
  deserializeGraph,
  isGraphConfig,
  isSparkUnsupported,
  linearToGraph,
  makeCallNode,
  makeNode,
  reorderNodes,
  serialize,
  serializeGraph,
  validateGraph,
  type Engine,
  type BuilderNode,
  type BuilderState,
  type DlqSettings,
  type GraphBuilderState,
  type PipelineConfigJson,
  type RetrySettings,
} from "@/lib/pipeline-config";

type Translate = (key: keyof Messages, vars?: Record<string, string | number>) => string;

export default function PipelineEditorPage() {
  const { slug, id } = useParams<{ slug: string; id: string }>();
  const ws = useWorkspaceFromSlug(slug);
  const { t } = useLocale();

  const [pipeline, setPipeline] = useState<PipelineSummary | null>(null);
  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [allPipelines, setAllPipelines] = useState<PipelineSummary[]>([]);
  const [state, setState] = useState<BuilderState>(() => blankBuilder());
  const [mode, setMode] = useState<"linear" | "graph">("linear");
  const [graphState, setGraphState] = useState<GraphBuilderState | null>(null);
  const [engine, setEngine] = useState<Engine>("local");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [dryRunning, setDryRunning] = useState(false);
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null);
  const [dirty, setDirty] = useState(false);
  const loadedRef = useRef(false);

  // Load pipeline + connections.
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
        setEngine((cfg?.engine as Engine) === "spark" ? "spark" : "local");
        // Graph pipelines (ADR-0030) open in the free-form graph editor.
        if (isGraphConfig(cfg)) {
          if (cancelled) return;
          setMode("graph");
          setGraphState(deserializeGraph(cfg, conns));
          setDirty(false);
          loadedRef.current = true;
          return;
        }
        const initial = deserialize(cfg, conns);
        // Downstream triggers (call-pipeline, ADR-0029) are surfaced as call
        // nodes on the canvas. Best-effort: a pending `0003` migration must not
        // break the editor or hide the pipeline list.
        let callNodes: BuilderNode[] = [];
        try {
          const triggers = await pipelinesApi.getTriggers(ws.id, id);
          callNodes = triggers.target_pipeline_ids.map((tid) => makeCallNode(tid));
        } catch {
          /* triggers table may not exist yet — ignore */
        }
        if (cancelled) return;
        const withCalls = { ...initial, nodes: [...initial.nodes, ...callNodes] };
        setState(withCalls);
        setSelectedId(withCalls.nodes[0]?.id ?? null);
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

  const addOperator = useCallback((operatorId: string) => {
    setState((prev) => {
      const next: BuilderNode = makeNode(operatorId);
      // A pipeline has exactly one source, so adding a source replaces the
      // existing one. Sinks fan out (ADR-0026): adding another sink keeps the
      // existing ones so the source can write to multiple destinations.
      const filtered = prev.nodes.filter((n) => {
        if (next.operatorId.startsWith("source:") && n.operatorId.startsWith("source:")) {
          return false;
        }
        return true;
      });
      const ordered = reorderNodes([...filtered, next]);
      return { ...prev, nodes: ordered };
    });
    setSelectedId(null);
    setDirty(true);
  }, []);

  const removeOperator = useCallback((nodeId: string) => {
    setState((prev) => ({
      ...prev,
      nodes: prev.nodes.filter((n) => n.id !== nodeId),
    }));
    setSelectedId((cur) => (cur === nodeId ? null : cur));
    setDirty(true);
  }, []);

  const reorderTransforms = useCallback((orderedIds: string[]) => {
    setState((prev) => {
      const byId = new Map(prev.nodes.map((n) => [n.id, n]));
      const newTransforms = orderedIds
        .map((tid) => byId.get(tid))
        .filter((n): n is BuilderNode => Boolean(n));
      const sources = prev.nodes.filter(
        (n) => findOperator(n.operatorId)?.kind === "source",
      );
      const sinks = prev.nodes.filter(
        (n) => findOperator(n.operatorId)?.kind === "sink",
      );
      const existingTransforms = prev.nodes.filter(
        (n) => findOperator(n.operatorId)?.kind === "transform",
      );
      if (newTransforms.length !== existingTransforms.length) return prev;
      return { ...prev, nodes: [...sources, ...newTransforms, ...sinks] };
    });
    setDirty(true);
  }, []);

  const selectNode = useCallback((nodeId: string) => {
    setSelectedId(nodeId);
  }, []);

  const moveTransform = useCallback((nodeId: string, dir: -1 | 1) => {
    setState((prev) => {
      const nodes = [...prev.nodes];
      const transformPositions = nodes
        .map((n, i) => ({ i, kind: findOperator(n.operatorId)?.kind }))
        .filter((x) => x.kind === "transform")
        .map((x) => x.i);
      const pos = transformPositions.findIndex((i) => nodes[i].id === nodeId);
      const swap = pos + dir;
      if (pos < 0 || swap < 0 || swap >= transformPositions.length) return prev;
      const a = transformPositions[pos];
      const b = transformPositions[swap];
      [nodes[a], nodes[b]] = [nodes[b], nodes[a]];
      return { ...prev, nodes };
    });
    setDirty(true);
  }, []);

  const updateNode = useCallback(
    (nodeId: string, values: Record<string, unknown>) => {
      setState((prev) => ({
        ...prev,
        nodes: prev.nodes.map((n) => (n.id === nodeId ? { ...n, data: values } : n)),
      }));
      setDirty(true);
    },
    [],
  );

  const updateRetry = useCallback((next: RetrySettings) => {
    setState((prev) => ({ ...prev, retry: next }));
    setDirty(true);
  }, []);

  const updateDlq = useCallback((next: DlqSettings) => {
    setState((prev) => ({ ...prev, dlq: next }));
    setDirty(true);
  }, []);

  const selectedNode = useMemo(
    () => state.nodes.find((n) => n.id === selectedId) ?? null,
    [state.nodes, selectedId],
  );

  const transformOrder = useMemo(
    () =>
      reorderNodes(state.nodes)
        .filter((n) => findOperator(n.operatorId)?.kind === "transform")
        .map((n) => n.id),
    [state.nodes],
  );
  const selectedTransformIndex =
    selectedNode && findOperator(selectedNode.operatorId)?.kind === "transform"
      ? transformOrder.indexOf(selectedNode.id)
      : -1;

  const updateGraph = useCallback((next: GraphBuilderState) => {
    setGraphState(next);
    setDirty(true);
  }, []);

  const switchToGraph = useCallback(() => {
    if (!window.confirm(t("graph.convertConfirm"))) return;
    setGraphState(linearToGraph(state));
    setMode("graph");
    setDirty(true);
  }, [state, t]);

  const onSave = useCallback(async () => {
    if (!ws || !pipeline) return;
    setSaving(true);
    try {
      const existingMode = (pipeline.current_config_json as { mode?: string } | null)?.mode;
      const m = existingMode === "stream" ? "stream" : "batch";
      if (mode === "graph" && graphState) {
        const issues = validateGraph(graphState);
        if (issues.length > 0) {
          toast.error(issues[0]);
          return;
        }
        const config = serializeGraph(graphState, { name: pipeline.name, mode: m, engine });
        const updated = await pipelinesApi.update(ws.id, pipeline.id, { config });
        setPipeline(updated);
        setDirty(false);
        toast.success(t("builder.saved", { version: updated.current_version ?? "?" }));
        return;
      }
      const config = serialize(state, { name: pipeline.name, mode: m, engine });
      const updated = await pipelinesApi.update(ws.id, pipeline.id, { config });
      // Call-pipeline nodes live outside config_json (ADR-0029) — persist them
      // as downstream triggers. Best-effort so a pending `0003` migration
      // doesn't fail the whole save.
      try {
        await pipelinesApi.setTriggers(ws.id, pipeline.id, callTargets(state.nodes));
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
  }, [ws, pipeline, state, mode, graphState, engine, t]);

  const setEngineDirty = useCallback((next: Engine) => {
    setEngine(next);
    setDirty(true);
  }, []);

  // Pipeline data mode (batch | stream) — drives palette connector filtering
  // and gates graph mode (graphs are batch-only, ADR-0030).
  const dataMode: "batch" | "stream" =
    (pipeline?.current_config_json as { mode?: string } | null)?.mode === "stream"
      ? "stream"
      : "batch";

  // Structural problems that would make a graph save fail server validation
  // (ADR-0030 tree rules) — surfaced before save so the user gets a clear reason.
  const graphIssues = useMemo(
    () => (mode === "graph" && graphState ? validateGraph(graphState) : []),
    [mode, graphState],
  );

  // Operators that can't run on Spark (ADR-0031) — surfaced as a warning when
  // the Spark engine is selected so the user fixes it before saving/running.
  const sparkBlockers = useMemo(() => {
    if (engine !== "spark") return [];
    const nodes =
      mode === "graph" && graphState ? graphState.nodes : state.nodes;
    return nodes
      .filter((n) => isSparkUnsupported(n.operatorId))
      .map((n) => findOperator(n.operatorId)?.label ?? n.operatorId);
  }, [engine, mode, graphState, state.nodes]);

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
            <label className="flex items-center gap-1.5 text-xs text-text-secondary">
              <span className="text-text-muted">{t("engine.label")}</span>
              <select
                value={engine}
                onChange={(e) => setEngineDirty(e.target.value as Engine)}
                className="h-8 rounded-md border border-border-subtle bg-elevated px-2 text-sm text-text focus-visible:border-accent focus-visible:outline-none"
                title={t("engine.help")}
              >
                <option value="local">{t("engine.local")}</option>
                <option value="spark">{t("engine.spark")}</option>
              </select>
            </label>
            {mode === "linear" ? (
              <Button
                variant="ghost"
                onClick={switchToGraph}
                disabled={!pipeline || dataMode === "stream"}
                title={dataMode === "stream" ? t("graph.streamNoGraph") : undefined}
              >
                {t("graph.switchToGraph")}
              </Button>
            ) : (
              <span className="rounded-sm border border-accent/40 bg-accent/10 px-2 py-1 text-xs font-medium text-accent">
                {t("graph.modeGraph")}
              </span>
            )}
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
            <Button onClick={onSave} loading={saving} disabled={!pipeline}>
              <SaveIcon size={16} />
              {t("common.save")}
            </Button>
          </div>
        }
      />
      {sparkBlockers.length > 0 ? (
        <div className="flex shrink-0 items-center gap-2 border-b border-error/40 bg-error/10 px-4 py-2 text-sm text-error">
          <XCircleIcon size={16} className="shrink-0" />
          <span>{t("engine.sparkUnsupported", { ops: sparkBlockers.join(", ") })}</span>
        </div>
      ) : null}
      {graphIssues.length > 0 ? (
        <div className="flex shrink-0 items-center gap-2 border-b border-warning/40 bg-warning/10 px-4 py-2 text-sm text-warning">
          <XCircleIcon size={16} className="shrink-0" />
          <span>{t("graph.invalid", { issue: graphIssues[0] })}</span>
        </div>
      ) : null}
      {mode === "graph" && graphState ? (
        <GraphEditor
          state={graphState}
          connections={connections}
          mode={dataMode}
          onChange={updateGraph}
        />
      ) : (
        <>
          <FlowSummary nodes={state.nodes} selectedId={selectedId} onSelect={selectNode} t={t} />
          <div className="flex min-h-0 flex-1 overflow-hidden">
            <Palette onAdd={addOperator} mode={dataMode} />
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="min-h-0 flex-1">
                <BuilderCanvas
                  nodes={state.nodes}
                  selectedId={selectedId}
                  onSelect={selectNode}
                  onRemove={removeOperator}
                  onDeselect={() => setSelectedId(null)}
                  onReorderTransforms={reorderTransforms}
                />
              </div>
              {dryRunResult ? (
                <DryRunPanel result={dryRunResult} onDismiss={() => setDryRunResult(null)} t={t} />
              ) : null}
            </div>
            {selectedNode ? (
              <PropertiesPanel
                node={selectedNode}
                connections={connections}
                workspaceId={ws?.id}
                pipelines={allPipelines}
                onChange={updateNode}
                transformIndex={selectedTransformIndex}
                transformCount={transformOrder.length}
                onMove={moveTransform}
              />
            ) : (
              <PipelineSettingsPanel
                retry={state.retry}
                dlq={state.dlq}
                connections={connections}
                onChangeRetry={updateRetry}
                onChangeDlq={updateDlq}
              />
            )}
          </div>
        </>
      )}
    </>
  );
}

/**
 * Plain-language read of the pipeline: source → transforms → sink(s) as
 * clickable chips, with fan-out sinks grouped and routing conditions hinted.
 */
function FlowSummary({
  nodes,
  selectedId,
  onSelect,
  t,
}: {
  nodes: BuilderNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  t: Translate;
}) {
  const ordered = reorderNodes(nodes);
  const toChip = (n: BuilderNode) => {
    const op = findOperator(n.operatorId);
    const kind = op?.kind ?? "transform";
    const needsConnection =
      (kind === "source" || kind === "sink") && !n.data.connection;
    const sub =
      kind === "source" || kind === "sink"
        ? needsConnection
          ? t("builder.flowNeedsConnection")
          : String(n.data.connection)
        : null;
    const cond =
      kind === "sink" && typeof n.data.when === "string" && n.data.when.trim()
        ? n.data.when.trim()
        : null;
    return { id: n.id, label: op?.label ?? n.operatorId, kind, needsConnection, sub, cond };
  };
  const isTerminal = (n: BuilderNode) => {
    const k = findOperator(n.operatorId)?.kind;
    return k === "sink" || k === "call";
  };
  const spine = ordered.filter((n) => !isTerminal(n));
  const sinks = ordered.filter(isTerminal);
  const spineChips = spine.map(toChip);
  const sinkChips = sinks.map(toChip);

  const chipButton = (c: ReturnType<typeof toChip>) => (
    <button
      type="button"
      onClick={() => onSelect(c.id)}
      className={cn(
        "flex shrink-0 items-center gap-2 rounded-md border px-2.5 py-1.5 text-left transition duration-150",
        selectedId === c.id
          ? "border-accent bg-overlay"
          : c.needsConnection
            ? "border-warning/50 hover:border-warning"
            : "border-border-subtle hover:border-border-strong hover:bg-overlay",
      )}
    >
      <span
        aria-hidden
        className="inline-block h-2 w-2 rounded-full"
        style={{ background: OPERATOR_KIND_ACCENT[c.kind] }}
      />
      <span className="flex min-w-0 flex-col leading-tight">
        <span className="text-sm font-medium text-text">{c.label}</span>
        {c.sub ? (
          <span
            className={cn(
              "text-[11px]",
              c.needsConnection ? "text-warning" : "text-text-muted",
            )}
          >
            {c.sub}
          </span>
        ) : null}
        {c.cond ? (
          <span
            className="max-w-[180px] truncate font-mono text-[10px] text-accent"
            title={c.cond}
          >
            {t("builder.routeIf", { cond: c.cond })}
          </span>
        ) : null}
      </span>
    </button>
  );

  return (
    <div className="flex shrink-0 items-center gap-2 overflow-x-auto border-b border-border-subtle bg-surface px-4 py-2.5">
      <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wider text-text-muted">
        {t("builder.flowTitle")}
      </span>
      <div className="flex items-center gap-1.5">
        {spineChips.map((c, i) => (
          <div key={c.id} className="flex items-center gap-1.5">
            {i > 0 ? (
              <ChevronRightIcon size={14} className="shrink-0 text-text-muted" />
            ) : null}
            {chipButton(c)}
          </div>
        ))}
        {sinkChips.length > 0 ? (
          <ChevronRightIcon size={14} className="shrink-0 text-text-muted" />
        ) : null}
        {sinkChips.length > 1 ? (
          <div className="flex flex-col gap-1 rounded-md border border-dashed border-border-subtle p-1">
            {sinkChips.map((c) => (
              <div key={c.id}>{chipButton(c)}</div>
            ))}
          </div>
        ) : (
          sinkChips.map((c) => <div key={c.id}>{chipButton(c)}</div>)
        )}
      </div>
      <span className="ml-auto hidden shrink-0 text-[11px] text-text-muted lg:block">
        {t("builder.flowHint")}
      </span>
    </div>
  );
}

function DryRunPanel({
  result,
  onDismiss,
  t,
}: {
  result: DryRunResponse;
  onDismiss: () => void;
  t: Translate;
}) {
  return (
    <div className="max-h-72 shrink-0 overflow-y-auto border-t border-border-subtle bg-surface px-4 py-3 text-sm">
      <header className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          {result.ok ? (
            <CheckCircle2Icon size={16} className="text-success" />
          ) : (
            <XCircleIcon size={16} className="text-error" />
          )}
          <span className="font-semibold text-text">
            {result.ok ? t("builder.dryRunPassedHeader") : t("builder.dryRunFailedHeader")}
          </span>
          <span className="text-xs text-text-muted">
            {t("builder.connectorsChecked", { count: result.connectors.length })}
          </span>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="text-xs text-text-muted hover:text-text"
        >
          {t("common.dismiss")}
        </button>
      </header>

      {result.errors.length > 0 ? (
        <div className="mb-3 rounded-md border border-error/40 bg-error/10 p-3">
          <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-error">
            {t("builder.configErrors")}
          </div>
          <ul className="space-y-1 text-xs text-text">
            {result.errors.map((err, i) => (
              <li key={i} className="font-mono">
                {err}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {result.connectors.length > 0 ? (
        <ul className="space-y-1.5">
          {result.connectors.map((c) => (
            <li
              key={c.name}
              className={cn(
                "flex items-start gap-2 rounded-sm border px-2 py-1.5",
                c.ok ? "border-success/30 bg-success/10" : "border-error/40 bg-error/10",
              )}
            >
              {c.ok ? (
                <CheckCircle2Icon size={14} className="mt-0.5 text-success" />
              ) : (
                <XCircleIcon size={14} className="mt-0.5 text-error" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-text">{c.name}</span>
                  <span className="rounded-sm bg-overlay px-1.5 py-0.5 font-mono text-[11px] text-text-secondary">
                    {c.type}
                  </span>
                </div>
                {c.error ? (
                  <pre className="mt-1 whitespace-pre-wrap break-words font-mono text-[11px] text-error">
                    {c.error}
                  </pre>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
