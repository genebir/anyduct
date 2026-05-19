"""Pipeline + Task: the in-Python orchestration API. SPEC.md §4.4.

Batch execution is via the sync :meth:`Pipeline.run`. Stream execution is via
the async :meth:`Pipeline.arun_stream` (Step 3.2).
Retry, DLQ routing, and auto-metrics emit are wired in Step 3.3.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from etl_plugins.config.models import DlqConfig, RetryConfig
from etl_plugins.core.connector import (
    BatchSink,
    BatchSource,
    Connector,
    StreamSink,
    StreamSource,
)
from etl_plugins.core.context import Context
from etl_plugins.core.cursor import CursorValue
from etl_plugins.core.exceptions import PipelineError, TaskError, TransformError
from etl_plugins.core.record import Record
from etl_plugins.observability.metrics import (
    DURATION_SECONDS,
    ERRORS_TOTAL,
    RECORDS_READ_TOTAL,
    RECORDS_WRITTEN_TOTAL,
    get_metrics,
)
from etl_plugins.utils.retry import retryable

TransformFn = Callable[[Record], Record | None]
"""Transform: takes a Record, returns the (possibly modified) Record, or None to drop it."""

Hook = Callable[..., None]
"""Pipeline hook — receives positional args specific to the event."""


@dataclass
class Task:
    """One ETL task: extract → transform* → load."""

    name: str | None = None
    source: str | None = None
    query: str | None = None
    source_options: dict[str, Any] = field(default_factory=dict)
    # Cursor column for incremental reads (Step 6.1). When set, ``Pipeline.run``
    # with ``cursor_from`` / ``cursor_to`` routes through ``source.read_since``;
    # the field must exist on every emitted record's ``data``.
    cursor_column: str | None = None
    transforms: list[TransformFn] = field(default_factory=list)
    sink: str | None = None
    sink_table: str | None = None
    sink_mode: str = "append"
    sink_key_columns: list[str] | None = None
    sink_options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def extract(
        cls,
        source: str,
        query: str | None = None,
        *,
        name: str | None = None,
        cursor_column: str | None = None,
        **options: Any,
    ) -> Task:
        return cls(
            name=name,
            source=source,
            query=query,
            source_options=dict(options),
            cursor_column=cursor_column,
        )

    def transform(self, fn: TransformFn) -> Task:
        self.transforms.append(fn)
        return self

    def load(
        self,
        sink: str,
        *,
        table: str | None = None,
        mode: str = "append",
        key_columns: list[str] | None = None,
        **options: Any,
    ) -> Task:
        self.sink = sink
        self.sink_table = table
        self.sink_mode = mode
        self.sink_key_columns = key_columns
        self.sink_options = dict(options)
        return self


@dataclass
class RunResult:
    """Outcome of a single Pipeline.run."""

    run_id: str
    pipeline_name: str
    success: bool
    records_read: int = 0
    records_written: int = 0
    duration_seconds: float = 0.0
    error: BaseException | None = None
    # Max cursor value seen across all tasks during a cursored run (Step 6.1).
    # ``None`` either means the pipeline wasn't cursored or no records were
    # emitted. Callers persist this back into their CursorState for the next
    # resume.
    new_cursor: CursorValue = None


@dataclass
class Pipeline:
    """A named sequence of Tasks.

    The caller is responsible for opening/closing connector instances passed to
    ``run``. Configuration-driven instantiation arrives in Step 1.5.
    """

    name: str
    mode: str = "batch"  # batch | stream
    tasks: list[Task] = field(default_factory=list)
    commit_strategy: str = "after_sink_flush"  # used by stream runtime; see SPEC.md §5.5
    retry: RetryConfig | None = None  # if set, wrap each task with @retryable
    dlq: DlqConfig | None = None  # if set, route TransformError records to this sink
    _hooks: dict[str, list[Hook]] = field(default_factory=dict)

    def add(self, task: Task) -> Pipeline:
        self.tasks.append(task)
        return self

    def on(self, event: str, hook: Hook) -> Pipeline:
        """Register a hook. Events: pre_run, post_run, on_error, on_task_start, on_task_end."""
        self._hooks.setdefault(event, []).append(hook)
        return self

    def run(
        self,
        context: Context | None = None,
        *,
        connectors: dict[str, Connector] | None = None,
        cursor_from: CursorValue = None,
        cursor_to: CursorValue = None,
    ) -> RunResult:
        """Run the batch pipeline.

        Step 6.1 backfill parameters:

        * ``cursor_from`` — exclusive lower bound on each task's
          ``cursor_column`` (only records with value ``> cursor_from``).
          ``None`` means "no lower bound" (full backfill or first run).
        * ``cursor_to`` — inclusive upper bound (records with value
          ``<= cursor_to``). ``None`` means "no upper bound" (tail).
        * If either is set, every task must have ``cursor_column`` defined;
          a ``TaskError`` is raised otherwise. The source connector must
          implement :meth:`BatchSource.read_since`.

        ``RunResult.new_cursor`` carries the max cursor value seen across
        all tasks — persist it via ``CursorState`` for the next resume.
        """
        if self.mode != "batch":
            raise PipelineError(
                f"Pipeline.run is batch-only — for mode={self.mode!r} use arun_stream()"
            )

        cursored = cursor_from is not None or cursor_to is not None

        ctx = context or Context(pipeline_name=self.name)
        conns = connectors or {}
        start = time.monotonic()
        metrics = get_metrics()
        attrs = {"pipeline": self.name, "mode": self.mode}

        result = RunResult(run_id=ctx.run_id, pipeline_name=self.name, success=False)
        task_runner = self._run_task
        if self.retry is not None:
            task_runner = retryable(**self._retry_kwargs())(task_runner)

        self._fire("pre_run", ctx)
        try:
            for task in self.tasks:
                if cursored and not task.cursor_column:
                    raise TaskError(
                        f"Task {task.name or task.source!r}: cursor_column is required "
                        "when Pipeline.run is called with cursor_from/cursor_to."
                    )
                self._fire("on_task_start", ctx, task)
                read_count, write_count, task_max = task_runner(task, conns, cursor_from, cursor_to)
                result.records_read += read_count
                result.records_written += write_count
                if task_max is not None and (
                    result.new_cursor is None or task_max > result.new_cursor  # type: ignore[operator]
                ):
                    result.new_cursor = task_max
                metrics.counter(RECORDS_READ_TOTAL).add(read_count, attrs)
                metrics.counter(RECORDS_WRITTEN_TOTAL).add(write_count, attrs)
                self._fire("on_task_end", ctx, task, write_count)
            result.success = True
        except Exception as exc:
            result.error = exc
            metrics.counter(ERRORS_TOTAL).add(1, {**attrs, "phase": "run"})
            self._fire("on_error", ctx, exc)
            raise
        finally:
            result.duration_seconds = time.monotonic() - start
            metrics.histogram(DURATION_SECONDS).record(result.duration_seconds, attrs)
            self._fire("post_run", ctx, result)

        return result

    def _run_task(
        self,
        task: Task,
        connectors: dict[str, Connector],
        cursor_from: CursorValue = None,
        cursor_to: CursorValue = None,
    ) -> tuple[int, int, CursorValue]:
        if not task.source:
            raise TaskError(f"Task missing source: {task!r}")
        if not task.sink:
            raise TaskError(f"Task missing sink: {task!r}")

        source = connectors.get(task.source)
        sink = connectors.get(task.sink)
        if source is None:
            raise TaskError(f"No connector instance provided for source '{task.source}'")
        if sink is None:
            raise TaskError(f"No connector instance provided for sink '{task.sink}'")
        if not isinstance(source, BatchSource):
            raise TaskError(f"Source '{task.source}' is not a BatchSource")
        if not isinstance(sink, BatchSink):
            raise TaskError(f"Sink '{task.sink}' is not a BatchSink")

        records_read = 0
        new_cursor: CursorValue = None
        dlq_enabled = self.dlq is not None
        metrics = get_metrics()

        cursored = cursor_from is not None or cursor_to is not None
        cursor_col = task.cursor_column if cursored else None

        def _source_iter() -> Iterator[Record]:
            if cursor_col is not None:
                yield from source.read_since(
                    cursor_col,
                    cursor_from,
                    query=task.query,
                    **task.source_options,
                )
            else:
                yield from source.read(query=task.query, **task.source_options)

        def _read_and_transform() -> Iterator[Record]:
            nonlocal records_read, new_cursor
            for raw in _source_iter():
                # Inclusive upper bound — skip rows beyond cursor_to. We still
                # consume them from the iterator so a non-ordered source doesn't
                # block on a buffered tail; ``read_since`` contracts ordering
                # ascending, so an ordered source will see strictly increasing
                # values and won't yield anything past cursor_to once the
                # break triggers (we ``return`` below to short-circuit).
                if cursor_col is not None:
                    cv = raw.data.get(cursor_col)
                    # cv is Any (Record.data is dict[str, Any]); the
                    # caller is responsible for picking a column whose
                    # values are mutually comparable with cursor_to /
                    # new_cursor.
                    if cursor_to is not None and cv is not None and cv > cursor_to:
                        # Ordering is ascending — anything after this is also > cursor_to.
                        return
                    if cv is not None and (new_cursor is None or cv > new_cursor):
                        new_cursor = cv
                records_read += 1
                record: Record | None = raw
                try:
                    for fn in task.transforms:
                        if record is None:
                            break
                        record = fn(record)
                except Exception as exc:
                    if dlq_enabled:
                        metrics.counter(ERRORS_TOTAL).add(
                            1, {"pipeline": self.name, "phase": "transform", "routed": "dlq"}
                        )
                        self._dlq_route_batch(connectors, raw)
                        continue
                    raise TransformError(f"transform {fn!r} failed on record {raw!r}") from exc
                if record is not None:
                    yield record

        # RDBMS sinks (sqlite/postgres/mysql) require ``table`` as a
        # keyword; without it ``write`` raises ``WriteError``. The YAML
        # builder strips ``table`` from ``sink_options`` and stores it
        # on ``task.sink_table``, so we re-thread it here. (Stream sinks
        # like Kafka pull it from ``sink_options['topic']`` directly —
        # see ``_run_task_stream`` below.)
        written = sink.write(
            _read_and_transform(),
            mode=task.sink_mode,
            key_columns=task.sink_key_columns,
            table=task.sink_table,
            **task.sink_options,
        )
        return records_read, written, new_cursor

    def _fire(self, event: str, *args: Any) -> None:
        for hook in self._hooks.get(event, []):
            hook(*args)

    # ---------- internal helpers ------------------------------------------

    def _retry_kwargs(self) -> dict[str, Any]:
        """Translate ``self.retry`` (RetryConfig) into ``@retryable`` kwargs."""
        rc = self.retry
        if rc is None:
            return {}
        out: dict[str, Any] = {
            "max_attempts": rc.max_attempts,
            "backoff": rc.backoff,
            "initial_delay_seconds": rc.initial_delay_seconds,
        }
        if rc.max_delay_seconds is not None:
            out["max_delay_seconds"] = rc.max_delay_seconds
        return out

    def _dlq_route_batch(
        self,
        connectors: dict[str, Connector],
        record: Record,
    ) -> None:
        """Best-effort write the offending record to the DLQ BatchSink."""
        if self.dlq is None:
            return
        sink = connectors.get(self.dlq.connection)
        if not isinstance(sink, BatchSink):
            return
        with contextlib.suppress(Exception):
            sink.write([record], mode=self.dlq.mode)

    async def _dlq_route_stream(
        self,
        connectors: dict[str, Connector],
        record: Record,
    ) -> None:
        """Best-effort publish the offending record to the DLQ StreamSink."""
        if self.dlq is None:
            return
        sink = connectors.get(self.dlq.connection)
        if not isinstance(sink, StreamSink):
            return
        topic = self.dlq.topic or "dlq"
        with contextlib.suppress(Exception):
            await sink.publish(topic, record)

    # ---------------- stream runtime (Step 3.2) ---------------------------

    async def arun_stream(
        self,
        context: Context | None = None,
        *,
        connectors: dict[str, Connector] | None = None,
        stop_after_records: int | None = None,
        stop_after_seconds: float | None = None,
    ) -> RunResult:
        """Run a stream pipeline (mode=='stream') until a stop condition fires.

        Stop conditions (any of):
          * ``stop_after_records`` — total records consumed across all tasks
          * ``stop_after_seconds`` — wall time since the call started
          * The async iterator returned by ``source.subscribe`` is exhausted
          * The task is cancelled (``KeyboardInterrupt`` / ``CancelledError``)
        """
        if self.mode != "stream":
            raise PipelineError(f"arun_stream is stream-only — for mode={self.mode!r} use run()")

        ctx = context or Context(pipeline_name=self.name)
        conns = connectors or {}
        start = time.monotonic()
        metrics = get_metrics()
        attrs = {"pipeline": self.name, "mode": self.mode}
        result = RunResult(run_id=ctx.run_id, pipeline_name=self.name, success=False)

        self._fire("pre_run", ctx)
        try:
            for task in self.tasks:
                self._fire("on_task_start", ctx, task)
                read_count, write_count = await self._arun_stream_task(
                    task,
                    conns,
                    stop_after_records=stop_after_records,
                    stop_after_seconds=stop_after_seconds,
                    started_at=start,
                )
                result.records_read += read_count
                result.records_written += write_count
                metrics.counter(RECORDS_READ_TOTAL).add(read_count, attrs)
                metrics.counter(RECORDS_WRITTEN_TOTAL).add(write_count, attrs)
                self._fire("on_task_end", ctx, task, write_count)
            result.success = True
        except Exception as exc:
            result.error = exc
            metrics.counter(ERRORS_TOTAL).add(1, {**attrs, "phase": "run"})
            self._fire("on_error", ctx, exc)
            raise
        finally:
            result.duration_seconds = time.monotonic() - start
            metrics.histogram(DURATION_SECONDS).record(result.duration_seconds, attrs)
            self._fire("post_run", ctx, result)
        return result

    async def _arun_stream_task(
        self,
        task: Task,
        connectors: dict[str, Connector],
        *,
        stop_after_records: int | None,
        stop_after_seconds: float | None,
        started_at: float,
    ) -> tuple[int, int]:
        if not task.source:
            raise TaskError(f"Stream task missing source: {task!r}")
        if not task.sink:
            raise TaskError(f"Stream task missing sink: {task!r}")

        source = connectors.get(task.source)
        sink = connectors.get(task.sink)
        if source is None:
            raise TaskError(f"No connector instance provided for source '{task.source}'")
        if sink is None:
            raise TaskError(f"No connector instance provided for sink '{task.sink}'")
        if not isinstance(source, StreamSource):
            raise TaskError(f"Source '{task.source}' is not a StreamSource")
        if not isinstance(sink, StreamSink):
            raise TaskError(f"Sink '{task.sink}' is not a StreamSink")

        topic_in = task.source_options.get("topic") or task.query
        if not topic_in:
            raise TaskError(
                f"stream source '{task.source}' requires 'topic' (in source.topic or source.query)"
            )
        group_id = task.source_options.get("group_id")
        topic_out = task.sink_options.get("topic") or task.sink_table
        if not topic_out:
            raise TaskError(
                f"stream sink '{task.sink}' requires 'topic' (in sink.topic or sink.table)"
            )

        buffer = task.sink_options.get("buffer") or {}
        max_records = int(buffer.get("max_records", 1) or 1)
        max_seconds = float(buffer.get("max_seconds", 0.0) or 0.0)

        records_read = 0
        records_written = 0
        pending = 0
        last_flush = time.monotonic()
        dlq_enabled = self.dlq is not None
        metrics = get_metrics()

        # Optionally wrap sink.publish with a retry policy.
        publish_fn = sink.publish
        if self.retry is not None:
            publish_fn = retryable(**self._retry_kwargs())(publish_fn)

        async def _flush_and_commit() -> None:
            nonlocal pending, last_flush
            await sink.flush()
            pending = 0
            last_flush = time.monotonic()
            if self.commit_strategy == "after_sink_flush":
                with contextlib.suppress(NotImplementedError):
                    await source.commit()

        try:
            async for raw in source.subscribe(topic_in, group_id=group_id):
                records_read += 1
                record: Record | None = raw
                try:
                    for fn in task.transforms:
                        if record is None:
                            break
                        record = fn(record)
                except Exception as exc:
                    if dlq_enabled:
                        metrics.counter(ERRORS_TOTAL).add(
                            1,
                            {"pipeline": self.name, "phase": "transform", "routed": "dlq"},
                        )
                        await self._dlq_route_stream(connectors, raw)
                        continue
                    raise TransformError(f"transform {fn!r} failed on record {raw!r}") from exc

                if record is not None:
                    await publish_fn(topic_out, record)
                    records_written += 1
                    pending += 1

                if pending >= max_records or (
                    max_seconds > 0 and (time.monotonic() - last_flush) >= max_seconds
                ):
                    await _flush_and_commit()

                if stop_after_records is not None and records_read >= stop_after_records:
                    break
                if (
                    stop_after_seconds is not None
                    and (time.monotonic() - started_at) >= stop_after_seconds
                ):
                    break
        finally:
            if pending:
                with contextlib.suppress(Exception):
                    await _flush_and_commit()

        return records_read, records_written
