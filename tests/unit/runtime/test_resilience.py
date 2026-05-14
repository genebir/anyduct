"""Retry / DLQ / auto-metrics tests for Pipeline.run + arun_stream [Step 3.3]."""

from __future__ import annotations

from typing import Any

import pytest

from etl_plugins.config.models import DlqConfig, RetryConfig
from etl_plugins.core.exceptions import TransformError
from etl_plugins.core.pipeline import Pipeline, Task
from etl_plugins.core.record import Record
from etl_plugins.observability.metrics import (
    DURATION_SECONDS,
    ERRORS_TOTAL,
    RECORDS_READ_TOTAL,
    RECORDS_WRITTEN_TOTAL,
    Attributes,
    Counter,
    Histogram,
    Metrics,
    NoOpCounter,
    NoOpHistogram,
    reset_metrics,
    set_metrics,
)
from tests.fixtures.connectors import (
    InMemoryBatchSink,
    InMemoryBatchSource,
    InMemoryStreamSink,
    InMemoryStreamSource,
)

# ---------- recording metrics backend -------------------------------------


class _RecordingCounter(Counter):
    def __init__(self) -> None:
        self.calls: list[tuple[int, dict]] = []

    def add(self, value: int = 1, attributes: Attributes | None = None) -> None:
        self.calls.append((value, dict(attributes or {})))


class _RecordingHistogram(Histogram):
    def __init__(self) -> None:
        self.calls: list[tuple[float, dict]] = []

    def record(self, value: float, attributes: Attributes | None = None) -> None:
        self.calls.append((value, dict(attributes or {})))


class _RecordingMetrics(Metrics):
    def __init__(self) -> None:
        self.counters: dict[str, _RecordingCounter] = {}
        self.histograms: dict[str, _RecordingHistogram] = {}

    def counter(self, name: str, description: str = "", unit: str = "") -> Counter:
        return self.counters.setdefault(name, _RecordingCounter())

    def histogram(self, name: str, description: str = "", unit: str = "") -> Histogram:
        return self.histograms.setdefault(name, _RecordingHistogram())


@pytest.fixture
def recording_metrics() -> Any:
    m = _RecordingMetrics()
    set_metrics(m)
    yield m
    reset_metrics()


# ---------- auto-metrics for batch run ------------------------------------


def test_batch_run_emits_records_and_duration_metrics(
    recording_metrics: _RecordingMetrics, sample_records: list[Record]
) -> None:
    src = InMemoryBatchSource(sample_records)
    snk = InMemoryBatchSink()
    task = Task(name="t", source="s", sink="k")
    p = Pipeline("p").add(task)
    src.connect()
    snk.connect()
    p.run(connectors={"s": src, "k": snk})

    read_counter = recording_metrics.counters[RECORDS_READ_TOTAL]
    written_counter = recording_metrics.counters[RECORDS_WRITTEN_TOTAL]
    dur_hist = recording_metrics.histograms[DURATION_SECONDS]
    assert sum(v for v, _ in read_counter.calls) == len(sample_records)
    assert sum(v for v, _ in written_counter.calls) == len(sample_records)
    assert len(dur_hist.calls) == 1
    assert dur_hist.calls[0][0] >= 0.0
    # all metric points carry pipeline + mode attributes
    for _v, attrs in (*read_counter.calls, *written_counter.calls):
        assert attrs["pipeline"] == "p"
        assert attrs["mode"] == "batch"


def test_batch_run_emits_errors_metric_on_failure() -> None:
    """A failing transform without DLQ should bump the errors counter once."""
    recording = _RecordingMetrics()
    set_metrics(recording)
    try:
        src = InMemoryBatchSource([Record(data={"x": 1})])
        snk = InMemoryBatchSink()

        def _boom(_: Record) -> Record:
            raise RuntimeError("nope")

        task = Task(name="t", source="s", sink="k", transforms=[_boom])
        p = Pipeline("p").add(task)
        src.connect()
        snk.connect()
        with pytest.raises(TransformError):
            p.run(connectors={"s": src, "k": snk})
        assert ERRORS_TOTAL in recording.counters
        assert len(recording.counters[ERRORS_TOTAL].calls) >= 1
    finally:
        reset_metrics()


