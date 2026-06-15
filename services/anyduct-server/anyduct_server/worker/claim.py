"""Atomic claim of the next pending run.

ADR-0021: ``runs`` is the queue. The claim is a single statement —

    SELECT * FROM runs
     WHERE status = 'pending' AND scheduled_at <= now()
     ORDER BY scheduled_at, created_at
     LIMIT 1
     FOR UPDATE SKIP LOCKED

— followed by an UPDATE on the same row transitioning it to
``running`` and stamping the worker identity + heartbeat. The ``SKIP
LOCKED`` clause is what lets N workers poll the same table without
double-claiming; each one walks past rows already held in another
worker's transaction.

The two operations live in a single transaction the caller supplies,
so the claim is atomic with the state transition. If anything in the
caller's transaction later fails before commit, the row falls back
into ``pending`` cleanly.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.db.enums import RunStatus
from anyduct_server.db.models import Run


async def claim_pending_run(
    session: AsyncSession,
    *,
    worker_id: str,
) -> Run | None:
    """Claim the oldest eligible pending run, or return ``None`` if the queue is empty.

    On success, the returned :class:`Run` row already has
    ``status=running``, ``worker_id``, ``started_at``, and
    ``heartbeat_at`` set — the caller's next responsibility is to
    execute the pipeline and write the terminal result.

    Concurrency note: this opens a row-level lock with ``SKIP LOCKED``.
    Other workers polling at the same instant will receive ``None`` (if
    only this row was eligible) or a *different* row (if multiple were
    eligible) — never the same row.
    """
    stmt = (
        select(Run)
        .where(Run.status == RunStatus.PENDING)
        .where(Run.scheduled_at <= func.now())
        .order_by(Run.scheduled_at, Run.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    run = result.scalar_one_or_none()
    if run is None:
        return None

    now = datetime.now(UTC)
    run.status = RunStatus.RUNNING
    run.worker_id = worker_id
    run.started_at = now
    run.heartbeat_at = now
    await session.flush()
    return run


__all__ = ["claim_pending_run"]
