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

from etl_plugins.observability.metrics import (
    Attributes,
    Counter,
    Histogram,
    Metrics,
    NoOpMetrics,
    get_metrics,
    set_metrics,
)
from etlx_server.db.enums import LogLevel
from etlx_server.db.models import RunLog, RunMetric

logger = logging.getLogger(__name__)

# A recorder is active when it sits in this map; key = ``run_id``.
_ACTIVE: dict[UUID, RunRecorder] = {}
_ACTIVE_LOCK = threading.Lock()

# Async-aware "current run id" — set on the main loop, inherited by
# ``asyncio.to_thread`` workers, used by :class:`RecordingMetrics`.
current_run_id: ContextVar[UUID | None] = ContextVar("etlx_current_run_id", default=None)

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
    context = {
        k: v for k, v in event_dict.items() if k not in {"event", "run_id", "level", "timestamp"}
    }
    rec.enqueue_log(
        level=_coerce_level(level_str),
        message=message,
        context=context,
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
    __slots__ = ("context", "level", "message", "ts")

    def __init__(self, *, level: LogLevel, message: str, context: dict[str, Any]) -> None:
        self.level = level
        self.message = message
        self.context = context
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
    ) -> None:
        self._factory = factory
        self._run_id = run_id
        self._max_batch = max_batch
        self._log_q: queue.SimpleQueue[_LogEntry] = queue.SimpleQueue()
        self._metric_q: queue.SimpleQueue[_MetricEntry] = queue.SimpleQueue()
        self._prev_metrics: Metrics | None = None

    # ---- producer side (thread-safe) -------------------------------------

    def enqueue_log(self, *, level: LogLevel, message: str, context: dict[str, Any]) -> None:
        self._log_q.put(_LogEntry(level=level, message=message, context=context))

    def enqueue_metric(self, *, name: str, value: float, attrs: dict[str, Any]) -> None:
        self._metric_q.put(_MetricEntry(name=name, value=value, attrs=attrs))

    # ---- async lifecycle -------------------------------------------------

    async def __aenter__(self) -> RunRecorder:
        with _ACTIVE_LOCK:
            _ACTIVE[self._run_id] = self
        self._prev_metrics = get_metrics()
        set_metrics(RecordingMetrics())
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
        # Final (and only) flush. Producers stop here; no concurrent
        # session use to worry about.
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
