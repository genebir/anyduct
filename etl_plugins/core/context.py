"""Per-run Context (identifiers, timing, logger). SPEC.md §4.4 / §9.2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog


@dataclass
class Context:
    """Carried through Pipeline → Task → Connector for a single run.

    ``logger`` is a structlog logger pre-bound with ``run_id`` and ``pipeline``,
    so every log line emitted from inside the run is correlated.
    """

    run_id: str = field(default_factory=lambda: uuid4().hex)
    pipeline_name: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    extras: dict[str, Any] = field(default_factory=dict)
    logger: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.logger = structlog.get_logger().bind(
            run_id=self.run_id,
            pipeline=self.pipeline_name,
        )
