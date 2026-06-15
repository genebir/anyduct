"""Enum types used across the metadata schema.

`PEP 435 <https://peps.python.org/pep-0435/>`_ ``StrEnum`` 형태 (Python 3.11+) —
SQLAlchemy의 ``Enum`` 컬럼이 그대로 사용한다.
"""

from __future__ import annotations

from enum import StrEnum


class WorkspaceRole(StrEnum):
    """워크스페이스 단위 역할 (ADR-0023). 글로벌 SuperAdmin은 ``users.is_superadmin``로 별도 표현."""

    OWNER = "owner"
    EDITOR = "editor"
    RUNNER = "runner"
    VIEWER = "viewer"


class AuthMethod(StrEnum):
    """사용자 인증 방식 (ADR-0023). 로컬은 password_hash 필수, OIDC는 nullable."""

    LOCAL = "local"
    OIDC_GOOGLE = "oidc:google"
    OIDC_AZURE = "oidc:azure"
    OIDC_OKTA = "oidc:okta"
    OIDC_GITHUB = "oidc:github"
    OIDC_GENERIC = "oidc:generic"


class PipelineMode(StrEnum):
    """파이프라인 실행 모드 (코어 ``PipelineConfig.mode``와 동일 값)."""

    BATCH = "batch"
    STREAM = "stream"


class RunStatus(StrEnum):
    """Run 라이프사이클 (ADR-0021).

    ``pending`` → 워커가 `SKIP LOCKED`로 claim → ``running`` →
    ``succeeded`` / ``failed`` / ``cancelled``. heartbeat 만료된 ``running``
    row는 zombie로 다른 워커가 회수해 재시도 (다시 ``pending``).
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LogLevel(StrEnum):
    """Run log severity."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
