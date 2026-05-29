"""Smoke-tests that the ``examples/*.yaml`` fixtures keep loading
cleanly (Phase AAH, 2026-05-29).

The cross-DB example doubles as user-facing documentation in
``CLAUDE.md`` / ADR-0072. If a future config-schema change breaks it,
the docs break too — better to catch that here than to ship a
copy-pasteable snippet that no longer parses.
"""

from __future__ import annotations

from pathlib import Path

from etl_plugins.config import load_pipeline

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"


def test_cross_db_migration_example_validates() -> None:
    pc = load_pipeline(EXAMPLES / "cross_db_migration.yaml")
    assert pc.name == "orders_replication"
    assert pc.mode == "batch"
    assert pc.sink is not None
    # The whole point of the example: auto_create_table + canonical
    # if_exists value land cleanly through the AAA→AAG narrowing.
    assert pc.sink.auto_create_table is True
    assert pc.sink.auto_create_if_exists == "skip"
