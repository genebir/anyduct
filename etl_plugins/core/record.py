"""Standard Record model. SPEC.md §4.2."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    """A normalized data record.

    All inputs (DB rows, Kafka messages, S3 lines, ...) are normalized into a Record.
    Connector-specific raw objects and position info (offset / lsn / sequence_number)
    are kept in ``metadata`` rather than mixed with the payload.
    """

    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    schema_version: str | None = None
