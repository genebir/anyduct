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

from etlx_server.worker.claim import claim_pending_run
from etlx_server.worker.executor import RunExecutor
from etlx_server.worker.heartbeat import heartbeat_loop
from etlx_server.worker.reaper import ZombieReaper
from etlx_server.worker.recorder import (
    RecordingMetrics,
    RunRecorder,
    current_run_id,
    log_processor,
)
from etlx_server.worker.runner import RunWorker
from etlx_server.worker.stream import StreamWorker

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
