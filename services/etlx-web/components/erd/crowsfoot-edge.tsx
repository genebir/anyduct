"use client";

/**
 * Floating crow's-foot ERD edge (Phase AHO).
 *
 * Each end attaches to the point on the table's border that faces the other
 * table (computed from live node geometry), and the cardinality marker is
 * oriented to that side. Because connection points follow the nodes rather
 * than a fixed Left/Right handle, lines don't pile up on top of each other
 * after auto-layout or dragging. Self-references draw a small loop.
 *
 * All routing math (facing-side anchors, sibling distribution, straight
 * snapping, obstacle-aware bends) lives in lib/erd-edge-geometry so the
 * auto-layout can predict these lines exactly (Phase ALB).
 */

import { useRef, useState } from "react";
import { useLocale } from "@/components/providers/locale-provider";
import {
  BaseEdge,
  EdgeLabelRenderer,
  Position,
  getSmoothStepPath,
  useInternalNode,
  useStore,
  type EdgeProps,
  type InternalNode,
} from "@xyflow/react";
import {
  anchorAt,
  bestCenter,
  distributeAnchor,
  sideNormal,
  straightSnap,
  type GRect,
  type Side,
} from "@/lib/erd-edge-geometry";

const STROKE = "rgb(var(--accent))";
const FOOT = 16; // crow's-foot depth
const SPREAD = 7; // crow's-foot half-width
const BAR = 11; // "one" bar offset from the border

const POS: Record<Side, Position> = {
  left: Position.Left,
  right: Position.Right,
  top: Position.Top,
  bottom: Position.Bottom,
};

function rectOf(node: InternalNode): GRect {
  return {
    x: node.internals.positionAbsolute.x,
    y: node.internals.positionAbsolute.y,
    w: node.measured.width ?? 220,
    h: node.measured.height ?? 80,
  };
}

/** Manual endpoint anchor (Phase ALA): a side of the node + ratio along it. */
export interface AnchorSpec {
  side: Side;
  t: number;
}

/** Project a flow-space pointer position onto the node border → AnchorSpec. */
function specFor(node: InternalNode, p: { x: number; y: number }): AnchorSpec {
  const { x: nx, y: ny, w, h } = rectOf(node);
  const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
  const dl = Math.abs(p.x - nx);
  const dr = Math.abs(p.x - (nx + w));
  const dt = Math.abs(p.y - ny);
  const db = Math.abs(p.y - (ny + h));
  const m = Math.min(dl, dr, dt, db);
  if (m === dl) return { side: "left", t: clamp((p.y - ny) / h, 0.05, 0.95) };
  if (m === dr) return { side: "right", t: clamp((p.y - ny) / h, 0.05, 0.95) };
  if (m === dt) return { side: "top", t: clamp((p.x - nx) / w, 0.05, 0.95) };
  return { side: "bottom", t: clamp((p.x - nx) / w, 0.05, 0.95) };
}

/** Crow's foot (many): three prongs from an apex out along the normal back
 *  to the border, spread perpendicular to the normal. */
function foot(x: number, y: number, side: Side): string {
  const { nx, ny } = sideNormal(side);
  const ax = x + nx * FOOT;
  const ay = y + ny * FOOT;
  // perpendicular
  const px = -ny;
  const py = nx;
  return (
    `M ${ax},${ay} L ${x + px * SPREAD},${y + py * SPREAD} ` +
    `M ${ax},${ay} L ${x},${y} ` +
    `M ${ax},${ay} L ${x - px * SPREAD},${y - py * SPREAD}`
  );
}

/** "one" bar: a tick perpendicular to the side, set off from the border. */
function oneBar(x: number, y: number, side: Side): string {
  const { nx, ny } = sideNormal(side);
  const bx = x + nx * BAR;
  const by = y + ny * BAR;
  const px = -ny;
  const py = nx;
  return `M ${bx + px * SPREAD},${by + py * SPREAD} L ${bx - px * SPREAD},${by - py * SPREAD}`;
}

function mark(card: string, x: number, y: number, side: Side): string {
  return card === "many" ? foot(x, y, side) : oneBar(x, y, side);
}

