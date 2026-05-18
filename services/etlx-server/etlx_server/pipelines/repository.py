"""PipelineRepository — Pipelines + immutable PipelineVersion history.

The two tables are managed together: ``add`` creates both atomically;
``ensure_version`` enforces the **idempotent** version pattern (same
``config_json`` as the current ``is_current=True`` row → no new version,
no row mutation); ``delete`` relies on FK cascade for versions and
schedules.

Mutations stay in the caller's transaction — the router pairs them with
audit recording + ``session.commit`` so all the rows land together (or
not at all).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.db.models import Pipeline, PipelineVersion


class PipelineNameTakenError(Exception):
    """Raised when ``add`` / ``update_metadata`` collides with an existing name."""


_ALLOWED_METADATA_FIELDS = frozenset({"name", "description"})


class PipelineRepository:
    """Async data access for ``pipelines`` + ``pipeline_versions``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- read ----------------------------------------------------------

    async def list_for_workspace(self, *, workspace_id: UUID) -> list[Pipeline]:
        result = await self._session.execute(
            select(Pipeline).where(Pipeline.workspace_id == workspace_id).order_by(Pipeline.name)
        )
        return list(result.scalars().all())

    async def get(self, *, workspace_id: UUID, pipeline_id: UUID) -> Pipeline | None:
        result = await self._session.execute(
            select(Pipeline).where(
                Pipeline.workspace_id == workspace_id, Pipeline.id == pipeline_id
            )
        )
        return result.scalar_one_or_none()

    async def get_current_version(self, *, pipeline_id: UUID) -> PipelineVersion | None:
        result = await self._session.execute(
            select(PipelineVersion)
            .where(
                PipelineVersion.pipeline_id == pipeline_id,
                PipelineVersion.is_current.is_(True),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_versions(self, *, pipeline_id: UUID) -> list[PipelineVersion]:
        result = await self._session.execute(
            select(PipelineVersion)
            .where(PipelineVersion.pipeline_id == pipeline_id)
            .order_by(PipelineVersion.version)
        )
        return list(result.scalars().all())

    # --- mutations -----------------------------------------------------

    async def add(
        self,
        *,
        workspace_id: UUID,
        name: str,
        description: str | None,
        config_json: dict[str, Any],
        created_by_user_id: UUID | None,
    ) -> tuple[Pipeline, PipelineVersion]:
        """Insert ``Pipeline`` + initial ``PipelineVersion(version=1, is_current=True)`` atomically."""
        pipeline = Pipeline(
            workspace_id=workspace_id,
            name=name,
            description=description,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(pipeline)
        try:
            await self._session.flush()
        except IntegrityError as e:
            await self._session.rollback()
            raise PipelineNameTakenError(
                f"pipeline name {name!r} is already taken in this workspace"
            ) from e
        version = PipelineVersion(
            pipeline_id=pipeline.id,
            version=1,
            config_json=config_json,
            is_current=True,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(version)
        await self._session.flush()
        return pipeline, version

    async def update_metadata(self, pipeline: Pipeline, /, **fields: Any) -> Pipeline:
        """Apply a whitelist of metadata fields (``name`` / ``description``)."""
        unknown = set(fields) - _ALLOWED_METADATA_FIELDS
        if unknown:
            raise ValueError(f"unknown pipeline metadata fields: {sorted(unknown)}")
        for key, value in fields.items():
            setattr(pipeline, key, value)
        try:
            await self._session.flush()
        except IntegrityError as e:
            await self._session.rollback()
            raise PipelineNameTakenError("pipeline name is already taken") from e
        return pipeline

    async def ensure_version(
        self,
        pipeline: Pipeline,
        config_json: dict[str, Any],
        *,
        created_by_user_id: UUID | None,
    ) -> tuple[PipelineVersion, bool]:
        """Idempotent version creation.

        Returns ``(row, was_created)``. If the current version's
        ``config_json`` already matches, the existing row is returned
        with ``was_created=False`` — no DB write happens. Otherwise the
        previous current row is flipped to ``is_current=False`` and a
        new row with ``version = prev.version + 1`` is inserted.
        """
        current = await self.get_current_version(pipeline_id=pipeline.id)
        if current is not None and current.config_json == config_json:
            return current, False
        next_version_num = 1 if current is None else current.version + 1
        if current is not None:
            current.is_current = False
        new_row = PipelineVersion(
            pipeline_id=pipeline.id,
            version=next_version_num,
            config_json=config_json,
            is_current=True,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(new_row)
        await self._session.flush()
        return new_row, True

    async def delete(self, pipeline: Pipeline) -> None:
        """Delete the pipeline. Cascade removes its versions + schedules."""
        await self._session.delete(pipeline)
        await self._session.flush()

    @staticmethod
    def snapshot(pipeline: Pipeline, current: PipelineVersion | None) -> dict[str, Any]:
        """JSON-safe view for audit before/after."""
        return {
            "name": pipeline.name,
            "description": pipeline.description,
            "current_version": current.version if current is not None else None,
            "config_json": current.config_json if current is not None else None,
        }


__all__ = ["PipelineNameTakenError", "PipelineRepository"]
