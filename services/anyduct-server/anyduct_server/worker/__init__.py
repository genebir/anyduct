"""ADR-0021 worker package — consumes the ``runs`` queue.

The ``runs`` table is the message queue itself: ``status='pending'`` rows
are claimed with ``FOR UPDATE SKIP LOCKED`` and transitioned through
``running`` to a terminal state. No separate broker exists.

Public surface:

* :class:`claim_pending_run` — atomic claim helper.
* :class:`RunExecutor` — given a claimed run, build the core Pipeline,
  execute it in a thread, and write the result back.
* :class:`RunWorker` — the long-running poll loop.

This slice (Step 9.3a) is batch-only. Stream pipelines live in their
own worker manager (Step 9.4, deferred).
"""

from anyduct_server.worker.claim import claim_pending_run
from anyduct_server.worker.executor import RunExecutor
from anyduct_server.worker.heartbeat import heartbeat_loop
from anyduct_server.worker.reaper import ZombieReaper
from anyduct_server.worker.recorder import (
    RecordingMetrics,
    RunRecorder,
    current_run_id,
    log_processor,
)
from anyduct_server.worker.runner import RunWorker
from anyduct_server.worker.stream import StreamWorker

__all__ = [
    "RecordingMetrics",
    "RunExecutor",
    "RunRecorder",
    "RunWorker",
    "StreamWorker",
    "ZombieReaper",
    "claim_pending_run",
    "current_run_id",
    "heartbeat_loop",
    "log_processor",
]
