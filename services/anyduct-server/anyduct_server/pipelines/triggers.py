"""Pipeline → pipeline downstream-trigger links (ADR-0029).

A :class:`PipelineTrigger` row is a directed edge "when source succeeds,
enqueue target". This repository owns reading and replacing the set of
downstream targets for a source pipeline. Workspace/existence/cycle checks
live in the router (which has the pipeline repo); this layer is pure data.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.db.models import PipelineTrigger


class PipelineTriggerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_targets(self, *, source_pipeline_id: UUID) -> list[UUID]:
        """Target pipeline IDs this pipeline triggers, oldest link first."""
        result = await self._session.execute(
            select(PipelineTrigger.target_pipeline_id)
            .where(PipelineTrigger.source_pipeline_id == source_pipeline_id)
            .order_by(PipelineTrigger.created_at)
        )
        return list(result.scalars().all())

    async def set_targets(
        self, *, source_pipeline_id: UUID, target_pipeline_ids: list[UUID]
    ) -> None:
        """Replace the full downstream-target set for a source pipeline.

        Idempotent: deletes the existing edges and inserts the given set
        (deduped, self-edges dropped). Caller commits.
        """
        await self._session.execute(
            delete(PipelineTrigger).where(PipelineTrigger.source_pipeline_id == source_pipeline_id)
        )
        seen: set[UUID] = set()
        for target_id in target_pipeline_ids:
            if target_id == source_pipeline_id or target_id in seen:
                continue
            seen.add(target_id)
            self._session.add(
                PipelineTrigger(
                    source_pipeline_id=source_pipeline_id,
                    target_pipeline_id=target_id,
                )
            )
        await self._session.flush()


__all__ = ["PipelineTriggerRepository"]
