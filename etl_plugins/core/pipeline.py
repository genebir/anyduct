"""Pipeline + Task: the in-Python orchestration API. SPEC.md §4.4.

Step 1.4 covers batch execution only. Stream execution (StreamSource/StreamSink),
retry, DLQ, and YAML-based pipeline building arrive in Step 3.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from etl_plugins.core.connector import BatchSink, BatchSource, Connector
from etl_plugins.core.context import Context
from etl_plugins.core.exceptions import PipelineError, TaskError, TransformError
from etl_plugins.core.record import Record

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
        **options: Any,
    ) -> Task:
        return cls(name=name, source=source, query=query, source_options=dict(options))

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


@dataclass
class Pipeline:
    """A named sequence of Tasks.

    The caller is responsible for opening/closing connector instances passed to
    ``run``. Configuration-driven instantiation arrives in Step 1.5.
    """

    name: str
    mode: str = "batch"  # batch | stream — only "batch" is implemented in Step 1.4.
    tasks: list[Task] = field(default_factory=list)
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
    ) -> RunResult:
        if self.mode != "batch":
            raise PipelineError(
                f"Pipeline mode '{self.mode}' not implemented yet — "
                f"stream pipelines arrive in Step 3."
            )

        ctx = context or Context(pipeline_name=self.name)
        conns = connectors or {}
        start = time.monotonic()

        result = RunResult(run_id=ctx.run_id, pipeline_name=self.name, success=False)

        self._fire("pre_run", ctx)
        try:
            for task in self.tasks:
                self._fire("on_task_start", ctx, task)
                read_count, write_count = self._run_task(task, conns)
                result.records_read += read_count
                result.records_written += write_count
                self._fire("on_task_end", ctx, task, write_count)
            result.success = True
        except Exception as exc:
            result.error = exc
            self._fire("on_error", ctx, exc)
            raise
        finally:
            result.duration_seconds = time.monotonic() - start
            self._fire("post_run", ctx, result)

        return result

    def _run_task(
        self,
        task: Task,
        connectors: dict[str, Connector],
    ) -> tuple[int, int]:
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

        def _read_and_transform() -> Iterator[Record]:
            nonlocal records_read
            for raw in source.read(query=task.query, **task.source_options):
                records_read += 1
                record: Record | None = raw
                for fn in task.transforms:
                    if record is None:
                        break
                    try:
                        record = fn(record)
                    except Exception as exc:
                        raise TransformError(f"transform {fn!r} failed on record {raw!r}") from exc
                if record is not None:
                    yield record

        written = sink.write(
            _read_and_transform(),
            mode=task.sink_mode,
            key_columns=task.sink_key_columns,
            **task.sink_options,
        )
        return records_read, written

    def _fire(self, event: str, *args: Any) -> None:
        for hook in self._hooks.get(event, []):
            hook(*args)
