"""Lineage run-event emission (ADR-0024 / ADR-0036).

``Pipeline.run`` emits a :class:`LineageEvent` at START and at COMPLETE/FAIL,
carrying the run id, job (pipeline) name, and the input/output :class:`AssetKey`
s derived from the pipeline's sources/sinks. A pluggable :class:`LineageEmitter`
decides what to do with it — the default is a no-op (zero overhead, no extra
dependency), the service plugs in a DB-backed emitter to persist lineage, and an
OpenLineage/Marquez wire backend is a later slice.

Same global get/set pattern as :mod:`etl_plugins.observability.metrics`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from etl_plugins.core.asset import AssetKey

# Event lifecycle states, mirroring OpenLineage RunEvent eventType.
START = "START"
COMPLETE = "COMPLETE"
FAIL = "FAIL"
ABORT = "ABORT"


@dataclass(frozen=True)
class LineageEvent:
    """One lineage run event. Inputs/outputs are the assets the run reads/writes
    (derived-first, ADR-0036)."""

    event_type: str  # START | COMPLETE | FAIL | ABORT
    run_id: str
    job_name: str
    inputs: tuple[AssetKey, ...] = ()
    outputs: tuple[AssetKey, ...] = ()
    records_read: int | None = None
    records_written: int | None = None
    error: str | None = None


class LineageEmitter(ABC):
    """Receives lineage run events. Implementations: NoOp (default), Collecting
    (tests / in-process capture), DB-backed (service), OpenLineage (later)."""

    @abstractmethod
    def emit(self, event: LineageEvent) -> None: ...


class NoOpLineageEmitter(LineageEmitter):
    """Default — drops events. Keeps the core dependency-free and zero-cost."""

    def emit(self, event: LineageEvent) -> None:
        return None


class CollectingLineageEmitter(LineageEmitter):
    """Captures events in a list. Used by tests and by callers (the worker) that
    want to drain events and persist them after a run."""

    def __init__(self) -> None:
        self.events: list[LineageEvent] = []

    def emit(self, event: LineageEvent) -> None:
        self.events.append(event)


_emitter: LineageEmitter = NoOpLineageEmitter()


def get_lineage_emitter() -> LineageEmitter:
    """Return the active lineage emitter (default: no-op)."""
    return _emitter


def set_lineage_emitter(emitter: LineageEmitter) -> None:
    """Install the process-wide lineage emitter."""
    global _emitter
    _emitter = emitter


def reset_lineage_emitter() -> None:
    """Restore the default no-op emitter (test teardown)."""
    global _emitter
    _emitter = NoOpLineageEmitter()


__all__ = [
    "ABORT",
    "COMPLETE",
    "FAIL",
    "START",
    "CollectingLineageEmitter",
    "LineageEmitter",
    "LineageEvent",
    "NoOpLineageEmitter",
    "get_lineage_emitter",
    "reset_lineage_emitter",
    "set_lineage_emitter",
]
