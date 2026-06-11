# Deployment

This page covers shipping the **services** (`etlx-server` + `etlx-web`) to a
real environment. For installing the pure Python library, see
[Install](getting-started/install.md). For local development without
containers, see [Quickstart](getting-started/quickstart.md).

The reference deployment is `services/docker-compose.prod.yml` — every
container, env var, and dependency edge listed here is exercised by that
file. The Helm chart (`services/charts/etlx`, see the Kubernetes section)
deploys the same topology.

## Topology

```
                 ┌──────────────┐
                 │   etlx-web   │  3000  (Next.js standalone)
                 └───────┬──────┘
                         │  REST + SSE
                 ┌───────▼──────┐
                 │  etlx-server │  8000  (FastAPI / uvicorn)
                 └───────┬──────┘
                         │
   ┌─────────────────────┼──────────────────────────┐
   │                     │                          │
┌──▼──────┐   ┌──────────▼─────────┐  ┌─────────────▼────────┐
│ migrate │   │ workers (3+ procs) │  │     metadata-db      │
│ (init)  │   │ worker / scheduler │  │  Postgres 16-alpine  │
│         │   │ reaper / stream    │  │                      │
└─────────┘   └────────────────────┘  └──────────────────────┘
```

Five process types share one image (`etlx-server:prod`); they differ only
in `command`. The web image (`etlx-web:prod`) is standalone — no Node
modules at runtime, just `node server.js`.

## Images

### `etlx-server`

```bash
docker build -f services/etlx-server/Dockerfile -t etlx-server:<tag> .
```

* Build context = repo root (the uv workspace needs `etl_plugins/` +
  `services/etlx-server/`).
* Multi-stage: `python:3.11-slim` + uv → resolves the lockfile into
  `/app/.venv`; final image runs as UID 10001 with `tini` as PID 1.
* `CMD` defaults to uvicorn. Override for workers (see below).
* `HEALTHCHECK` probes `/ready` (DB ping). Workers in compose disable
  this — they don't expose HTTP.

### `etlx-web`

```bash
docker build -f services/etlx-web/Dockerfile \
    --build-arg NEXT_PUBLIC_API_URL=https://api.example.com \
    -t etlx-web:<tag> .
```

* `NEXT_PUBLIC_*` is inlined at build time — change it by rebuilding.
  Runtime env vars do **not** override values Next has already inlined
  into the client bundle.
* Multi-stage: deps → builder → minimal node:22-alpine + standalone.
* Runs as UID 10001 with `tini` as PID 1.

## Environment variables

`.env.example` is the source of truth. The high-impact ones:

| Variable | Required? | Notes |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+asyncpg://USER:PASS@host:port/db` |
| `AUTH_JWT_PRIVATE_KEY_PEM` | yes | Full PEM body. Generate with `etlx admin gen-jwt-keys`. |
| `AUTH_JWT_PUBLIC_KEY_PEM` | yes | matching public key |
| `AUTH_JWT_ISSUER` / `AUDIENCE` | no | defaults `etlx-server` / `etlx` |
| `CORS_ORIGINS` | no | **JSON array.** Example: `["https://app.example.com"]` |
| `SECRET_BACKEND` | no | `env` (default), `file`, `vault`, `aws_sm`, `gcp_sm` |
| `ENVIRONMENT` | no | `production` hides `/docs` and `/redoc` |
| `SERVICE_NAME` | no | stamped onto OTel resource attributes |

JWT keys are PEM blobs. In Kubernetes, mount them as a `Secret`:

```yaml
- name: AUTH_JWT_PRIVATE_KEY_PEM
  valueFrom:
    secretKeyRef:
      name: etlx-jwt
      key: private.pem
```

In compose, export from local files (the pattern used in
`docker-compose.prod.yml`):

```bash
export AUTH_JWT_PRIVATE_KEY_PEM="$(cat /etc/etlx/jwt_private.pem)"
export AUTH_JWT_PUBLIC_KEY_PEM="$(cat /etc/etlx/jwt_public.pem)"
```

## One-command local production stack

