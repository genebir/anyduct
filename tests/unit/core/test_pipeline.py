"""Pipeline / Task / RunResult 테스트."""

from __future__ import annotations

import pytest

from etl_plugins.core.context import Context
from etl_plugins.core.exceptions import PipelineError, TaskError, TransformError
from etl_plugins.core.pipeline import Pipeline, RunResult, Task
from etl_plugins.core.record import Record

from .conftest import InMemoryBatchSink, InMemoryBatchSource

# ---------- Task builder ----------


def test_task_extract_returns_task() -> None:
    t = Task.extract("pg", "SELECT 1")
    assert t.source == "pg"
    assert t.query == "SELECT 1"


def test_task_chaining_returns_same_task() -> None:
    t = (
        Task.extract("pg", "SELECT 1")
        .transform(lambda r: r)
        .load("sf", table="X", mode="upsert", key_columns=["id"])
    )
    assert t.source == "pg"
    assert t.sink == "sf"
    assert t.sink_table == "X"
    assert t.sink_mode == "upsert"
    assert t.sink_key_columns == ["id"]
    assert len(t.transforms) == 1


def test_task_source_options_captured() -> None:
    t = Task.extract("pg", "SELECT *", chunk_size=500, ssl=True)
    assert t.source_options == {"chunk_size": 500, "ssl": True}


def test_task_load_options_captured() -> None:
    t = Task.extract("pg", "SELECT *").load("sf", table="X", custom_opt=42)
    assert t.sink_options == {"custom_opt": 42}


# ---------- Pipeline.run ----------


def test_run_no_transforms(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
    sample_records: list[Record],
) -> None:
    pipeline = Pipeline("p1").add(Task.extract("src", "q").load("snk", table="T"))
    result = pipeline.run(connectors={"src": in_memory_source, "snk": in_memory_sink})

    assert isinstance(result, RunResult)
    assert result.success is True
    assert result.records_read == 3
    assert result.records_written == 3
    assert in_memory_sink.records == sample_records
    assert in_memory_sink.last_mode == "append"


