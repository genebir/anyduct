"""Structural checks for the adapter modules [Step 4].

These tests verify the lazy-import design works:
  * each adapter module imports cleanly without the orchestrator installed
  * attribute access raises a helpful ``ImportError`` if the orchestrator
    is missing, or returns a real symbol if it's installed
"""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.parametrize("module", ["airflow", "dagster", "prefect"])
def test_adapter_module_imports_without_orchestrator(module: str) -> None:
    """Importing the adapter module must succeed even if the orchestrator
    package is not installed — the actual import is lazy."""
    mod = importlib.import_module(f"etl_plugins.adapters.{module}")
    assert mod is not None
    assert hasattr(mod, "__all__")


def _airflow_installed() -> bool:
    try:
        import airflow  # noqa: F401

        return True
    except ImportError:
        return False


def _dagster_installed() -> bool:
    try:
        import dagster  # noqa: F401

        return True
    except ImportError:
        return False


def _prefect_installed() -> bool:
    try:
        import prefect  # noqa: F401

        return True
    except ImportError:
        return False


def test_airflow_missing_raises_helpful_importerror() -> None:
    if _airflow_installed():
        pytest.skip("airflow is installed — cannot test the error path")
    from etl_plugins.adapters import airflow as adapter

    with pytest.raises(ImportError, match="etl-plugins\\[airflow\\]"):
        _ = adapter.ETLPluginsOperator


def test_dagster_missing_raises_helpful_importerror() -> None:
    if _dagster_installed():
        pytest.skip("dagster is installed — cannot test the error path")
    from etl_plugins.adapters import dagster as adapter

    with pytest.raises(ImportError, match="etl-plugins\\[dagster\\]"):
        _ = adapter.EtlPluginsResource
    with pytest.raises(ImportError, match="etl-plugins\\[dagster\\]"):
        _ = adapter.etl_plugins_op


def test_prefect_missing_raises_helpful_importerror() -> None:
    if _prefect_installed():
        pytest.skip("prefect is installed — cannot test the error path")
    from etl_plugins.adapters import prefect as adapter

    with pytest.raises(ImportError, match="etl-plugins\\[prefect\\]"):
        _ = adapter.run_etl_pipeline_flow


def test_unknown_attribute_raises_attributeerror() -> None:
    """Accessing a non-public attribute should be a normal AttributeError."""
    from etl_plugins.adapters import airflow, dagster, prefect

    for adapter in (airflow, dagster, prefect):
        with pytest.raises(AttributeError):
            _ = adapter.NonExistentSymbol


# ---------------------------------------------------------------------------
# Tests that exercise the real orchestrator — only run when installed.
# ---------------------------------------------------------------------------


def test_airflow_operator_delegates_to_run_pipeline_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("airflow")
    from etl_plugins.adapters import airflow as adapter
    from etl_plugins.core.pipeline import RunResult

    captured: dict[str, object] = {}

    def _fake_run(pipeline_path, *, connections_path=None, **kw):  # type: ignore[no-untyped-def]
        captured["pipeline_path"] = pipeline_path
        captured["connections_path"] = connections_path
        return RunResult(
            run_id="r1", pipeline_name="p", success=True, records_read=1, records_written=1
        )

    monkeypatch.setattr("etl_plugins.adapters.airflow.run_pipeline_yaml", _fake_run)

    operator_cls = adapter.ETLPluginsOperator
    op = operator_cls(
        task_id="t",
        pipeline_yaml="/tmp/p.yaml",
        connections="/tmp/c.yaml",
    )
    out = op.execute(context={})
    assert captured == {"pipeline_path": "/tmp/p.yaml", "connections_path": "/tmp/c.yaml"}
    assert out["success"] is True
    assert out["records_written"] == 1


def test_dagster_resource_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("dagster")
    from etl_plugins.adapters import dagster as adapter
    from etl_plugins.core.pipeline import RunResult

    captured: dict[str, object] = {}

    def _fake_run(pipeline_path, *, connections_path=None, **kw):  # type: ignore[no-untyped-def]
        captured["pipeline_path"] = pipeline_path
        captured["connections_path"] = connections_path
        return RunResult(run_id="r1", pipeline_name="p", success=True)

    monkeypatch.setattr("etl_plugins.adapters.dagster.run_pipeline_yaml", _fake_run)

    resource_cls = adapter.EtlPluginsResource
    res = resource_cls(connections_path="/tmp/c.yaml")
    result = res.run("/tmp/p.yaml")
    assert captured == {"pipeline_path": "/tmp/p.yaml", "connections_path": "/tmp/c.yaml"}
    assert result.success is True


def test_prefect_task_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("prefect")
    from etl_plugins.adapters import prefect as adapter
    from etl_plugins.core.pipeline import RunResult

    captured: dict[str, object] = {}

    def _fake_run(pipeline_path, *, connections_path=None, **kw):  # type: ignore[no-untyped-def]
        captured["pipeline_path"] = pipeline_path
        captured["connections_path"] = connections_path
        return RunResult(run_id="r1", pipeline_name="p", success=True, records_written=3)

    monkeypatch.setattr("etl_plugins.adapters.prefect.run_pipeline_yaml", _fake_run)

    flow_fn = adapter.run_etl_pipeline_flow
    # Prefect flows are still callable directly during testing
    out = flow_fn("/tmp/p.yaml", "/tmp/c.yaml")
    assert captured == {"pipeline_path": "/tmp/p.yaml", "connections_path": "/tmp/c.yaml"}
    assert out["records_written"] == 3
