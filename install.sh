#!/usr/bin/env bash
# =============================================================================
# install.sh — 모노레포 환경 설치 (idempotent + cross-platform)
#
# 매번 다시 실행해도 안전합니다. 이미 설치된 것은 건드리지 않고, 누락된 것만
# 채웁니다. Linux / macOS / WSL2 / Git Bash 어디서나 동작합니다.
#
# 사용:
#     ./install.sh                  # 모든 단계 실행
#     ./install.sh --skip-docker    # docker compose / Alembic 건너뛰기
#     ./install.sh --skip-web       # pnpm install 건너뛰기
#     ./install.sh --help           # 도움말
#
# 무엇을 하는가:
#   1. uv (없으면 설치)
#   2. uv sync --all-packages       # 코어 + services/anyduct-server 의존성
#   3. .env (없으면 .env.example에서 복사)
#   4. pre-commit hook (가능한 경우)
#   5. pnpm install                  # services/anyduct-web (Node ≥22 필요)
#   6. docker compose up -d          # postgres / kafka / minio / redis
#   7. Postgres 준비 대기 + alembic upgrade head
#   8. 개발용 JWT RSA 키페어 생성 (.run/keys/, 이미 있으면 건너뜀)
#
# 종료 코드:
#   0 = 성공
#   1 = 치명적 실패 (uv 설치 불가 등)
#   2 = 사용법 오류
# =============================================================================
set -euo pipefail

# ----- 색 + 로그 -------------------------------------------------------------
if [ -t 1 ]; then
    C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'; C_RED=$'\033[0;31m'
    C_BLUE=$'\033[0;36m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
else
    C_GREEN=""; C_YELLOW=""; C_RED=""; C_BLUE=""; C_DIM=""; C_RESET=""
fi

log_info()  { printf '%s==>%s %s\n'   "$C_BLUE"   "$C_RESET" "$*"; }
log_ok()    { printf '%s  ok%s  %s\n' "$C_GREEN"  "$C_RESET" "$*"; }
log_warn()  { printf '%sWARN%s  %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
log_error() { printf '%sERR %s  %s\n' "$C_RED"    "$C_RESET" "$*" 1>&2; }
log_skip()  { printf '%s  skip%s %s\n' "$C_DIM"   "$C_RESET" "$*"; }

# ----- 인자 파싱 -------------------------------------------------------------
SKIP_DOCKER=0
SKIP_WEB=0

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-docker) SKIP_DOCKER=1 ;;
        --skip-web)    SKIP_WEB=1 ;;
        -h|--help)
            sed -n '2,27p' "$0"; exit 0 ;;
        *)
            log_error "unknown argument: $1"; exit 2 ;;
    esac
    shift
done

# ----- 작업 디렉토리: 스크립트 위치 = 리포 루트 -----------------------------
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
cd "$SCRIPT_DIR"
log_info "repo root: $SCRIPT_DIR"

# ----- OS 감지 ---------------------------------------------------------------
OS=$(uname -s 2>/dev/null || echo unknown)
case "$OS" in
    Linux*)   PLATFORM="linux" ;;
    Darwin*)  PLATFORM="macos" ;;
    MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
    *)        PLATFORM="unknown" ;;
esac
log_info "platform: $PLATFORM ($OS)"

mkdir -p .run/keys .run/logs

# =============================================================================
# 1. uv (Python 패키지 관리)
# =============================================================================
if command -v uv >/dev/null 2>&1; then
    log_ok "uv already installed ($(uv --version 2>/dev/null || echo unknown))"
else
    log_info "installing uv via official installer"
    if ! command -v curl >/dev/null 2>&1; then
        log_error "curl is required to install uv. Install curl first."
        exit 1
    fi
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # 새 셸 없이도 uv를 즉시 쓸 수 있게 PATH 업데이트
    if [ -d "$HOME/.local/bin" ]; then
        export PATH="$HOME/.local/bin:$PATH"
    fi
    if ! command -v uv >/dev/null 2>&1; then
        log_error "uv installed but not on PATH. Add \$HOME/.local/bin and re-run."
        exit 1
    fi
    log_ok "uv installed: $(uv --version)"
fi

# =============================================================================
# 2. Python 의존성 동기화 (코어 + services/anyduct-server)
# =============================================================================
log_info "uv sync --all-packages (core + services/anyduct-server)"
uv sync --all-packages
log_ok "python deps synced"

# =============================================================================
# 3. .env (이미 있으면 보존 — 시크릿이 들어있을 수 있음)
# =============================================================================
if [ -f .env ]; then
    log_skip ".env already exists — preserved"
elif [ -f .env.example ]; then
    cp .env.example .env
    log_ok ".env created from .env.example (fill in secret values)"
else
    log_warn "no .env.example found — skipping"
fi

# =============================================================================
# 4. pre-commit hook (있는 환경에서만)
# =============================================================================
if uv run --quiet pre-commit --version >/dev/null 2>&1; then
    log_info "installing pre-commit hook"
    uv run pre-commit install >/dev/null
    log_ok "pre-commit hook installed"
else
    log_skip "pre-commit not in deps — skipping hook install"
fi

# =============================================================================
# 5. services/anyduct-web — pnpm install
# =============================================================================
if [ "$SKIP_WEB" -eq 1 ]; then
    log_skip "--skip-web: pnpm install"
