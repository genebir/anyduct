"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { PlayIcon, SaveIcon } from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { Button } from "@/components/ui/button";
import { Palette } from "@/components/builder/palette";
import { PropertiesPanel } from "@/components/builder/properties-panel";
import { BuilderCanvas } from "@/components/builder/builder-canvas";
import {
  ApiError,
  connectionsApi,
  pipelinesApi,
  type ConnectionSummary,
  type PipelineSummary,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import {
  blankBuilder,
  deserialize,
  makeNode,
  reorderNodes,
  serialize,
  type BuilderNode,
  type BuilderState,
  type PipelineConfigJson,
} from "@/lib/pipeline-config";

export default function PipelineEditorPage() {
  const { slug, id } = useParams<{ slug: string; id: string }>();
  const ws = useWorkspaceFromSlug(slug);

  const [pipeline, setPipeline] = useState<PipelineSummary | null>(null);
  const [connections, setConnections] = useState<ConnectionSummary[]>([]);
  const [state, setState] = useState<BuilderState>(() => blankBuilder());
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
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
        const initial = deserialize(p.current_config_json as PipelineConfigJson | null);
        setState(initial);
        setSelectedId(initial.nodes[0]?.id ?? null);
        loadedRef.current = true;
      } catch (err) {
        toast.error(
          err instanceof ApiError ? err.message : "Couldn't load pipeline.",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws, id]);

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
      return { nodes: ordered };
    });
    setSelectedId(null);
  }, []);

  const removeOperator = useCallback((nodeId: string) => {
    setState((prev) => ({
      nodes: prev.nodes.filter((n) => n.id !== nodeId),
    }));
    setSelectedId((cur) => (cur === nodeId ? null : cur));
  }, []);

  const selectNode = useCallback((nodeId: string) => {
    setSelectedId(nodeId);
  }, []);

  const updateNode = useCallback(
    (nodeId: string, values: Record<string, unknown>) => {
      setState((prev) => ({
        nodes: prev.nodes.map((n) =>
          n.id === nodeId ? { ...n, data: values } : n,
        ),
      }));
    },
    [],
  );

  const selectedNode = useMemo(
    () => state.nodes.find((n) => n.id === selectedId) ?? null,
    [state.nodes, selectedId],
  );

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
      toast.success(`Saved pipeline v${updated.current_version ?? "?"}`);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Save failed.",
      );
    } finally {
      setSaving(false);
    }
  }, [ws, pipeline, state]);

  const onTrigger = useCallback(async () => {
    if (!ws || !pipeline) return;
    try {
      await pipelinesApi.trigger(ws.id, pipeline.id);
      toast.success(`Run queued for ${pipeline.name}`);
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Trigger failed.",
      );
    }
  }, [ws, pipeline]);

  return (
    <>
      <Header
        title={pipeline?.name ?? "Pipeline builder"}
        subtitle={
          pipeline
            ? `${ws?.name ?? ""} · v${pipeline.current_version ?? "draft"}`
            : "Loading…"
        }
        actions={
          <div className="flex gap-2">
            <Button
              variant="ghost"
              onClick={onTrigger}
              disabled={!pipeline?.current_version}
            >
              <PlayIcon size={16} />
              Trigger
            </Button>
            <Button onClick={onSave} loading={saving} disabled={!pipeline}>
              <SaveIcon size={16} />
              Save
            </Button>
          </div>
        }
      />
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <Palette onAdd={addOperator} />
        <div className="min-w-0 flex-1">
          <BuilderCanvas
            nodes={state.nodes}
            selectedId={selectedId}
            onSelect={selectNode}
            onRemove={removeOperator}
          />
        </div>
        <PropertiesPanel
          node={selectedNode}
          connections={connections}
          onChange={updateNode}
        />
      </div>
    </>
  );
}
