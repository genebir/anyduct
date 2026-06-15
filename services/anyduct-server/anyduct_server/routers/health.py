"""Liveness + readiness probes.

* ``/health`` — liveness. Returns 200 as long as the process is running. The
  endpoint deliberately does not touch external systems.
* ``/ready`` — readiness. Verifies the metadata DB is reachable with a
  ``SELECT 1``. Returns 503 if the DB is unreachable (K8s should keep traffic
  off this pod until it recovers).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from anyduct_server.dependencies import get_engine


class HealthStatus(BaseModel):
    """Response model for liveness."""

    status: Literal["ok"] = "ok"


class ReadinessStatus(BaseModel):
    """Response model for readiness."""

    status: Literal["ready", "degraded"]
    database: Literal["ok", "error"]
    detail: str | None = None


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthStatus)
async def liveness() -> HealthStatus:
    """Liveness probe — does not touch external systems."""
    return HealthStatus()


@router.get("/ready", response_model=ReadinessStatus)
async def readiness(engine: AsyncEngine = Depends(get_engine)) -> JSONResponse:  # noqa: B008
    """Readiness probe — pings the metadata DB."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # broad except: any failure means "not ready"
        body = ReadinessStatus(status="degraded", database="error", detail=type(exc).__name__)
        return JSONResponse(body.model_dump(), status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    body = ReadinessStatus(status="ready", database="ok")
    return JSONResponse(body.model_dump(), status_code=status.HTTP_200_OK)