```bash
# 1. Generate JWT keypair (one time per deploy)
etlx admin gen-jwt-keys --out-dir .run

# 2. Export the PEMs so compose's env interpolation picks them up
export AUTH_JWT_PRIVATE_KEY_PEM="$(cat .run/jwt_private.pem)"
export AUTH_JWT_PUBLIC_KEY_PEM="$(cat .run/jwt_public.pem)"

# 3. Build + start the whole stack (db → migrate → server/workers → web)
docker compose -f services/docker-compose.prod.yml up -d --build

# 4. Seed an admin user
docker compose -f services/docker-compose.prod.yml exec etlx-server \
    etlx-server admin create-user --email you@example.com --name You --superadmin

# 5. Open the UI
xdg-open http://localhost:3000      # or just visit it
```

`docker compose ... down` stops everything. Add `-v` to also drop the
metadata DB volume.

## Bundled metadata DB image (optional)

By default `docker-compose.prod.yml` uses vanilla `postgres:16-alpine`,
and the `etlx-migrate` init-job applies the schema on first boot. For
**demos / ephemeral environments / CI** where you want the schema
(and optionally sample data) pre-loaded, swap in the
`etlx-postgres` image:

```bash
# 1. Build the bundled image (schema only — production-safe)
make bundle-db
# 2. Tell compose to use it
export ETLX_DB_IMAGE=etlx-postgres:dev
docker compose -f services/docker-compose.prod.yml up -d --build
# `etlx-migrate` still runs — but on a fresh volume the schema is
# already at head, so it's a no-op (~200ms).
```

For a one-command demo with a known login (`demo@example.com` /
`demopass`) and a pre-created `demo` workspace:

```bash
make bundle-db-demo
export ETLX_DB_IMAGE=etlx-postgres:demo
docker compose -f services/docker-compose.prod.yml up -d --build
# Visit http://localhost:3000 and log in directly — no `admin
# create-user` step needed.
```

The bundled image uses the upstream postgres `/docker-entrypoint-initdb.d/`
convention. Files committed under `services/etlx-postgres/init/`:

* `00-schema.sql` — DDL for all 14 tables + indexes + enums.
* `01-alembic-head.sql` — INSERT into `alembic_version` so the schema
  is marked as already migrated.

Regenerate after a new Alembic revision lands:

```bash
make seed-schema      # spins up a throwaway postgres, runs migrate,
                      # dumps + commits the new SQL
```

**Never use the `:demo` image in production** — the password is
committed to the repo. Use the schema-only `:dev` (or rebuild as a
new tag) and seed the admin via `etlx admin create-user`.

## Migrations

The `etlx-migrate` service runs `alembic upgrade head` once and exits;
other services use `depends_on: { etlx-migrate: { condition:
service_completed_successfully } }` so they don't race the schema. In
Kubernetes this becomes a `Job` (preferred) or an `initContainer`.

Manual runs:

```bash
docker compose -f services/docker-compose.prod.yml run --rm etlx-migrate \
    etlx-server db current        # show current revision
docker compose -f services/docker-compose.prod.yml run --rm etlx-migrate \
    etlx-server db upgrade head   # apply pending
docker compose -f services/docker-compose.prod.yml run --rm etlx-migrate \
    etlx-server db downgrade -1   # rollback one revision
