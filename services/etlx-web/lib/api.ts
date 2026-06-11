/**
 * Typed REST client for etlx-server.
 *
 * DTOs mirror `etlx_server.auth.schemas` — field names match the FastAPI
 * `response_model` exactly so the FE never has to translate between snake
 * and camel case.
 *
 * Behavior:
 * - Reads JWT from `localStorage["etlx.token"]` per call (no in-memory cache)
 *   so signing in/out from any tab doesn't desync.
 * - On 401 clears tokens and dispatches a `etlx:unauthorized` window event;
 *   the auth provider listens for that and routes to `/login`.
 */

import type { ErdDesign } from "@/lib/erd-design";

const DEFAULT_BASE = "http://localhost:8000";
const TOKEN_KEY = "etlx.token";
const REFRESH_KEY = "etlx.refresh";

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export function apiBaseUrl(): string {
  if (typeof process !== "undefined") {
    const fromEnv = process.env.NEXT_PUBLIC_ETLX_API_URL;
    if (fromEnv) return fromEnv.replace(/\/$/, "");
  }
  return DEFAULT_BASE;
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setTokens(access: string, refresh: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_KEY, access);
  window.localStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_KEY);
}

type ApiInit = Omit<RequestInit, "body"> & {
  json?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
};

export async function api<T = unknown>(
  path: string,
  init: ApiInit = {},
): Promise<T> {
  const url = new URL(path.startsWith("http") ? path : apiBaseUrl() + path);
  if (init.query) {
    for (const [k, v] of Object.entries(init.query)) {
      if (v === undefined || v === null) continue;
      url.searchParams.set(k, String(v));
    }
  }

  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.json !== undefined) headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(url, {
    ...init,
    headers,
    body: init.json !== undefined ? JSON.stringify(init.json) : undefined,
    cache: "no-store",
  });

  if (res.status === 401 && token) {
    clearTokens();
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("etlx:unauthorized"));
    }
  }

  if (res.status === 204) return undefined as T;

  const contentType = res.headers.get("content-type") ?? "";
  const body: unknown = contentType.includes("application/json")
    ? await res.json()
    : await res.text();

  if (!res.ok) {
    const detail =
      (body as { detail?: string })?.detail ??
      (typeof body === "string" ? body : res.statusText);
    throw new ApiError(res.status, detail, body);
  }

  return body as T;
}

/* ─────────────────────────────────────────────────────────────────────────
   Typed DTOs (subset). Field names mirror etlx_server.auth.schemas.
   ─────────────────────────────────────────────────────────────────────── */

export type Role = "owner" | "editor" | "runner" | "viewer";
export type PipelineMode = "batch" | "stream";
export type RunStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface CurrentUser {
  id: string;
  email: string;
  name: string;
  is_superadmin: boolean;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  slug: string;
  color_hex: string;
  role: Role | null;
}

export interface MembershipSummary {
  id: string;
  user_id: string;
  email: string;
  name: string;
  role: Role;
}

export interface ConnectionSummary {
  id: string;
  workspace_id: string;
  name: string;
  type: string;
  config_json: Record<string, unknown>;
  secret_refs: string[];
}

export interface WorkspaceVariableEntry {
  name: string;
  value: unknown;
  description: string | null;
}

/** Phase ABR (2026-06-01) — one entry of the versions list. The
 *  ``config_json`` mirrors what the worker executed, regardless of
 *  later edits on the pipeline. Used by the run-detail "Config that
 *  ran" panel. */
export interface PipelineVersionEntry {
  id: string;
  version: number;
  is_current: boolean;
  config_json: Record<string, unknown>;
  created_at: string;
}

export interface PipelineSummary {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  current_version: number | null;
  current_config_json: Record<string, unknown> | null;
}

export interface ScheduleSummary {
  id: string;
  pipeline_id: string;
  name: string;
  mode: PipelineMode;
  cron_expr: string | null;
  is_active: boolean;
  config_overrides: Record<string, unknown>;
}

