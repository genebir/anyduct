#!/usr/bin/env bash
# =============================================================================
# start.sh — etlx-server + etlx-web 백그라운드 기동 (idempotent)
#
# Linux / macOS / WSL2 / Git Bash 어디서나 동작합니다. 이미 실행 중인 프로세스는
# 다시 띄우지 않고 상태만 보고합니다.
#
# 사용:
#     ./start.sh                # 전부 시작 (docker + migrate + server + worker + reaper + web)
#     ./start.sh --restart      # 먼저 stop.sh로 전부 내린 뒤 새로 띄움 (코드 변경 반영)
#     ./start.sh --no-web       # 웹 dev 서버는 건너뛰기
#     ./start.sh --no-worker    # worker + reaper 건너뛰기 (server만)
#     ./start.sh --no-docker    # docker compose / Alembic 건너뛰기
#     ./start.sh --no-migrate   # docker는 띄우되 alembic upgrade는 생략
#     ./start.sh --help
#
# 코드를 바꾼 뒤에는 ``--restart``를 써야 한다 — 기본 start.sh는 idempotent라
# 이미 떠 있는 프로세스를 그대로 두므로(옛 코드가 계속 실행됨).
#
# 무엇을 하는가:
#   1. docker compose up -d              (--no-docker 면 생략)
#   2. alembic upgrade head              (--no-migrate / --no-docker 면 생략)
#   3. uvicorn etlx_server.main:app      (백그라운드, PID/log: .run/)
#   4. etlx-server worker run            (백그라운드, --no-worker 면 생략)
#   5. etlx-server reaper run            (백그라운드, --no-worker 면 생략)
#   6. pnpm --filter @etlx/web dev       (백그라운드, --no-web 이면 생략)
#   7. /health + 웹 포트가 응답할 때까지 polling
#
# Postgres 포트는 LOCAL_PG_PORT(기본 55432, docker-compose.dev.yml 기본값)를 따른다.
# 시크릿은 file 백엔드(.run/secrets.json)를 기본으로 써서 UI에서 만든 연결의
# 시크릿을 server·worker가 동일하게 해석한다.
#
# 상태/로그:
#   .run/etlx-server.pid      .run/logs/etlx-server.log
#   .run/etlx-web.pid         .run/logs/etlx-web.log
# =============================================================================
set -euo pipefail

# ----- 색 + 로그 -------------------------------------------------------------
if [ -t 1 ]; then
    C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'; C_RED=$'\033[0;31m'
    C_BLUE=$'\033[0;36m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
    C_GREEN=""; C_YELLOW=""; C_RED=""; C_BLUE=""; C_DIM=""; C_RESET=""
fi
log_info()  { printf '%s==>%s %s\n'    "$C_BLUE"   "$C_RESET" "$*"; }
log_ok()    { printf '%s  ok%s   %s\n' "$C_GREEN"  "$C_RESET" "$*"; }
log_warn()  { printf '%sWARN%s   %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
log_error() { printf '%sERR %s   %s\n' "$C_RED"    "$C_RESET" "$*" 1>&2; }
log_skip()  { printf '%s  skip%s %s\n' "$C_DIM"    "$C_RESET" "$*"; }

# ----- 인자 파싱 -------------------------------------------------------------
START_WEB=1
START_DOCKER=1
RUN_MIGRATE=1
START_WORKER=1
RESTART=0
while [ $# -gt 0 ]; do
    case "$1" in
        --restart)    RESTART=1 ;;
        --no-web)     START_WEB=0 ;;
        --no-worker)  START_WORKER=0 ;;
        --no-docker)  START_DOCKER=0; RUN_MIGRATE=0 ;;
        --no-migrate) RUN_MIGRATE=0 ;;
        -h|--help)    sed -n '2,34p' "$0"; exit 0 ;;
        *) log_error "unknown argument: $1"; exit 2 ;;
    esac
    shift
done

# ----- 리포 루트로 이동 ------------------------------------------------------
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
cd "$SCRIPT_DIR"

# --restart: tear everything down first so the idempotent checks below spawn
# fresh processes with the current code (a plain start.sh skips running ones).
if [ "$RESTART" -eq 1 ]; then
    log_info "restart: stopping existing processes first"
    "$SCRIPT_DIR/stop.sh" || true
