"""Context 테스트."""

from __future__ import annotations

from datetime import UTC, datetime

from etl_plugins.core.context import Context


def test_defaults_are_populated() -> None:
    ctx = Context()
    assert isinstance(ctx.run_id, str)
    assert len(ctx.run_id) == 32  # uuid4().hex
    assert ctx.pipeline_name is None
    assert ctx.started_at.tzinfo == UTC
    assert ctx.extras == {}
    assert ctx.logger is not None


def test_custom_values() -> None:
    started = datetime(2026, 1, 1, tzinfo=UTC)
    ctx = Context(
        run_id="my-run",
        pipeline_name="orders_to_dw",
        started_at=started,
        extras={"trigger": "manual"},
    )
    assert ctx.run_id == "my-run"
    assert ctx.pipeline_name == "orders_to_dw"
    assert ctx.started_at == started
    assert ctx.extras == {"trigger": "manual"}


def test_run_ids_are_unique() -> None:
    a, b = Context(), Context()
    assert a.run_id != b.run_id


def test_extras_default_independent() -> None:
    a = Context()
    b = Context()
    a.extras["x"] = 1
    assert "x" not in b.extras


def test_logger_is_bound_logger_with_context() -> None:
    ctx = Context(run_id="r1", pipeline_name="p1")
    # structlog BoundLogger는 _context 속성에 바인딩 정보를 보관한다
    bound = getattr(ctx.logger, "_context", {})
    assert bound.get("run_id") == "r1"
    assert bound.get("pipeline") == "p1"
