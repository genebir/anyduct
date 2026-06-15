"""Bridge core observability → metadata DB (Step 9.3c).

While :class:`RunExecutor` has a pipeline running in a thread-pool
worker, the core emits structlog events and metric points through
the singletons installed in :mod:`etl_plugins.observability`. We want
those captured into ``run_logs`` and ``run_metrics`` so the UI Run
detail page actually has something to show.

Approach
--------

* :class:`RunRecorder` is created per run on the asyncio main loop.
* It registers itself in a module-level dict keyed by ``run_id`` and
  sets a :class:`contextvars.ContextVar` so the active recorder is
  discoverable from any thread that inherits the asyncio context
  (``asyncio.to_thread`` does — Python 3.11+ ``concurrent.futures``
  propagates ``contextvars.copy_context()``).
* A custom :class:`RecordingMetrics` is installed as the global
  metrics backend for the duration of the run; every ``.add`` /
  ``.record`` call enqueues a point against the active recorder.
* A structlog processor (:func:`log_processor`) is installed at
  worker startup; it inspects each event dict for ``run_id`` and, if
  it matches an active recorder, enqueues the line.
* Producers (structlog processor, recording metrics) write into
  :class:`queue.SimpleQueue` — thread-safe, lock-free from the caller
  side. The recorder flushes the queues into the metadata DB exactly
  once at :meth:`__aexit__`. A periodic drain would conflict with the
  executor's concurrent session use; the live-tail SSE endpoint will
  catch up via polling instead.

Multiple recorders can be active concurrently in the same process
(future-proofing the single-threaded ``RunWorker`` poll loop), since
the queues are scoped per ``run_id``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import queue
import threading
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from anyduct_server.db.enums import LogLevel
from anyduct_server.db.models import RunLog, RunMetric
from etl_plugins.observability.metrics import (
    Attributes,
    Counter,
    Histogram,
    Metrics,
    NoOpMetrics,
    get_metrics,
    set_metrics,
)

logger = logging.getLogger(__name__)

# A recorder is active when it sits in this map; key = ``run_id``.
_ACTIVE: dict[UUID, RunRecorder] = {}
_ACTIVE_LOCK = threading.Lock()

# Async-aware "current run id" — set on the main loop, inherited by
# ``asyncio.to_thread`` workers, used by :class:`RecordingMetrics`.
current_run_id: ContextVar[UUID | None] = ContextVar("anyduct_current_run_id", default=None)

# Cap on enqueued items per flush — keeps the final commit transaction
# bounded even if a connector goes log-crazy mid-run.
_MAX_BATCH = 2000


# --- log level mapping ------------------------------------------------------

_LEVEL_MAP: dict[str, LogLevel] = {
    "debug": LogLevel.DEBUG,
    "info": LogLevel.INFO,
    "warn": LogLevel.WARNING,
    "warning": LogLevel.WARNING,
    "error": LogLevel.ERROR,
    "critical": LogLevel.ERROR,
    "exception": LogLevel.ERROR,
}


def _coerce_level(raw: Any) -> LogLevel:
    if isinstance(raw, str):
        return _LEVEL_MAP.get(raw.lower(), LogLevel.INFO)
    return LogLevel.INFO


# --- public structlog processor --------------------------------------------


def log_processor(
    _logger: Any, method_name: str, event_dict: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Structlog processor — forward matching events into the recorder queue.

    Installed once at worker startup. Cheap when no recorders are active.

    The event is matched by the ``run_id`` field that
    :class:`etl_plugins.core.context.Context` binds on every line. Events
    without ``run_id`` (or whose id doesn't match an active recorder) flow
    through unchanged.
    """
    raw_id = event_dict.get("run_id")
    if not raw_id:
        return event_dict

    try:
        run_id = UUID(str(raw_id))
    except (ValueError, TypeError):
        return event_dict

    with _ACTIVE_LOCK:
        rec = _ACTIVE.get(run_id)
    if rec is None:
        return event_dict

    level_str = event_dict.get("level", method_name)
    message = str(event_dict.get("event", ""))
    # Phase M (2026-05-26): pull ``node_id`` out of the event_dict (set by
    # the worker via ``structlog.contextvars.bind_contextvars(node_id=...)``
    # around each node's execution and merged in by ``merge_contextvars``)
    # so the recorder can persist it as a first-class column. Removed from
    # the context dict to avoid duplicating it in ``context_json``.
    raw_node_id = event_dict.get("node_id")
    node_id = str(raw_node_id) if raw_node_id else None
    context = {
        k: v
        for k, v in event_dict.items()
        if k not in {"event", "run_id", "level", "timestamp", "node_id"}
    }
    rec.enqueue_log(
        level=_coerce_level(level_str),
        message=message,
        context=context,
        node_id=node_id,
    )
    return event_dict


