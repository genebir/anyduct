# etlx-server

FastAPI backend for the etl-plugins web UI. **Step 7.1 placeholder** — the package
boots with `/health` + `/version` only; real schema, auth, and CRUD arrive in
Step 7.2 onward.

This package is a **uv workspace member**. Inside the repo it resolves
`etl-plugins` via path; outside the repo it pulls the published PyPI version.

```bash
# from repo root
uv run --package etlx-server uvicorn etlx_server.main:app --reload --port 8000
curl localhost:8000/health
```

See:
- ADR-0017 (service-layer split) · ADR-0019 (FastAPI) · ADR-0020 (PostgreSQL)
- ADR-0021 (PG-backed worker queue) · ADR-0022 (monorepo) · ADR-0023 (auth)
- ROADMAP §7~§9, §11
