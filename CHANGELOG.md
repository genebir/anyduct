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

- **Core 추상화** [Step 1.4]
  - `etl_plugins/core/exceptions.py` — `ETLError` 계층 (12개)
  - `etl_plugins/core/record.py` — Pydantic `Record` (extra="forbid")
  - `etl_plugins/core/schema.py` — `Field`, `Schema` (frozen, minimal)
  - `etl_plugins/core/connector.py` — `Connector` / `BatchSource` / `BatchSink` / `StreamSource` / `StreamSink` ABCs
  - `etl_plugins/core/registry.py` — `ConnectorRegistry` 데코레이터 + entry-point 자동 로드 (fail-soft)
  - `etl_plugins/core/context.py` — `Context` (uuid run_id, structlog bound logger)
  - `etl_plugins/core/pipeline.py` — `Pipeline` + `Task` (fluent builder) + `RunResult` + hooks (pre_run/post_run/on_error/on_task_start/on_task_end)
  - 패키지 루트 / `core/__init__.py` re-exports (28 public 심볼)
  - 단위 테스트 57개 (`tests/unit/core/`) — mypy strict / ruff / pytest 일괄 통과

- **Config 로더** [Step 1.5]
  - `etl_plugins/config/models.py` — Pydantic 모델: `ConnectionConfig`/`ConnectionsConfig` (extra=allow/forbid 이중 기준), `PipelineConfig`/`SourceConfig`/`SinkConfig`/`TransformConfig`/`RetryConfig`/`ObservabilityConfig`/`BufferConfig`/`CommitConfig`
  - `etl_plugins/config/secrets.py` — `SecretBackend` ABC + `EnvSecretBackend`/`StaticSecretBackend` + vault/aws_sm/gcp_sm `NotImplementedError` 스텁 + `get_secret_backend(name)` 팩토리
  - `etl_plugins/config/loader.py` — `${VAR}` 치환 (`[A-Z_][A-Z0-9_]*`만, 미설정 시 즉시 ConfigError) + `!secret <path>` YAML 태그 (`SecretRef`로 보존) + `load_yaml`/`load_config`/`load_connections`/`load_pipeline`/`load_dotenv`
  - 의존성 추가: `python-dotenv>=1.0` (deps), `types-PyYAML>=6.0` (dev)
  - 단위 테스트 49개 (`tests/unit/config/`)
  - 패키지 루트 re-exports: 43개 public 심볼로 증가

- **Observability 베이스** [Step 1.6]
  - `etl_plugins/observability/logging.py` — `configure_logging` (idempotent, JSON/dev console) + `mask_sensitive_values` structlog processor + `make_secret_masker` (substring keyword match, 14개 기본 키워드)
  - `etl_plugins/observability/metrics.py` — `Metrics`/`Counter`/`Histogram` ABC + `NoOp*` 기본 구현 + `get/set/reset_metrics` + SPEC.md §9.2 표준 메트릭 이름 5개 (`RECORDS_READ_TOTAL`, `RECORDS_WRITTEN_TOTAL`, `ERRORS_TOTAL`, `LAG_SECONDS`, `DURATION_SECONDS`)
  - `etl_plugins/observability/tracing.py` — `Tracer`/`Span` ABC + `NoOpTracer`/`NoOpSpan`. `Span.__exit__`가 예외 발생 시 자동으로 `record_exception` 호출
  - 단위 테스트 30개 (`tests/unit/observability/`)
  - 패키지 루트 re-exports: `configure_logging`, `Metrics`/`Counter`/`Histogram`/`Tracer`/`Span`, `get/set_metrics`, `get/set_tracer`

### Decisions
- ADR-0001: SPEC.md와 CLAUDE.md 역할 분리
- ADR-0002: Foundation 기술 스택 확정 (Python 3.11+, uv, Pydantic v2, Typer, structlog, hatchling, Apache-2.0)
- ADR-0003: Step 1.4 Core 추상화 주요 결정 (fluent builder, caller-managed lifecycle, hooks via `.on(...)`, batch-only Step 1.4, `ConnectError` 명명, 등)
- ADR-0004: Step 1.5 Config 로더 설계 결정 (로드 순서, `${VAR}` 단순 문법, `extra=allow/forbid` 이중 기준, SecretBackend ABC + 스텁)
- ADR-0005: Step 1.6 Observability 설계 결정 (ABC+NoOp 패턴, structlog JSON 기본, substring 마스킹, OTel 실구현은 Step 6로 이동)

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
