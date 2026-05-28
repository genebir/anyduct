"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ActivityIcon,
  HistoryIcon,
  KeyboardIcon,
  LayoutGridIcon,
  PlayIcon,
  RedoIcon,
  SaveIcon,
  UndoIcon,
  XCircleIcon,
  ZapIcon,
} from "lucide-react";
import { toast } from "sonner";
import { Header } from "@/components/shell/header";
import { BackfillDialog } from "@/components/pipelines/backfill-dialog";
import { Button } from "@/components/ui/button";
import dynamic from "next/dynamic";
import { PipelineSettingsPanel } from "@/components/builder/pipeline-settings-panel";

// Lazy-load the entire GraphEditor subtree so @xyflow/react (~200 KB) is
// requested *after* the editor's loading.tsx skeleton renders, not before
// the page is even shown. ssr: false because xyflow accesses ``window`` at
// import time and would crash the server render. The fallback is null so
// the surrounding flex layout is preserved while the chunk fetches; the
// real skeleton is the route-level loading.tsx (instant) — by the time
// that resolves and graphState is ready, this dynamic import is usually
// already cached.
const GraphEditor = dynamic(
  () => import("@/components/builder/graph-editor").then((m) => m.GraphEditor),
  { ssr: false, loading: () => null },
);
import {
  ApiError,
  connectionsApi,
  pipelinesApi,
  type ConnectionSummary,
  type DryRunResponse,
  type PipelineSummary,
} from "@/lib/api";
import { useWorkspaceFromSlug } from "@/lib/workspace-context";
import { autoLayoutGraph } from "@/lib/auto-layout";
import { useGraphHistory, useGraphHistoryShortcuts } from "@/lib/use-graph-history";
import { useLocale } from "@/components/providers/locale-provider";
import { ShortcutsDialog } from "@/components/builder/shortcuts-dialog";
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
  validateGraphStructured,
  DEFAULT_DLQ,
  DEFAULT_RETRY,
  type DlqSettings,
  type GraphBuilderState,
  type GraphIssue,
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
  // Graph state lives in a history-tracking hook (Phase L1, 2026-05-26):
  // every commit pushes onto an undo stack capped at 100 snapshots so
  // Cmd+Z / Cmd+Shift+Z work from anywhere in the builder. ``null``
  // while the pipeline is fetching — lets the page render a loading
  // skeleton instead of flashing the default source→sink graph (user
  // report — '기본 파이프라인 형태의 노드들이 보였다가 해당 파이프
  // 라인에 맞는 노드들로 변경되는 걸로 보이거든').
  const history = useGraphHistory();
  useGraphHistoryShortcuts(history);
  const graphState = history.state;
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  // Saving + auto-layout — defined further below; declare stable
  // wrappers so the Cmd+S / Cmd+L keyboard bindings don't have to wait
  // for function identity each render.
  const onSaveRef = useRef<() => void>(() => {});
  const onAutoLayoutRef = useRef<() => void>(() => {});
  // Global keyboard bindings: '?' opens the cheat-sheet, Cmd+S / Ctrl+S
  // saves, Cmd+L / Ctrl+L auto-layouts the graph. All bail when the
  // user is typing into an editable surface so we don't hijack
  // browser-native input behaviour.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target;
      const inEditable =
        target instanceof HTMLElement &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable ||
          target.getAttribute("role") === "textbox");
      if (e.key === "?" && !inEditable) {
        e.preventDefault();
        setShortcutsOpen(true);
        return;
      }
      // Cmd+S / Ctrl+S — let it fire even inside form fields (analysts
      // expect to save mid-type without clicking out first); browser's
      // default "save page" must always lose to our save.
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s" && !e.shiftKey) {
        e.preventDefault();
        onSaveRef.current();
      }
      // Cmd+L / Ctrl+L — auto-layout. Bail inside editable surfaces so
      // we don't fight browser-native "focus the address bar" on
      // Cmd+L when the user is typing into a JSON field; the toolbar
      // button stays as the no-keyboard fallback.
      if (
        (e.metaKey || e.ctrlKey) &&
        e.key.toLowerCase() === "l" &&
        !e.shiftKey &&
        !inEditable
      ) {
        e.preventDefault();
        onAutoLayoutRef.current();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const [retry, setRetry] = useState<RetrySettings>({ ...DEFAULT_RETRY });
  const [dlq, setDlq] = useState<DlqSettings>({ ...DEFAULT_DLQ });
  const [variables, setVariables] = useState<Record<string, unknown>>({});
  const [triggers, setTriggers] = useState<string[]>([]);
  const [autoMaterialize, setAutoMaterialize] = useState(false);
  const [freshnessSla, setFreshnessSla] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [dryRunning, setDryRunning] = useState(false);
  const [dryRunResult, setDryRunResult] = useState<DryRunResponse | null>(null);
  // Two-track dirty tracking (Phase L2, 2026-05-26 user request "Undo
  // 할 게 없을 때는 사실상 변경사항이 없는 것이기 때문에 저장되지 않은
  // 변경사항이라는 상태가 아니여야 해"):
  //   * ``savedGraphIndex`` — the history index whose snapshot matches
  //     what's persisted on the server. Set at load (=0) and at save
  //     (=history.index). The graph counts as dirty iff the current
  //     history.index differs.
  //   * ``metaDirty`` — non-graph state (retry / dlq / variables /
  //     triggers / autoMaterialize / freshness) doesn't live in the
  //     undo stack, so we track it separately. Toggled true by the
  //     meta updaters, cleared on save.
  // The overall ``dirty`` flag is the OR — so Cmd+Z back to the saved
  // baseline correctly drops the "unsaved" badge if no meta change is
  // pending.
  const [savedGraphIndex, setSavedGraphIndex] = useState<number | null>(null);
  const [metaDirty, setMetaDirty] = useState(false);
  // Phase Q (2026-05-28): backfill dialog state. Same dialog the
  // pipelines list page uses — re-mounting it here lets the user
  // backfill without going back to the list. Disabled below when the
  // pipeline has no saved version or has unsaved changes (the server
  // would 400 with a stale config either way).
  const [backfillOpen, setBackfillOpen] = useState(false);

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
        // ``setInitial`` resets the undo stack so the load itself is
        // never an undo target — the first thing the user can undo is
        // their first edit, not the network fetch.
        if (isGraphConfig(cfg)) {
          history.setInitial(deserializeGraph(cfg, conns));
        } else if (cfg) {
          history.setInitial(linearToGraph(deserialize(cfg, conns)));
        } else {
          history.setInitial(blankGraph());
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
        // The just-loaded snapshot becomes our "saved baseline" — any
        // edit pushes the history index past 0 and turns the graph
        // dirty; an undo back to 0 returns to clean.
        setSavedGraphIndex(0);
        setMetaDirty(false);
      } catch (err) {
        toast.error(err instanceof ApiError ? err.message : t("builder.loadFailed"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [ws, id, t]);

  const updateGraph = useCallback(
    (next: GraphBuilderState) => {
      // history.commit handles dirty-tracking implicitly via index.
      // No metaDirty flip needed — graph changes are tracked entirely
      // through the history index vs savedGraphIndex comparison below.
      history.commit(next);
    },
    [history],
  );

  const updateRetry = useCallback((next: RetrySettings) => {
    setRetry(next);
    setMetaDirty(true);
  }, []);
  const updateDlq = useCallback((next: DlqSettings) => {
    setDlq(next);
    setMetaDirty(true);
  }, []);
  const updateVariables = useCallback((next: Record<string, unknown>) => {
    setVariables(next);
    setMetaDirty(true);
  }, []);
  const updateTriggers = useCallback((next: string[]) => {
    setTriggers(next);
    setMetaDirty(true);
  }, []);

  // Effective dirty flag: meta-state changes OR graph differs from
  // last-saved index. Memoised so the header badge / toast hint update
  // synchronously with undo/redo.
  const dirty = useMemo(() => {
    if (metaDirty) return true;
    if (savedGraphIndex === null) return false; // still loading
    return savedGraphIndex !== history.index;
  }, [metaDirty, savedGraphIndex, history.index]);

  // Pipeline data mode (batch | stream) drives the palette's connector
  // filtering. The mode itself is fixed at creation time and not editable
  // here — it lives in the saved config.
  const dataMode: "batch" | "stream" =
    (pipeline?.current_config_json as { mode?: string } | null)?.mode === "stream"
      ? "stream"
      : "batch";

  // Structural problems that would make the save fail server validation,
  // surfaced inline as the user edits (live, not just at save time —
  // Phase L1 audit finding: analysts complained that the previous "first
  // issue only at save" UX let problems pile up invisibly).
  const graphIssues = useMemo<GraphIssue[]>(
    () => (graphState ? validateGraphStructured(graphState) : []),
    [graphState],
  );
  // Banner row click → focus the offending node inside the editor. The
  // ``nonce`` lets the user click the SAME row twice in a row and still
  // re-trigger focus (the editor's effect dep is the whole object).
  const [focusRequest, setFocusRequest] = useState<{ nodeId: string; nonce: number } | null>(
    null,
  );
  const focusNode = useCallback((nodeId: string) => {
    setFocusRequest((cur) => ({ nodeId, nonce: (cur?.nonce ?? 0) + 1 }));
  }, []);

  // Deleted-connection detection (Phase L1 audit fix 2026-05-26): a
  // source/sink/transform may still reference a connection name that
  // got deleted in /connections after the pipeline was last saved.
  // We catch that on load + every edit so the analyst sees a banner
  // *before* clicking save and getting a generic 'Connection X not
  // found' from the server.
  const deletedConnectionNodes = useMemo(() => {
    if (!graphState) return [] as { id: string; name: string }[];
    const known = new Set(connections.map((c) => c.name));
    const missing: { id: string; name: string }[] = [];
    for (const node of graphState.nodes) {
      const conn = node.data.connection;
      if (typeof conn === "string" && conn && !known.has(conn)) {
        missing.push({ id: node.id, name: conn });
      }
    }
    return missing;
  }, [graphState, connections]);

  const onSave = useCallback(async () => {
    if (!ws || !pipeline || !graphState) {
      return;
    }
    if (graphIssues.length > 0) {
      // Banner is already showing every issue; surface a summary in the
      // toast so the user notices the failure but isn't told the same
      // thing twice in two different places.
      toast.error(
        graphIssues.length === 1
          ? graphIssues[0].message
          : `${graphIssues.length} issues to fix — see banner above the canvas`,
      );
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
      // Stash the current history index as the saved baseline. The next
      // commit pushes index past this value → dirty flips back on; an
      // undo back to this exact index returns to clean.
      setSavedGraphIndex(history.index);
      setMetaDirty(false);
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

  // Keep the Cmd+S binding pointing at the latest onSave without
  // re-attaching the global listener on every render.
  useEffect(() => {
    onSaveRef.current = onSave;
  }, [onSave]);

  // Phase O (2026-05-28): auto-layout reflows node positions into a
  // tidy LR hierarchy via dagre. One history commit so Cmd+Z restores
  // the prior layout exactly (autoLayoutGraph preserves data/edges,
  // only positions change, so the undo target is honest). No-op on
  // empty / loading graphs.
  const onAutoLayout = useCallback(() => {
    if (!graphState || graphState.nodes.length === 0) return;
    history.commit(autoLayoutGraph(graphState));
  }, [graphState, history]);
  useEffect(() => {
    onAutoLayoutRef.current = onAutoLayout;
  }, [onAutoLayout]);

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
            {/* Undo / Redo / Shortcuts — leftmost so they form a tight
                'editor controls' cluster, separate from the action
                buttons (Save / Trigger) on the right. ``title`` doubles
                as a tooltip + screen-reader label; the keyboard shortcut
                lives in :class:`ShortcutsDialog`. */}
            <button
              type="button"
              onClick={history.undo}
              disabled={!history.canUndo}
              aria-label={t("shortcuts.undo")}
              title={`${t("shortcuts.undo")} (Cmd+Z)`}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-text-secondary transition duration-150 hover:bg-overlay hover:text-text disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
            >
              <UndoIcon size={15} />
            </button>
            <button
              type="button"
              onClick={history.redo}
              disabled={!history.canRedo}
              aria-label={t("shortcuts.redo")}
              title={`${t("shortcuts.redo")} (Cmd+Shift+Z)`}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-text-secondary transition duration-150 hover:bg-overlay hover:text-text disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
            >
              <RedoIcon size={15} />
            </button>
            <button
              type="button"
              onClick={onAutoLayout}
              disabled={!graphState || graphState.nodes.length === 0}
              aria-label={t("shortcuts.autoLayout")}
              title={`${t("shortcuts.autoLayout")} (Cmd+L)`}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-text-secondary transition duration-150 hover:bg-overlay hover:text-text disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
            >
              <LayoutGridIcon size={15} />
            </button>
            <button
              type="button"
              onClick={() => setShortcutsOpen(true)}
              aria-label={t("shortcuts.title")}
              title={`${t("shortcuts.title")} (?)`}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-text-secondary transition duration-150 hover:bg-overlay hover:text-text"
            >
              <KeyboardIcon size={15} />
            </button>
            <div className="mx-1 h-5 w-px bg-border-subtle" aria-hidden />
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
                  setMetaDirty(true);
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
                  setMetaDirty(true);
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
            {/* Backfill (ADR-0039) — same dialog the pipelines list
                page uses. Mounting here means the user editing a
                pipeline doesn't have to navigate back to the list to
                rerun a cursor range. Same disabled rules as Trigger:
                pipeline must have a saved version (server needs the
                current config to run, draft edits would be ignored).
                Unsaved changes don't block backfill — the dialog
                operates on the *saved* version (Save first if you
                want today's edits to apply). Phase Q (2026-05-28). */}
            <Button
              variant="ghost"
              onClick={() => setBackfillOpen(true)}
              disabled={!pipeline?.current_version}
              title={
                !pipeline?.current_version
                  ? t("builder.saveFirst")
                  : t("backfill.action")
              }
            >
              <HistoryIcon size={16} />
              {t("backfill.action")}
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
      {deletedConnectionNodes.length > 0 ? (
        <div
          className="flex shrink-0 items-center gap-2 border-b border-error/40 bg-error/10 px-4 py-2 text-sm text-error"
          role="alert"
        >
          <XCircleIcon size={16} className="shrink-0" />
          <span>
            {t("graph.deletedConnectionBanner", {
              count: deletedConnectionNodes.length,
            })}
          </span>
          <button
            type="button"
            onClick={() => focusNode(deletedConnectionNodes[0].id)}
            className="ml-auto text-xs underline-offset-2 hover:underline"
          >
            {t("graph.menuEdit")} →
          </button>
        </div>
      ) : null}
      {graphIssues.length > 0 ? (
        <ValidationBanner issues={graphIssues} onFocus={focusNode} t={t} />
      ) : null}
      {/* Until the pipeline is fetched + materialised into graphState,
          render a quiet placeholder. The previous code initialised
          graphState to ``blankGraph()`` and flashed the default
          source→sink canvas before snapping to the loaded graph
          (user report: "기본 파이프라인 형태의 노드들이 보였다가
          해당 파이프라인에 맞는 노드들로 변경되는 걸로 보이거든").
          The placeholder fills the same flex slot so the layout
          doesn't jump when the editor swaps in. */}
      <ShortcutsDialog open={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
      {/* Backfill dialog — re-uses the pipelines-list dialog; mounts
          only when ``backfillOpen`` so the form resets each time the
          user opens it (dialog's own ``useEffect`` clears from/to on
          ``open``). Pipeline is the LOADED, saved row — drafts don't
          flow through here. Phase Q (2026-05-28). */}
      <BackfillDialog
        open={backfillOpen}
        workspaceId={ws?.id ?? ""}
        pipeline={pipeline}
        onClose={() => setBackfillOpen(false)}
      />
      {graphState ? (
        <GraphEditor
          state={graphState}
          connections={connections}
          mode={dataMode}
          onChange={updateGraph}
          workspaceId={ws?.id}
          focusRequest={focusRequest}
          settingsPanel={settingsPanel}
          dryRunPanel={
            dryRunResult ? (
              <DryRunPanel result={dryRunResult} onDismiss={() => setDryRunResult(null)} t={t} />
            ) : null
          }
        />
      ) : (
        <div
          role="status"
          aria-busy
          className="flex min-h-0 flex-1 items-center justify-center text-sm text-text-muted"
        >
          {t("common.loading")}
        </div>
      )}
    </>
  );
}

// --- Validation banner ------------------------------------------------------
//
// Replaces the old single-line "first issue" banner with a collapsible
// rich list. Two design goals from the L1 audit:
//   * Analysts see every blocker in one place (so they don't fix one,
//     re-save, and discover the next).
//   * Engineers can jump to the offending node in one click, no hunting.
//
// Collapsed by default once the list passes 3 items so the banner
// stays out of the canvas's way; the header summary always tells the
// user how many blockers remain.

function ValidationBanner({
  issues,
  onFocus,
  t,
}: {
  issues: GraphIssue[];
  onFocus: (nodeId: string) => void;
  t: Translate;
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded || issues.length <= 3 ? issues : issues.slice(0, 3);
  const hidden = issues.length - visible.length;
  const summary =
    issues.length === 1
      ? t("graph.invalidSingle")
      : t("graph.invalidCount", { n: issues.length });
  return (
    <div
      className="shrink-0 border-b border-warning/40 bg-warning/10 px-4 py-2 text-sm text-warning"
      role="alert"
      aria-live="polite"
    >
      <div className="flex items-center gap-2">
        <XCircleIcon size={16} className="shrink-0" />
        <span className="font-medium">{summary}</span>
        {issues.length > 3 ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="ml-auto text-xs underline-offset-2 hover:underline"
          >
            {expanded
              ? t("common.showLess") ?? "Show less"
              : `+${hidden} more`}
          </button>
        ) : null}
      </div>
      <ul className="ml-6 mt-1 space-y-0.5 text-xs">
        {visible.map((issue, i) => (
          <li key={i} className="flex items-baseline gap-1">
            <span aria-hidden>•</span>
            {issue.nodeId ? (
              <button
                type="button"
                onClick={() => onFocus(issue.nodeId!)}
                className="text-left text-warning underline-offset-2 hover:underline focus-visible:underline focus-visible:outline-none"
                title={t("graph.menuEdit")}
              >
                {issue.message}
              </button>
            ) : (
              <span>{issue.message}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

// --- Dry run panel (graph-only mode reuses the same component) -------------

// Phase N (2026-05-28): Dry-run feedback used to be a wall of bullets
// indistinguishable from a console dump. The audit specifically called
// this out as "Dry run 결과 보기 어려움". Three changes here:
//   * Connector list always rendered with explicit ✓/✗ per row so the
//     operator sees what passed alongside what failed (not just "here
//     are the broken ones").
//   * Per-connector errors land in a copy-able monospace code block —
//     when a connector returns a SQL error the operator now copies the
//     full text without selecting through highlighted bullets.
//   * The whole panel respects the same theme tokens as the rest of
//     the builder (border-error / bg-error/5 banding for failed rows).
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
  const failedConnectors = result.connectors.filter((c) => !c.ok);
  return (
    <div
      className="shrink-0 max-h-[40vh] overflow-y-auto border-t border-border-subtle bg-elevated px-4 py-3 text-sm"
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
            <span className="inline-flex items-center gap-1 font-semibold text-error">
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
      {/* Top-level errors (graph build / config / etc.) — these are
          NOT connector errors, they're emitted before connectors are
          even touched. Render as their own copyable block so the
          operator can paste the message into a ticket. */}
      {result.errors.length > 0 ? (
        <ul className="mt-2 space-y-1">
          {result.errors.map((e, i) => (
            <li
              key={i}
              className="rounded-md border border-error/40 bg-error/5 p-2 font-mono text-xs text-error"
            >
              <CopyButton text={e} t={t} />
              <div className="whitespace-pre-wrap break-words pr-7">{e}</div>
            </li>
          ))}
        </ul>
      ) : null}
      {/* Connector outcomes — show ALL of them (not just failures), so
          the user has the full picture. Failed rows get the error
          banded panel + copy button; healthy rows are a one-line ✓. */}
      {result.connectors.length > 0 ? (
        <ul className="mt-2 space-y-1">
          {result.connectors.map((c, i) => {
            if (c.ok) {
              return (
                <li
                  key={i}
                  className="flex items-center gap-2 px-2 py-1 text-xs text-text-secondary"
                >
                  <span className="text-success">✓</span>
                  <code className="font-mono text-text">{c.name}</code>
                  <span className="text-text-muted">— {t("builder.dryRunOk")}</span>
                </li>
              );
            }
            const msg = c.error ?? t("builder.dryRunFailedHeader");
            return (
              <li
                key={i}
                className="relative rounded-md border border-error/40 bg-error/5 p-2 text-xs"
              >
                <CopyButton text={`${c.name}: ${msg}`} t={t} />
                <div className="flex items-baseline gap-2 pr-7">
                  <span className="text-error">✗</span>
                  <code className="font-mono font-semibold text-error">{c.name}</code>
                </div>
                <pre className="mt-1 ml-5 whitespace-pre-wrap break-words pr-7 font-mono text-text-secondary">
                  {msg}
                </pre>
              </li>
            );
          })}
        </ul>
      ) : null}
      {/* Summary footer when there are failures — same role as the
          validation banner's "N issues" header. Helps the user
          calibrate effort without counting bullets. */}
      {failedConnectors.length > 0 ? (
        <div className="mt-2 text-[11px] text-text-muted">
          {t("builder.dryRunFailedCount", {
            failed: failedConnectors.length,
            total: result.connectors.length,
          })}
        </div>
      ) : null}
    </div>
  );
}

/** Tiny absolute-positioned copy-to-clipboard button used inside the
 *  dry-run error blocks. Falls back silently when the Clipboard API
 *  isn't available (older Safari / file://) — copy is a convenience,
 *  not a critical path. */
function CopyButton({ text, t }: { text: string; t: Translate }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard unavailable — silently no-op */
        }
      }}
      title={copied ? t("common.copied") : t("common.copy")}
      aria-label={t("common.copy")}
      className="absolute right-1.5 top-1.5 rounded-sm bg-elevated px-1.5 py-0.5 text-[10px] text-text-muted hover:bg-overlay hover:text-text"
    >
      {copied ? "✓" : "⧉"}
    </button>
  );
}
