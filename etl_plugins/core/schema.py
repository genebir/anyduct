"""Lightweight schema description for Records.

Intentionally minimal in Step 1.4. Step 1.7+ may extend with type coercion,
nested/array types, and pyarrow interop.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Field(BaseModel):
    """A named, typed column in a Schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    type: str  # int64 | float64 | string | bool | timestamp | bytes | ...
    nullable: bool = True


class Schema(BaseModel):
    """A collection of Fields describing record shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fields: tuple[Field, ...]

    def column_names(self) -> list[str]:
        return [f.name for f in self.fields]

    def field_by_name(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)
