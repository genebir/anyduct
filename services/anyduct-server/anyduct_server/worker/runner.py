"""Long-running worker loop — claim, execute, repeat.

The worker process boots once per replica, gets an
:class:`async_sessionmaker` + :class:`SecretBackend` from the caller
(typically the ``anyduct-server worker run`` CLI), and then polls until
:meth:`stop` is called.

Each iteration:

1. Open a fresh session, call :func:`claim_pending_run` inside it,
   commit. The transaction holds the row lock only for the duration of
   the claim — not the full execution.
2. If a row was claimed, hand it to :class:`RunExecutor`, which opens
   its own session for the execution + result write.
3. If the queue was empty, wait ``poll_interval`` seconds (or until
   ``stop()`` fires, whichever comes first).

The split between claim transaction and execute transaction matters:
a multi-minute pipeline must not hold a row lock the whole time
(another worker should be able to claim subsequent rows in parallel),
and a Postgres transaction held that long would also fight with
``autovacuum``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from anyduct_server.worker.claim import claim_pending_run
from anyduct_server.worker.executor import RunExecutor
from etl_plugins.config.secrets import SecretBackend

logger = logging.getLogger(__name__)


class RunWorker:
    """Single-replica poll loop."""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        backend: SecretBackend,
        *,
        worker_id: str,
        poll_interval: float = 1.0,
        log_flush_interval_seconds: float | None = None,
    ) -> None:
        self._factory = factory
        self._backend = backend
        self._worker_id = worker_id
        self._poll_interval = poll_interval
        self._log_flush_interval = log_flush_interval_seconds
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        """Drive the loop until :meth:`stop` is called.

        Returns after the in-flight iteration finishes — never mid-claim
        or mid-execute. Callers expecting timely shutdown should also
        bound their work via the underlying pipeline's timeout policy
        (added in Step 9.5).
        """
        logger.info(
            "worker %s starting (poll_interval=%.2fs)", self._worker_id, self._poll_interval
        )
        while not self._stop_event.is_set():
            try:
                run_id = await self._try_claim()
            except Exception:
                # Claim itself failed (DB hiccup, etc.). Don't crash the
                # worker — log + back off + try again.
                logger.exception("worker %s: claim failed", self._worker_id)
                run_id = None

            if run_id is not None:
                try:
                    await RunExecutor(
                        self._factory,
                        self._backend,
                        worker_id=self._worker_id,
                        log_flush_interval_seconds=self._log_flush_interval,
                    ).execute(run_id)
                except Exception:
                    # Executor swallows pipeline errors and writes them
                    # to the row. Anything reaching here means the row
                    # update itself blew up — log + keep going.
                    logger.exception(
                        "worker %s: executor crashed on run %s",
                        self._worker_id,
                        run_id,
                    )
                # Loop immediately — there may be another row waiting.
                continue

            # Empty queue: wait for either ``poll_interval`` or stop().
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)
        logger.info("worker %s stopped", self._worker_id)

    async def _try_claim(self) -> UUID | None:
        async with self._factory() as session:
            run = await claim_pending_run(session, worker_id=self._worker_id)
            if run is None:
                await session.commit()
                return None
            run_id = run.id
            # Commit the claim now so the row is visibly ``running``
            # before the (possibly long) execution starts.
            await session.commit()
            return run_id

    def stop(self) -> None:
        """Request a graceful shutdown — loop exits after current iteration."""
        self._stop_event.set()


__all__ = ["RunWorker"]