```

## Worker scaling

The batch worker (`etlx-worker`), scheduler, reaper, and stream-worker
are each separate processes sharing one image. **All four are
multi-replica safe** — every contended row is claimed with
`FOR UPDATE SKIP LOCKED`, so adding replicas never double-runs anything:

| Process | Contention point | Scaling unit |
|---|---|---|
| `etlx-worker` | runs queue (ADR-0021) | pending runs — add replicas for run throughput |
| scheduler | schedule rows (ADR-0041 K2) | HA only — one tick fires each schedule once |
| stream-worker | schedule rows + RUNNING re-check (K2b) | active *stream schedules* — replicas split schedules, not one stream's partitions |
| reaper | stale runs (SKIP LOCKED) | HA only |

```bash
docker compose -f services/docker-compose.prod.yml up -d --scale etlx-worker=4
```

The no-double-claim property is exercised by a real-concurrency e2e
(2026-06-10, P3a): three replicas claiming one queue distribute twelve
runs with no overlap, and two live `RunWorker` processes co-drain a
shared queue to completion.

### Scaling one big run — partitioned backfill

Adding workers spreads *runs*; a single huge historical load is still
one run. Split it into parallel windows with **partitioned backfill**
(ADR-0095) — each consecutive boundary pair becomes an independent
sub-run over `(left, right]` on the source's `cursor_column`, and the
worker fleet claims the windows concurrently:

```bash
curl -X POST "$API/workspaces/$WS/pipelines/$PID/partitioned-backfill" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"boundaries": ["2025-01-01", "2025-04-01", "2025-07-01", "2025-10-01", "2026-01-01"]}'
# → 4 sub-runs, half-open windows: no overlap, union = (first, last]
```

The same control lives in the UI's Backfill dialog ("Split points").
Windows execute fully independently — suitable for plain
extract-load / per-row transforms; don't split pipelines whose
aggregation or dedupe crosses window boundaries. Pick boundaries from
what you know of the data's distribution (the server intentionally does
not auto-split a min/max scan into equal arithmetic windows — that
produces skew on gappy ids or bursty time ranges).

## Kubernetes (Helm)

`services/charts/etlx` deploys the same topology (verified end-to-end on
a kind cluster: install → migrate hook → all pods Ready → login →
pipeline run SUCCEEDED through a k8s worker):

```bash
helm install etlx services/charts/etlx \
  --set-file jwt.privateKeyPem=.run/jwt_private.pem \
  --set-file jwt.publicKeyPem=.run/jwt_public.pem
```

* **Embedded Postgres is dev-only** — set `postgresql.enabled=false` and
  `externalDatabase.url` for production.
* **Migrations run as a `post-install,pre-upgrade` hook Job.** Ship new
  images with `helm upgrade` — a bare `kubectl rollout restart` skips
  the hook and leaves new code ahead of the schema (UndefinedColumn at
  runtime; we hit exactly this during chart verification).
* Scale run throughput with `worker.replicas` (SKIP LOCKED queue — see
  Worker scaling above). Scheduler/reaper/stream-worker default to 1
  replica (extra replicas are HA, not throughput).
* The production server image installs the lightweight connector tier
  via the `etlx-server[connectors]` extra (postgres/mysql/duckdb/s3/
  kafka/mongodb/redis/sqs/kinesis/dynamodb/rabbitmq/nats). Heavy or
  C-extension drivers (cassandra, mssql, vertica, cloud DWs) need a
  custom image layer: `RUN uv pip install 'etl-plugins[snowflake]'`.

## Health, readiness, observability

* `GET /health` — liveness. Always 200 while the process is running.
* `GET /ready` — readiness. 200 only when the metadata DB responds to
  `SELECT 1`. Use this in Kubernetes `readinessProbe`.
* OTel + Prometheus exporter: see [Observability](guides/observability.md).
  Default backend is NoOp; opt in via `configure_otel(...)` at process
  startup (Step 11 will wire this from env vars).

## Backups

The metadata DB is the only stateful surface this stack owns. Snapshot
the `metadata_pgdata` volume (compose) or your managed Postgres (RDS,
Cloud SQL, ...) with the usual `pg_dump` cadence. Cursors, connection
secret references, runs history, and audit log are all in this DB.

```bash
docker compose -f services/docker-compose.prod.yml exec metadata-db \
    pg_dump -U etlx etlx > backup-$(date +%F).sql
```

Secret values live in the configured `SECRET_BACKEND` (Vault / AWS SM /
GCP SM / file). Back those up independently per the vendor's
documentation — the metadata DB only holds `${SECRET:...}` placeholders,
not the secret bytes.

## What this stack does **not** include

* **Reverse proxy / TLS.** Put a real proxy (Caddy, Traefik, nginx, an
  ingress controller) in front. The default container listens on plain
  HTTP. `CORS_ORIGINS` should match the public URL of the web UI.
* **Multi-region replication** of the metadata DB. Set up read replicas
  + failover at the Postgres layer if you need it.
* (The Helm chart used to be on this list — it now exists, see below.)
