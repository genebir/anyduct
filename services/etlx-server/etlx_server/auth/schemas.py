"""Pydantic request/response models for the auth router."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=512)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenPair(BaseModel):
    """OAuth2-shaped response. ``token_type`` is always ``bearer``."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    """Lifetime of ``access_token`` in seconds."""


class CurrentUser(BaseModel):
    """Minimal user identity injected into protected endpoints."""

    id: UUID
    email: EmailStr
    name: str
    is_superadmin: bool


class OidcProviderSummary(BaseModel):
    """Public-safe provider info — never leaks ``client_secret``."""

    name: str
    display_name: str | None = None


class OidcAuthorizeResponse(BaseModel):
    """Returned from ``GET /auth/oidc/login`` — the FE redirects the browser
    to ``authorize_url`` and stores nothing (state is embedded in the URL)."""

    authorize_url: str
    state: str


class OidcCallbackResponse(TokenPair):
    """Same shape as ``TokenPair`` plus the original ``return_to`` so the FE
    can redirect to the page the user clicked from."""

    return_to: str | None = None


_SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
_COLOR_PATTERN = r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"


class WorkspaceSummary(BaseModel):
    """Compact workspace identity used by RBAC-aware endpoints."""

    id: UUID
    name: str
    slug: str
    color_hex: str
    role: str | None = None
    """Caller's role in this workspace; ``None`` means SuperAdmin bypass."""


class WorkspaceCreateRequest(BaseModel):
    """Body of ``POST /workspaces``."""

    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=64, pattern=_SLUG_PATTERN)
    color_hex: str = Field(default="#FF3D8B", pattern=_COLOR_PATTERN)


class WorkspaceUpdateRequest(BaseModel):
    """PATCH-style body of ``PATCH /workspaces/{id}`` — all fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=64, pattern=_SLUG_PATTERN)
    color_hex: str | None = Field(default=None, pattern=_COLOR_PATTERN)

    def as_field_dict(self) -> dict[str, Any]:
        """Return only the fields the caller actually set (``model_dump(exclude_unset=True)``)."""
        return self.model_dump(exclude_unset=True)


class MembershipSummary(BaseModel):
    """One row of ``/workspaces/{id}/memberships`` — joined user identity + role."""

    id: UUID
    user_id: UUID
    email: EmailStr
    name: str
    role: str


class MembershipCreateRequest(BaseModel):
    """Body of ``POST /workspaces/{id}/memberships`` — add by email."""

    email: EmailStr
    role: Literal["owner", "editor", "runner", "viewer"]


class MembershipUpdateRequest(BaseModel):
    """Body of ``PATCH /workspaces/{id}/memberships/{user_id}`` — role only."""

    role: Literal["owner", "editor", "runner", "viewer"]


class ConnectionSummary(BaseModel):
    """One row of the connections response. ``config_json`` carries the
    ``${SECRET:...}`` placeholders — never the resolved values."""

    id: UUID
    workspace_id: UUID
    name: str
    type: str
    config_json: dict[str, Any]
    secret_refs: list[str]


class WorkspaceVariableEntry(BaseModel):
    """One workspace-global variable (ADR-0041, V2). Non-secret config value."""

    name: str
    value: Any
    description: str | None = None


class WorkspaceVariableSetRequest(BaseModel):
    """Body of ``PUT /workspaces/{id}/variables/{name}`` — upsert a global variable."""

    value: Any = None
    description: str | None = Field(default=None, max_length=2000)


class ConnectionCreateRequest(BaseModel):
    """Body of ``POST /workspaces/{id}/connections``.

    ``config`` may contain ``{"$secret": "<key>"}`` markers wherever a string
    would go; each referenced key must appear in ``secrets``. Extra keys in
    ``secrets`` (not referenced from config) → 422.
    """

    name: str = Field(min_length=1, max_length=255)
    type: str = Field(min_length=1, max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)


class ConnectionUpdateRequest(BaseModel):
    """PATCH body — every field optional.

    Touching ``config`` always re-syncs secrets: pre-existing backend
    entries that are no longer referenced get deleted, and new secret keys
    must be supplied via ``secrets``. Updating only ``name`` is allowed
    without re-sending config/secrets.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, Any] | None = None
    secrets: dict[str, str] | None = None


class ConnectionTestResult(BaseModel):
    """Response from ``POST /connections/{id}/test``."""

    ok: bool
    error: str | None = None


