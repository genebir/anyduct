"""Task-orchestration DAG: depends_on + topological execution (ADR-0028)."""

from __future__ import annotations

import pytest

from etl_plugins.core.exceptions import PipelineError
from etl_plugins.core.pipeline import (
    TASK_FAILED,
    TASK_SKIPPED,
    TASK_SUCCESS,
    TASK_UPSTREAM_FAILED,
    BranchRule,
    Pipeline,
    Task,
)
from etl_plugins.core.record import Record

from .conftest import InMemoryBatchSink, InMemoryBatchSource


def _task(name: str, *, depends_on: list[str] | None = None) -> Task:
    return Task(
        name=name,
        source="src",
        sink="snk",
        sink_table="t",
        depends_on=depends_on or [],
    )


def test_no_depends_on_preserves_list_order() -> None:
    p = Pipeline("p")
    p.add(_task("c"))
    p.add(_task("a"))
    p.add(_task("b"))
    # Without any depends_on the original order is untouched (backward compat).
    assert [t.name for t in p._ordered_tasks()] == ["c", "a", "b"]


def test_topological_order_respects_dependencies() -> None:
    p = Pipeline("p")
    p.add(_task("load", depends_on=["transform"]))
    p.add(_task("transform", depends_on=["extract"]))
    p.add(_task("extract"))
    assert [t.name for t in p._ordered_tasks()] == ["extract", "transform", "load"]


def test_stable_tiebreak_among_ready_tasks() -> None:
    # Two roots + a join. Roots keep original list order; join runs last.
    p = Pipeline("p")
    p.add(_task("join", depends_on=["a", "b"]))
    p.add(_task("a"))
    p.add(_task("b"))
    assert [t.name for t in p._ordered_tasks()] == ["a", "b", "join"]


def test_cycle_detected() -> None:
    p = Pipeline("p")
    p.add(_task("a", depends_on=["b"]))
    p.add(_task("b", depends_on=["a"]))
    with pytest.raises(PipelineError, match="cycle"):
        p._ordered_tasks()


def test_self_dependency_rejected() -> None:
    p = Pipeline("p")
    p.add(_task("a", depends_on=["a"]))
    with pytest.raises(PipelineError, match="depends on itself"):
        p._ordered_tasks()


def test_missing_dependency_rejected() -> None:
    p = Pipeline("p")
    p.add(_task("a", depends_on=["ghost"]))
    with pytest.raises(PipelineError, match="unknown task 'ghost'"):
        p._ordered_tasks()


def test_unnamed_task_with_deps_rejected() -> None:
    p = Pipeline("p")
    p.add(Task(source="src", sink="snk", sink_table="t"))  # no name
    p.add(_task("b", depends_on=["a"]))
    with pytest.raises(PipelineError, match="non-empty 'name'"):
        p._ordered_tasks()


def test_duplicate_task_name_rejected() -> None:
    p = Pipeline("p")
    p.add(_task("dup", depends_on=["x"]))
    p.add(_task("dup"))
    p.add(_task("x"))
    with pytest.raises(PipelineError, match="duplicate task name"):
        p._ordered_tasks()


def test_run_executes_tasks_in_dependency_order() -> None:
    """End-to-end: a downstream task sees rows the upstream task wrote."""
    # Shared store: extract writes into `mid`, load reads from `mid`.
    seed = InMemoryBatchSource([Record(data={"id": 1}), Record(data={"id": 2})])
    mid_store = InMemoryBatchSink()
    final = InMemoryBatchSink()

    order: list[str] = []
    p = Pipeline("p")
    p.add(Task(name="extract", source="seed", sink="mid", sink_table="m"))
    p.add(Task(name="load", source="seed", sink="final", sink_table="f", depends_on=["extract"]))
    p.on("on_task_start", lambda ctx, task: order.append(task.name or ""))

    result = p.run(connectors={"seed": seed, "mid": mid_store, "final": final})
    assert result.success is True
    assert order == ["extract", "load"]


# ---------- branching + trigger rules + states (ADR-0028 P1.3) ----------


class _FailingSink(InMemoryBatchSink):
    def write(self, records, *, mode="append", key_columns=None, **options):  # type: ignore[no-untyped-def]
        list(records)  # drain the generator so source side-effects run
        raise RuntimeError("boom")


def _src(n: int = 2) -> InMemoryBatchSource:
    return InMemoryBatchSource([Record(data={"id": i}) for i in range(n)])


def _t(name: str, sink: str, *, depends_on=None, **kw) -> Task:  # type: ignore[no-untyped-def]
    return Task(
        name=name, source="src", sink=sink, sink_table="t", depends_on=depends_on or [], **kw
    )


def test_branch_selects_downstream_and_skips_rest() -> None:
    big, small, b = InMemoryBatchSink(), InMemoryBatchSink(), InMemoryBatchSink()
    p = Pipeline("p")
    p.add(
        _t(
            "branch",
            "b",
            branch=[BranchRule("records_written > 1", ["big"]), BranchRule(None, ["small"])],
        )
    )
    p.add(_t("big", "big", depends_on=["branch"]))
    p.add(_t("small", "small", depends_on=["branch"]))

    result = p.run(connectors={"src": _src(2), "b": b, "big": big, "small": small})
    assert result.success is True
    assert result.task_states == {
        "branch": TASK_SUCCESS,
        "big": TASK_SUCCESS,
        "small": TASK_SKIPPED,
    }
    assert len(big.records) == 2
    assert small.records == []