fi

RUN_DIR=".run"
LOG_DIR="$RUN_DIR/logs"
KEY_DIR="$RUN_DIR/keys"
SERVER_PID="$RUN_DIR/etlx-server.pid"
WEB_PID="$RUN_DIR/etlx-web.pid"
WORKER_PID="$RUN_DIR/etlx-worker.pid"
REAPER_PID="$RUN_DIR/etlx-reaper.pid"
SCHEDULER_PID="$RUN_DIR/etlx-scheduler.pid"
SERVER_LOG="$LOG_DIR/etlx-server.log"
WEB_LOG="$LOG_DIR/etlx-web.log"
WORKER_LOG="$LOG_DIR/etlx-worker.log"
REAPER_LOG="$LOG_DIR/etlx-reaper.log"
SCHEDULER_LOG="$LOG_DIR/etlx-scheduler.log"

SERVER_HOST="${ETLX_SERVER_HOST:-127.0.0.1}"
SERVER_PORT="${ETLX_SERVER_PORT:-8000}"
WEB_PORT="${ETLX_WEB_PORT:-3000}"

# Postgres host port — matches docker-compose.dev.yml's ${LOCAL_PG_PORT:-55432}.
PG_PORT="${LOCAL_PG_PORT:-55432}"
# Single source of truth for the DB URL used by migration + server + worker.
# Literal dev defaults from .env.example — not real credentials.
DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://etl:etl@127.0.0.1:${PG_PORT}/etl_dev}"  # pragma: allowlist secret
export DATABASE_URL
# Writable secret backend so secrets entered in the UI (server writes them)
# resolve identically in the worker. Override by exporting before running.
export SECRET_BACKEND="${SECRET_BACKEND:-file}"
export SECRET_BACKEND_FILE_PATH="${SECRET_BACKEND_FILE_PATH:-$SCRIPT_DIR/$RUN_DIR/secrets.json}"
# Allow the local web dev origin to call the API (empty = CORS middleware off,
# which the browser treats as blocked for cross-origin requests).
export CORS_ORIGINS="${CORS_ORIGINS:-[\"http://localhost:$WEB_PORT\",\"http://127.0.0.1:$WEB_PORT\"]}"

mkdir -p "$LOG_DIR" "$KEY_DIR"

# ----- helpers ---------------------------------------------------------------
is_running() {
    # is_running PID_FILE → 0 if alive, 1 otherwise.
    local pid_file="$1"
    [ -f "$pid_file" ] || return 1
    local pid
    pid=$(cat "$pid_file" 2>/dev/null || true)
    [ -n "$pid" ] || return 1
    # POSIX 신호 0: 프로세스 존재 여부만 검사 (실제로 죽이지 않음).
    kill -0 "$pid" 2>/dev/null
}

wait_for_tcp() {
    # wait_for_tcp HOST PORT TIMEOUT_SEC LABEL
    local host="$1" port="$2" timeout="$3" label="$4"
    local elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        if (exec 3<>/dev/tcp/"$host"/"$port") 2>/dev/null; then
            exec 3<&- 3>&-
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    log_error "$label did not start within ${timeout}s (host=$host port=$port)"
    return 1
}

wait_for_http() {
    # wait_for_http URL TIMEOUT_SEC LABEL — uses curl if available, else TCP only.
    local url="$1" timeout="$2" label="$3"
    if ! command -v curl >/dev/null 2>&1; then
        # No curl — fall back to TCP probe; not as strict but works.
        local host port
        # Extract host + port from a URL like http://127.0.0.1:8000/...
        host=$(printf '%s' "$url" | sed -E 's|^[a-z]+://([^:/]+).*$|\1|')
        port=$(printf '%s' "$url" | sed -nE 's|^[a-z]+://[^:/]+:([0-9]+).*$|\1|p')
        port="${port:-80}"
        wait_for_tcp "$host" "$port" "$timeout" "$label"
        return $?
    fi
    local elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    log_error "$label did not respond at $url within ${timeout}s"
    return 1
}

