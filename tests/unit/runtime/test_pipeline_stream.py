"""Stream-mode Pipeline tests (Step 3.2) using InMemoryStream connectors."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from etl_plugins.core.exceptions import PipelineError, TaskError
from etl_plugins.core.pipeline import Pipeline, Task
from etl_plugins.core.record import Record
from etl_plugins.core.registry import ConnectorRegistry
from etl_plugins.runtime.runner import arun_stream_pipeline_yaml
from tests.fixtures.connectors import (
    InMemoryBatchSink,
    InMemoryBatchSource,
    InMemoryStreamSink,
    InMemoryStreamSource,
)


def _records(n: int) -> list[Record]:
    return [Record(data={"i": i, "k": f"v{i}"}) for i in range(n)]


# ---------- Pipeline.arun_stream — happy path -----------------------------


def test_run_on_stream_mode_rejects() -> None:
    p = Pipeline("p", mode="stream")
    with pytest.raises(PipelineError, match="arun_stream"):
        p.run(connectors={})


@pytest.mark.asyncio
async def test_arun_stream_basic_passthrough() -> None:
    src = InMemoryStreamSource(_records(5))
    snk = InMemoryStreamSink()
    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out"},
    )
    p = Pipeline("p", mode="stream").add(task)
    result = await p.arun_stream(connectors={"s": src, "k": snk})
    assert result.success is True
    assert result.records_read == 5
    assert result.records_written == 5
    assert [t for t, _ in snk.published] == ["out"] * 5
    assert [r.data["i"] for _, r in snk.published] == [0, 1, 2, 3, 4]


@pytest.mark.asyncio
async def test_arun_stream_applies_transforms() -> None:
    src = InMemoryStreamSource(_records(3))
    snk = InMemoryStreamSink()

    def _upper_keys(rec: Record) -> Record:
        return Record(data={k.upper(): v for k, v in rec.data.items()}, metadata=rec.metadata)

    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out"},
        transforms=[_upper_keys],
    )
    p = Pipeline("p", mode="stream").add(task)
    await p.arun_stream(connectors={"s": src, "k": snk})
    assert all("I" in r.data and "K" in r.data for _, r in snk.published)


@pytest.mark.asyncio
async def test_arun_stream_transform_drops_record() -> None:
    src = InMemoryStreamSource(_records(4))
    snk = InMemoryStreamSink()

    def _drop_odd(rec: Record) -> Record | None:
        return rec if rec.data["i"] % 2 == 0 else None

    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out"},
        transforms=[_drop_odd],
    )
    p = Pipeline("p", mode="stream").add(task)
    result = await p.arun_stream(connectors={"s": src, "k": snk})
    assert result.records_read == 4
    assert result.records_written == 2
    assert [r.data["i"] for _, r in snk.published] == [0, 2]


@pytest.mark.asyncio
async def test_arun_stream_commit_called_after_each_flush() -> None:
    """Default ``commit_strategy='after_sink_flush'`` + ``max_records=1`` → 1 commit per record."""
    src = InMemoryStreamSource(_records(3))
    snk = InMemoryStreamSink()
    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out", "buffer": {"max_records": 1}},
    )
    p = Pipeline("p", mode="stream").add(task)
    await p.arun_stream(connectors={"s": src, "k": snk})
    # 3 flushes (one per record because max_records=1)
    assert snk.flush_calls == 3
    assert len(src.commits) == 3


@pytest.mark.asyncio
async def test_arun_stream_buffer_max_records_batches_flushes() -> None:
    """``max_records=5`` should produce only 1 flush+commit for 5 records (plus none extra)."""
    src = InMemoryStreamSource(_records(5))
    snk = InMemoryStreamSink()
    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out", "buffer": {"max_records": 5}},
    )
    p = Pipeline("p", mode="stream").add(task)
    await p.arun_stream(connectors={"s": src, "k": snk})
    # Exactly one buffer-triggered flush at record #5. Finally block sees pending=0 → no extra flush.
    assert snk.flush_calls == 1
    assert len(src.commits) == 1


@pytest.mark.asyncio
async def test_arun_stream_buffer_partial_final_flush() -> None:
    """3 records with max_records=5 → 0 buffer flushes during run, 1 final flush from finally."""
    src = InMemoryStreamSource(_records(3))
    snk = InMemoryStreamSink()
    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out", "buffer": {"max_records": 5}},
    )
    p = Pipeline("p", mode="stream").add(task)
    await p.arun_stream(connectors={"s": src, "k": snk})
    assert snk.flush_calls == 1
    assert len(src.commits) == 1


@pytest.mark.asyncio
async def test_arun_stream_commit_skipped_when_strategy_other() -> None:
    """Setting a non-``after_sink_flush`` strategy disables commits but still flushes."""
    src = InMemoryStreamSource(_records(2))
    snk = InMemoryStreamSink()
    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out", "buffer": {"max_records": 1}},
    )
    p = Pipeline("p", mode="stream", commit_strategy="manual").add(task)
    await p.arun_stream(connectors={"s": src, "k": snk})
    assert snk.flush_calls == 2
    assert src.commits == []  # commit_strategy != after_sink_flush → no commits


@pytest.mark.asyncio
async def test_arun_stream_stop_after_records_truncates() -> None:
    src = InMemoryStreamSource(_records(100))
    snk = InMemoryStreamSink()
    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out", "buffer": {"max_records": 1}},
    )
    p = Pipeline("p", mode="stream").add(task)
    result = await p.arun_stream(connectors={"s": src, "k": snk}, stop_after_records=7)
    assert result.records_read == 7
    assert result.records_written == 7


@pytest.mark.asyncio
async def test_arun_stream_stop_after_seconds() -> None:
    """Slow source: yields each record after a tiny sleep — stop_after_seconds should bound it."""

    class _SlowSource(InMemoryStreamSource):
        async def subscribe(self, topic, *, group_id=None, **options):  # type: ignore[no-untyped-def, override]
            for r in self._records:
                await asyncio.sleep(0.05)
                yield r

    src = _SlowSource(_records(100))
    snk = InMemoryStreamSink()
    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out", "buffer": {"max_records": 1}},
    )
    p = Pipeline("p", mode="stream").add(task)
    result = await p.arun_stream(connectors={"s": src, "k": snk}, stop_after_seconds=0.15)
    assert 0 < result.records_read <= 100
    assert result.duration_seconds <= 1.0  # generous upper bound


# ---------- Pipeline.arun_stream — validation errors ----------------------


@pytest.mark.asyncio
async def test_arun_stream_rejects_batch_mode() -> None:
    p = Pipeline("p", mode="batch")
    with pytest.raises(PipelineError, match="stream-only"):
        await p.arun_stream(connectors={})


@pytest.mark.asyncio
async def test_arun_stream_missing_topic_on_source() -> None:
    task = Task(name="t", source="s", sink="k", sink_options={"topic": "out"})
    p = Pipeline("p", mode="stream").add(task)
    with pytest.raises(TaskError, match="topic"):
        await p.arun_stream(connectors={"s": InMemoryStreamSource(), "k": InMemoryStreamSink()})


@pytest.mark.asyncio
async def test_arun_stream_missing_topic_on_sink() -> None:
    task = Task(name="t", source="s", source_options={"topic": "in"}, sink="k")
    p = Pipeline("p", mode="stream").add(task)
    with pytest.raises(TaskError, match="topic"):
        await p.arun_stream(connectors={"s": InMemoryStreamSource(), "k": InMemoryStreamSink()})


@pytest.mark.asyncio
async def test_arun_stream_rejects_batch_source() -> None:
    """A BatchSource in a stream task should fail TaskError."""
    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out"},
    )
    p = Pipeline("p", mode="stream").add(task)
    with pytest.raises(TaskError, match="StreamSource"):
        await p.arun_stream(connectors={"s": InMemoryBatchSource(), "k": InMemoryStreamSink()})


@pytest.mark.asyncio
async def test_arun_stream_rejects_batch_sink() -> None:
    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out"},
    )
    p = Pipeline("p", mode="stream").add(task)
    with pytest.raises(TaskError, match="StreamSink"):
        await p.arun_stream(connectors={"s": InMemoryStreamSource(), "k": InMemoryBatchSink()})


# ---------- arun_stream_pipeline_yaml end-to-end --------------------------


@pytest.fixture(autouse=True)
def _ensure_inmem_stream_registered() -> Iterator[None]:
    src_orig = ConnectorRegistry._registry.get("stream-test-source")
    snk_orig = ConnectorRegistry._registry.get("stream-test-sink")
    ConnectorRegistry.register("stream-test-source", replace=True)(InMemoryStreamSource)
    ConnectorRegistry.register("stream-test-sink", replace=True)(InMemoryStreamSink)
    yield
    if src_orig is None:
        ConnectorRegistry._registry.pop("stream-test-source", None)
    if snk_orig is None:
        ConnectorRegistry._registry.pop("stream-test-sink", None)


@pytest.mark.asyncio
async def test_arun_stream_pipeline_yaml_e2e(tmp_path: Path) -> None:
    pipe_yaml = tmp_path / "pipe.yaml"
    pipe_yaml.write_text(
        """\
name: e2e_stream
mode: stream
source:
  connection: src
  topic: in
sink:
  connection: snk
  topic: out
  buffer:
    max_records: 2
commit:
  strategy: after_sink_flush
"""
    )

    src = InMemoryStreamSource(_records(4))
    snk = InMemoryStreamSink()
    result = await arun_stream_pipeline_yaml(
        pipe_yaml,
        extra_connectors={"src": src, "snk": snk},
        stop_after_records=4,
    )
    assert result.success is True
    assert result.records_read == 4
    assert result.records_written == 4
    assert snk.flush_calls == 2  # buffer flushes at records 2 and 4
    assert len(src.commits) == 2