# --- recording metrics backend ---------------------------------------------


class _RecordingCounter(Counter):
    def __init__(self, name: str) -> None:
        self._name = name

    def add(self, value: int = 1, attributes: Attributes | None = None) -> None:
        rec = _current_recorder()
        if rec is None:
            return
        rec.enqueue_metric(name=self._name, value=float(value), attrs=dict(attributes or {}))


class _RecordingHistogram(Histogram):
    def __init__(self, name: str) -> None:
        self._name = name

    def record(self, value: float, attributes: Attributes | None = None) -> None:
        rec = _current_recorder()
        if rec is None:
            return
        rec.enqueue_metric(name=self._name, value=float(value), attrs=dict(attributes or {}))


class RecordingMetrics(Metrics):
    """Metrics backend that pipes points into the active :class:`RunRecorder`.

    No buffering of instrument objects — each ``counter(name)`` call returns
    a new bound writer because the core never caches instruments either, so
    the allocation overhead is negligible compared to the metric work
    itself.
    """

    def counter(self, name: str, description: str = "", unit: str = "") -> Counter:
        return _RecordingCounter(name)

    def histogram(self, name: str, description: str = "", unit: str = "") -> Histogram:
        return _RecordingHistogram(name)


def _current_recorder() -> RunRecorder | None:
    run_id = current_run_id.get()
    if run_id is None:
        return None
    with _ACTIVE_LOCK:
        return _ACTIVE.get(run_id)


# --- RunRecorder ------------------------------------------------------------


class _LogEntry:
    __slots__ = ("context", "level", "message", "node_id", "ts")

    def __init__(
        self,
        *,
        level: LogLevel,
        message: str,
        context: dict[str, Any],
        node_id: str | None = None,
    ) -> None:
        self.level = level
        self.message = message
        self.context = context
        # Phase M (2026-05-26): captured separately from context_json so
        # the run-detail UI can filter by node without parsing the
        # context blob. ``None`` means "run-level log" (build, summary,
        # connector setup — anything outside a specific node's
        # execution window).
        self.node_id = node_id
        self.ts = datetime.now(UTC)


class _MetricEntry:
    __slots__ = ("attrs", "name", "ts", "value")

    def __init__(self, *, name: str, value: float, attrs: dict[str, Any]) -> None:
        self.name = name
        self.value = value
        self.attrs = attrs
        self.ts = datetime.now(UTC)


