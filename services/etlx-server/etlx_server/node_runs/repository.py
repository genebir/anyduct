"""``node_runs`` queue operations (ADR-0041, Phase H1).

The caller supplies the :class:`AsyncSession` / transaction; these methods never
commit. :meth:`claim_ready` is the node-level analogue of
:func:`etlx_server.worker.claim.claim_pending_run` — ``FOR UPDATE SKIP LOCKED``
on the oldest ready node so N workers don't double-claim.

Readiness is a **dependency counter**: ``pending_deps`` starts at
``len(depends_on)``; :meth:`mark_succeeded` decrements it for every pending node
that lists the just-finished node in ``depends_on`` (JSONB ``@>`` containment).
``pending_deps == 0`` ⇒ the node is claimable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from etlx_server.db.enums import RunStatus
from etlx_server.db.models import NodeRun

_MAX_ERROR_MESSAGE_LEN = 2000


@dataclass(frozen=True)
class NodeSpec:
    """A graph node to enqueue: its id, kind, and direct upstream node ids."""

    node_id: str
    kind: str
    depends_on: list[str] = field(default_factory=list)


class NodeRunRepository:
    """CRUD + claim for the ``node_runs`` queue. Session-bound, never commits."""

    async def create_for_run(
        self, session: AsyncSession, run_id: UUID, specs: Iterable[NodeSpec]
    ) -> list[NodeRun]:
        """Insert one pending ``node_run`` per spec; ``pending_deps = len(depends_on)``."""
        rows = [
            NodeRun(
                run_id=run_id,
                node_id=spec.node_id,
                kind=spec.kind,
                depends_on=list(spec.depends_on),
                pending_deps=len(spec.depends_on),
                status=RunStatus.PENDING,
            )
            for spec in specs
        ]
        session.add_all(rows)
        await session.flush()
        return rows

    async def claim_ready(self, session: AsyncSession, *, worker_id: str) -> NodeRun | None:
        """Claim the oldest ready node (pending, no outstanding deps), or ``None``.

        Ready across *all* runs so any worker drains any pipeline's frontier.
        On success the row is already ``running`` with worker/started/heartbeat set.
        """
        stmt = (
            select(NodeRun)
            .where(NodeRun.status == RunStatus.PENDING)
            .where(NodeRun.pending_deps == 0)
            .order_by(NodeRun.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        node_run = (await session.execute(stmt)).scalar_one_or_none()
        if node_run is None:
            return None
        now = datetime.now(UTC)
        node_run.status = RunStatus.RUNNING
        node_run.worker_id = worker_id
        node_run.started_at = now
        node_run.heartbeat_at = now
        node_run.attempt += 1
        await session.flush()
        return node_run

    async def mark_succeeded(
        self,
        session: AsyncSession,
        node_run: NodeRun,
        *,
        records_read: int = 0,
        records_written: int = 0,
        output_ref: str | None = None,
    ) -> None:
        """Mark ``node_run`` succeeded and release its downstream dependents."""
        now = datetime.now(UTC)
        node_run.status = RunStatus.SUCCEEDED
        node_run.finished_at = now
        node_run.heartbeat_at = now
        node_run.records_read = records_read
        node_run.records_written = records_written
        node_run.output_ref = output_ref
        # Decrement readiness counter for every pending node that depends on this
        # one (JSONB ``depends_on @> [node_id]``). When it hits 0, the node is ready.
        await session.execute(
            update(NodeRun)
            .where(NodeRun.run_id == node_run.run_id)
            .where(NodeRun.status == RunStatus.PENDING)
            .where(NodeRun.depends_on.contains([node_run.node_id]))
            .values(pending_deps=NodeRun.pending_deps - 1)
        )
        await session.flush()

    async def mark_failed(
        self,
        session: AsyncSession,
        node_run: NodeRun,
        *,
        error_class: str,
        error_message: str,
    ) -> None:
        """Mark ``node_run`` failed. Downstreams stay blocked (never released)."""
        now = datetime.now(UTC)
        node_run.status = RunStatus.FAILED
        node_run.finished_at = now
        node_run.heartbeat_at = now
        node_run.error_class = error_class
        node_run.error_message = error_message[:_MAX_ERROR_MESSAGE_LEN]
        await session.flush()

    async def list_for_run(self, session: AsyncSession, run_id: UUID) -> list[NodeRun]:
        """All node_runs for a pipeline run, oldest first."""
        stmt = select(NodeRun).where(NodeRun.run_id == run_id).order_by(NodeRun.created_at)
        return list((await session.execute(stmt)).scalars().all())

    # ----- ADR-0041 H3a: live status updates by id (in-process executor) -----
    # The callbacks in execute_graph_nodes_concurrent open fresh sessions to
    # commit per-node status mid-run so the UI sees progress live (rather than
    # only after the whole run finishes). They use *_by_id variants since the
    # callbacks have a node_run UUID, not a NodeRun ORM instance.

    async def set_running(
        self, session: AsyncSession, *, node_run_id: UUID, worker_id: str
    ) -> None:
        """Transition a pre-inserted node_run to ``running`` (live H3a update)."""
        now = datetime.now(UTC)
        await session.execute(
            update(NodeRun)
            .where(NodeRun.id == node_run_id)
            .values(
                status=RunStatus.RUNNING,
                worker_id=worker_id,
                started_at=now,
                heartbeat_at=now,
                attempt=NodeRun.attempt + 1,
            )
        )
        await session.flush()

    async def set_succeeded(
        self,
        session: AsyncSession,
        *,
        node_run_id: UUID,
        records_read: int = 0,
        records_written: int = 0,
        output_ref: str | None = None,
    ) -> None:
        """Mark a node_run succeeded (by id) + release downstream pending_deps —
        keeps DB consistent with :meth:`mark_succeeded` (object variant) so
        future cross-worker `claim_ready` sees freshly-ready nodes."""
        node_run = await session.get(NodeRun, node_run_id)
        if node_run is None:
            raise ValueError(f"node_run {node_run_id} not found")
        now = datetime.now(UTC)
        node_run.status = RunStatus.SUCCEEDED
        node_run.finished_at = now
        node_run.heartbeat_at = now
        node_run.records_read = records_read
        node_run.records_written = records_written
        node_run.output_ref = output_ref
        await session.execute(
            update(NodeRun)
            .where(NodeRun.run_id == node_run.run_id)
            .where(NodeRun.status == RunStatus.PENDING)
            .where(NodeRun.depends_on.contains([node_run.node_id]))
            .values(pending_deps=NodeRun.pending_deps - 1)
        )
        await session.flush()

    async def set_failed(
        self,
        session: AsyncSession,
        *,
        node_run_id: UUID,
        error_class: str,
        error_message: str,
    ) -> None:
        """Mark a node_run failed (by id). Downstreams stay blocked."""
        now = datetime.now(UTC)
        await session.execute(
            update(NodeRun)
            .where(NodeRun.id == node_run_id)
            .values(
                status=RunStatus.FAILED,
                finished_at=now,
                heartbeat_at=now,
                error_class=error_class,
                error_message=(error_message or "")[:_MAX_ERROR_MESSAGE_LEN] or None,
            )
        )
        await session.flush()

    async def set_cancelled(self, session: AsyncSession, *, node_run_id: UUID) -> None:
        """Mark a node_run cancelled (upstream failed → not run, by id)."""
        now = datetime.now(UTC)
        await session.execute(
            update(NodeRun)
            .where(NodeRun.id == node_run_id)
            .values(status=RunStatus.CANCELLED, finished_at=now)
        )
        await session.flush()


__all__ = ["NodeRunRepository", "NodeSpec"]
