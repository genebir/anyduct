"""YAML <-> metadata DB sync tests (Step 7.3).

Cover the four key promises:

1. ``!secret`` tags become ``${SECRET:<path>}`` placeholders in DB + populate
   ``connections.secret_refs`` — plaintext never lands in the DB.
2. ``${VAR}`` env-var placeholders pass through verbatim (no expansion).
3. Re-importing unchanged YAML produces no new ``PipelineVersion``.
4. Round-trip (import -> export) regenerates equivalent YAML — including the
   ``!secret`` tags.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from etlx_server.db.enums import PipelineMode
from etlx_server.db.models import Connection, Pipeline, PipelineVersion, Schedule, Workspace
from etlx_server.io.yaml_sync import (
    export_workspace,
    import_connections_yaml,
    import_pipeline_yaml,
    import_yaml_dir,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


CONNECTIONS_YAML = """\
connections:
  warehouse:
    type: postgres
    host: pg.internal
    port: 5432
    user: etl
    password: !secret vault/pg/password
    database: dw
  events:
    type: kafka
    bootstrap_servers: ${KAFKA_BROKERS}
    topic: events
"""

PIPELINE_YAML = """\
name: orders_to_dw
mode: batch
schedule: "0 * * * *"
source:
  connection: warehouse
  query: "SELECT * FROM orders WHERE updated_at > :since"
sink:
  connection: warehouse
  table: orders_dw
  mode: upsert
  key_columns: [id]
transforms:
  - type: rename
    columns: {customer: customer_id}
retry:
  max_attempts: 5
  backoff: exponential
  initial_delay_seconds: 1.0
"""

STREAM_PIPELINE_YAML = """\
name: events_to_kafka
mode: stream
source:
  connection: events
  topic: events
sink:
  connection: events
  topic: events_clean
