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
  deserialize,
  makeNode,
  reorderNodes,
  serialize,
  type BuilderNode,
  type BuilderState,
  type DlqSettings,
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
  const [state, setState] = useState<BuilderState>(() => blankBuilder());
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
        const [p, conns] = await Promise.all([
          pipelinesApi.get(ws.id, id),
          connectionsApi.list(ws.id),
        ]);
        if (cancelled) return;
        setPipeline(p);
        setConnections(conns);
        const initial = deserialize(
          p.current_config_json as PipelineConfigJson | null,
          conns,
        );
        setState(initial);
        setSelectedId(initial.nodes[0]?.id ?? null);
        setDirty(false);
        loadedRef.current = true;
      } catch (err) {
        toast.error(
          err instanceof ApiError ? err.message : t("builder.loadFailed"),
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws, id, t]);

  const addOperator = useCallback((operatorId: string) => {
    setState((prev) => {
      const next: BuilderNode = makeNode(operatorId);
      // If user adds a duplicate source/sink, replace the existing one rather
      // than allowing two — core PipelineConfig has exactly one of each.
      const filtered = prev.nodes.filter((n) => {
        if (next.operatorId.startsWith("source:") && n.operatorId.startsWith("source:")) {
          return false;
        }
        if (next.operatorId.startsWith("sink:") && n.operatorId.startsWith("sink:")) {
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

  // Apply a drag-reorder from the canvas: rebuild the node list as
  // sources → transforms(in dragged order) → sinks.
  const reorderTransforms = useCallback((orderedIds: string[]) => {
    setState((prev) => {
      const byId = new Map(prev.nodes.map((n) => [n.id, n]));
      const newTransforms = orderedIds
        .map((id) => byId.get(id))
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
      // Bail if the drag order didn't cover exactly the transform set.
      if (newTransforms.length !== existingTransforms.length) return prev;
      return { ...prev, nodes: [...sources, ...newTransforms, ...sinks] };
    });
    setDirty(true);
  }, []);

  const selectNode = useCallback((nodeId: string) => {
    setSelectedId(nodeId);
  }, []);

  // Reorder transforms — execution order matters and the canvas only
  // auto-sorts by kind, so swap the selected transform with its neighbor
  // within the transform run.
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
        nodes: prev.nodes.map((n) =>
          n.id === nodeId ? { ...n, data: values } : n,
        ),
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

  // Position of the selected transform within the transform run (in canvas
  // order), so the panel can enable/disable the move-left/right controls.
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

  const onSave = useCallback(async () => {
    if (!ws || !pipeline) return;
    setSaving(true);
    try {
      const existingMode =
        (pipeline.current_config_json as { mode?: string } | null)?.mode;
      const config = serialize(state, {
        name: pipeline.name,
        mode: existingMode === "stream" ? "stream" : "batch",
      });
      const updated = await pipelinesApi.update(ws.id, pipeline.id, {
        config,
      });
      setPipeline(updated);
      setDirty(false);
      toast.success(
        t("builder.saved", { version: updated.current_version ?? "?" }),
      );
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("builder.saveFailed"),
      );
    } finally {
      setSaving(false);
    }
  }, [ws, pipeline, state, t]);

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
      if (result.ok) {
        toast.success(t("builder.dryRunPassed"));
      } else {
        toast.error(t("builder.dryRunIssues"));
      }
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : t("builder.dryRunFailedToast"),
      );
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
      toast.error(
        err instanceof ApiError ? err.message : t("pipelines.triggerFailed"),
      );
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
      <FlowSummary
        nodes={state.nodes}
        selectedId={selectedId}
        onSelect={selectNode}
        t={t}
      />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <Palette onAdd={addOperator} />
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
            <DryRunPanel
              result={dryRunResult}
              onDismiss={() => setDryRunResult(null)}
              t={t}
            />
          ) : null}
        </div>
        {selectedNode ? (
          <PropertiesPanel
            node={selectedNode}
            connections={connections}
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
  );
}

/**
 * Plain-language read of the pipeline: source → transforms → sink as
 * clickable chips. Gives non-developers an at-a-glance "what does this do"
 * without decoding the canvas, and surfaces missing connections inline.
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
  const chips = ordered.map((n) => {
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
    return { id: n.id, label: op?.label ?? n.operatorId, kind, needsConnection, sub };
  });

  return (
    <div className="flex shrink-0 items-center gap-2 overflow-x-auto border-b border-border-subtle bg-surface px-4 py-2.5">
      <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wider text-text-muted">
        {t("builder.flowTitle")}
      </span>
      <div className="flex items-center gap-1.5">
        {chips.map((c, i) => (
          <div key={c.id} className="flex items-center gap-1.5">
            {i > 0 ? (
              <ChevronRightIcon size={14} className="shrink-0 text-text-muted" />
            ) : null}
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
              <span className="flex flex-col leading-tight">
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
              </span>
            </button>
          </div>
        ))}
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
            {result.ok
              ? t("builder.dryRunPassedHeader")
              : t("builder.dryRunFailedHeader")}
          </span>
          <span className="text-xs text-text-muted">
            {t("builder.connectorsChecked", {
              count: result.connectors.length,
            })}
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
                c.ok
                  ? "border-success/30 bg-success/10"
                  : "border-error/40 bg-error/10",
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
