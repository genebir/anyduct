"""DLQ record preview service scenarios (Phase DLQ-1, ADR-0075).

Builds directly on the DLQ routing scenario (Phase II): a pipeline whose
transform raises on bad rows routes them to a DLQ table. Here we then
read those records *back* through :class:`DlqPreviewService` — the server
side of the "what actually failed?" DLQ viewer.

* **DLQP1** — happy path: after a run routes 2 bad rows to ``bad_orders``,
  the preview returns exactly those 2 records (available=True).
* **DLQP2** — ``limit`` is honoured (only the first row comes back).
* **DLQP3** — a pipeline with no ``dlq`` reports ``available=False``
  with ``reason="no_dlq"`` rather than erroring.
* **DLQP4** — a DLQ pointing at a missing connection reports
  ``reason="connection_missing"`` (no crash).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from etlx_server.pipelines.dlq_preview import DlqPreviewService
from sqlalchemy.ext.asyncio import AsyncSession

from etl_plugins.config.secrets import StaticSecretBackend
from tests.db.test_dlq_scenarios import _run_one, _seed_orders_warehouse
from tests.db.test_worker_lifecycle import (
    _seed_connection,
    _seed_pending_run,
    _seed_pipeline,
    _seed_workspace,
)

pytestmark = pytest.mark.asyncio

_TRANSFORM = (
    "def transform(record):\n"
    "    if record.data['amount'] < 0:\n"
    "        raise ValueError('negative amount')\n"
    "    return record\n"
)


def _dlq_cfg(*, with_dlq: bool = True, dlq_connection: str = "dst") -> dict:
    cfg: dict = {
        "name": "p",
        "source": {"connection": "src", "query": "SELECT id, amount FROM raw_orders"},
        "transforms": [{"type": "custom_python", "code": _TRANSFORM}],
        "sink": {"connection": "dst", "table": "clean_orders", "mode": "append"},
    }
    if with_dlq:
        cfg["dlq"] = {"connection": dlq_connection, "table": "bad_orders", "mode": "append"}
    return cfg


async def _seed_and_run(session: AsyncSession, tmp_path: Path, *, slug: str, cfg: dict):  # type: ignore[no-untyped-def]
    db_path = _seed_orders_warehouse(tmp_path)
    ws = await _seed_workspace(session, slug=slug)
    await _seed_connection(
        session, workspace_id=ws.id, name="src", config={"database": str(db_path)}
    )
    await _seed_connection(
        session, workspace_id=ws.id, name="dst", config={"database": str(db_path)}
    )
    p, pv = await _seed_pipeline(session, workspace_id=ws.id, name="p", config=cfg)
    await _seed_pending_run(
        session, workspace_id=ws.id, pipeline_id=p.id, pipeline_version_id=pv.id
    )
    await _run_one(session, slug)
    return p, pv


async def test_dlqp1_preview_returns_routed_records(session: AsyncSession, tmp_path: Path) -> None:
    p, pv = await _seed_and_run(session, tmp_path, slug="dlqp1", cfg=_dlq_cfg())

    preview = await DlqPreviewService(session, StaticSecretBackend()).preview(p, pv, limit=50)

    assert preview.available is True
    assert preview.reason is None
    assert preview.connection == "dst"
    assert preview.table == "bad_orders"
    # The two negative-amount rows the transform rejected (II1).
    by_id = sorted(preview.records, key=lambda r: r["id"])
    assert by_id == [{"id": 2, "amount": -50}, {"id": 4, "amount": -75}]


async def test_dlqp2_preview_honours_limit(session: AsyncSession, tmp_path: Path) -> None:
    p, pv = await _seed_and_run(session, tmp_path, slug="dlqp2", cfg=_dlq_cfg())

    preview = await DlqPreviewService(session, StaticSecretBackend()).preview(p, pv, limit=1)

    assert preview.available is True
    assert len(preview.records) == 1


async def test_dlqp3_no_dlq_reports_reason(session: AsyncSession, tmp_path: Path) -> None:
    p, pv = await _seed_and_run(session, tmp_path, slug="dlqp3", cfg=_dlq_cfg(with_dlq=False))

    preview = await DlqPreviewService(session, StaticSecretBackend()).preview(p, pv, limit=50)

    assert preview.available is False
    assert preview.reason == "no_dlq"
    assert preview.records == []


async def test_dlqp4_missing_dlq_connection_reports_reason(
    session: AsyncSession, tmp_path: Path
) -> None:
    # DLQ points at a connection name that was never created in the workspace.
    p, pv = await _seed_and_run(
        session, tmp_path, slug="dlqp4", cfg=_dlq_cfg(dlq_connection="ghost")
    )

    preview = await DlqPreviewService(session, StaticSecretBackend()).preview(p, pv, limit=50)

    assert preview.available is False
    assert preview.reason == "connection_missing"
    assert preview.connection == "ghost"