elif command -v pnpm >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
    NODE_MAJOR=$(node -p "process.versions.node.split('.')[0]" 2>/dev/null || echo 0)
    if [ "$NODE_MAJOR" -ge 22 ]; then
        log_info "pnpm install --frozen-lockfile (anyduct-web)"
        pnpm install --frozen-lockfile
        log_ok "web deps installed"
    else
        log_warn "Node $NODE_MAJOR < 22 — anyduct-web needs Node 22+. Skipping pnpm install."
        log_warn "  nvm install 22 && ./install.sh"
    fi
else
    log_skip "node/pnpm missing — anyduct-web skipped"
    log_skip "  install: nvm install 22 && corepack enable && corepack prepare pnpm@9 --activate"
fi

# =============================================================================
# 6. docker compose dev infra
# =============================================================================
DOCKER_OK=0
if [ "$SKIP_DOCKER" -eq 1 ]; then
    log_skip "--skip-docker: dev infra"
elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    log_info "docker compose up -d (postgres/kafka/minio/redis)"
    if docker compose -f docker/docker-compose.dev.yml up -d; then
        DOCKER_OK=1
        log_ok "dev infra up"
    else
        # Some compose images may be temporarily missing (e.g. Bitnami's
        # bitnami/kafka:3.7 vanished from Docker Hub in mid-2026). We
        # still continue — Postgres alone is enough for the metadata DB,
        # and the migration step probes it explicitly.
        DOCKER_OK=1
        log_warn "docker compose reported errors — continuing (will verify Postgres next)"
    fi
else
    log_warn "docker not available — dev infra and Alembic migrations will be skipped"
    log_warn "  install Docker Desktop or Docker Engine first"
fi

# =============================================================================
# 7. Wait for Postgres + alembic upgrade head
# =============================================================================
wait_for_pg() {
    # POSIX-friendly TCP probe. /dev/tcp works in bash on Linux/macOS/WSL2/Git Bash.
    local host="${1:-127.0.0.1}" port="${2:-5432}" timeout="${3:-60}"
    local elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        if (exec 3<>/dev/tcp/"$host"/"$port") 2>/dev/null; then
            exec 3<&- 3>&-
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 1
}

if [ "$DOCKER_OK" -eq 1 ]; then
    # Postgres host port — matches docker-compose.dev.yml's ${LOCAL_PG_PORT:-55432}.
    _PG_PORT="${LOCAL_PG_PORT:-55432}"
    log_info "waiting for Postgres (localhost:$_PG_PORT) ..."
    if wait_for_pg 127.0.0.1 "$_PG_PORT" 60; then
        log_ok "postgres reachable on port $_PG_PORT"
        log_info "alembic upgrade head (metadata DB)"
        # Non-fatal — auth/port conflicts (e.g. native Postgres already on
        # the host port) get reported but don't abort the rest of install.
        # Literal dev defaults from .env.example — not real credentials.
        _DEV_DB_URL="${DATABASE_URL:-postgresql+asyncpg://etl:etl@127.0.0.1:${_PG_PORT}/etl_dev}"  # pragma: allowlist secret
        if (
            cd services/anyduct-server
            DATABASE_URL="$_DEV_DB_URL" uv run alembic upgrade head
        ); then
            log_ok "metadata schema is at head"
        else
            log_warn "alembic upgrade failed — see error above"
            log_warn "  common cause: a native Postgres on host port $_PG_PORT is intercepting"
            log_warn "  the compose container. Try one of:"
            log_warn "    (a) stop the native Postgres, then: ./install.sh"
            log_warn "    (b) override creds: DATABASE_URL=... ./install.sh"
            log_warn "    (c) edit docker/docker-compose.dev.yml to map a free port"
        fi
    else
        log_warn "postgres did not become ready in 60s — skipping migrations"
        log_warn "  re-run after Docker has started: ./install.sh"
    fi
fi

# =============================================================================
# 8. Dev JWT keypair (only if missing)
# =============================================================================
KEY_DIR=".run/keys"
PRIV_KEY="$KEY_DIR/jwt-private.pem"
PUB_KEY="$KEY_DIR/jwt-public.pem"

if [ -f "$PRIV_KEY" ] && [ -f "$PUB_KEY" ]; then
    log_skip "dev JWT keypair already exists at $KEY_DIR/"
else
    log_info "generating dev JWT RSA keypair → $KEY_DIR/"
    if command -v openssl >/dev/null 2>&1; then
        openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
            -out "$PRIV_KEY" -quiet 2>/dev/null
        openssl rsa -in "$PRIV_KEY" -pubout -out "$PUB_KEY" 2>/dev/null
    else
        # Fallback: use the python helper bundled with the server.
        uv run --package anyduct-server python - <<PY
from pathlib import Path
from anyduct_server.auth.jwt_service import generate_rsa_keypair_pem
priv, pub = generate_rsa_keypair_pem(bits=2048)
Path("$PRIV_KEY").write_bytes(priv)
Path("$PUB_KEY").write_bytes(pub)
PY
    fi
    chmod 600 "$PRIV_KEY" || true
    log_ok "dev JWT keypair written (chmod 600 on private key)"
fi

# =============================================================================
# Done
# =============================================================================
cat <<EOF

${C_GREEN}install complete.${C_RESET}

Next steps:
  ${C_BLUE}./start.sh${C_RESET}             # start anyduct-server + anyduct-web in background
  ${C_BLUE}./stop.sh${C_RESET}              # stop them (add --all to also stop docker)
  ${C_BLUE}make help${C_RESET}              # other Make targets

Quick sanity check:
  ${C_DIM}uv run pytest -m "not it"${C_RESET}                       # core unit tests
  ${C_DIM}uv run pytest services/anyduct-server/tests${C_RESET}        # server tests

EOF