export interface RunSummary {
  id: string;
  workspace_id: string;
  pipeline_id: string;
  pipeline_version_id: string;
  schedule_id: string | null;
  triggered_by_user_id: string | null;
  status: RunStatus;
  scheduled_at: string;
  started_at: string | null;
  finished_at: string | null;
  records_read: number;
  records_written: number;
  duration_seconds: number | null;
  error_class: string | null;
  created_at: string;
  /** ADR-0095 — partitioned-backfill sub-run marker ({group, index, of}).
   *  Lets the runs list badge sibling windows of one split. */
  partition: { group?: string; index?: number; of?: number } | null;
}

export interface RunDetail extends RunSummary {
  heartbeat_at: string | null;
  worker_id: string | null;
  error_message: string | null;
  result_json: Record<string, unknown>;
  /** Stamped by POST /runs/{rid}/cancel (Phase P, 2026-05-28). When
   *  set on a still-running row, the worker will land the cancel at
   *  the next node boundary; the UI uses this to show a "Cancelling…"
   *  chip in the gap between request and final CANCELLED status. */
  cancel_requested_at: string | null;
}

export type LogLevel = "debug" | "info" | "warning" | "error";

export interface RunLogEntry {
  id: string;
  ts: string;
  level: LogLevel;
  /** Graph node that emitted this log line, or ``null`` for run-level
   *  logs (build / connector setup / summary). Populated server-side
   *  by the worker's structlog ContextVar bind (Phase M, 2026-05-26). */
  node_id: string | null;
  message: string;
  context_json: Record<string, unknown>;
}

export interface RunMetricEntry {
  id: string;
  name: string;
  value: number;
  attrs_json: Record<string, unknown>;
  recorded_at: string;
}

/* ─────────────────────────────────────────────────────────────────────────
   Auth helpers
   ─────────────────────────────────────────────────────────────────────── */

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
}

export async function login(
  email: string,
  password: string,
): Promise<TokenPair> {
  const data = await api<TokenPair>("/auth/login", {
    method: "POST",
    json: { email, password },
  });
  setTokens(data.access_token, data.refresh_token);
  return data;
}

export async function me(): Promise<CurrentUser> {
  return api<CurrentUser>("/auth/me");
}

export async function logout(): Promise<void> {
  try {
    await api("/auth/logout", { method: "POST" });
  } catch {
    /* even if server-side logout fails, clear local state */
  } finally {
    clearTokens();
  }
}

/* ─────────────────────────────────────────────────────────────────────────
   Domain endpoints
   ─────────────────────────────────────────────────────────────────────── */

export const workspacesApi = {
  list: () => api<WorkspaceSummary[]>("/workspaces"),
  get: (id: string) => api<WorkspaceSummary>(`/workspaces/${id}`),
  create: (body: { name: string; slug: string; color_hex?: string }) =>
    api<WorkspaceSummary>("/workspaces", { method: "POST", json: body }),
  update: (
    id: string,
    body: { name?: string; slug?: string; color_hex?: string },
  ) =>
    api<WorkspaceSummary>(`/workspaces/${id}`, {
      method: "PATCH",
      json: body,
    }),
  delete: (id: string) =>
    api<void>(`/workspaces/${id}`, { method: "DELETE" }),
};

export interface AuditLogEntry {
  id: string;
  actor_user_id: string | null;
  workspace_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  before_json: Record<string, unknown> | null;
  after_json: Record<string, unknown> | null;
  ip: string | null;
  user_agent: string | null;
  created_at: string;
}

export const auditApi = {
  query: (
    workspaceId: string,
    query: {
      actor_user_id?: string;
      resource_type?: string;
      resource_id?: string;
      /** Filter by exact action name (e.g. ``"run.sql_executed"``).
       *  Phase U (2026-05-28) — useful for showing only the data-
       *  plane events the worker records, hiding control-plane noise. */
      action?: string;
      limit?: number;
      offset?: number;
    } = {},
  ) =>
    api<AuditLogEntry[]>("/audit", {
      query: { workspace_id: workspaceId, ...query },
    }),
};

export const membershipsApi = {
  list: (workspaceId: string) =>
    api<MembershipSummary[]>(`/workspaces/${workspaceId}/memberships`),
  add: (workspaceId: string, body: { email: string; role: Role }) =>
    api<MembershipSummary>(`/workspaces/${workspaceId}/memberships`, {
      method: "POST",
      json: body,
    }),
  updateRole: (workspaceId: string, userId: string, role: Role) =>
    api<MembershipSummary>(
      `/workspaces/${workspaceId}/memberships/${userId}`,
      { method: "PATCH", json: { role } },
    ),
  remove: (workspaceId: string, userId: string) =>
    api<void>(`/workspaces/${workspaceId}/memberships/${userId}`, {
      method: "DELETE",
    }),
};

