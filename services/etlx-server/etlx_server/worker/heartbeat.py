"""Heartbeat helper for in-flight runs.

While :class:`RunExecutor` has a pipeline running in
:func:`asyncio.to_thread`, we want the row's ``heartbeat_at`` to keep
ticking — without it, a stuck pipeline (or a worker that lost its DB
connection but is still walking the thread pool) would never be
distinguishable from a successful long run. The :class:`ZombieReaper`
relies on this stamp to know which ``running`` rows have actually
been abandoned.

The loop runs in the asyncio main thread (the pipeline runs in a
worker thread via ``to_thread``), with its own fresh
:class:`AsyncSession` per update so it never shares a transaction
with anything else.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from etlx_server.db.models import Run

logger = logging.getLogger(__name__)


async def heartbeat_loop(
    factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    *,
    stop_event: asyncio.Event,
    interval_seconds: float,
) -> None:
    """Stamp ``runs.heartbeat_at = now()`` every ``interval_seconds``.

    Returns when ``stop_event`` fires. Errors during a heartbeat are
    logged and swallowed — a transient DB hiccup must not crash an
    in-flight pipeline.
    """
    while not stop_event.is_set():
        # Wait first, so the executor's own claim stamp (set just
        # before this loop starts) is the first heartbeat and we don't
        # double-stamp it back-to-back.
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            return
        except TimeoutError:
            pass

        try:
            async with factory() as session:
                await session.execute(
                    update(Run).where(Run.id == run_id).values(heartbeat_at=datetime.now(UTC))
                )
                await session.commit()
        except Exception:  # log + keep going
            logger.exception("heartbeat update failed for run %s", run_id)


__all__ = ["heartbeat_loop"]
