#!/usr/bin/env bash
# =============================================================================
# bootstrap.sh — 원커맨드 환경 셋업
# 사용: ./scripts/bootstrap.sh
# =============================================================================
set -euo pipefail

# 저장소 루트로 이동
cd "$(dirname "$0")/.."

echo "==> ETL Plugins bootstrap (repo: $(pwd))"

# --- 1. uv 설치 확인 ---
if ! command -v uv >/dev/null 2>&1; then
    echo "==> uv가 설치되어 있지 않음. 설치 중..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo "    uv 설치 완료. PATH에 ~/.local/bin 이 포함되어야 합니다."
    echo "    필요시 새 셸에서 다시 실행하세요."
fi

# --- 2. 의존성 동기화 (dev 그룹 포함) ---
echo "==> uv sync"
uv sync

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

# --- 6. 로컬 dev 인프라 기동 ---
if command -v docker >/dev/null 2>&1; then
    echo "==> docker compose up (postgres / kafka / minio / redis)"
    docker compose -f docker/docker-compose.dev.yml up -d
else
    echo "==> docker 미설치 — 통합 테스트는 docker 설치 후 사용 가능"
fi

# --- 7. 연결 점검 (Step 3에서 etlx CLI 구현 후 활성화) ---
# uv run etlx test-connection --all

cat <<'EOF'

==> Bootstrap 완료.

다음 할 일:
  1) .env의 시크릿 값을 채우세요
  2) ROADMAP.md의 다음 미체크 항목 확인
  3) `make help` 로 사용 가능한 명령 확인
EOF