spawn_background() {
    # spawn_background PID_FILE LOG_FILE CMD [ARGS...]
    # Starts CMD detached, captures PID, redirects stdout+stderr to LOG_FILE.
    local pid_file="$1" log_file="$2"
    shift 2
    # nohup keeps the process alive after the parent shell exits; the
    # `</dev/null` detaches stdin so the script doesn't hang on TTY input.
    nohup "$@" </dev/null >>"$log_file" 2>&1 &
    echo $! >"$pid_file"
}

# =============================================================================
# 1. docker compose dev infra
# =============================================================================
if [ "$START_DOCKER" -eq 1 ]; then
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        log_info "docker compose up -d"
        # Some images in the compose file may be temporarily unavailable. We
        # still want partial up (just Postgres is enough for the server). The
        # subsequent TCP probe is the real readiness check.
        if docker compose -f docker/docker-compose.dev.yml up -d >/dev/null 2>&1; then
            log_ok "docker compose up -d done"
        else
            log_warn "docker compose reported errors — continuing if Postgres came up anyway"
        fi
    else
        log_warn "docker not available — start it manually, then re-run ./start.sh"
        RUN_MIGRATE=0
    fi
else
    log_skip "--no-docker"
fi

# =============================================================================
# 2. alembic upgrade head
# =============================================================================
if [ "$RUN_MIGRATE" -eq 1 ]; then
    log_info "waiting for Postgres on 127.0.0.1:$PG_PORT ..."
    if wait_for_tcp 127.0.0.1 "$PG_PORT" 30 "postgres"; then
        log_info "alembic upgrade head (DATABASE_URL → :$PG_PORT)"
        # Non-fatal — continue even if auth/port conflicts surface. Uses the
        # same DATABASE_URL the server + worker will use, so all three agree.
        if (
            cd services/etlx-server
            uv run alembic upgrade head
        ) >/dev/null 2>&1; then
            log_ok "metadata schema at head"
        else
            log_warn "alembic upgrade failed — server will still start but DB-backed"
            log_warn "  endpoints may 5xx. Check whose Postgres is on :$PG_PORT and re-run."
        fi
    else
        log_warn "postgres not ready — skipping migrations (run ./install.sh later)"
    fi
else
    log_skip "alembic upgrade head"
fi

# =============================================================================
# 3. JWT keypair from install.sh (used by etlx-server)
# =============================================================================
JWT_PRIVATE="$KEY_DIR/jwt-private.pem"
JWT_PUBLIC="$KEY_DIR/jwt-public.pem"
# Fallback to the flat .run/jwt_*.pem layout some setups use.
if [ ! -f "$JWT_PRIVATE" ] && [ -f "$RUN_DIR/jwt_private.pem" ]; then
    JWT_PRIVATE="$RUN_DIR/jwt_private.pem"
    JWT_PUBLIC="$RUN_DIR/jwt_public.pem"
fi
if [ ! -f "$JWT_PRIVATE" ] || [ ! -f "$JWT_PUBLIC" ]; then
    log_warn "JWT keypair missing under $KEY_DIR/ — auth endpoints will be unconfigured"
    log_warn "  run ./install.sh to generate one"
else
    # Export so uvicorn's process inherits the keys via env.
    AUTH_JWT_PRIVATE_KEY_PEM=$(cat "$JWT_PRIVATE")
    AUTH_JWT_PUBLIC_KEY_PEM=$(cat "$JWT_PUBLIC")
    export AUTH_JWT_PRIVATE_KEY_PEM AUTH_JWT_PUBLIC_KEY_PEM
fi

# =============================================================================
# 4. etlx-server (uvicorn) — idempotent start
# =============================================================================
if is_running "$SERVER_PID"; then
    log_skip "etlx-server already running (pid=$(cat "$SERVER_PID"))"
else
    log_info "starting etlx-server on $SERVER_HOST:$SERVER_PORT (log: $SERVER_LOG)"
    # Production-like: no --reload. Use `uv run --package etlx-server` so it
    # resolves against the workspace member's venv. DATABASE_URL +
    # SECRET_BACKEND were exported above (shared with migration + worker).
    spawn_background "$SERVER_PID" "$SERVER_LOG" \
        uv run --package etlx-server uvicorn etlx_server.main:app \
            --host "$SERVER_HOST" --port "$SERVER_PORT"
    log_ok "etlx-server spawned (pid=$(cat "$SERVER_PID"))"
    if ! wait_for_http "http://$SERVER_HOST:$SERVER_PORT/health" 30 "etlx-server"; then
        log_error "tail of $SERVER_LOG:"
        tail -n 30 "$SERVER_LOG" 1>&2 || true
        exit 1
    fi
    log_ok "etlx-server is healthy"