export interface ConnectionCreateBody {
  name: string;
  type: string;
  config: Record<string, unknown>;
  secrets: Record<string, string>;
}

export interface ConnectionUpdateBody {
  name?: string;
  config?: Record<string, unknown>;
  secrets?: Record<string, string>;
}

export const connectionsApi = {
  list: (workspaceId: string) =>
    api<ConnectionSummary[]>(`/workspaces/${workspaceId}/connections`),
  get: (workspaceId: string, id: string) =>
    api<ConnectionSummary>(`/workspaces/${workspaceId}/connections/${id}`),
  create: (workspaceId: string, body: ConnectionCreateBody) =>
    api<ConnectionSummary>(`/workspaces/${workspaceId}/connections`, {
      method: "POST",
      json: body,
    }),
  update: (workspaceId: string, id: string, body: ConnectionUpdateBody) =>
    api<ConnectionSummary>(`/workspaces/${workspaceId}/connections/${id}`, {
      method: "PATCH",
      json: body,
    }),
  delete: (workspaceId: string, id: string) =>
    api<void>(`/workspaces/${workspaceId}/connections/${id}`, {
      method: "DELETE",
    }),
  test: (workspaceId: string, id: string) =>
    api<{ ok: boolean; error: string | null }>(
      `/workspaces/${workspaceId}/connections/${id}/test`,
      { method: "POST" },
    ),
  tables: (workspaceId: string, id: string) =>
    api<{ tables: string[] }>(
      `/workspaces/${workspaceId}/connections/${id}/tables`,
    ),
  columns: (workspaceId: string, id: string, table: string) =>
    api<{ table: string; columns: { name: string; type: string }[] }>(
      `/workspaces/${workspaceId}/connections/${id}/columns?table=${encodeURIComponent(table)}`,
    ),
};

export const pipelinesApi = {
  list: (workspaceId: string) =>
    api<PipelineSummary[]>(`/workspaces/${workspaceId}/pipelines`),
  get: (workspaceId: string, id: string) =>
    api<PipelineSummary>(`/workspaces/${workspaceId}/pipelines/${id}`),
  create: (
    workspaceId: string,
    body: {
      name: string;
      description?: string;
      config: Record<string, unknown>;
    },
  ) =>
    api<PipelineSummary>(`/workspaces/${workspaceId}/pipelines`, {
      method: "POST",
      json: body,
    }),
  update: (
    workspaceId: string,
    id: string,
    body: { name?: string; description?: string; config?: Record<string, unknown> },
  ) =>
    api<PipelineSummary>(`/workspaces/${workspaceId}/pipelines/${id}`, {
      method: "PATCH",
      json: body,
    }),
  trigger: (workspaceId: string, id: string) =>
    api<RunSummary>(`/workspaces/${workspaceId}/pipelines/${id}/trigger`, {
      method: "POST",
      json: {},
    }),
  backfill: (
    workspaceId: string,
    id: string,
    body: { cursor_from?: string | number | null; cursor_to?: string | number | null },
  ) =>
    api<RunSummary>(`/workspaces/${workspaceId}/pipelines/${id}/backfill`, {
      method: "POST",
      json: body,
    }),
  /** Phase P3b (ADR-0095) — split one cursor range into N parallel
   *  sub-runs (one per consecutive boundary pair, half-open windows).
   *  The multi-replica worker fleet claims them concurrently. */
  partitionedBackfill: (
    workspaceId: string,
    id: string,
    body: { boundaries: (string | number)[] },
  ) =>
    api<RunSummary[]>(`/workspaces/${workspaceId}/pipelines/${id}/partitioned-backfill`, {
      method: "POST",
      json: body,
    }),
  dryRun: (workspaceId: string, id: string) =>
    api<DryRunResponse>(
      `/workspaces/${workspaceId}/pipelines/${id}/dry-run`,
      { method: "POST", json: {} },
    ),
  /** Phase DLQ-2 (2026-06-04) — read a bounded sample of the pipeline's
   *  dead-letter-queue records (ADR-0075). Read-only. */
  dlqRecords: (workspaceId: string, id: string, limit = 50) =>
    api<DlqPreviewResponse>(
      `/workspaces/${workspaceId}/pipelines/${id}/dlq/records?limit=${limit}`,
    ),
  /** Phase ABR (2026-06-01) — versions list. Used by the run-detail
   *  page to show the *exact* config the run executed (the current
   *  pipeline may have been edited since). */
  listVersions: (workspaceId: string, id: string) =>
    api<PipelineVersionEntry[]>(
      `/workspaces/${workspaceId}/pipelines/${id}/versions`,
    ),
  delete: (workspaceId: string, id: string) =>
    api<void>(`/workspaces/${workspaceId}/pipelines/${id}`, {
      method: "DELETE",
    }),
  getTriggers: (workspaceId: string, id: string) =>
    api<PipelineTriggers>(`/workspaces/${workspaceId}/pipelines/${id}/triggers`),
  setTriggers: (workspaceId: string, id: string, targetPipelineIds: string[]) =>
    api<PipelineTriggers>(`/workspaces/${workspaceId}/pipelines/${id}/triggers`, {
      method: "PUT",
      json: { target_pipeline_ids: targetPipelineIds },
    }),
};

