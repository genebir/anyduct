"""Retry decorator. SPEC.md §9.1.

``@retryable`` wraps a sync or async callable with a tenacity retry policy
(exponential backoff + optional jitter) and emits observability events
(structlog warning + metrics counter) on each retry.

Example::

    @retryable(max_attempts=5, on=(ReadError, TimeoutError))
    def fetch_batch():
        ...

    @retryable(max_attempts=3, backoff="fixed", jitter=False)
    async def publish(msg):
        ...
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar, cast, overload

import structlog
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_fixed,
    wait_random_exponential,
)

from etl_plugins.observability.metrics import ERRORS_TOTAL, get_metrics

P = ParamSpec("P")
R = TypeVar("R")

ExceptionTypes = type[BaseException] | tuple[type[BaseException], ...]

_log = structlog.get_logger("etl_plugins.retry")


def _before_sleep(state: RetryCallState) -> None:
    """structlog warning + metrics counter on every retry."""
    exc = state.outcome.exception() if state.outcome is not None else None
    fn_name = state.fn.__name__ if state.fn is not None else "<unknown>"
    _log.warning(
        "retrying",
        function=fn_name,
        attempt=state.attempt_number,
        exception_type=type(exc).__name__ if exc is not None else None,
        exception=str(exc) if exc is not None else None,
    )
    get_metrics().counter(ERRORS_TOTAL, description="errors causing a retry").add(
        1, {"function": fn_name, "phase": "retry"}
    )


def _build_wait(
    backoff: str,
    initial_delay_seconds: float,
    max_delay_seconds: float,
    jitter: bool,
) -> Any:
    if backoff == "fixed":
        return wait_fixed(initial_delay_seconds)
    if backoff == "exponential":
        if jitter:
            return wait_random_exponential(multiplier=initial_delay_seconds, max=max_delay_seconds)
        return wait_exponential(multiplier=initial_delay_seconds, max=max_delay_seconds)
    raise ValueError(f"unknown backoff strategy: {backoff!r} (use 'fixed' or 'exponential')")


@overload
def retryable(
    fn: Callable[P, Awaitable[R]],
    /,
) -> Callable[P, Awaitable[R]]: ...


@overload
def retryable(
    fn: Callable[P, R],
    /,
) -> Callable[P, R]: ...


@overload
def retryable(
    *,
    max_attempts: int = ...,
    on: ExceptionTypes = ...,
    backoff: str = ...,
    initial_delay_seconds: float = ...,
    max_delay_seconds: float = ...,
    jitter: bool = ...,
) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def retryable(
    fn: Callable[..., Any] | None = None,
    /,
    *,
    max_attempts: int = 3,
    on: ExceptionTypes = Exception,
    backoff: str = "exponential",
    initial_delay_seconds: float = 1.0,
    max_delay_seconds: float = 60.0,
    jitter: bool = True,
) -> Any:
    """Wrap ``fn`` (sync or async) with a retry policy.

    Parameters
    ----------
    max_attempts
        Maximum number of attempts including the first.
    on
        Exception type(s) that trigger a retry. Others propagate immediately.
    backoff
        ``"exponential"`` (default) or ``"fixed"``.
    initial_delay_seconds
        Multiplier for exponential / delay for fixed.
    max_delay_seconds
        Cap on the wait between attempts (exponential only).
    jitter
        Add randomness to the delay (exponential only; recommended).
    """
    wait = _build_wait(backoff, initial_delay_seconds, max_delay_seconds, jitter)
    stop = stop_after_attempt(max_attempts)
    retry_cond = retry_if_exception_type(on)

    def decorator(target: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(target):
            async_retrying = AsyncRetrying(
                stop=stop,
                wait=wait,
                retry=retry_cond,
                reraise=True,
                before_sleep=_before_sleep,
            )

            @functools.wraps(target)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                async for attempt in async_retrying:
                    with attempt:
                        return await target(*args, **kwargs)
                raise RuntimeError("AsyncRetrying exited without result")

            return async_wrapper

        sync_retrying = Retrying(
            stop=stop,
            wait=wait,
            retry=retry_cond,
            reraise=True,
            before_sleep=_before_sleep,
        )

        @functools.wraps(target)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return sync_retrying(target, *args, **kwargs)

        return sync_wrapper

    # @retryable (no parens)
    if fn is not None:
        return decorator(fn)

    # @retryable(...) (with parens)
    return cast("Callable[[Callable[..., Any]], Callable[..., Any]]", decorator)