"""


def _write_yaml_tree(base: Path) -> None:
    (base / "connections.yaml").write_text(CONNECTIONS_YAML, encoding="utf-8")
    pdir = base / "pipelines"
    pdir.mkdir()
    (pdir / "orders_to_dw.yaml").write_text(PIPELINE_YAML, encoding="utf-8")
    (pdir / "events_to_kafka.yaml").write_text(STREAM_PIPELINE_YAML, encoding="utf-8")


async def _make_workspace(session: AsyncSession, slug: str | None = None) -> Workspace:
    ws = Workspace(name="W", slug=slug or f"yaml-{datetime.now().microsecond}")
    session.add(ws)
    await session.flush()
    return ws


async def test_import_connections_strips_secrets(session: AsyncSession, tmp_path: Path) -> None:
    ws = await _make_workspace(session)
    yaml_path = tmp_path / "connections.yaml"
    yaml_path.write_text(CONNECTIONS_YAML, encoding="utf-8")

    count = await import_connections_yaml(session, ws.id, yaml_path)
    assert count == 2

    rows = (await session.execute(select(Connection).order_by(Connection.name))).scalars().all()
    by_name = {r.name: r for r in rows}

    warehouse = by_name["warehouse"]
    assert warehouse.type == "postgres"
    assert warehouse.config_json["host"] == "pg.internal"
    # Secret is replaced by a placeholder + tracked by reference.
    assert warehouse.config_json["password"] == "${SECRET:vault/pg/password}"
    assert "password" not in (warehouse.secret_refs or [])  # not the field name
    assert "vault/pg/password" in warehouse.secret_refs

    events = by_name["events"]
    # ${VAR} placeholders pass through verbatim — never expanded at import time.
    assert events.config_json["bootstrap_servers"] == "${KAFKA_BROKERS}"
    assert events.secret_refs == []


async def test_import_pipeline_creates_version_and_schedule(
    session: AsyncSession, tmp_path: Path
) -> None:
    ws = await _make_workspace(session)
    yaml_path = tmp_path / "orders.yaml"
    yaml_path.write_text(PIPELINE_YAML, encoding="utf-8")

    version, created, schedule = await import_pipeline_yaml(session, ws.id, yaml_path)
    assert created is True
    assert version.version == 1
    assert version.is_current is True

    pipeline = (
        await session.execute(select(Pipeline).where(Pipeline.id == version.pipeline_id))
    ).scalar_one()
    assert pipeline.name == "orders_to_dw"
    assert pipeline.workspace_id == ws.id
    assert version.config_json["source"]["query"].startswith("SELECT")
    assert version.config_json["sink"]["mode"] == "upsert"

    assert schedule is not None
    assert schedule.cron_expr == "0 * * * *"
    assert schedule.mode == PipelineMode.BATCH
    assert schedule.is_active is True


async def test_reimport_unchanged_yaml_is_idempotent(session: AsyncSession, tmp_path: Path) -> None:
    ws = await _make_workspace(session)
    yaml_path = tmp_path / "orders.yaml"
    yaml_path.write_text(PIPELINE_YAML, encoding="utf-8")

    _, created_1, _ = await import_pipeline_yaml(session, ws.id, yaml_path)
    _, created_2, _ = await import_pipeline_yaml(session, ws.id, yaml_path)
    assert created_1 is True
    assert created_2 is False

    versions = (
        (
            await session.execute(
                select(PipelineVersion).where(PipelineVersion.pipeline_id.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    assert len(versions) == 1


async def test_reimport_changed_yaml_bumps_version(session: AsyncSession, tmp_path: Path) -> None:
    ws = await _make_workspace(session)
    yaml_path = tmp_path / "orders.yaml"
    yaml_path.write_text(PIPELINE_YAML, encoding="utf-8")
    await import_pipeline_yaml(session, ws.id, yaml_path)

    # Change retry policy — should create v2 and demote v1.
    changed = PIPELINE_YAML.replace("max_attempts: 5", "max_attempts: 10")
    yaml_path.write_text(changed, encoding="utf-8")
    v2, created, _ = await import_pipeline_yaml(session, ws.id, yaml_path)
    assert created is True
    assert v2.version == 2
    assert v2.is_current is True

    versions = (
        (await session.execute(select(PipelineVersion).order_by(PipelineVersion.version)))
        .scalars()
        .all()
    )
    assert [v.version for v in versions] == [1, 2]
    assert versions[0].is_current is False
    assert versions[1].is_current is True


async def test_stream_pipeline_creates_schedule_without_cron(
    session: AsyncSession, tmp_path: Path
) -> None:
    ws = await _make_workspace(session)
    yaml_path = tmp_path / "stream.yaml"
    yaml_path.write_text(STREAM_PIPELINE_YAML, encoding="utf-8")
    _, _, schedule = await import_pipeline_yaml(session, ws.id, yaml_path)
    assert schedule is not None
    assert schedule.cron_expr is None
    assert schedule.mode == PipelineMode.STREAM


async def test_import_dir_summary_counts(session: AsyncSession, tmp_path: Path) -> None:
    ws = await _make_workspace(session)
    _write_yaml_tree(tmp_path)

    result = await import_yaml_dir(session, ws.id, tmp_path)
    assert result.connections_upserted == 2
    assert result.pipelines_upserted == 2
    assert result.pipeline_versions_created == 2
    assert result.pipeline_versions_unchanged == 0
    assert result.schedules_upserted == 2

    # Second pass — nothing should change.
    result2 = await import_yaml_dir(session, ws.id, tmp_path)
    assert result2.pipeline_versions_created == 0
    assert result2.pipeline_versions_unchanged == 2


async def test_export_round_trips(session: AsyncSession, tmp_path: Path) -> None:
    ws = await _make_workspace(session)
    in_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    in_dir.mkdir()
    _write_yaml_tree(in_dir)

    await import_yaml_dir(session, ws.id, in_dir)
    result = await export_workspace(session, ws.id, out_dir)
    assert result.connections_written == 2
    assert result.pipelines_written == 2

    # Re-parse the exported YAML using the core loader and assert that the
    # !secret tags survived as SecretRef objects — round-trip correctness
    # matters more than exact text formatting (PyYAML quotes paths containing
    # '/' as a conservative default, which is still valid YAML).
    from etl_plugins.config.loader import SecretRef, load_yaml

    reparsed = load_yaml(out_dir / "connections.yaml")
    assert reparsed["connections"]["warehouse"]["password"] == SecretRef("vault/pg/password")
    # ${VAR} placeholders pass through verbatim.
    assert reparsed["connections"]["events"]["bootstrap_servers"] == "${KAFKA_BROKERS}"

    assert (out_dir / "pipelines" / "orders_to_dw.yaml").is_file()
    assert (out_dir / "pipelines" / "events_to_kafka.yaml").is_file()

    # Second import from exported tree should be idempotent: every pipeline's
    # current version still has the same config_json.
    ws2 = await _make_workspace(session, slug="round-trip-2")
    await import_yaml_dir(session, ws2.id, out_dir)
    after = await import_yaml_dir(session, ws2.id, out_dir)
    assert after.pipeline_versions_created == 0


async def test_export_secret_tag_via_pipeline_version(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Pipeline-level !secret values (e.g. in transforms/sink) round-trip too."""
    pipeline_yaml_with_secret = """\
name: with_secret
mode: batch
schedule: "* * * * *"
source:
  connection: src
sink:
  connection: dst
  table: t
  api_token: !secret vault/api/token
"""
    ws = await _make_workspace(session)
    p = tmp_path / "p.yaml"
    p.write_text(pipeline_yaml_with_secret, encoding="utf-8")
    await import_pipeline_yaml(session, ws.id, p)

    version = (
        await session.execute(select(PipelineVersion).where(PipelineVersion.is_current.is_(True)))
    ).scalar_one()
    assert version.config_json["sink"]["api_token"] == "${SECRET:vault/api/token}"

    out_dir = tmp_path / "out"
    await export_workspace(session, ws.id, out_dir)
    from etl_plugins.config.loader import SecretRef, load_yaml

    reparsed = load_yaml(out_dir / "pipelines" / "with_secret.yaml")
    assert reparsed["sink"]["api_token"] == SecretRef("vault/api/token")


async def test_schedule_removed_when_yaml_drops_schedule(
    session: AsyncSession, tmp_path: Path
) -> None:
    ws = await _make_workspace(session)
    yaml_path = tmp_path / "p.yaml"
    yaml_path.write_text(PIPELINE_YAML, encoding="utf-8")
    await import_pipeline_yaml(session, ws.id, yaml_path)

    # Remove schedule line, re-import — Schedule row should be gone.
    without_schedule = "\n".join(
        line for line in PIPELINE_YAML.splitlines() if "schedule:" not in line
    )
    yaml_path.write_text(without_schedule, encoding="utf-8")
    _, _, schedule = await import_pipeline_yaml(session, ws.id, yaml_path)
    assert schedule is None
    remaining = (
        (await session.execute(select(Schedule).where(Schedule.name == "default"))).scalars().all()
    )
    assert remaining == []