class AssetSummary(BaseModel):
    """One row of ``GET /workspaces/{ws}/assets`` (ADR-0036).

    ``column_lineage_opaque`` (Phase UU, 2026-05-29): exposed so the
    list view can show a "traceable" badge per asset without forcing
    a per-row hit on ``/column-lineage``. The flag is already on the
    ``Asset`` model (ADR-0041 J2); we just hadn't surfaced it in the
    summary schema. Analyst dogfooding caught the omission.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    asset_key: str
    kind: str | None = None
    last_materialized_at: datetime | None = None
    column_lineage_opaque: bool = False


class AssetRef(BaseModel):
    """A neighbouring asset in a lineage response."""

    id: UUID
    asset_key: str
    kind: str | None = None


class AssetLineageResponse(BaseModel):
    """Response from ``GET /workspaces/{ws}/assets/{id}/lineage`` — direct
    upstream + downstream neighbours."""

    id: UUID
    asset_key: str
    upstream: list[AssetRef]
    downstream: list[AssetRef]


class AssetMaterializationEntry(BaseModel):
    """One row of ``GET /workspaces/{ws}/assets/{id}/materializations``."""

    model_config = ConfigDict(from_attributes=True)

    run_id: UUID | None = None
    records_written: int
    materialized_at: datetime


class ColumnUpstreamRef(BaseModel):
    """One upstream column for a downstream column (ADR-0041 J2)."""

    asset_id: UUID
    asset_key: str
    column: str


class AssetColumnEntry(BaseModel):
    """One column of an asset + its upstream column refs (ADR-0041 J2)."""

    name: str
    upstreams: list[ColumnUpstreamRef]


class AssetColumnLineageResponse(BaseModel):
    """Response from ``GET /workspaces/{ws}/assets/{id}/column-lineage``.

    ``opaque`` = the worker derived this asset's column mapping as
    untraceable (python transform, ``SELECT *``, join, direct table source).
    When true, ``columns`` is typically empty — the UI shows a badge instead
    of a drill-down. ``columns`` is also empty when no successful run has
    materialized this asset yet (the two states are distinguished by
    ``opaque``).
    """

    id: UUID
    asset_key: str
    opaque: bool
    columns: list[AssetColumnEntry]


class ConnectionTablesResult(BaseModel):
    """Response from ``GET /connections/{id}/tables`` (ADR-0033)."""

    tables: list[str]


class ColumnEntry(BaseModel):
    """One column in a ``GET /connections/{id}/columns`` response."""

    name: str
    type: str


class ConnectionColumnsResult(BaseModel):
    """Response from ``GET /connections/{id}/columns?table=...`` (ADR-0033)."""

    table: str
    columns: list[ColumnEntry]


class PipelineVersionEntry(BaseModel):
    """One row of ``GET /workspaces/{ws}/pipelines/{pid}/versions``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    is_current: bool
    config_json: dict[str, Any]
    created_at: datetime


class PipelineSummary(BaseModel):
    """Pipeline + a flattened view of the current version's config."""

    id: UUID
    workspace_id: UUID
    name: str
    description: str | None = None
    current_version: int | None = None
    current_config_json: dict[str, Any] | None = None


class PipelineCreateRequest(BaseModel):
    """Body of ``POST /workspaces/{id}/pipelines``."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    # Full ``PipelineConfig`` dump *minus* ``name`` — server injects ``name``
    # from the body so the two stay in sync without forcing the client to
    # repeat themselves.
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineUpdateRequest(BaseModel):
    """PATCH body — every field optional."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    config: dict[str, Any] | None = None


class PipelineTriggersBody(BaseModel):
    """GET/PUT body for downstream pipeline triggers (ADR-0029)."""

    target_pipeline_ids: list[UUID] = Field(default_factory=list)