# ---------- retry on batch ------------------------------------------------


def test_batch_retry_retries_task_on_transient_failure() -> None:
    """A flaky source raises once then succeeds — retry should make the task succeed."""
    attempts: list[int] = []

    class _FlakySource(InMemoryBatchSource):
        def read(self, query=None, *, chunk_size=10_000, **opts):  # type: ignore[no-untyped-def, override]
            attempts.append(1)
            if len(attempts) < 2:
                raise RuntimeError("transient")
            yield from self._records

    src = _FlakySource([Record(data={"k": 1})])
    snk = InMemoryBatchSink()
    task = Task(name="t", source="s", sink="k")
    p = Pipeline(
        "p",
        retry=RetryConfig(max_attempts=3, backoff="fixed", initial_delay_seconds=0.0),
    ).add(task)
    src.connect()
    snk.connect()
    result = p.run(connectors={"s": src, "k": snk})
    assert result.success is True
    assert len(attempts) == 2
    assert len(snk.records) == 1


def test_batch_retry_exhausts_and_reraises() -> None:
    """If max_attempts is exceeded the original exception propagates."""

    class _AlwaysFails(InMemoryBatchSource):
        def read(self, query=None, *, chunk_size=10_000, **opts):  # type: ignore[no-untyped-def, override]
            raise RuntimeError("permanent")
            yield  # pragma: no cover - make this a generator

    src = _AlwaysFails([])
    snk = InMemoryBatchSink()
    task = Task(name="t", source="s", sink="k")
    p = Pipeline(
        "p",
        retry=RetryConfig(max_attempts=2, backoff="fixed", initial_delay_seconds=0.0),
    ).add(task)
    src.connect()
    snk.connect()
    with pytest.raises(RuntimeError, match="permanent"):
        p.run(connectors={"s": src, "k": snk})


# ---------- DLQ on batch --------------------------------------------------


def test_batch_dlq_routes_failed_transform_records_and_continues() -> None:
    """Records that fail a transform should land in the DLQ sink, not the main sink."""
    src = InMemoryBatchSource(
        [
            Record(data={"i": 1, "bad": False}),
            Record(data={"i": 2, "bad": True}),
            Record(data={"i": 3, "bad": False}),
        ]
    )
    snk = InMemoryBatchSink()
    dlq = InMemoryBatchSink()

    def _no_bad(rec: Record) -> Record:
        if rec.data.get("bad"):
            raise ValueError("bad row")
        return rec

    task = Task(name="t", source="s", sink="k", transforms=[_no_bad])
    p = Pipeline(
        "p",
        dlq=DlqConfig(connection="d", mode="append"),
    ).add(task)
    src.connect()
    snk.connect()
    dlq.connect()
    result = p.run(connectors={"s": src, "k": snk, "d": dlq})
    assert result.success is True
    assert [r.data["i"] for r in snk.records] == [1, 3]
    assert [r.data["i"] for r in dlq.records] == [2]


def test_batch_no_dlq_means_transform_failure_propagates() -> None:
    """Without DLQ configured, the first transform failure raises TransformError."""
    src = InMemoryBatchSource([Record(data={"x": 1})])
    snk = InMemoryBatchSink()

    def _boom(_: Record) -> Record:
        raise ValueError("nope")

    task = Task(name="t", source="s", sink="k", transforms=[_boom])
    p = Pipeline("p").add(task)
    src.connect()
    snk.connect()
    with pytest.raises(TransformError):
        p.run(connectors={"s": src, "k": snk})


# ---------- DLQ + retry interaction ---------------------------------------


