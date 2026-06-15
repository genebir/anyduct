"""Pipeline CRUD + versions (Step 8.5d).

| Method | Path                                              | Auth     |
|--------|---------------------------------------------------|----------|
| GET    | ``/workspaces/{ws}/pipelines``                    | Viewer+  |
| POST   | ``/workspaces/{ws}/pipelines``                    | Editor+  |
| GET    | ``/workspaces/{ws}/pipelines/{pid}``              | Viewer+  |
| PATCH  | ``/workspaces/{ws}/pipelines/{pid}``              | Editor+  |
| DELETE | ``/workspaces/{ws}/pipelines/{pid}``              | Editor+  |
| GET    | ``/workspaces/{ws}/pipelines/{pid}/versions``     | Viewer+  |

The version model is **immutable history**: every successful POST/PATCH
that changes ``config_json`` produces a fresh
:class:`PipelineVersion` (``version`` increments, ``is_current`` flips on
the prior row); identical ``config_json`` re-submitted is a no-op
(:meth:`PipelineRepository.ensure_version`). Audit ``pipeline.update``
includes a ``version_created`` flag so the audit log distinguishes
real edits from no-op PATCHes.

``PipelineConfig`` (core) provides structural validation — bad shapes
produce 422 with the Pydantic error chain. The server injects
``config["name"]`` from the body so users don't have to repeat it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.audit.dependencies import get_audit_service
from anyduct_server.audit.service import AuditService
from anyduct_server.auth.schemas import (
    CursorStatsResponse,
    DlqPreviewResponse,
    DryRunConnectorCheck,
    DryRunLintWarning,
    DryRunResponse,
    PartitionedBackfillRequest,
    PipelineCreateRequest,
    PipelineSummary,
    PipelineTriggersBody,
    PipelineUpdateRequest,
    PipelineVersionEntry,
    RunBackfillRequest,
    RunSummary,
    RunTriggerRequest,
)
from anyduct_server.auth.workspace_context import (
    WorkspaceContext,
    require_workspace_role,
)
from anyduct_server.db.enums import WorkspaceRole
from anyduct_server.db.models import Pipeline, PipelineVersion
from anyduct_server.dependencies import get_secret_backend_dep, get_session
from anyduct_server.pipelines.cursor_stats import CursorStatsService
from anyduct_server.pipelines.dlq_preview import DlqPreviewService
from anyduct_server.pipelines.dry_run import DryRunService
from anyduct_server.pipelines.repository import (
    PipelineNameTakenError,
    PipelineRepository,
)
from anyduct_server.pipelines.triggers import PipelineTriggerRepository
from anyduct_server.runs.repository import RunRepository
from etl_plugins.config.models import PipelineConfig
from etl_plugins.config.secrets import SecretBackend

router = APIRouter(prefix="/workspaces/{workspace_id}/pipelines", tags=["pipelines"])

_require_viewer = Depends(require_workspace_role(WorkspaceRole.VIEWER))
_require_runner = Depends(require_workspace_role(WorkspaceRole.RUNNER))
_require_editor = Depends(require_workspace_role(WorkspaceRole.EDITOR))


def _validate_config(config: dict[str, Any], *, name: str) -> dict[str, Any]:
    """Inject ``name`` and run it through the core ``PipelineConfig`` validator.

    Returns the canonical JSON dump (post-validation) — that's what
    lands in ``pipeline_versions.config_json`` and is what the version
    idempotency check compares against.
    """
    payload = dict(config)
    payload["name"] = name
    try:
        cfg = PipelineConfig.model_validate(payload)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"invalid pipeline config: {e.errors()}",
        ) from e
    return cfg.model_dump(mode="json")


def _to_summary(pipeline: Pipeline, current: PipelineVersion | None) -> PipelineSummary:
    return PipelineSummary(
        id=pipeline.id,
        workspace_id=pipeline.workspace_id,
        name=pipeline.name,
        description=pipeline.description,
        current_version=current.version if current is not None else None,
        current_config_json=current.config_json if current is not None else None,
    )


@router.get("", response_model=list[PipelineSummary])
async def list_pipelines(
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[PipelineSummary]:
    repo = PipelineRepository(session)
    pipelines = await repo.list_for_workspace(workspace_id=ctx.workspace.id)
    out: list[PipelineSummary] = []
    for p in pipelines:
        current = await repo.get_current_version(pipeline_id=p.id)
        out.append(_to_summary(p, current))
    return out


@router.post("", response_model=PipelineSummary, status_code=status.HTTP_201_CREATED)
async def create_pipeline(
    body: PipelineCreateRequest,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> PipelineSummary:
    canonical = _validate_config(body.config, name=body.name)
    repo = PipelineRepository(session)
    try:
        pipeline, version = await repo.add(
            workspace_id=ctx.workspace.id,
            name=body.name,
            description=body.description,
            config_json=canonical,
            created_by_user_id=ctx.user.id,
        )
    except PipelineNameTakenError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="pipeline.create",
        resource_type="pipeline",
        resource_id=str(pipeline.id),
        before=None,
        after=PipelineRepository.snapshot(pipeline, version),
    )
    await session.commit()
    return _to_summary(pipeline, version)


@router.get("/{pipeline_id}", response_model=PipelineSummary)
async def get_pipeline(
    pipeline_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PipelineSummary:
    repo = PipelineRepository(session)
    pipeline = await repo.get(workspace_id=ctx.workspace.id, pipeline_id=pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pipeline not found")
    current = await repo.get_current_version(pipeline_id=pipeline.id)
    return _to_summary(pipeline, current)


@router.patch("/{pipeline_id}", response_model=PipelineSummary)
async def update_pipeline(
    pipeline_id: UUID,
    body: PipelineUpdateRequest,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> PipelineSummary:
    repo = PipelineRepository(session)
    pipeline = await repo.get(workspace_id=ctx.workspace.id, pipeline_id=pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pipeline not found")
    if body.name is None and body.description is None and body.config is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one field is required",
        )

    current_before = await repo.get_current_version(pipeline_id=pipeline.id)
    before = PipelineRepository.snapshot(pipeline, current_before)

    metadata_fields: dict[str, Any] = {}
    if body.name is not None:
        metadata_fields["name"] = body.name
    if body.description is not None:
        metadata_fields["description"] = body.description
    if metadata_fields:
        try:
            await repo.update_metadata(pipeline, **metadata_fields)
        except PipelineNameTakenError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

    version_created = False
    if body.config is not None:
        canonical = _validate_config(body.config, name=pipeline.name)
        _, version_created = await repo.ensure_version(
            pipeline, canonical, created_by_user_id=ctx.user.id
        )

    current_after = await repo.get_current_version(pipeline_id=pipeline.id)
    after = PipelineRepository.snapshot(pipeline, current_after)

    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="pipeline.update",
        resource_type="pipeline",
        resource_id=str(pipeline.id),
        before=before,
        after={**after, "version_created": version_created},
    )
    await session.commit()
    return _to_summary(pipeline, current_after)


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pipeline(
    pipeline_id: UUID,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> None:
    repo = PipelineRepository(session)
    pipeline = await repo.get(workspace_id=ctx.workspace.id, pipeline_id=pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pipeline not found")
    current = await repo.get_current_version(pipeline_id=pipeline.id)
    before = PipelineRepository.snapshot(pipeline, current)
    pipeline_uuid = pipeline.id

    await repo.delete(pipeline)
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="pipeline.delete",
        resource_type="pipeline",
        resource_id=str(pipeline_uuid),
        before=before,
        after=None,
    )
    await session.commit()


@router.get("/{pipeline_id}/versions", response_model=list[PipelineVersionEntry])
async def list_pipeline_versions(
    pipeline_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[PipelineVersionEntry]:
    repo = PipelineRepository(session)
    pipeline = await repo.get(workspace_id=ctx.workspace.id, pipeline_id=pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pipeline not found")
    versions = await repo.list_versions(pipeline_id=pipeline.id)
    return [PipelineVersionEntry.model_validate(v) for v in versions]


# --- Downstream triggers (call-pipeline, ADR-0029) -------------------------


@router.get("/{pipeline_id}/triggers", response_model=PipelineTriggersBody)
async def get_pipeline_triggers(
    pipeline_id: UUID,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> PipelineTriggersBody:
    repo = PipelineRepository(session)
    pipeline = await repo.get(workspace_id=ctx.workspace.id, pipeline_id=pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pipeline not found")
    targets = await PipelineTriggerRepository(session).list_targets(source_pipeline_id=pipeline.id)
    return PipelineTriggersBody(target_pipeline_ids=targets)


@router.put("/{pipeline_id}/triggers", response_model=PipelineTriggersBody)
async def set_pipeline_triggers(
    pipeline_id: UUID,
    body: PipelineTriggersBody,
    ctx: WorkspaceContext = _require_editor,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> PipelineTriggersBody:
    repo = PipelineRepository(session)
    pipeline = await repo.get(workspace_id=ctx.workspace.id, pipeline_id=pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pipeline not found")

    # Every target must be a pipeline in the same workspace; a self-trigger is
    # nonsensical (and the worker would skip it as a cycle anyway).
    targets: list[UUID] = []
    for target_id in dict.fromkeys(body.target_pipeline_ids):  # dedupe, keep order
        if target_id == pipeline.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="a pipeline cannot trigger itself",
            )
        target = await repo.get(workspace_id=ctx.workspace.id, pipeline_id=target_id)
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"target pipeline {target_id} not found in workspace",
            )
        targets.append(target_id)

    trigger_repo = PipelineTriggerRepository(session)
    before = await trigger_repo.list_targets(source_pipeline_id=pipeline.id)
    await trigger_repo.set_targets(source_pipeline_id=pipeline.id, target_pipeline_ids=targets)
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="pipeline.triggers_set",
        resource_type="pipeline",
        resource_id=str(pipeline.id),
        before={"target_pipeline_ids": [str(t) for t in before]},
        after={"target_pipeline_ids": [str(t) for t in targets]},
    )
    await session.commit()
    return PipelineTriggersBody(target_pipeline_ids=targets)


# --- Action endpoints (Step 8.6) -------------------------------------------


async def _load_pipeline_and_current(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    pipeline_id: UUID,
) -> tuple[Pipeline, PipelineVersion]:
    """Resolve pipeline + current version, raising 404/409 as needed.

    Used by both ``dry-run`` and ``trigger``. A pipeline with no
    ``is_current`` version is technically impossible via the public API
    (``add`` inserts v1 atomically), but ``409 Conflict`` is the right
    answer if the row got there some other way.
    """
    repo = PipelineRepository(session)
    pipeline = await repo.get(workspace_id=workspace_id, pipeline_id=pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="pipeline not found")
    current = await repo.get_current_version(pipeline_id=pipeline.id)
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="pipeline has no current version — nothing to run",
        )
    return pipeline, current


@router.post("/{pipeline_id}/dry-run", response_model=DryRunResponse)
async def dry_run_pipeline(
    pipeline_id: UUID,
    ctx: WorkspaceContext = _require_runner,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    backend: SecretBackend = Depends(get_secret_backend_dep),  # noqa: B008
) -> DryRunResponse:
    """Build + health-check the pipeline without queuing a run.

    Read-only: the only DB writes are connector health probes (network
    I/O on the connectors themselves). No audit row — dry-run is a
    "would this work?" question, not an action.
    """
    pipeline, current = await _load_pipeline_and_current(
        session, workspace_id=ctx.workspace.id, pipeline_id=pipeline_id
    )
    outcome = await DryRunService(session, backend).run(pipeline, current)
    return DryRunResponse(
        ok=outcome.ok,
        errors=list(outcome.errors),
        connectors=[
            DryRunConnectorCheck(name=c.name, type=c.type, ok=c.ok, error=c.error)
            for c in outcome.connectors
        ],
        warnings=[
            DryRunLintWarning(code=w.code, message=w.message, location=w.location)
            for w in outcome.warnings
        ],
    )


@router.get("/{pipeline_id}/dlq/records", response_model=DlqPreviewResponse)
async def preview_dlq_records(
    pipeline_id: UUID,
    limit: int = 50,
    ctx: WorkspaceContext = _require_viewer,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    backend: SecretBackend = Depends(get_secret_backend_dep),  # noqa: B008
) -> DlqPreviewResponse:
    """Read a bounded sample of the pipeline's dead-letter-queue records.

    Read-only (no audit). Resolves the current version's ``dlq`` config,
    and — when the DLQ sink is readable (an RDBMS table) — returns up to
    ``limit`` rows. When it isn't (no DLQ, a Kafka topic, a write-only
    sink) the response carries ``available=False`` + a ``reason`` the UI
    turns into guidance. See ADR-0075 / Phase DLQ-1.
    """
    limit = max(1, min(limit, 200))
    pipeline, current = await _load_pipeline_and_current(
        session, workspace_id=ctx.workspace.id, pipeline_id=pipeline_id
    )
    p = await DlqPreviewService(session, backend).preview(pipeline, current, limit=limit)
    return DlqPreviewResponse(
        available=p.available,
        reason=p.reason,
        connection=p.connection,
        table=p.table,
        connector_type=p.connector_type,
        records=p.records,
        error=p.error,
    )


@router.get("/{pipeline_id}/cursor-stats", response_model=CursorStatsResponse)
async def cursor_stats(
    pipeline_id: UUID,
    windows: int = 0,
    ctx: WorkspaceContext = _require_runner,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    backend: SecretBackend = Depends(get_secret_backend_dep),  # noqa: B008
) -> CursorStatsResponse:
    """MIN/MAX/COUNT over the source's ``cursor_column`` (ADR-0095 f/u).

    Powers the Backfill dialog's "suggest split points" — the server only
    reports the range; the operator edits and confirms the boundaries
    (auto-splitting was rejected in ADR-0095: arithmetic windows skew).
    Read-only (no audit); ``available=False`` + ``reason`` when the
    pipeline has no cursor or the source can't answer.
    """
    pipeline, current = await _load_pipeline_and_current(
        session, workspace_id=ctx.workspace.id, pipeline_id=pipeline_id
    )
    windows = max(0, min(windows, 64))
    s = await CursorStatsService(session, backend).stats(pipeline, current, windows=windows)
    return CursorStatsResponse(
        available=s.available,
        reason=s.reason,
        connection=s.connection,
        connector_type=s.connector_type,
        cursor_column=s.cursor_column,
        min_value=s.min_value,
        max_value=s.max_value,
        row_count=s.row_count,
        quantiles=s.quantiles,
        error=s.error,
    )


@router.post(
    "/{pipeline_id}/trigger",
    response_model=RunSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_pipeline(
    pipeline_id: UUID,
    body: RunTriggerRequest,
    ctx: WorkspaceContext = _require_runner,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> RunSummary:
    """Enqueue a pending Run for the pipeline's current version.

    Returns ``202 Accepted`` — the worker (Step 9) is what actually
    moves the row through ``running`` / ``succeeded`` / ``failed``.
    Until the worker exists, the row simply sits in ``pending``.
    """
    pipeline, current = await _load_pipeline_and_current(
        session, workspace_id=ctx.workspace.id, pipeline_id=pipeline_id
    )
    run = await RunRepository(session).add_manual(
        pipeline=pipeline,
        version=current,
        triggered_by_user_id=ctx.user.id,
        # Per-run params (자유도 1단계) ride on result_json (like backfill range)
        # — the worker reads them into the RuntimeContext for {{ params.x }}.
        result_json={"params": body.params} if body.params else None,
    )
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="run.trigger",
        resource_type="run",
        resource_id=str(run.id),
        before=None,
        after={
            "pipeline_id": str(pipeline.id),
            "pipeline_version_id": str(current.id),
            "version": current.version,
            "source": "manual",
        },
    )
    await session.commit()
    return RunSummary.model_validate(run)


def _config_has_cursor(config_json: dict[str, object] | None) -> bool:
    cfg = config_json or {}
    src = cfg.get("source")
    if isinstance(src, dict) and src.get("cursor_column"):
        return True
    tasks = cfg.get("tasks")
    if isinstance(tasks, list):
        for t in tasks:
            ts = t.get("source") if isinstance(t, dict) else None
            if isinstance(ts, dict) and ts.get("cursor_column"):
                return True
    return False


@router.post(
    "/{pipeline_id}/backfill",
    response_model=RunSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def backfill_pipeline(
    pipeline_id: UUID,
    body: RunBackfillRequest,
    ctx: WorkspaceContext = _require_runner,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> RunSummary:
    """Enqueue a backfill run over a cursor range (ADR-0039). The pipeline's
    source must declare a ``cursor_column``; otherwise this is a 400."""
    pipeline, current = await _load_pipeline_and_current(
        session, workspace_id=ctx.workspace.id, pipeline_id=pipeline_id
    )
    if not _config_has_cursor(current.config_json):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pipeline has no source cursor_column — backfill needs an incremental cursor",
        )
    run = await RunRepository(session).add_manual(
        pipeline=pipeline,
        version=current,
        triggered_by_user_id=ctx.user.id,
        result_json={
            "source": "backfill",
            "backfill": {"cursor_from": body.cursor_from, "cursor_to": body.cursor_to},
        },
    )
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="run.backfill",
        resource_type="run",
        resource_id=str(run.id),
        before=None,
        after={
            "pipeline_id": str(pipeline.id),
            "pipeline_version_id": str(current.id),
            "cursor_from": body.cursor_from,
            "cursor_to": body.cursor_to,
        },
    )
    await session.commit()
    return RunSummary.model_validate(run)


@router.post(
    "/{pipeline_id}/partitioned-backfill",
    response_model=list[RunSummary],
    status_code=status.HTTP_202_ACCEPTED,
)
async def partitioned_backfill_pipeline(
    pipeline_id: UUID,
    body: PartitionedBackfillRequest,
    ctx: WorkspaceContext = _require_runner,
    session: AsyncSession = Depends(get_session),  # noqa: B008
    audit: AuditService = Depends(get_audit_service),  # noqa: B008
) -> list[RunSummary]:
    """Split one cursor range into N parallel sub-runs (ADR-0095).

    Each consecutive boundary pair becomes an independent backfill run
    over ``(left, right]`` — the existing SKIP LOCKED queue spreads them
    across worker replicas, so a large historical load scales out with
    worker count instead of needing a distributed engine. Windows are
    half-open, so the sub-runs never overlap and their union is exactly
    ``(first, last]``. ``result_json.partition`` ties the group together
    for observability.
    """
    pipeline, current = await _load_pipeline_and_current(
        session, workspace_id=ctx.workspace.id, pipeline_id=pipeline_id
    )
    if not _config_has_cursor(current.config_json):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pipeline has no source cursor_column — backfill needs an incremental cursor",
        )
    group = str(uuid4())
    windows = list(zip(body.boundaries, body.boundaries[1:], strict=False))
    repo = RunRepository(session)
    runs = []
    for index, (left, right) in enumerate(windows):
        runs.append(
            await repo.add_manual(
                pipeline=pipeline,
                version=current,
                triggered_by_user_id=ctx.user.id,
                result_json={
                    "source": "backfill",
                    "backfill": {"cursor_from": left, "cursor_to": right},
                    "partition": {"group": group, "index": index, "of": len(windows)},
                },
            )
        )
    await audit.record(
        actor_user_id=ctx.user.id,
        workspace_id=ctx.workspace.id,
        action="run.backfill_partitioned",
        resource_type="pipeline",
        resource_id=str(pipeline.id),
        before=None,
        after={
            "pipeline_version_id": str(current.id),
            "group": group,
            "partitions": len(windows),
            "boundaries": list(body.boundaries),
            "run_ids": [str(r.id) for r in runs],
        },
    )
    await session.commit()
    return [RunSummary.model_validate(r) for r in runs]
