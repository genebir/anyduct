"""Cron scheduler (Step 9.2).

Periodically inspects active batch :class:`Schedule` rows and creates
pending :class:`Run` rows when their next firing time has arrived.
Workers (Step 9.3a) then claim those rows via the existing queue
machinery — the scheduler is just an enqueuer, never an executor.

We derive each schedule's "last firing" from
``max(runs.scheduled_at) where runs.schedule_id = <id>`` rather than
storing it on the ``schedules`` row directly; no new migration is
needed for this slice. Stream schedules (``cron_expr IS NULL``) are
skipped — they're managed by the stream worker (Step 9.4, deferred).
"""

from anyduct_server.scheduler.scheduler import Scheduler

__all__ = ["Scheduler"]