fi

# =============================================================================
# 5. etlx-server worker + reaper — idempotent start
#    The worker executes claimed runs; the reaper fails out stuck/zombie runs.
#    Both inherit DATABASE_URL + SECRET_BACKEND exported above, and run only
#    after migrations so they never hit a missing table.
# =============================================================================
if [ "$START_WORKER" -eq 0 ]; then
    log_skip "--no-worker"
else
    if is_running "$WORKER_PID"; then
        log_skip "etlx-worker already running (pid=$(cat "$WORKER_PID"))"
    else
        log_info "starting etlx-worker (log: $WORKER_LOG)"
        spawn_background "$WORKER_PID" "$WORKER_LOG" \
            uv run --package etlx-server etlx-server worker run --log-flush-interval 2.0
        log_ok "etlx-worker spawned (pid=$(cat "$WORKER_PID"))"
    fi
    if is_running "$REAPER_PID"; then
        log_skip "etlx-reaper already running (pid=$(cat "$REAPER_PID"))"
    else
        log_info "starting etlx-reaper (log: $REAPER_LOG)"
        spawn_background "$REAPER_PID" "$REAPER_LOG" \
            uv run --package etlx-server etlx-server reaper run
        log_ok "etlx-reaper spawned (pid=$(cat "$REAPER_PID"))"
    fi
    # Scheduler: fires cron schedules + freshness-based re-runs (ADR-0038).
    if is_running "$SCHEDULER_PID"; then
        log_skip "etlx-scheduler already running (pid=$(cat "$SCHEDULER_PID"))"
    else
        log_info "starting etlx-scheduler (log: $SCHEDULER_LOG)"
        spawn_background "$SCHEDULER_PID" "$SCHEDULER_LOG" \
            uv run --package etlx-server etlx-server scheduler run
        log_ok "etlx-scheduler spawned (pid=$(cat "$SCHEDULER_PID"))"
    fi
fi

# =============================================================================
# 6. etlx-web (Next.js dev) — idempotent start
# =============================================================================
if [ "$START_WEB" -eq 0 ]; then
    log_skip "--no-web"
elif is_running "$WEB_PID"; then
    log_skip "etlx-web already running (pid=$(cat "$WEB_PID"))"
elif ! command -v pnpm >/dev/null 2>&1; then
    log_warn "pnpm not on PATH — etlx-web skipped (run ./install.sh first)"
else
    log_info "starting etlx-web on port $WEB_PORT (log: $WEB_LOG)"
    spawn_background "$WEB_PID" "$WEB_LOG" \
        pnpm --filter @etlx/web dev --port "$WEB_PORT"
    log_ok "etlx-web spawned (pid=$(cat "$WEB_PID"))"
    # Next.js boot can take a while on cold start (>30s sometimes).
    if ! wait_for_http "http://127.0.0.1:$WEB_PORT/" 90 "etlx-web"; then
        log_warn "etlx-web did not become responsive in 90s — check $WEB_LOG"
        # Don't exit; partial-up is acceptable.
    else
        log_ok "etlx-web is responding"
    fi
fi

# =============================================================================
# Done
# =============================================================================
cat <<EOF

${C_GREEN}stack is up.${C_RESET}

  etlx-server   http://$SERVER_HOST:$SERVER_PORT      ${C_DIM}(/docs, /health, /version)${C_RESET}
EOF
if [ "$START_WORKER" -eq 1 ]; then
    echo "  etlx-worker   ${C_DIM}(executes runs)${C_RESET}        etlx-reaper ${C_DIM}(fails out zombies)${C_RESET}"
fi
if [ "$START_WEB" -eq 1 ]; then
    echo "  etlx-web      http://127.0.0.1:$WEB_PORT          ${C_DIM}(Next.js dev)${C_RESET}"
fi
cat <<EOF

  logs:    tail -f .run/logs/etlx-server.log .run/logs/etlx-worker.log
  stop:    ./stop.sh            (--all to also stop docker compose)

EOF
