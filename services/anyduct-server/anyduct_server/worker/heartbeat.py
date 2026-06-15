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
import threading
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from anyduct_server.db.models import Run

logger = logging.getLogger(__name__)


async def heartbeat_loop(
    factory: async_sessionmaker[AsyncSession],
    run_id: UUID,
    *,
    stop_event: asyncio.Event,
    interval_seconds: float,
    cancel_event: threading.Event | None = None,
) -> None:
    """Stamp ``runs.heartbeat_at = now()`` every ``interval_seconds``.

    Returns when ``stop_event`` fires. Errors during a heartbeat are
    logged and swallowed — a transient DB hiccup must not crash an
    in-flight pipeline.

    ``cancel_event`` (Phase P, 2026-05-28): when supplied, each tick
    also reads ``runs.cancel_requested_at``; if non-null the
    threading.Event is ``set()``. The node-level graph executor checks
    this between waves and bails out cooperatively. Passing
    ``None`` keeps the loop pure-heartbeat for callers that don't care
    about cancellation (test paths, legacy non-node-level runs).
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
                # One round-trip: UPDATE-RETURNING so the cancel poll
                # piggybacks on the heartbeat write instead of adding a
                # second query per tick. Postgres returns the (now
                # updated) row; we look at cancel_requested_at to
                # decide whether to signal.
                await session.execute(
                    update(Run).where(Run.id == run_id).values(heartbeat_at=datetime.now(UTC))
                )
                if cancel_event is not None and not cancel_event.is_set():
                    result = await session.execute(
                        select(Run.cancel_requested_at).where(Run.id == run_id)
                    )
                    requested = result.scalar_one_or_none()
                    if requested is not None:
                        cancel_event.set()
                        logger.info("run %s: cancel requested at %s", run_id, requested)
                await session.commit()
        except Exception:  # log + keep going
            logger.exception("heartbeat update failed for run %s", run_id)


__all__ = ["heartbeat_loop"]
