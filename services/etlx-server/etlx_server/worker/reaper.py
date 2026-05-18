"""Zombie run reaper.

Worker crashes (OOM, segfault, SIGKILL, machine power-off) leave Run
rows stranded in ``running`` with a stale ``heartbeat_at`` — no other
:class:`RunWorker` will ever pick them up because the claim query
filters on ``pending``. The reaper is a separate background process
that sweeps these rows on a schedule and transitions them to
``failed`` so the user (or a future Step 9.5 auto-retry policy) can
deal with them via the existing :class:`RunRepository.add_retry`
path.

Why ``failed`` and not "back to ``pending``"?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Auto-resubmitting is appealing but it's how you build a thundering
herd: a poison row that kills every worker that touches it would
loop forever, taking out the whole fleet. Marking it ``failed`` with
``error_class='ZombieReaped'`` makes the cause visible in the UI
(Step 10) and forces a deliberate retry — same pattern as any other
failure mode.

The reaper is its own CLI subcommand (``etlx-server reaper run``)
because operationally it has different scaling characteristics from
the executor worker (one reaper is enough for many workers) and a
separate process means a reaper crash can't take a worker with it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from etlx_server.db.enums import RunStatus
from etlx_server.db.models import Run

logger = logging.getLogger(__name__)


_MAX_ERROR_MESSAGE_LEN = 2000


class ZombieReaper:
    """Periodically mark stale ``running`` rows as ``failed``."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        heartbeat_timeout_seconds: float = 60.0,
        scan_interval_seconds: float = 30.0,
        batch_limit: int = 100,
    ) -> None:
        self._factory = factory
        self._heartbeat_timeout = heartbeat_timeout_seconds
        self._scan_interval = scan_interval_seconds
        self._batch_limit = batch_limit
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        """Drive the reap loop until :meth:`stop` is called."""
        logger.info(
            "reaper starting (timeout=%.1fs interval=%.1fs)",
            self._heartbeat_timeout,
            self._scan_interval,
        )
        while not self._stop_event.is_set():
            try:
                reaped = await self.reap_once()
            except Exception:
                logger.exception("reaper: scan failed")
                reaped = 0
            if reaped:
                logger.info("reaper: marked %d stale run(s) as failed", reaped)
            # Wait for the next scan or stop, whichever comes first.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._scan_interval)
        logger.info("reaper stopped")

    async def reap_once(self) -> int:
        """One scan + transition cycle. Returns number of rows reaped.

        Uses ``FOR UPDATE SKIP LOCKED`` so multiple reaper replicas
        (rare, but supported) don't double-reap the same row. A worker
        that just stamped a heartbeat will hold the row lock briefly,
        which keeps the reaper from racing the heartbeat itself.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=self._heartbeat_timeout)
        async with self._factory() as session:
            stmt = (
                select(Run)
                .where(Run.status == RunStatus.RUNNING)
                .where(Run.heartbeat_at.is_not(None))
                .where(Run.heartbeat_at < cutoff)
                .order_by(Run.heartbeat_at)
                .limit(self._batch_limit)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            zombies = list(result.scalars().all())
            if not zombies:
                return 0
            now = datetime.now(UTC)
            for run in zombies:
                # Compute the message *before* mutating so the heartbeat
                # value reported is the stale one, not "now".
                stale_seconds = (now - run.heartbeat_at).total_seconds()  # type: ignore[operator]
                msg = (
                    f"worker {run.worker_id!r} stopped heartbeating "
                    f"{stale_seconds:.0f}s ago (threshold {self._heartbeat_timeout:.0f}s)"
                )
                run.status = RunStatus.FAILED
                run.error_class = "ZombieReaped"
                run.error_message = msg[:_MAX_ERROR_MESSAGE_LEN]
                run.finished_at = now
            await session.commit()
            return len(zombies)

    def stop(self) -> None:
        """Request a graceful shutdown — loop exits after current scan."""
        self._stop_event.set()


__all__ = ["ZombieReaper"]
