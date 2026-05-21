"""Optional connector capability: execute a raw SQL statement (ADR-0035).

A connector that can run a standalone statement (``DELETE`` / ``TRUNCATE`` /
arbitrary DML/DDL) implements :class:`SqlExecutor`. The Pipeline uses it for
*pre-load actions* — e.g. clearing the target before a load so re-running is
idempotent (delete-then-insert). Connectors that can't (object stores, Kafka,
HTTP) simply don't implement it, and callers guard with
``isinstance(conn, SqlExecutor)`` (the Protocol is ``runtime_checkable``).

The statement is executed verbatim — it is operator-authored SQL against the
operator's own database, exactly like a source ``query``. There is no
parameter binding here.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SqlExecutor(Protocol):
    """A connector that can execute a standalone SQL statement."""

    def execute_statement(self, statement: str) -> int:
        """Execute ``statement`` and commit. Returns affected row count (or -1
        when the driver can't report one)."""
        ...


__all__ = ["SqlExecutor"]
