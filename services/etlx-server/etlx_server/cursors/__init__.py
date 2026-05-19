"""DB-backed CursorState for incremental syncs (Step 6.1, ADR-0024).

The core ships :class:`etl_plugins.core.cursor.CursorState` as an ABC with
:class:`InMemoryCursorState` + :class:`FileCursorState` batteries-included
implementations. The server adds a third — :class:`DbCursorState` — backed
by the ``cursors`` metadata table, scoped to one workspace so different
workspaces can use the same key without collision.

Typical usage (worker / API):

    from etlx_server.cursors import DbCursorState

    state = DbCursorState(session_factory, workspace_id=ws.id)
    prior = state.get(f"pipeline:{p.id}:task:{t.id}")
    result = pipeline.run(cursor_from=prior.value if prior else None)
    if result.new_cursor is not None:
        state.update(f"pipeline:{p.id}:task:{t.id}", "id", result.new_cursor)
"""

from etlx_server.cursors.repository import CursorRepository
from etlx_server.cursors.state import DbCursorState

__all__ = ["CursorRepository", "DbCursorState"]