export interface PipelineTriggers {
  target_pipeline_ids: string[];
}

export interface DryRunConnectorCheck {
  name: string;
  type: string;
  ok: boolean;
  error: string | null;
}

/** One advisory lint warning from dry-run (Phase DD/AAK/FF server-side).
 *  Never flips ``ok`` to false — an accuracy nudge the user can close
 *  (e.g. "this python transform has no column_mapping; catalog lineage
 *  will rely on a heuristic"). */
export interface DryRunLintWarning {
  code: string;
  message: string;
  location: string | null;
}

export interface DryRunResponse {
  ok: boolean;
  errors: string[];
  connectors: DryRunConnectorCheck[];
  /** Advisory lint hints (Phase AEN — web surfaces them). Optional for
   *  back-compat with older server builds. */
  warnings?: DryRunLintWarning[];
}

/** Phase DLQ-2 (2026-06-04) — GET .../pipelines/{id}/dlq/records (ADR-0075).
 *  ``available`` is true only when records were read; otherwise ``reason``
 *  is a stable code (no_dlq / stream_dlq / connection_missing /
 *  connection_build_failed / sink_not_readable / unsafe_table /
 *  invalid_config / read_failed) the UI maps to a message. */
export interface DlqPreviewResponse {
  available: boolean;
  reason: string | null;
  connection: string | null;
  table: string | null;
  connector_type: string | null;
  records: Record<string, unknown>[];
  error: string | null;
}

export interface ScheduleCreateBody {
  name: string;
  mode: PipelineMode;
  cron_expr: string | null;
  is_active?: boolean;
  config_overrides?: Record<string, unknown>;
}

export interface ScheduleUpdateBody {
  name?: string;
  cron_expr?: string | null;
  is_active?: boolean;
  config_overrides?: Record<string, unknown>;
}

