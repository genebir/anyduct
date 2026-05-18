"""Pipeline CRUD + version management (Step 8.5d).

Public surface:

* :class:`PipelineRepository` — async DB access for ``pipelines`` and
  ``pipeline_versions``.
* :class:`PipelineNameTakenError` — UNIQUE violation lifted into a clean
  409 by the router.

Version idempotency mirrors :mod:`etlx_server.io.yaml_sync` — a PATCH
whose ``config_json`` matches the current version's exactly reuses the
existing row and bumps no counters.
"""

from etlx_server.pipelines.repository import (
    PipelineNameTakenError,
    PipelineRepository,
)

__all__ = ["PipelineNameTakenError", "PipelineRepository"]
