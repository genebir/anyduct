"""Workspace-global pipeline variables (ADR-0041, V2).

Globals merge *under* a pipeline's local ``variables`` block at build time
(locals win); see :func:`etl_plugins.config.variables.resolve_config_variables`.
"""

from etlx_server.variables.repository import WorkspaceVariableRepository

__all__ = ["WorkspaceVariableRepository"]
