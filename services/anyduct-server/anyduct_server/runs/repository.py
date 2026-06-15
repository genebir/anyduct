"""RunRepository — runs / run_logs / run_metrics access.

The metadata DB is the source of truth for what happened on a
pipeline; the worker engine (Step 9) updates ``status`` /
``started_at`` / ``records_*`` / etc., so the API surface here is
**read-mostly**. The only writes are the two "queue a row" helpers
used by Step 8.6 action endpoints:

* :meth:`add_manual` — UI button "run now" creates a pending row.
* :meth:`add_retry` — UI button "retry this run" clones a failed/
  cancelled row as a new pending one.

Neither helper modifies an existing run row — the worker is still the
only writer for state transitions. Filters on the listing endpoint are
kept narrow on purpose: ``status``, ``pipeline_id``, ``schedule_id``.
Anything more sophisticated (date ranges, full-text search across
error messages, etc.) is a follow-up — adding it now without a UI
driving the shape would just freeze guesses.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anyduct_server.db.enums import RunStatus
from anyduct_server.db.models import Pipeline, PipelineVersion, Run, RunLog, RunMetric

# Statuses a run must be in for ``add_retry`` to accept it. ``pending`` /
# ``running`` would be a "run it twice" mistake; ``succeeded`` already did
# its job and rerunning is a fresh ``trigger``, not a retry.
_RETRYABLE_STATUSES: frozenset[RunStatus] = frozenset({RunStatus.FAILED, RunStatus.CANCELLED})
# A run is cancel-eligible while it can still be stopped — pending (hasn't
# started so we can flip it immediately) or running (worker will land the
# cancel at the next node boundary). Anything else is already terminal.
_CANCELLABLE_STATUSES: frozenset[RunStatus] = frozenset({RunStatus.PENDING, RunStatus.RUNNING})


class RunNotRetryableError(Exception):
    """Raised by :meth:`RunRepository.add_retry` when the source run isn't terminal-failed."""


class RunNotCancellableError(Exception):
    """Raised by :meth:`RunRepository.request_cancel` when the run is already terminal
    (succeeded/failed/cancelled) and there's nothing left to stop."""


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
        node_id: str | None = None,
    ) -> list[RunLog]:
        """List run_logs, optionally filtered to one node's execution.

        ``node_id`` semantics (Phase M, 2026-05-26):
            * ``None``      — no filter, return all logs.
            * ``"__run__"`` — only run-level logs (RunLog.node_id IS NULL):
                              build / connector setup / summary.
            * any string    — only logs from that graph node's execution.
        Uses the ``ix_run_logs_run_node_ts`` partial-style index so the
        filtered query is a single index range scan even on long runs.
        """
        stmt = select(RunLog).where(RunLog.run_id == run_id)
        if node_id == "__run__":
            stmt = stmt.where(RunLog.node_id.is_(None))
        elif node_id is not None:
            stmt = stmt.where(RunLog.node_id == node_id)
        stmt = (
            stmt.order_by(RunLog.ts)
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

    # --- mutations (Step 8.6 action endpoints) ----------------------------

    async def add_manual(
        self,
        *,
        pipeline: Pipeline,
        version: PipelineVersion,
        triggered_by_user_id: UUID,
        result_json: dict[str, Any] | None = None,
    ) -> Run:
        """Enqueue a pending Run row from a manual trigger.

        ``schedule_id`` is left ``NULL`` to mark this as user-driven (the
        worker's claim loop uses the same query regardless). The Run row
        is the message queue itself per ADR-0021.
        """
        run = Run(
            workspace_id=pipeline.workspace_id,
            pipeline_id=pipeline.id,
            pipeline_version_id=version.id,
            schedule_id=None,
            triggered_by_user_id=triggered_by_user_id,
            status=RunStatus.PENDING,
            result_json=result_json or {},
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def add_retry(
        self,
        original: Run,
        *,
        triggered_by_user_id: UUID,
    ) -> Run:
        """Clone a failed/cancelled run as a fresh pending one.

        Same ``pipeline_version_id`` and ``schedule_id`` as the original
        — the retry is "do the same thing again", not "do the latest
        version". ``result_json.retry_of`` carries the link back so
        forensics can trace the lineage; if the original was itself a
        retry the chain stays explicit (we don't transitively unwrap).

        Raises :class:`RunNotRetryableError` if ``original.status`` isn't
        in {failed, cancelled}.
        """
        if original.status not in _RETRYABLE_STATUSES:
            raise RunNotRetryableError(
                f"run status {original.status.value!r} is not retryable; "
                f"only failed/cancelled runs may be retried"
            )
        run = Run(
            workspace_id=original.workspace_id,
            pipeline_id=original.pipeline_id,
            pipeline_version_id=original.pipeline_version_id,
            schedule_id=original.schedule_id,
            triggered_by_user_id=triggered_by_user_id,
            status=RunStatus.PENDING,
            result_json={"retry_of": str(original.id)},
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def request_cancel(self, run: Run) -> Run:
        """Mark ``run`` for cancellation. Returns the updated row.

        Phase P (2026-05-28). Two-track semantics depending on the
        run's current status:

          * **pending** — the worker hasn't claimed it yet. Flip status
            to ``cancelled`` immediately with ``finished_at = now`` so
            the row never becomes a claim target. (The worker's
            ``FOR UPDATE SKIP LOCKED`` query already filters to
            ``status = pending``, so a race where the worker claims
            in-between this read and write is harmless: the worker
            wins, status goes to running, and the next cancel call
            takes the running path below.)

          * **running** — record ``cancel_requested_at = now`` only.
            The worker's heartbeat loop polls this column each tick;
            when set, it signals a threading.Event the node-level
            graph executor checks between waves. Final status (CANCELLED)
            is written by the worker, not here — keeps the worker the
            single writer for run-status transitions.

        Raises :class:`RunNotCancellableError` for terminal rows
        (succeeded / failed / cancelled — nothing to stop).
        """
        if run.status not in _CANCELLABLE_STATUSES:
            raise RunNotCancellableError(
                f"run status {run.status.value!r} is terminal; "
                f"only pending/running runs may be cancelled"
            )
        now = datetime.now(UTC)
        run.cancel_requested_at = now
        if run.status == RunStatus.PENDING:
            # Pre-claim cancel — flip the row to terminal directly so
            # the worker never picks it up. No worker collision: the
            # claim query filters by status=PENDING; this transitions
            # it out of that set.
            run.status = RunStatus.CANCELLED
            run.finished_at = now
        await self._session.flush()
        return run


__all__ = [
    "RunNotCancellableError",
    "RunNotRetryableError",
    "RunRepository",
]
