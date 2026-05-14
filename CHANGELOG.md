# Changelog

이 파일은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 형식을 따르며,
[Semantic Versioning](https://semver.org/spec/v2.0.0.html)을 따른다.

릴리스 시 `pyproject.toml`의 버전과 이 파일의 헤더가 일치해야 한다 (release workflow에서 검증).

---

## [Unreleased]

### Added
- **Harness 문서** [Step 1.0]
  - `SPEC.md` — 기존 설계 명세서 분리 (마스터 설계 문서)
  - `CLAUDE.md` — 세션 컨텍스트 문서 (§8.2 형식)
  - `ROADMAP.md` — Step 1~6 진행 상태 (체크박스)
  - `DECISIONS.md` — ADR 양식 + ADR-0001/0002
  - `DEVELOPMENT.md` — 신규 환경 인계 / 부트스트랩 / 트러블슈팅
  - `CHANGELOG.md` — 변경 이력 (이 파일)
- **스캐폴딩** [Step 1.1]
  - `pyproject.toml` (Python ≥3.11, hatchling, Pydantic/Typer/structlog/tenacity/Jinja2 의존성)
  - `.python-version` (3.11)
  - `.gitignore` (`.env`/시크릿 보호, Python/uv/캐시 제외)
  - `.editorconfig`
  - `.pre-commit-config.yaml` + `.yamllint.yaml` (ruff, mypy, detect-secrets, yamllint)
  - `Makefile` (`setup`/`test`/`lint`/`fmt`/`up`/`down`/`clean`/...)
  - `README.md`
  - `etl_plugins/__init__.py` + `py.typed` (placeholder 패키지)
  - `uv.lock` (의존성 락파일 — 커밋 대상)
- **환경 재현** [Step 1.2]
  - `.env.example` (모든 시크릿 환경변수 placeholder)
  - `scripts/bootstrap.sh` (원커맨드 셋업: uv → .env → pre-commit → docker)
  - `docker/docker-compose.dev.yml` (postgres 16 / kafka 3.7 KRaft / minio / redis 7)
- **CI** [Step 1.3]
  - `.github/workflows/ci.yml` (lint, mypy, yamllint, detect-secrets, unit matrix py3.11/3.12, integration job, build smoke)
  - `.github/workflows/release.yml` (태그 트리거, 버전·CHANGELOG 일치 검증, uv build, PyPI Trusted Publishing, GitHub release)
  - `.secrets.baseline` (detect-secrets scan, results 비어있음)
  - `tests/` 초기 구조 (`__init__.py`, `conftest.py`, `test_package.py` 2 tests)
  - `pyproject.toml`: dynamic version (`etl_plugins/__init__.py.__version__` 단일 소스)

### Decisions
- ADR-0001: SPEC.md와 CLAUDE.md 역할 분리
- ADR-0002: Foundation 기술 스택 확정 (Python 3.11+, uv, Pydantic v2, Typer, structlog, hatchling, Apache-2.0)

### Changed
- (없음)

### Deprecated
- (없음)

### Removed
- (없음)

### Fixed
- (없음)

### Security
- (없음)