export function CrowsFootEdge({ id, source, target, label, style, data, selected }: EdgeProps) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  const edges = useStore((s) => s.edges);
  const nodeLookup = useStore((s) => s.nodeLookup);
  const transform = useStore((s) => s.transform);
  const domNode = useStore((s) => s.domNode);
  const zoom = transform[2];
  // Manual bend dragging (Phase AKZ): live ratio while dragging, committed to
  // the design (relation.centerRatio) on pointer-up via data.onCenterRatio.
  const [dragRatio, setDragRatio] = useState<number | null>(null);
  const dragStart = useRef<{ ratio: number; px: number } | null>(null);
  // Manual endpoint anchors (Phase ALA): live spec while dragging an end handle.
  const [dragAnchor, setDragAnchor] = useState<{ end: "source" | "target"; spec: AnchorSpec } | null>(null);
  const { t } = useLocale();
  if (!sourceNode || !targetNode) return null;

  const sourceCard = (data?.sourceCard as string) ?? "many";
  const targetCard = (data?.targetCard as string) ?? "one";

  // Self-reference: draw a small loop off the right side.
  if (source === target) {
    const w = sourceNode.measured.width ?? 220;
    const h = sourceNode.measured.height ?? 80;
    const rx = sourceNode.internals.positionAbsolute.x + w;
    const ry = sourceNode.internals.positionAbsolute.y;
    const y1 = ry + h * 0.3;
    const y2 = ry + h * 0.6;
    const bulge = 46;
    const path = `M ${rx},${y1} C ${rx + bulge},${y1} ${rx + bulge},${y2} ${rx},${y2}`;
    return (
      <>
        <BaseEdge id={id} path={path} style={style} />
        <path d={mark(sourceCard, rx, y1, "right")} stroke={STROKE} strokeWidth={1.5} fill="none" />
        <path d={mark(targetCard, rx, y2, "right")} stroke={STROKE} strokeWidth={1.5} fill="none" />
        {label ? (
          <EdgeLabelRenderer>
            <div
              style={{
                position: "absolute",
                transform: `translate(0, -50%) translate(${rx + bulge}px, ${(y1 + y2) / 2}px)`,
                pointerEvents: "none",
              }}
              className="rounded bg-bg/80 px-1 font-mono text-[10px] text-text-muted"
            >
              {label}
            </div>
          </EdgeLabelRenderer>
        ) : null}
      </>
    );
  }

  const sRect = rectOf(sourceNode);
  const tRect = rectOf(targetNode);
  const getRect = (nid: string): GRect | undefined => {
    const n = nodeLookup.get(nid);
    return n ? rectOf(n) : undefined;
  };

  const manualS = dragAnchor?.end === "source" ? dragAnchor.spec : (data?.sourceAnchor as AnchorSpec | undefined);
  const manualT = dragAnchor?.end === "target" ? dragAnchor.spec : (data?.targetAnchor as AnchorSpec | undefined);
  const sa = manualS
    ? { ...anchorAt(sRect, manualS.side, manualS.t), side: manualS.side }
    : distributeAnchor(source, target, id, edges, getRect);
  const ta = manualT
    ? { ...anchorAt(tRect, manualT.side, manualT.t), side: manualT.side }
    : distributeAnchor(target, source, id, edges, getRect);
  // Move the bend off any table the default midpoint path would cut through.
  const obstacles: GRect[] = [];
  for (const [nid, n] of nodeLookup) {
    if (nid === source || nid === target) continue;
    if (n.type === "shape") continue; // background boxes may be crossed
    obstacles.push(rectOf(n));
  }
  const horizRoute =
    (sa.side === "left" || sa.side === "right") && (ta.side === "left" || ta.side === "right");
  const vertRoute =
    (sa.side === "top" || sa.side === "bottom") && (ta.side === "top" || ta.side === "bottom");
  // ── Bend minimisation (Phase AKZ): nearly-aligned anchors snap to a dead-
  // straight line — most of the "중구난방" feel comes from tiny S-bends.
  const anyManualAnchor = !!manualS || !!manualT;
  const snapped = anyManualAnchor
    ? { sp: { x: sa.x, y: sa.y }, tp: { x: ta.x, y: ta.y }, straight: false }
    : straightSnap({ x: sa.x, y: sa.y }, { x: ta.x, y: ta.y }, sa.side, ta.side, sRect, tRect);
  const sp = snapped.sp;
  const tp = snapped.tp;
  const straight = snapped.straight;
  const sPos = POS[sa.side];
  const tPos = POS[ta.side];
  // Bend position: manual ratio (dragged/persisted) wins; else obstacle-aware.
  const axisA = horizRoute ? sp.x : sp.y;
  const axisB = horizRoute ? tp.x : tp.y;
  const manualRatio = dragRatio ?? (data?.centerRatio as number | undefined);
  const autoCenter = straight
    ? undefined
    : horizRoute
      ? bestCenter(sp, tp, true, obstacles)
      : vertRoute
        ? bestCenter(sp, tp, false, obstacles)
        : undefined;
  const center =
    !straight && manualRatio !== undefined && Math.abs(axisB - axisA) > 24
      ? axisA + (axisB - axisA) * manualRatio
      : autoCenter;
  const [path, labelX, labelY] = straight
    ? [`M ${sp.x},${sp.y} L ${tp.x},${tp.y}`, (sp.x + tp.x) / 2, (sp.y + tp.y) / 2]
    : getSmoothStepPath({
        sourceX: sp.x,
        sourceY: sp.y,
        sourcePosition: sPos,
        targetX: tp.x,
        targetY: tp.y,
        targetPosition: tPos,
        ...(horizRoute && center !== undefined ? { centerX: center } : {}),
        ...(vertRoute && center !== undefined ? { centerY: center } : {}),
      });
  // Drag handle on the middle segment (selected, routable edges only).
  const routable = !straight && (horizRoute || vertRoute) && Math.abs(axisB - axisA) > 24;
  const onCenterRatio = data?.onCenterRatio as ((edgeId: string, ratio: number | undefined) => void) | undefined;
  const curRatio =
    manualRatio ?? (center !== undefined ? (center - axisA) / (axisB - axisA) : 0.5);
  const handleX = horizRoute ? (center ?? (sp.x + tp.x) / 2) : (sp.x + tp.x) / 2;
  const handleY = horizRoute ? (sp.y + tp.y) / 2 : (center ?? (sp.y + tp.y) / 2);

  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      {selected && (data?.onAnchor as unknown) ? (
        <EdgeLabelRenderer>
          {([
            ["source", sp, sourceNode] as const,
            ["target", tp, targetNode] as const,
          ]).map(([end, p, node]) => (
            <div
              key={end}
              className="nodrag nopan"
              style={{
                position: "absolute",
                transform: `translate(-50%, -50%) translate(${p.x}px, ${p.y}px)`,
                pointerEvents: "all",
                cursor: "move",
                width: 11,
                height: 11,
                borderRadius: 3,
                background: "rgb(var(--bg-elevated))",
                border: "2px solid rgb(var(--accent))",
                boxShadow: "0 1px 3px rgb(0 0 0 / 0.3)",
              }}
              title={t("erdDesign.edgeAnchorHint")}
              onPointerDown={(e) => {
                e.stopPropagation();
                (e.target as Element).setPointerCapture(e.pointerId);
                setDragAnchor({ end, spec: specFor(node, p) });
              }}
              onPointerMove={(e) => {
                if (!dragAnchor || dragAnchor.end !== end || !domNode) return;
                const r = domNode.getBoundingClientRect();
                const fp = {
                  x: (e.clientX - r.left - transform[0]) / zoom,
                  y: (e.clientY - r.top - transform[1]) / zoom,
                };
                setDragAnchor({ end, spec: specFor(node, fp) });
              }}
              onPointerUp={() => {
                if (dragAnchor && dragAnchor.end === end) {
                  (data!.onAnchor as (eid: string, which: string, spec: AnchorSpec | undefined) => void)(
                    id,
                    end,
                    dragAnchor.spec,
                  );
                }
                setDragAnchor(null);
              }}
              onDoubleClick={(e) => {
                e.stopPropagation();
                (data!.onAnchor as (eid: string, which: string, spec: AnchorSpec | undefined) => void)(
                  id,
                  end,
                  undefined,
                );
              }}
            />
          ))}
        </EdgeLabelRenderer>
      ) : null}
      {selected && routable && onCenterRatio ? (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan"
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${handleX}px, ${handleY}px)`,
              pointerEvents: "all",
              cursor: horizRoute ? "ew-resize" : "ns-resize",
              width: 14,
              height: 14,
              borderRadius: 7,
              background: "rgb(var(--accent))",
              border: "2px solid rgb(var(--bg-elevated))",
              boxShadow: "0 1px 3px rgb(0 0 0 / 0.3)",
            }}
            title={t("erdDesign.edgeBendHint")}
            onPointerDown={(e) => {
              e.stopPropagation();
              (e.target as Element).setPointerCapture(e.pointerId);
              dragStart.current = { ratio: curRatio, px: horizRoute ? e.clientX : e.clientY };
              setDragRatio(curRatio);
            }}
            onPointerMove={(e) => {
              if (!dragStart.current) return;
              const d = ((horizRoute ? e.clientX : e.clientY) - dragStart.current.px) / zoom;
              const next = dragStart.current.ratio + d / (axisB - axisA);
              setDragRatio(Math.min(0.95, Math.max(0.05, next)));
            }}
            onPointerUp={() => {
              if (dragStart.current && dragRatio !== null) onCenterRatio(id, dragRatio);
              dragStart.current = null;
              setDragRatio(null);
            }}
            onDoubleClick={(e) => {
              e.stopPropagation();
              onCenterRatio(id, undefined);
            }}
          />
        </EdgeLabelRenderer>
      ) : null}
      <path d={mark(sourceCard, sp.x, sp.y, sa.side)} stroke={STROKE} strokeWidth={1.5} fill="none" />
      <path d={mark(targetCard, tp.x, tp.y, ta.side)} stroke={STROKE} strokeWidth={1.5} fill="none" />
      {label ? (
        <EdgeLabelRenderer>
          <div
            style={{
              position: "absolute",
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: "none",
            }}
            className="rounded bg-bg/80 px-1 font-mono text-[10px] text-text-muted"
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

/** Stable edgeTypes map for ReactFlow. */
export const ERD_EDGE_TYPES = { crowsfoot: CrowsFootEdge };
