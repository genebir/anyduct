"""Metadata schema models — Alembic이 검출할 수 있게 한 곳에서 re-export.

import 순서가 중요: 각 모델이 ``Base.metadata``에 자신을 등록하려면 import 시점에
파일이 evaluated 되어야 한다.
"""

from etlx_server.db.models.asset import (
    Asset,
    AssetColumn,
    AssetEdge,
    AssetMaterialization,
    ColumnLineageEdge,
)
from etlx_server.db.models.audit import AuditLog
from etlx_server.db.models.connection import Connection
from etlx_server.db.models.cursor import Cursor
from etlx_server.db.models.node_run import NodeRun
from etlx_server.db.models.pipeline import (
    Pipeline,
    PipelineTrigger,
    PipelineVersion,
    Schedule,
)
from etlx_server.db.models.run import Run, RunLog, RunMetric
from etlx_server.db.models.workspace import (
    Membership,
    PersonalAccessToken,
    User,
    Workspace,
)
from etlx_server.db.models.workspace_variable import WorkspaceVariable

__all__ = [
    "Asset",
    "AssetColumn",
    "AssetEdge",
    "AssetMaterialization",
    "AuditLog",
    "ColumnLineageEdge",
    "Connection",
    "Cursor",
    "Membership",
    "NodeRun",
    "PersonalAccessToken",
    "Pipeline",
    "PipelineTrigger",
    "PipelineVersion",
    "Run",
    "RunLog",
    "RunMetric",
    "Schedule",
    "User",
    "Workspace",
    "WorkspaceVariable",
]
