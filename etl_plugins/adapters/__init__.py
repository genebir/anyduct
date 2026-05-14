"""Orchestrator adapters — thin wrappers around the runtime layer.

Each submodule corresponds to a specific orchestrator (Airflow, Dagster,
Prefect) and is **lazily imported**: the submodule itself loads without the
orchestrator package installed, but accessing the public symbols triggers the
real import. This keeps ``import etl_plugins`` cheap and avoids pulling in
heavy dependencies that aren't needed.

Install the relevant extra to actually use one::

    pip install 'etl-plugins[airflow]'
    pip install 'etl-plugins[dagster]'
    pip install 'etl-plugins[prefect]'
"""

__all__ = ["airflow", "dagster", "prefect"]
