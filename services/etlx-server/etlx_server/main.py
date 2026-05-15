"""FastAPI app entrypoint — Step 7.1 placeholder.

Only `/health` and `/version` are wired. Real schema/auth/CRUD endpoints
arrive in Step 7.2~8.x.
"""

from __future__ import annotations

from fastapi import FastAPI

from etl_plugins import __version__ as core_version
from etlx_server import __version__ as server_version

app = FastAPI(
    title="etlx-server",
    description="ETL Plugins service backend — Step 7.1 placeholder.",
    version=server_version,
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. K8s readinessProbe-friendly."""
    return {"status": "ok"}


@app.get("/version")
async def version() -> dict[str, str]:
    return {"server": server_version, "core": core_version}
