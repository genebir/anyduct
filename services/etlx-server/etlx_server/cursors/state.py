"""``DbCursorState`` — sync :class:`etl_plugins.core.cursor.CursorState`
backed by the metadata DB.

The core ABC is sync (so the same call site works from a script + from
within ``Pipeline.run``). The metadata DB is reached through async
SQLAlchemy + asyncpg by the rest of the server, but asyncpg pools are
bound to the event loop that created them — so a sync CursorState that
internally calls into an async session bound to a *different* loop
would fail with "got Future attached to a different loop".

We sidestep that by using a **separate sync** SQLAlchemy engine with
the psycopg driver, dedicated to cursor state. The connection URL is
the same Postgres database (so DB-side ON CASCADE etc. still work);
the engine itself is independent of the FastAPI app's async engine.

Two ways to construct:

* :meth:`DbCursorState.from_url` — handed a ``postgresql://`` URL, builds
  its own engine. Best for standalone scripts.
* :meth:`__init__` — handed a pre-built :class:`Engine`. Best for the
  worker, which can keep a long-lived engine per process.

Each instance is scoped to one workspace.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from etl_plugins.core.cursor import Cursor, CursorState, CursorValue
from etlx_server.db.models import Cursor as CursorRow


class DbCursorState(CursorState):
    """Sync :class:`CursorState` backed by the metadata DB.

    Scoped to one workspace — different workspaces can use the same key
    without collision (PK is ``(workspace_id, name)``).
    """

    def __init__(self, engine: Engine, *, workspace_id: UUID) -> None:
        self._engine = engine
        self._workspace_id = workspace_id

    @classmethod
    def from_url(cls, url: str, *, workspace_id: UUID) -> DbCursorState:
        """Build a state with its own sync engine from a Postgres URL.

        ``url`` may be either the async form (``postgresql+asyncpg://...``)
        or the bare form (``postgresql://...``); the asyncpg suffix is
        swapped for the sync psycopg driver (``postgresql+psycopg://``) so
        callers can pass the same env var used by the FastAPI app without
        SQLAlchemy falling back to the psycopg2 default driver.
        """
        sync_url = url.replace("+asyncpg", "+psycopg")
        if "+" not in sync_url.split("://", 1)[0]:
            # bare postgresql:// → use psycopg (v3)
            sync_url = sync_url.replace("postgresql://", "postgresql+psycopg://", 1)
        engine = create_engine(sync_url, future=True)
        return cls(engine, workspace_id=workspace_id)

    def get(self, name: str) -> Cursor | None:
        with Session(self._engine) as session:
            row = session.execute(
                select(CursorRow).where(
                    CursorRow.workspace_id == self._workspace_id,
                    CursorRow.name == name,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return Cursor(column=row.cursor_column, value=row.cursor_value)

    def set(self, name: str, cursor: Cursor) -> None:
        wrapped = _wrap_value(cursor.value)
        stmt = (
            insert(CursorRow)
            .values(
                workspace_id=self._workspace_id,
                name=name,
                cursor_column=cursor.column,
                cursor_value=wrapped,
            )
            .on_conflict_do_update(
                index_elements=["workspace_id", "name"],
                set_={"cursor_column": cursor.column, "cursor_value": wrapped},
            )
        )
        with Session(self._engine) as session:
            session.execute(stmt)
            session.commit()

    def delete(self, name: str) -> None:
        with Session(self._engine) as session:
            session.execute(
                delete(CursorRow).where(
                    CursorRow.workspace_id == self._workspace_id,
                    CursorRow.name == name,
                )
            )
            session.commit()


def _wrap_value(value: CursorValue) -> Any:
    """JSONB-friendly representation. ``datetime`` becomes ISO-8601.

    JSONB returns strings as strings, so a datetime that goes in as ISO
    comes back as a string — the cursor abstraction's strict ``>``
    works on ISO-8601 lexicographically (chronological order is
    preserved).
    """
    if isinstance(value, datetime):
        return value.isoformat()
    return value