class SensorSummary(BaseModel):
    """One row of ``GET /workspaces/{ws}/sensors`` (ADR-0041 K3b).

    ``last_check_at`` / ``last_triggered_at`` / ``last_result_json`` are
    server-maintained — the UI surfaces them so an operator can debug a
    quiet sensor without re-running it (manual ``POST .../check``).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    type: str
    config_json: dict[str, Any]
    target_pipeline_id: UUID | None
    poll_interval_seconds: int
    is_active: bool
    last_check_at: datetime | None
    last_triggered_at: datetime | None
    last_result_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class SensorCreateRequest(BaseModel):
    """Body of ``POST /workspaces/{ws}/sensors``.

    ``type`` must already be registered with the core's
    :func:`etl_plugins.core.sensor.build_sensor` — the server rejects
    unknown types with HTTP 422 + the list of valid ones so the UI can
    surface the right help.
    """

    name: str = Field(min_length=1, max_length=255)
    type: str = Field(min_length=1, max_length=64)
    config_json: dict[str, Any] = Field(default_factory=dict)
    target_pipeline_id: UUID | None = None
    poll_interval_seconds: int = Field(default=60, ge=5, le=86400)
    is_active: bool = True


class SensorUpdateRequest(BaseModel):
    """PATCH body — every field optional. ``type`` is immutable (a
    different sensor type is structurally a different sensor). Pass
    ``target_pipeline_id: null`` explicitly to clear it (orphan the
    sensor)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    config_json: dict[str, Any] | None = None
    target_pipeline_id: UUID | None = None
    poll_interval_seconds: int | None = Field(default=None, ge=5, le=86400)
    is_active: bool | None = None

    def as_field_dict(self) -> dict[str, Any]:
        # ``exclude_unset`` so omitted fields stay unset (vs explicit
        # ``null`` which means "clear").
        return self.model_dump(exclude_unset=True)


class SensorCheckResponse(BaseModel):
    """Response from ``POST /workspaces/{ws}/sensors/{id}/check`` —
    runs the configured ``check()`` once + returns the result. Does NOT
    enqueue a trigger run (operator-controlled manual check)."""

    triggered: bool
    message: str | None
    metadata: dict[str, Any]


class ScheduleSummary(BaseModel):
    """One row of ``GET /workspaces/{ws}/pipelines/{pid}/schedules``."""

    id: UUID
    pipeline_id: UUID
    name: str
    mode: Literal["batch", "stream"]
    cron_expr: str | None
    is_active: bool
    config_overrides: dict[str, Any]


class ScheduleCreateRequest(BaseModel):
    """Body of ``POST /workspaces/{ws}/pipelines/{pid}/schedules``.

    ``mode='batch'`` requires ``cron_expr``; ``mode='stream'`` allows
    ``cron_expr=None`` (the worker keeps the stream pipeline alive
    indefinitely) or an optional re-arm cron.
    """

    name: str = Field(min_length=1, max_length=255)
    mode: Literal["batch", "stream"]
    cron_expr: str | None = Field(default=None, max_length=64)
    is_active: bool = True
    config_overrides: dict[str, Any] = Field(default_factory=dict)


class ScheduleUpdateRequest(BaseModel):
    """PATCH body — every field optional. Mode is immutable (delete + recreate
    if you actually want to change the underlying execution model)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    cron_expr: str | None = Field(default=None, max_length=64)
    is_active: bool | None = None
    config_overrides: dict[str, Any] | None = None

    def as_field_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class RunSummary(BaseModel):
    """Compact row for the runs table (workspace dashboard)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    pipeline_id: UUID
    pipeline_version_id: UUID
    schedule_id: UUID | None
    triggered_by_user_id: UUID | None
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    scheduled_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    records_read: int
    records_written: int
    duration_seconds: float | None
    error_class: str | None
    created_at: datetime


class RunDetail(RunSummary):
    """Drill-down view — adds the worker bookkeeping + raw error message + result_json."""

    heartbeat_at: datetime | None
    worker_id: str | None
    error_message: str | None
    result_json: dict[str, Any]
    # Stamped by POST /runs/{rid}/cancel (Phase P, 2026-05-28). When
    # set on a RUNNING row the worker will land the cancellation at
    # the next node boundary; the UI uses this to show "Cancelling…"
    # in the gap between request and final status.
    cancel_requested_at: datetime | None = None


class RunLogEntry(BaseModel):
    """One row of ``GET /workspaces/{ws}/runs/{id}/logs``.

    ``node_id`` (Phase M, 2026-05-26) is populated for logs emitted
    inside a specific graph node's execution. ``None`` means the log
    was emitted at run level (build / connector setup / summary etc.).
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    ts: datetime
    level: Literal["debug", "info", "warning", "error"]
    message: str
    node_id: str | None = None
    context_json: dict[str, Any]


class RunMetricEntry(BaseModel):
    """One row of ``GET /workspaces/{ws}/runs/{id}/metrics``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    value: float
    attrs_json: dict[str, Any]
    recorded_at: datetime


