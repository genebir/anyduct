#!/usr/bin/env bash
# =============================================================================
# bootstrap.sh — 원커맨드 환경 셋업 (모노레포 대응)
# 사용: ./scripts/bootstrap.sh
#
# 이 스크립트가 끝나면 다음이 모두 가능해야 한다:
#   - 코어 라이브러리:    uv run pytest -m "not it"
#   - 코어 통합 테스트:    uv run pytest -m it           (Docker 필요)
#   - services/anyduct-server: uv run --package anyduct-server uvicorn anyduct_server.main:app
#   - services/anyduct-web:    pnpm --filter @anyduct/web dev
#   - 의존 검증:          uv run lint-imports
#
# Prerequisites (없으면 안내만 하고 계속):
#   - Python 3.11+, uv, Docker, Node 22+, pnpm 9+
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
echo "==> ETL Plugins bootstrap (repo: $(pwd))"

# --- 1. uv 설치 확인 ---
if ! command -v uv >/dev/null 2>&1; then
    echo "==> uv가 설치되어 있지 않음. 설치 중..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo "    uv 설치 완료. 새 셸에서 PATH에 ~/.local/bin 이 포함되는지 확인하세요."
fi

# --- 2. Python deps + workspace members (코어 + services/anyduct-server) ---
echo "==> uv sync --all-packages (코어 + services/anyduct-server)"
uv sync --all-packages

# --- 3. .env 생성 (이미 있으면 보존) ---
if [ ! -f .env ]; then
    cp .env.example .env
    echo "==> .env created from .env.example  (시크릿 값을 채우세요)"
else
    echo "==> .env 이미 존재 — 보존"
fi

# --- 4. pre-commit hook 설치 ---
echo "==> pre-commit install"
uv run pre-commit install

# --- 5. detect-secrets baseline (없으면 초기 생성) ---
if [ ! -f .secrets.baseline ]; then
    echo "==> detect-secrets baseline 생성"
    uv run detect-secrets scan > .secrets.baseline
fi

# --- 6. import-graph 검증 (ADR-0017 / ADR-0022) ---
echo "==> lint-imports (단방향 의존 검증)"
uv run lint-imports

# --- 7. services/anyduct-web — Node + pnpm + 의존성 ---
if command -v pnpm >/dev/null 2>&1 && command -v node >/dev/null 2>&1; then
    NODE_MAJOR=$(node -p "process.versions.node.split('.')[0]")
    if [ "${NODE_MAJOR}" -ge 22 ]; then
        echo "==> pnpm install (services/anyduct-web, frozen lockfile)"
        pnpm install --frozen-lockfile
    else
        echo "==> Node ${NODE_MAJOR} detected — services/anyduct-web requires >=22. Skipping pnpm install."
        echo "    nvm install 22 후 './scripts/bootstrap.sh'를 다시 실행하세요."
    fi
else
    echo "==> Node / pnpm 미설치 — services/anyduct-web 작업은 다음 명령 후:"
    echo "    nvm install 22 && npm i -g pnpm@9 && ./scripts/bootstrap.sh"
fi

# --- 8. 로컬 dev 인프라 기동 (Docker가 있으면) ---
if command -v docker >/dev/null 2>&1; then
    echo "==> docker compose up (postgres / kafka / minio / redis)"
    docker compose -f docker/docker-compose.dev.yml up -d
else
    echo "==> docker 미설치 — 통합 테스트는 docker 설치 후 사용 가능"
fi

cat <<'EOF'

==> Bootstrap 완료.

다음 할 일:
  1) .env의 시크릿 값을 채우세요
  2) CLAUDE.md §5 (현재 단계) → ROADMAP.md (다음 미체크 항목) → 최근 커밋 로그 순서로 확인
  3) `make help` 로 사용 가능한 명령 확인

코어 / 서비스 빠르게 실행해 보기:
  uv run pytest -m "not it"                                       # 코어 unit
  uv run pytest services/anyduct-server/tests                        # server placeholder unit
  uv run --package anyduct-server uvicorn anyduct_server.main:app --reload
  pnpm --filter @anyduct/web dev                                     # http://localhost:3000
EOF
