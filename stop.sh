#!/usr/bin/env bash
# =============================================================================
# stop.sh — anyduct-server + anyduct-web 정리 종료 (idempotent)
#
# Linux / macOS / WSL2 / Git Bash 어디서나 동작합니다. 이미 죽어있거나 PID
# 파일이 없으면 그냥 "not running"으로 보고하고 종료합니다 (실패 아님).
#
# 사용:
#     ./stop.sh                # server + web만 중지 (docker는 그대로)
#     ./stop.sh --all          # 추가로 docker compose down (volume은 보존)
#     ./stop.sh --no-server    # web만 중지
#     ./stop.sh --no-web       # server만 중지
#     ./stop.sh --help
#
# 절차:
#   1. SIGTERM 보냄 (정리 기회 줌)
#   2. 5초 대기
#   3. 살아있으면 SIGKILL
#   4. PID 파일 삭제
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
STOP_DOCKER=0
STOP_SERVER=1
STOP_WEB=1
while [ $# -gt 0 ]; do
    case "$1" in
        --all|-a)     STOP_DOCKER=1 ;;
        --no-server)  STOP_SERVER=0 ;;
        --no-web)     STOP_WEB=0 ;;
        -h|--help)    sed -n '2,19p' "$0"; exit 0 ;;
        *) log_error "unknown argument: $1"; exit 2 ;;
    esac
    shift
done

# ----- 리포 루트 -------------------------------------------------------------
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
cd "$SCRIPT_DIR"

RUN_DIR=".run"
SERVER_PID="$RUN_DIR/anyduct-server.pid"
WEB_PID="$RUN_DIR/anyduct-web.pid"
WORKER_PID="$RUN_DIR/anyduct-worker.pid"
REAPER_PID="$RUN_DIR/anyduct-reaper.pid"
SCHEDULER_PID="$RUN_DIR/anyduct-scheduler.pid"
SENSOR_SCHEDULER_PID="$RUN_DIR/anyduct-sensor-scheduler.pid"

# ----- helpers ---------------------------------------------------------------
stop_pid_file() {
    # stop_pid_file LABEL PID_FILE
    local label="$1" pid_file="$2"
    if [ ! -f "$pid_file" ]; then
        log_skip "$label: no PID file ($pid_file) — not running"
        return 0
    fi
    local pid
    pid=$(cat "$pid_file" 2>/dev/null || true)
    if [ -z "$pid" ]; then
        log_warn "$label: PID file is empty — removing"
        rm -f "$pid_file"
        return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        log_skip "$label: pid $pid not alive — cleaning stale PID file"
        rm -f "$pid_file"
        return 0
    fi

    log_info "stopping $label (pid=$pid) — sending SIGTERM"
    kill -TERM "$pid" 2>/dev/null || true
    # Give it up to 5 seconds to exit cleanly.
    local elapsed=0
    while [ "$elapsed" -lt 5 ]; do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$pid_file"
            log_ok "$label stopped cleanly (TERM)"
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done

    log_warn "$label still alive after TERM — sending SIGKILL"
    kill -KILL "$pid" 2>/dev/null || true
    # Wait briefly for the kernel to reap.
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        log_error "$label (pid=$pid) refuses to die — investigate manually"
        return 1
    fi
    rm -f "$pid_file"
    log_ok "$label stopped (KILL)"
}

# =============================================================================
# 1. anyduct-web first (it depends on anyduct-server in dev)
# =============================================================================
if [ "$STOP_WEB" -eq 1 ]; then
    stop_pid_file "anyduct-web" "$WEB_PID"
else
    log_skip "--no-web"
fi

# =============================================================================
# 2. anyduct-server worker + reaper (stopped alongside the server)
# =============================================================================
if [ "$STOP_SERVER" -eq 1 ]; then
    stop_pid_file "anyduct-worker" "$WORKER_PID"
    stop_pid_file "anyduct-reaper" "$REAPER_PID"
    stop_pid_file "anyduct-scheduler" "$SCHEDULER_PID"
    stop_pid_file "anyduct-sensor-scheduler" "$SENSOR_SCHEDULER_PID"
fi

# =============================================================================
# 3. anyduct-server
# =============================================================================
if [ "$STOP_SERVER" -eq 1 ]; then
    stop_pid_file "anyduct-server" "$SERVER_PID"
else
    log_skip "--no-server"
fi

# =============================================================================
# 4. docker compose (only with --all; volumes are preserved)
# =============================================================================
if [ "$STOP_DOCKER" -eq 1 ]; then
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        log_info "docker compose down (volumes preserved)"
        docker compose -f docker/docker-compose.dev.yml down >/dev/null
        log_ok "docker compose stopped"
    else
        log_warn "docker not available — nothing to stop"
    fi
else
    log_skip "docker compose left running (use --all to also stop)"
fi

cat <<EOF

${C_GREEN}stop complete.${C_RESET}

  start again:    ./start.sh
  remove data:    docker compose -f docker/docker-compose.dev.yml down -v   ${C_DIM}(WIPES Postgres data)${C_RESET}

EOF