class NodeRunEntry(BaseModel):
    """One row of ``GET /workspaces/{ws}/runs/{id}/node-runs`` (ADR-0041 H3b).

    Per-node execution record for a graph pipeline run with ``node_level=true`` —
    drives the Run-detail DAG view. ``status`` evolves live during execution:
    ``pending → running → succeeded|failed|cancelled``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    node_id: str
    kind: str
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    depends_on: list[str]
    pending_deps: int
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime | None
    worker_id: str | None
    attempt: int
    records_read: int
    records_written: int
    error_class: str | None
    error_message: str | None
    output_ref: str | None


class DryRunConnectorCheck(BaseModel):
    """One row of ``DryRunResult.connectors`` — per-connection outcome."""

    name: str
    type: str
    ok: bool
    error: str | None = None


class DryRunLintWarning(BaseModel):
    """One advisory lint warning (Phase DD, 2026-05-29).

    Distinct from ``errors``: a warning never flips ``ok`` to False. The
    UI surfaces these as hints — e.g. "this python transform has no
    ``column_mapping``, catalog lineage will rely on a heuristic".
    """

    code: str
    message: str
    location: str | None = None


class DryRunResponse(BaseModel):
    """Response of ``POST /workspaces/{ws}/pipelines/{pid}/dry-run``.

    ``ok`` summarizes the answer; ``errors`` carries top-level issues
    (config invalid, build failure, missing connection name, etc.);
    ``connectors`` reports per-connection results so the UI can show
    which credential needs attention. ``warnings`` (Phase DD) carries
    advisory lint hints that don't block running but flag accuracy
    gaps the user can close (e.g. missing ``column_mapping`` on a
    python transform).
    """

    ok: bool
    errors: list[str] = Field(default_factory=list)
    connectors: list[DryRunConnectorCheck] = Field(default_factory=list)
    warnings: list[DryRunLintWarning] = Field(default_factory=list)


class DlqPreviewResponse(BaseModel):
    """Response of ``GET /workspaces/{ws}/pipelines/{pid}/dlq/records``.

    A bounded sample of the records the pipeline routed to its dead-letter
    queue (Phase DLQ-1, ADR-0075). ``available`` is true only when records
    were read; otherwise ``reason`` is a stable code the UI maps to a
    message (e.g. ``no_dlq``, ``sink_not_readable``, ``read_failed``).
    ``records`` is a list of raw row objects (the failed records as
    written to the DLQ sink).
    """

    available: bool
    reason: str | None = None
    connection: str | None = None
    table: str | None = None
    connector_type: str | None = None
    records: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class RunTriggerRequest(BaseModel):
    """Body of ``POST /workspaces/{ws}/pipelines/{pid}/trigger``.

    Intentionally empty for now — the action enqueues the *current*
    PipelineVersion with no overrides. Leaving the body in place
    (rather than collapsing to a parameterless POST) keeps room for
    future fields (``config_overrides``, ``scheduled_at``, ...) without
    a breaking change to the URL.
    """


class RunBackfillRequest(BaseModel):
    """Body of ``POST /workspaces/{ws}/pipelines/{pid}/backfill`` (ADR-0039).

    Runs the current version over a cursor range on the source's
    ``cursor_column``: records with value ``> cursor_from`` and
    ``<= cursor_to``. Either bound may be omitted (open range). Values are
    scalars compared by the DB against the cursor column (e.g. an ISO date
    string for a timestamp column, an int for a sequence)."""

    cursor_from: str | int | float | None = None
    cursor_to: str | int | float | None = None


class AuditLogEntry(BaseModel):
    """One row of the ``audit_log`` table, shaped for the ``/audit`` response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_user_id: UUID | None
    workspace_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    before_json: dict[str, Any] | None
    after_json: dict[str, Any] | None
    ip: str | None
    user_agent: str | None
    created_at: datetime


__all__ = [
    "AuditLogEntry",
    "ConnectionCreateRequest",
    "ConnectionSummary",
    "ConnectionTestResult",
    "ConnectionUpdateRequest",
    "CurrentUser",
    "DlqPreviewResponse",
    "DryRunConnectorCheck",
    "DryRunResponse",
    "LoginRequest",
    "MembershipCreateRequest",
    "MembershipSummary",
    "MembershipUpdateRequest",
    "NodeRunEntry",
    "OidcAuthorizeResponse",
    "OidcCallbackResponse",
    "OidcProviderSummary",
    "PipelineCreateRequest",
    "PipelineSummary",
    "PipelineUpdateRequest",
    "PipelineVersionEntry",
    "RefreshRequest",
    "RunBackfillRequest",
    "RunDetail",
    "RunLogEntry",
    "RunMetricEntry",
    "RunSummary",
    "RunTriggerRequest",
    "ScheduleCreateRequest",
    "ScheduleSummary",
    "ScheduleUpdateRequest",
    "TokenPair",
    "WorkspaceCreateRequest",
    "WorkspaceSummary",
    "WorkspaceUpdateRequest",
]
