"""RunRepository — read-only query over runs / run_logs / run_metrics.

The metadata DB is the source of truth for what happened on a
pipeline; the worker engine (Step 9) is the only writer for these
tables, so the API surface here intentionally exposes **no** mutations.
The shape is:

* ``list_for_workspace`` — filtered + paginated history view for the
  workspace runs table in the UI.
* ``get`` — drill-down to a single run.
* ``list_logs`` / ``list_metrics`` — child collections for the run
  detail page.

Filters are kept narrow on purpose: ``status``, ``pipeline_id``,
``schedule_id``. Anything more sophisticated (date ranges, full-text
search across error messages, etc.) is a follow-up — adding it now
without a UI driving the shape would just freeze guesses.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.db.enums import RunStatus
from etlx_server.db.models import Run, RunLog, RunMetric

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500
_MAX_LOG_LIMIT = 1000


def _clamp(value: int, *, low: int, high: int) -> int:
    return max(low, min(value, high))


class RunRepository:
    """Async read-only data access for runs + their logs/metrics."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_workspace(
        self,
        *,
        workspace_id: UUID,
        status: RunStatus | None = None,
        pipeline_id: UUID | None = None,
        schedule_id: UUID | None = None,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[Run]:
        stmt = select(Run).where(Run.workspace_id == workspace_id)
        if status is not None:
            stmt = stmt.where(Run.status == status)
        if pipeline_id is not None:
            stmt = stmt.where(Run.pipeline_id == pipeline_id)
        if schedule_id is not None:
            stmt = stmt.where(Run.schedule_id == schedule_id)
        stmt = (
            stmt.order_by(Run.created_at.desc())
            .limit(_clamp(limit, low=1, high=_MAX_LIMIT))
            .offset(max(offset, 0))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get(self, *, workspace_id: UUID, run_id: UUID) -> Run | None:
        result = await self._session.execute(
            select(Run).where(
                Run.workspace_id == workspace_id,
                Run.id == run_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_logs(
        self,
        *,
        run_id: UUID,
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> list[RunLog]:
        stmt = (
            select(RunLog)
            .where(RunLog.run_id == run_id)
            .order_by(RunLog.ts)
            .limit(_clamp(limit, low=1, high=_MAX_LOG_LIMIT))
            .offset(max(offset, 0))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_metrics(self, *, run_id: UUID) -> list[RunMetric]:
        result = await self._session.execute(
            select(RunMetric).where(RunMetric.run_id == run_id).order_by(RunMetric.recorded_at)
        )
        return list(result.scalars().all())


__all__ = ["RunRepository"]