export const schedulesApi = {
  list: (workspaceId: string, pipelineId: string) =>
    api<ScheduleSummary[]>(
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/schedules`,
    ),
  create: (workspaceId: string, pipelineId: string, body: ScheduleCreateBody) =>
    api<ScheduleSummary>(
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/schedules`,
      { method: "POST", json: body },
    ),
  update: (
    workspaceId: string,
    pipelineId: string,
    id: string,
    body: ScheduleUpdateBody,
  ) =>
    api<ScheduleSummary>(
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/schedules/${id}`,
      { method: "PATCH", json: body },
    ),
  delete: (workspaceId: string, pipelineId: string, id: string) =>
    api<void>(
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/schedules/${id}`,
      { method: "DELETE" },
    ),
  toggle: (workspaceId: string, pipelineId: string, id: string) =>
    api<ScheduleSummary>(
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/schedules/${id}/toggle`,
      { method: "POST" },
    ),
};

export interface NodeRunEntry {
  /** Per-node execution record for a graph run with ``node_level=true``
   * (ADR-0041 H3b). Status evolves live: pending → running → succeeded|failed|cancelled. */
  id: string;
  node_id: string;
  kind: string;
  status: RunStatus;
  depends_on: string[];
  pending_deps: number;
  started_at: string | null;
  finished_at: string | null;
  heartbeat_at: string | null;
  worker_id: string | null;
  attempt: number;
  records_read: number;
  records_written: number;
  error_class: string | null;
  error_message: string | null;
  output_ref: string | null;
}

export const runsApi = {
  list: (
    workspaceId: string,
    query: {
      limit?: number;
      status?: RunStatus;
      pipeline_id?: string;
      schedule_id?: string;
    } = {},
  ) => api<RunSummary[]>(`/workspaces/${workspaceId}/runs`, { query }),
  get: (workspaceId: string, runId: string) =>
    api<RunDetail>(`/workspaces/${workspaceId}/runs/${runId}`),
  logs: (
    workspaceId: string,
    runId: string,
    query: { limit?: number; offset?: number; node_id?: string } = {},
  ) =>
    api<RunLogEntry[]>(`/workspaces/${workspaceId}/runs/${runId}/logs`, {
      // ``node_id`` is the filter knob added in Phase M (2026-05-26):
      //   * undefined / omitted → all logs
      //   * any string          → just that graph node's logs
      //   * "__run__"           → only run-level (build / setup / summary)
      query,
    }),
  metrics: (workspaceId: string, runId: string) =>
    api<RunMetricEntry[]>(`/workspaces/${workspaceId}/runs/${runId}/metrics`),
  nodeRuns: (workspaceId: string, runId: string) =>
    api<NodeRunEntry[]>(`/workspaces/${workspaceId}/runs/${runId}/node-runs`),
  retry: (workspaceId: string, runId: string) =>
    api<RunSummary>(`/workspaces/${workspaceId}/runs/${runId}/retry`, {
      method: "POST",
      json: {},
    }),
  cancel: (workspaceId: string, runId: string) =>
    // Phase P (2026-05-28). Server returns the updated RunDetail —
    // pending rows flip to status=cancelled immediately, running rows
    // come back with cancel_requested_at stamped (status changes to
    // cancelled once the worker lands the next wave boundary).
    api<RunDetail>(`/workspaces/${workspaceId}/runs/${runId}/cancel`, {
      method: "POST",
      json: {},
    }),
  logsStreamUrl: (workspaceId: string, runId: string) =>
    `${apiBaseUrl()}/workspaces/${workspaceId}/runs/${runId}/logs/stream`,
};

// --- Assets / lineage (catalog, ADR-0036) ----------------------------------

export interface AssetSummary {
  id: string;
  asset_key: string;
  kind: string | null;
  last_materialized_at: string | null;
  /** Phase UU (server) / AEH (web) — false when column-level lineage is
   *  traceable for this asset, true when it's opaque (no per-column
   *  mapping, e.g. Kafka/HTTP sinks). Surfaced so the analyst sees
   *  traceability in the list without opening each asset. Optional for
   *  back-compat with older server builds. */
  column_lineage_opaque?: boolean;
}

export interface AssetRef {
  id: string;
  asset_key: string;
  kind: string | null;
}

export interface AssetLineageResponse {
  id: string;
  asset_key: string;
  upstream: AssetRef[];
  downstream: AssetRef[];
}

export interface AssetMaterializationEntry {
  run_id: string | null;
  records_written: number;
  materialized_at: string;
}

// --- Column lineage (ADR-0041 J2/J3) ---------------------------------------

export interface ColumnUpstreamRef {
  asset_id: string;
  asset_key: string;
  column: string;
}

export interface AssetColumnEntry {
  name: string;
  upstreams: ColumnUpstreamRef[];
}

export interface AssetColumnLineageResponse {
  id: string;
  asset_key: string;
  opaque: boolean;
  columns: AssetColumnEntry[];
}

export const variablesApi = {
  list: (workspaceId: string) =>
    api<WorkspaceVariableEntry[]>(`/workspaces/${workspaceId}/variables`),
  set: (
    workspaceId: string,
    name: string,
    body: { value: unknown; description?: string | null },
  ) =>
    api<WorkspaceVariableEntry>(
      `/workspaces/${workspaceId}/variables/${encodeURIComponent(name)}`,
      { method: "PUT", json: body },
    ),
  delete: (workspaceId: string, name: string) =>
    api<void>(`/workspaces/${workspaceId}/variables/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
};

// --- Sensors (ADR-0041 K3c) ------------------------------------------------

export interface SensorSummary {
  id: string;
  workspace_id: string;
  name: string;
  type: string;
  config_json: Record<string, unknown>;
  target_pipeline_id: string | null;
  poll_interval_seconds: number;
  is_active: boolean;
  last_check_at: string | null;
  last_triggered_at: string | null;
  last_result_json: {
    triggered?: boolean;
    message?: string | null;
    metadata?: Record<string, unknown>;
  } | null;
  created_at: string;
  updated_at: string;
}

export interface SensorCreateBody {
  name: string;
  type: string;
  config_json: Record<string, unknown>;
  target_pipeline_id?: string | null;
  poll_interval_seconds?: number;
  is_active?: boolean;
}

export interface SensorUpdateBody {
  name?: string;
  config_json?: Record<string, unknown>;
  target_pipeline_id?: string | null;
  poll_interval_seconds?: number;
  is_active?: boolean;
}

export interface SensorCheckResponse {
  triggered: boolean;
  message: string | null;
  metadata: Record<string, unknown>;
}

export const sensorsApi = {
  list: (workspaceId: string) =>
    api<SensorSummary[]>(`/workspaces/${workspaceId}/sensors`),
  get: (workspaceId: string, id: string) =>
    api<SensorSummary>(`/workspaces/${workspaceId}/sensors/${id}`),
  create: (workspaceId: string, body: SensorCreateBody) =>
    api<SensorSummary>(`/workspaces/${workspaceId}/sensors`, {
      method: "POST",
      json: body,
    }),
  update: (workspaceId: string, id: string, body: SensorUpdateBody) =>
    api<SensorSummary>(`/workspaces/${workspaceId}/sensors/${id}`, {
      method: "PATCH",
      json: body,
    }),
  delete: (workspaceId: string, id: string) =>
    api<void>(`/workspaces/${workspaceId}/sensors/${id}`, { method: "DELETE" }),
  check: (workspaceId: string, id: string) =>
    api<SensorCheckResponse>(`/workspaces/${workspaceId}/sensors/${id}/check`, {
      method: "POST",
    }),
};

export const assetsApi = {
  list: (workspaceId: string) =>
    api<AssetSummary[]>(`/workspaces/${workspaceId}/assets`),
  lineage: (workspaceId: string, assetId: string) =>
    api<AssetLineageResponse>(`/workspaces/${workspaceId}/assets/${assetId}/lineage`),
  materializations: (workspaceId: string, assetId: string) =>
    api<AssetMaterializationEntry[]>(
      `/workspaces/${workspaceId}/assets/${assetId}/materializations`,
    ),
  columnLineage: (workspaceId: string, assetId: string) =>
    api<AssetColumnLineageResponse>(
      `/workspaces/${workspaceId}/assets/${assetId}/column-lineage`,
    ),
};

// --- ERD diagrams (Phase AHD, ADR-0090) — server-backed ERD store ---

export interface ErdDiagramSummary {
  id: string;
  name: string;
  table_count: number;
  updated_at: string;
}

export interface ErdDiagramDetail {
  id: string;
  name: string;
  design_json: ErdDesign;
  updated_at: string;
}

export const erdApi = {
  list: (workspaceId: string) =>
    api<ErdDiagramSummary[]>(`/workspaces/${workspaceId}/erd-diagrams`),
  get: (workspaceId: string, id: string) =>
    api<ErdDiagramDetail>(`/workspaces/${workspaceId}/erd-diagrams/${id}`),
  create: (workspaceId: string, body: { name: string; design_json: ErdDesign }) =>
    api<ErdDiagramDetail>(`/workspaces/${workspaceId}/erd-diagrams`, {
      method: "POST",
      json: body,
    }),
  update: (
    workspaceId: string,
    id: string,
    body: { name?: string; design_json?: ErdDesign },
  ) =>
    api<ErdDiagramDetail>(`/workspaces/${workspaceId}/erd-diagrams/${id}`, {
      method: "PATCH",
      json: body,
    }),
  delete: (workspaceId: string, id: string) =>
    api<void>(`/workspaces/${workspaceId}/erd-diagrams/${id}`, { method: "DELETE" }),
};