def test_run_with_mapping_transform(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    def upper_name(r: Record) -> Record:
        return Record(data={**r.data, "name": r.data["name"].upper()}, metadata=r.metadata)

    Pipeline("p").add(Task.extract("src").transform(upper_name).load("snk")).run(
        connectors={"src": in_memory_source, "snk": in_memory_sink}
    )

    assert [r.data["name"] for r in in_memory_sink.records] == ["ALICE", "BOB", "CAROL"]


def test_run_with_filter_transform(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    def only_even(r: Record) -> Record | None:
        return r if r.data["id"] % 2 == 0 else None

    result = (
        Pipeline("p")
        .add(Task.extract("src").transform(only_even).load("snk"))
        .run(connectors={"src": in_memory_source, "snk": in_memory_sink})
    )

    assert result.records_read == 3
    assert result.records_written == 1
    assert in_memory_sink.records[0].data["id"] == 2


def test_run_propagates_query_to_source(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    Pipeline("p").add(Task.extract("src", "SELECT 42").load("snk")).run(
        connectors={"src": in_memory_source, "snk": in_memory_sink}
    )
    assert in_memory_source.last_query == "SELECT 42"


def test_run_propagates_mode_and_key_columns(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    Pipeline("p").add(Task.extract("src").load("snk", mode="upsert", key_columns=["id"])).run(
        connectors={"src": in_memory_source, "snk": in_memory_sink}
    )
    assert in_memory_sink.last_mode == "upsert"
    assert in_memory_sink.last_key_columns == ["id"]


def test_run_creates_default_context_when_none(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    result = (
        Pipeline("p")
        .add(Task.extract("src").load("snk"))
        .run(connectors={"src": in_memory_source, "snk": in_memory_sink})
    )
    assert result.run_id  # 비어있지 않은 uuid


def test_run_uses_supplied_context(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    ctx = Context(run_id="fixed-id", pipeline_name="p")
    result = (
        Pipeline("p")
        .add(Task.extract("src").load("snk"))
        .run(ctx, connectors={"src": in_memory_source, "snk": in_memory_sink})
    )
    assert result.run_id == "fixed-id"


# ---------- error paths ----------


def test_run_unknown_source_connector_raises(
    in_memory_sink: InMemoryBatchSink,
) -> None:
    pipeline = Pipeline("p").add(Task.extract("missing").load("snk"))
    with pytest.raises(TaskError, match="source 'missing'"):
        pipeline.run(connectors={"snk": in_memory_sink})


def test_run_unknown_sink_connector_raises(
    in_memory_source: InMemoryBatchSource,
) -> None:
    pipeline = Pipeline("p").add(Task.extract("src").load("missing"))
    with pytest.raises(TaskError, match="sink 'missing'"):
        pipeline.run(connectors={"src": in_memory_source})


def test_run_task_without_source_raises(in_memory_sink: InMemoryBatchSink) -> None:
    pipeline = Pipeline("p").add(Task(sink="snk"))
    with pytest.raises(TaskError, match="missing source"):
        pipeline.run(connectors={"snk": in_memory_sink})


def test_run_task_without_sink_raises(in_memory_source: InMemoryBatchSource) -> None:
    pipeline = Pipeline("p").add(Task(source="src"))
    with pytest.raises(TaskError, match="missing sink"):
        pipeline.run(connectors={"src": in_memory_source})


def test_run_source_typed_as_sink_raises(in_memory_sink: InMemoryBatchSink) -> None:
    # sink만 두 개 — 첫 번째를 source로 쓰면 BatchSource가 아니므로 실패
    pipeline = Pipeline("p").add(Task.extract("snk1").load("snk2"))
    with pytest.raises(TaskError, match="not a BatchSource"):
        pipeline.run(connectors={"snk1": in_memory_sink, "snk2": InMemoryBatchSink()})


def test_run_sink_typed_as_source_raises(in_memory_source: InMemoryBatchSource) -> None:
    pipeline = Pipeline("p").add(Task.extract("src1").load("src2"))
    with pytest.raises(TaskError, match="not a BatchSink"):
        pipeline.run(connectors={"src1": in_memory_source, "src2": InMemoryBatchSource()})


def test_transform_error_wrapped(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    def bad(r: Record) -> Record:
        raise ValueError("nope")

    pipeline = Pipeline("p").add(Task.extract("src").transform(bad).load("snk"))
    with pytest.raises(TransformError) as info:
        pipeline.run(connectors={"src": in_memory_source, "snk": in_memory_sink})
    assert isinstance(info.value.__cause__, ValueError)


def test_stream_mode_run_routes_to_arun_stream() -> None:
    p = Pipeline("p", mode="stream")
    with pytest.raises(PipelineError, match="arun_stream"):
        p.run(connectors={})


# ---------- hooks ----------


def test_hooks_fire_in_order(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    calls: list[str] = []
    pipeline = (
        Pipeline("p")
        .add(Task.extract("src").load("snk"))
        .on("pre_run", lambda ctx: calls.append("pre"))
        .on("on_task_start", lambda ctx, t: calls.append(f"task_start:{t.source}"))
        .on("on_task_end", lambda ctx, t, n: calls.append(f"task_end:{n}"))
        .on("post_run", lambda ctx, r: calls.append(f"post:{r.success}"))
    )
    pipeline.run(connectors={"src": in_memory_source, "snk": in_memory_sink})
    assert calls == ["pre", "task_start:src", "task_end:3", "post:True"]


def test_on_error_hook_fires_and_exception_propagates(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    seen: list[BaseException] = []

    def bad(r: Record) -> Record:
        raise RuntimeError("kaboom")

    pipeline = (
        Pipeline("p")
        .add(Task.extract("src").transform(bad).load("snk"))
        .on("on_error", lambda ctx, exc: seen.append(exc))
    )

    with pytest.raises(TransformError):
        pipeline.run(connectors={"src": in_memory_source, "snk": in_memory_sink})

    assert len(seen) == 1
    assert isinstance(seen[0], TransformError)


def test_post_run_hook_runs_even_on_error(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    captured: list[RunResult] = []

    def bad(_: Record) -> Record:
        raise RuntimeError("x")

    pipeline = (
        Pipeline("p")
        .add(Task.extract("src").transform(bad).load("snk"))
        .on("post_run", lambda ctx, r: captured.append(r))
    )

    with pytest.raises(TransformError):
        pipeline.run(connectors={"src": in_memory_source, "snk": in_memory_sink})

    assert len(captured) == 1
    assert captured[0].success is False
    assert isinstance(captured[0].error, TransformError)


def test_duration_recorded(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    result = (
        Pipeline("p")
        .add(Task.extract("src").load("snk"))
        .run(connectors={"src": in_memory_source, "snk": in_memory_sink})
    )
    assert result.duration_seconds >= 0


# ---------- Pipeline.run cursor backfill (Step 6.1) ----------


def test_task_extract_captures_cursor_column() -> None:
    t = Task.extract("pg", "SELECT id, ts FROM t", cursor_column="ts")
    assert t.cursor_column == "ts"
    # cursor_column must not leak into source_options.
    assert "cursor_column" not in t.source_options


def test_run_without_cursor_uses_regular_read(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
    sample_records: list[Record],
) -> None:
    """Backwards compat: a pipeline without cursor params behaves exactly
    like before — cursor_column on the task is ignored."""
    result = (
        Pipeline("p")
        .add(Task.extract("src", "q", cursor_column="id").load("snk", table="T"))
        .run(connectors={"src": in_memory_source, "snk": in_memory_sink})
    )
    assert result.records_read == len(sample_records)
    assert result.new_cursor is None  # not a cursored run


def test_run_with_cursor_from_filters_strictly(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    """``cursor_from=1`` returns ids 2 and 3 only; id 1 is excluded."""
    result = (
        Pipeline("p")
        .add(Task.extract("src", "q", cursor_column="id").load("snk", table="T"))
        .run(
            connectors={"src": in_memory_source, "snk": in_memory_sink},
            cursor_from=1,
        )
    )
    assert result.records_read == 2
    assert sorted(r.data["id"] for r in in_memory_sink.records) == [2, 3]
    assert result.new_cursor == 3


def test_run_with_cursor_to_caps_upper_bound(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    """``cursor_to=2`` keeps ids 1 and 2 (inclusive); 3 is excluded."""
    result = (
        Pipeline("p")
        .add(Task.extract("src", "q", cursor_column="id").load("snk", table="T"))
        .run(
            connectors={"src": in_memory_source, "snk": in_memory_sink},
            cursor_to=2,
        )
    )
    assert sorted(r.data["id"] for r in in_memory_sink.records) == [1, 2]
    assert result.new_cursor == 2


def test_run_with_cursor_window(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    """``cursor_from=1, cursor_to=2`` → only id 2 (exclusive lower, inclusive upper)."""
    result = (
        Pipeline("p")
        .add(Task.extract("src", "q", cursor_column="id").load("snk", table="T"))
        .run(
            connectors={"src": in_memory_source, "snk": in_memory_sink},
            cursor_from=1,
            cursor_to=2,
        )
    )
    assert [r.data["id"] for r in in_memory_sink.records] == [2]
    assert result.new_cursor == 2


def test_run_cursor_requires_cursor_column_on_task(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    """A cursored run with no ``cursor_column`` on the task is a config error."""
    with pytest.raises(TaskError, match="cursor_column"):
        Pipeline("p").add(Task.extract("src", "q").load("snk", table="T")).run(
            connectors={"src": in_memory_source, "snk": in_memory_sink},
            cursor_from=0,
        )


def test_run_two_batch_resume_is_idempotent(
    in_memory_source: InMemoryBatchSource,
    in_memory_sink: InMemoryBatchSink,
) -> None:
    """First run reads everything from cursor_from=0; second resumes from
    result.new_cursor and yields nothing — no overlap, no rewind."""
    pipeline = Pipeline("p").add(
        Task.extract("src", "q", cursor_column="id").load("snk", table="T")
    )
    first = pipeline.run(
        connectors={"src": in_memory_source, "snk": in_memory_sink},
        cursor_from=0,
    )
    assert first.records_read == 3
    assert first.new_cursor == 3
    in_memory_sink.records.clear()
    second = pipeline.run(
        connectors={"src": in_memory_source, "snk": in_memory_sink},
        cursor_from=first.new_cursor,
    )
    assert second.records_read == 0
    assert second.new_cursor is None
    assert in_memory_sink.records == []


def test_run_cursor_tracks_max_across_tasks(
    sample_records: list[Record],
    in_memory_sink: InMemoryBatchSink,
) -> None:
    """Two tasks, two sources — RunResult.new_cursor is the overall max."""
    src1 = InMemoryBatchSource(sample_records)
    src2 = InMemoryBatchSource(
        [
            Record(data={"id": 10}),
            Record(data={"id": 20}),
        ]
    )
    pipeline = (
        Pipeline("p")
        .add(Task.extract("src1", "q", cursor_column="id").load("snk", table="T1"))
        .add(Task.extract("src2", "q", cursor_column="id").load("snk", table="T2"))
    )
    result = pipeline.run(
        connectors={"src1": src1, "src2": src2, "snk": in_memory_sink},
        cursor_from=0,
    )
    assert result.new_cursor == 20