def test_batch_dlq_with_retry() -> None:
    """DLQ should still receive bad records when retry is enabled (each retry sees DLQ working)."""
    src = InMemoryBatchSource(
        [Record(data={"i": 1, "bad": False}), Record(data={"i": 2, "bad": True})]
    )
    snk = InMemoryBatchSink()
    dlq = InMemoryBatchSink()

    def _no_bad(rec: Record) -> Record:
        if rec.data.get("bad"):
            raise ValueError("bad")
        return rec

    task = Task(name="t", source="s", sink="k", transforms=[_no_bad])
    p = Pipeline(
        "p",
        retry=RetryConfig(max_attempts=3, backoff="fixed", initial_delay_seconds=0.0),
        dlq=DlqConfig(connection="d"),
    ).add(task)
    src.connect()
    snk.connect()
    dlq.connect()
    p.run(connectors={"s": src, "k": snk, "d": dlq})
    assert [r.data["i"] for r in dlq.records] == [2]


# ---------- stream metrics + DLQ ------------------------------------------


@pytest.mark.asyncio
async def test_stream_run_emits_metrics(recording_metrics: _RecordingMetrics) -> None:
    src = InMemoryStreamSource([Record(data={"i": i}) for i in range(4)])
    snk = InMemoryStreamSink()
    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out", "buffer": {"max_records": 1}},
    )
    p = Pipeline("p", mode="stream").add(task)
    src.connect()
    snk.connect()
    await p.arun_stream(connectors={"s": src, "k": snk}, stop_after_records=4)
    assert sum(v for v, _ in recording_metrics.counters[RECORDS_READ_TOTAL].calls) == 4
    assert sum(v for v, _ in recording_metrics.counters[RECORDS_WRITTEN_TOTAL].calls) == 4
    assert len(recording_metrics.histograms[DURATION_SECONDS].calls) == 1


@pytest.mark.asyncio
async def test_stream_dlq_routes_bad_records() -> None:
    src = InMemoryStreamSource(
        [
            Record(data={"i": 1, "bad": False}),
            Record(data={"i": 2, "bad": True}),
            Record(data={"i": 3, "bad": False}),
        ]
    )
    snk = InMemoryStreamSink()
    dlq = InMemoryStreamSink()

    def _no_bad(rec: Record) -> Record:
        if rec.data.get("bad"):
            raise ValueError("bad")
        return rec

    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out", "buffer": {"max_records": 1}},
        transforms=[_no_bad],
    )
    p = Pipeline(
        "p",
        mode="stream",
        dlq=DlqConfig(connection="d", topic="dlq_out"),
    ).add(task)
    src.connect()
    snk.connect()
    dlq.connect()
    await p.arun_stream(connectors={"s": src, "k": snk, "d": dlq}, stop_after_records=3)
    assert [r.data["i"] for _, r in snk.published] == [1, 3]
    assert [(t, r.data["i"]) for t, r in dlq.published] == [("dlq_out", 2)]


# ---------- stream retry on publish ---------------------------------------


@pytest.mark.asyncio
async def test_stream_retry_wraps_publish() -> None:
    """A flaky sink.publish should retry, then succeed."""
    src = InMemoryStreamSource([Record(data={"k": 1})])

    class _FlakySink(InMemoryStreamSink):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def publish(self, topic, record, key=None):  # type: ignore[no-untyped-def, override]
            self.calls += 1
            if self.calls < 2:
                raise RuntimeError("transient publish")
            await super().publish(topic, record, key)

    snk = _FlakySink()
    task = Task(
        name="t",
        source="s",
        source_options={"topic": "in"},
        sink="k",
        sink_options={"topic": "out", "buffer": {"max_records": 1}},
    )
    p = Pipeline(
        "p",
        mode="stream",
        retry=RetryConfig(max_attempts=3, backoff="fixed", initial_delay_seconds=0.0),
    ).add(task)
    src.connect()
    snk.connect()
    result = await p.arun_stream(connectors={"s": src, "k": snk}, stop_after_records=1)
    assert result.success is True
    assert snk.calls == 2
    assert len(snk.published) == 1


# ---------- NoOpCounter / NoOpHistogram default safety --------------------


def test_default_metrics_backend_is_noop() -> None:
    reset_metrics()
    from etl_plugins.observability.metrics import get_metrics

    m = get_metrics()
    assert isinstance(m.counter("x"), NoOpCounter)
    assert isinstance(m.histogram("y"), NoOpHistogram)
