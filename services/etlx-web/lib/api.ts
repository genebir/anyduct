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
};

export const connectionsApi = {
  list: (workspaceId: string) =>
    api<ConnectionSummary[]>(`/workspaces/${workspaceId}/connections`),
  test: (workspaceId: string, id: string) =>
    api<{ ok: boolean; error: string | null }>(
      `/workspaces/${workspaceId}/connections/${id}/test`,
      { method: "POST" },
    ),
};

export const pipelinesApi = {
  list: (workspaceId: string) =>
    api<PipelineSummary[]>(`/workspaces/${workspaceId}/pipelines`),
  get: (workspaceId: string, id: string) =>
    api<PipelineSummary>(`/workspaces/${workspaceId}/pipelines/${id}`),
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
};

export const schedulesApi = {
  list: (workspaceId: string, pipelineId: string) =>
    api<ScheduleSummary[]>(
      `/workspaces/${workspaceId}/pipelines/${pipelineId}/schedules`,
    ),
};

export const runsApi = {
  list: (workspaceId: string, query: { limit?: number; status?: RunStatus } = {}) =>
    api<RunSummary[]>(`/workspaces/${workspaceId}/runs`, { query }),
};
