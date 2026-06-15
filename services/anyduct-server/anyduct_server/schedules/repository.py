"""ScheduleRepository — trigger policies attached to a pipeline.

A ``Schedule`` is a thin wrapper around a cron expression (batch mode)
or a "stream-active" flag (stream mode). The repository keeps mutations
inside the caller's transaction so the router can pair them with audit
records and a single ``session.commit``.

cron expressions are validated with :mod:`croniter` — the same library
the worker engine (Step 9) will use to plan the next firing time, so
"a schedule that saves" and "a schedule that the worker actually fires"
agree by construction.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from croniter import croniter  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.db.enums import PipelineMode
from anyduct_server.db.models import Schedule


class InvalidCronError(ValueError):
    """Raised when a cron expression fails :func:`croniter.is_valid`."""


_ALLOWED_UPDATE_FIELDS = frozenset({"name", "cron_expr", "is_active", "config_overrides"})


def validate_cron_for_mode(*, mode: PipelineMode, cron_expr: str | None) -> None:
    """Enforce mode-specific cron requirements.

    * ``batch``: ``cron_expr`` is required and must parse.
    * ``stream``: ``cron_expr`` may be ``None`` (continuously active);
      if supplied, it must still be valid (re-arm schedule).
    """
    if mode == PipelineMode.BATCH and not cron_expr:
        raise InvalidCronError("batch schedules require a cron_expr")
    if cron_expr is not None and not croniter.is_valid(cron_expr):
        raise InvalidCronError(f"invalid cron expression: {cron_expr!r}")


class ScheduleRepository:
    """Async data access for ``schedules`` — scoped to a pipeline."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- read ----------------------------------------------------------

    async def list_for_pipeline(self, *, pipeline_id: UUID) -> list[Schedule]:
        result = await self._session.execute(
            select(Schedule).where(Schedule.pipeline_id == pipeline_id).order_by(Schedule.name)
        )
        return list(result.scalars().all())

    async def get(self, *, pipeline_id: UUID, schedule_id: UUID) -> Schedule | None:
        result = await self._session.execute(
            select(Schedule).where(
                Schedule.pipeline_id == pipeline_id,
                Schedule.id == schedule_id,
            )
        )
        return result.scalar_one_or_none()

    # --- mutations -----------------------------------------------------

    async def add(
        self,
        *,
        pipeline_id: UUID,
        name: str,
        mode: PipelineMode,
        cron_expr: str | None,
        is_active: bool,
        config_overrides: dict[str, Any],
        created_by_user_id: UUID | None,
    ) -> Schedule:
        validate_cron_for_mode(mode=mode, cron_expr=cron_expr)
        schedule = Schedule(
            pipeline_id=pipeline_id,
            name=name,
            mode=mode,
            cron_expr=cron_expr,
            is_active=is_active,
            config_overrides=config_overrides,
            created_by_user_id=created_by_user_id,
        )
        self._session.add(schedule)
        await self._session.flush()
        return schedule

    async def update(self, schedule: Schedule, /, **fields: Any) -> Schedule:
        unknown = set(fields) - _ALLOWED_UPDATE_FIELDS
        if unknown:
            raise ValueError(f"unknown schedule fields: {sorted(unknown)}")
        # If cron_expr is touched, revalidate against the current mode.
        if "cron_expr" in fields:
            validate_cron_for_mode(mode=schedule.mode, cron_expr=fields["cron_expr"])
        for key, value in fields.items():
            setattr(schedule, key, value)
        await self._session.flush()
        return schedule

    async def delete(self, schedule: Schedule) -> None:
        await self._session.delete(schedule)
        await self._session.flush()

    @staticmethod
    def snapshot(schedule: Schedule) -> dict[str, Any]:
        """JSON-safe view for audit before/after."""
        return {
            "name": schedule.name,
            "mode": schedule.mode.value,
            "cron_expr": schedule.cron_expr,
            "is_active": schedule.is_active,
            "config_overrides": schedule.config_overrides,
        }


__all__ = [
    "InvalidCronError",
    "ScheduleRepository",
    "validate_cron_for_mode",
]