def test_branch_default_rule_when_no_match() -> None:
    big, small, b = InMemoryBatchSink(), InMemoryBatchSink(), InMemoryBatchSink()
    p = Pipeline("p")
    p.add(
        _t(
            "branch",
            "b",
            branch=[BranchRule("records_written > 5", ["big"]), BranchRule(None, ["small"])],
        )
    )
    p.add(_t("big", "big", depends_on=["branch"]))
    p.add(_t("small", "small", depends_on=["branch"]))

    result = p.run(connectors={"src": _src(2), "b": b, "big": big, "small": small})
    assert result.task_states["big"] == TASK_SKIPPED
    assert result.task_states["small"] == TASK_SUCCESS


def test_failed_task_marks_downstream_upstream_failed_and_raises() -> None:
    p = Pipeline("p")
    p.add(_t("a", "bad"))
    p.add(_t("b", "ok", depends_on=["a"]))
    with pytest.raises(RuntimeError, match="boom"):
        p.run(connectors={"src": _src(), "bad": _FailingSink(), "ok": InMemoryBatchSink()})


def test_failed_task_states_recorded() -> None:
    p = Pipeline("p")
    p.add(_t("a", "bad"))
    p.add(_t("b", "ok", depends_on=["a"]))
    captured: dict[str, str] = {}
    p.on("post_run", lambda ctx, res: captured.update(res.task_states))
    with pytest.raises(RuntimeError):
        p.run(connectors={"src": _src(), "bad": _FailingSink(), "ok": InMemoryBatchSink()})
    assert captured == {"a": TASK_FAILED, "b": TASK_UPSTREAM_FAILED}


def test_failed_task_error_and_records_captured() -> None:
    """A failed DAG task records its error in ``result.task_errors`` and each
    succeeded task records its counts in ``result.task_records`` — both feed the
    per-step DAG monitoring view (ADR-0099, 2026-06-19)."""
    p = Pipeline("p")
    p.add(_t("root", "ok"))
    p.add(_t("bad", "bad", depends_on=["root"]))
    captured: dict[str, object] = {}
    p.on(
        "post_run",
        lambda ctx, res: captured.update(
            task_errors=dict(res.task_errors), task_records=dict(res.task_records)
        ),
    )
    with pytest.raises(RuntimeError):
        p.run(connectors={"src": _src(3), "ok": InMemoryBatchSink(), "bad": _FailingSink()})
    errors = captured["task_errors"]
    assert "bad" in errors  # type: ignore[operator]
    assert errors["bad"][0]  # error_class is a non-empty string  # type: ignore[index]
    # The successful root recorded its (read, written) counts.
    assert captured["task_records"]["root"][0] == 3  # type: ignore[index]


def test_independent_branch_runs_after_sibling_fails() -> None:
    ok_sink, b = InMemoryBatchSink(), InMemoryBatchSink()
    # A DAG with a shared root and two independent children; one fails, the
    # sibling must still run (failure isolated to its own branch).
    p = Pipeline("p")
    p.add(_t("root", "b"))
    p.add(_t("fails", "bad", depends_on=["root"]))
    p.add(_t("sibling", "ok", depends_on=["root"]))
    captured: dict[str, str] = {}
    p.on("post_run", lambda ctx, res: captured.update(res.task_states))
    with pytest.raises(RuntimeError):
        p.run(connectors={"src": _src(3), "b": b, "bad": _FailingSink(), "ok": ok_sink})
    assert captured["fails"] == TASK_FAILED
    assert captured["sibling"] == TASK_SUCCESS
    assert len(ok_sink.records) == 3


def test_trigger_rule_all_done_runs_despite_upstream_failure() -> None:
    ok = InMemoryBatchSink()
    p = Pipeline("p")
    p.add(_t("a", "bad"))
    p.add(_t("cleanup", "ok", depends_on=["a"], trigger_rule="all_done"))
    captured: dict[str, str] = {}
    p.on("post_run", lambda ctx, res: captured.update(res.task_states))
    with pytest.raises(RuntimeError):
        p.run(connectors={"src": _src(2), "bad": _FailingSink(), "ok": ok})
    # all_done: cleanup runs even though upstream failed.
    assert captured["a"] == TASK_FAILED
    assert captured["cleanup"] == TASK_SUCCESS
    assert len(ok.records) == 2


def test_trigger_rule_none_failed_skips_on_failure() -> None:
    p = Pipeline("p")
    p.add(_t("a", "bad"))
    p.add(_t("b", "ok", depends_on=["a"], trigger_rule="none_failed"))
    captured: dict[str, str] = {}
    p.on("post_run", lambda ctx, res: captured.update(res.task_states))
    with pytest.raises(RuntimeError):
        p.run(connectors={"src": _src(), "bad": _FailingSink(), "ok": InMemoryBatchSink()})
    assert captured["b"] == TASK_UPSTREAM_FAILED


def test_skip_propagates_to_descendants() -> None:
    big, small, leaf, b = (InMemoryBatchSink() for _ in range(4))
    p = Pipeline("p")
    p.add(_t("branch", "b", branch=[BranchRule(None, ["small"])]))  # always picks small
    p.add(_t("big", "big", depends_on=["branch"]))
    p.add(_t("leaf", "leaf", depends_on=["big"]))  # descendant of skipped `big`
    p.add(_t("small", "small", depends_on=["branch"]))
    captured: dict[str, str] = {}
    p.on("post_run", lambda ctx, res: captured.update(res.task_states))

    p.run(connectors={"src": _src(), "b": b, "big": big, "leaf": leaf, "small": small})
    assert captured["big"] == TASK_SKIPPED
    # leaf's only upstream (big) was skipped → all_success skips leaf too.
    assert captured["leaf"] == TASK_SKIPPED
    assert captured["small"] == TASK_SUCCESS
