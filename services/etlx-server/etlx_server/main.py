"""Production entry point — ``uvicorn etlx_server.main:app``.

Thin shim: build the app with the env-driven default settings via the
factory. All wiring lives in :mod:`etlx_server.app_factory`.
"""

from __future__ import annotations

from etlx_server.app_factory import create_app

app = create_app()
