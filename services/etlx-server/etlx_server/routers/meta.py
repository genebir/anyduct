"""Service metadata — ``/version`` and similar."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from etl_plugins import __version__ as core_version
from etlx_server import __version__ as server_version
from etlx_server.dependencies import get_settings
from etlx_server.settings import Settings


class VersionInfo(BaseModel):
    """Build identifiers — useful for client-side feature gating + bug reports."""

    server: str
    core: str
    service: str
    environment: str


router = APIRouter(tags=["meta"])


@router.get("/version", response_model=VersionInfo)
async def version(settings: Settings = Depends(get_settings)) -> VersionInfo:  # noqa: B008
    return VersionInfo(
        server=server_version,
        core=core_version,
        service=settings.service_name,
        environment=settings.environment.value,
    )