class RunRecorder:
    """Per-run capture of structlog events + metric points.

    Use as an async context manager from the executor:

    .. code-block:: python

        async with RunRecorder(factory, run.id) as recorder:
            ctx_token = current_run_id.set(run.id)
            try:
                await asyncio.to_thread(_run_pipeline_in_thread, ...)
            finally:
                current_run_id.reset(ctx_token)
    """

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        run_id: UUID,
        *,
        max_batch: int = _MAX_BATCH,
        flush_interval_seconds: float | None = None,
    ) -> None:
        """
        Parameters
        ----------
        factory
            ``async_sessionmaker`` used for every flush. Each ``factory()``
            call must yield an *independent* session — concurrent commits
            from the periodic drain and the executor's own session updates
            would otherwise step on each other (Python's ``AsyncSession``
            isn't safe for concurrent use).
        run_id
            Run this recorder belongs to. Determines which structlog events
            and metric calls get captured.
        max_batch
            Cap on items pulled from each queue per flush.
        flush_interval_seconds
            When set, runs a background asyncio task that flushes pending
            logs / metrics every N seconds so the Run-detail page can show
            them live instead of after the worker finishes. ``None`` (the
            default) flushes only once at ``__aexit__`` — fine for unit
            tests where a shared session would conflict with concurrent
            commits, and the live-tail SSE catches up via polling.
        """
        self._factory = factory
        self._run_id = run_id
        self._max_batch = max_batch
        self._flush_interval = flush_interval_seconds
        self._log_q: queue.SimpleQueue[_LogEntry] = queue.SimpleQueue()
        self._metric_q: queue.SimpleQueue[_MetricEntry] = queue.SimpleQueue()
        self._prev_metrics: Metrics | None = None
        self._stop = asyncio.Event()
        self._drain_task: asyncio.Task[None] | None = None

    # ---- producer side (thread-safe) -------------------------------------

    def enqueue_log(
        self,
        *,
        level: LogLevel,
        message: str,
        context: dict[str, Any],
        node_id: str | None = None,
    ) -> None:
        self._log_q.put(_LogEntry(level=level, message=message, context=context, node_id=node_id))

    def enqueue_metric(self, *, name: str, value: float, attrs: dict[str, Any]) -> None:
        self._metric_q.put(_MetricEntry(name=name, value=value, attrs=attrs))

    # ---- async lifecycle -------------------------------------------------

    async def __aenter__(self) -> RunRecorder:
        with _ACTIVE_LOCK:
            _ACTIVE[self._run_id] = self
        self._prev_metrics = get_metrics()
        set_metrics(RecordingMetrics())
        if self._flush_interval is not None:
            self._drain_task = asyncio.create_task(self._drain_loop())
        return self

    async def __aexit__(
        self,
        _et: type[BaseException] | None,
        _ev: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        # Drop the recorder from the active map *first* so any in-flight
        # structlog event or metric call after this point is silently
        # ignored instead of queueing for an already-finished recorder.
        with _ACTIVE_LOCK:
            _ACTIVE.pop(self._run_id, None)
        if self._prev_metrics is not None:
            set_metrics(self._prev_metrics)
        else:
            set_metrics(NoOpMetrics())
        # Stop the periodic drain (if any) before the final flush. The drain
        # task may be mid-flush — await it so we don't issue two commits
        # against the same session in production-shaped factories that
        # happen to share state (e.g. test adapters).
        self._stop.set()
        if self._drain_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._drain_task
            self._drain_task = None
        await self._flush()

    async def _drain_loop(self) -> None:
        """Background task: flush queues on a timer until ``stop`` is set.

        Skipped entirely when ``flush_interval_seconds`` is ``None``.
        """
        interval = self._flush_interval
        assert interval is not None  # mypy
        while not self._stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            if self._stop.is_set():
                # __aexit__ will do the final flush itself; bail without
                # racing it.
                return
            await self._flush()

    async def _flush(self) -> None:
        logs = _drain_queue(self._log_q, cap=self._max_batch)
        metrics = _drain_queue(self._metric_q, cap=self._max_batch)
        if not logs and not metrics:
            return
        try:
            async with self._factory() as session:
                for entry in logs:
                    session.add(
                        RunLog(
                            run_id=self._run_id,
                            ts=entry.ts,
                            level=entry.level,
                            message=entry.message,
                            node_id=entry.node_id,
                            context_json=entry.context,
                        )
                    )
                for m in metrics:
                    session.add(
                        RunMetric(
                            run_id=self._run_id,
                            name=m.name,
                            value=m.value,
                            attrs_json=m.attrs,
                            recorded_at=m.ts,
                        )
                    )
                await session.commit()
        except Exception:
            # Failing to persist run_logs must never crash the worker — the
            # underlying pipeline can still complete and report status. Log
            # to the worker's own stdlib logger so the operator sees it.
            logger.exception(
                "recorder: failed to flush %d log(s) / %d metric(s) for run %s",
                len(logs),
                len(metrics),
                self._run_id,
            )


def _drain_queue(q: queue.SimpleQueue[Any], *, cap: int) -> list[Any]:
    out: list[Any] = []
    while len(out) < cap:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            break
    return out


__all__ = ["RecordingMetrics", "RunRecorder", "current_run_id", "log_processor"]
