# ROADMAP

> Step 단위 진행 상태. 체크박스로 관리. 작업 중단 시 해당 항목에 `← 작업 중 (YYYY-MM-DD, 다음 할 일: ...)` 표시.
> 모든 커밋 메시지에 Step 번호 포함 (예: `feat(connector): add postgres source [Step 2.1]`).
> 전체 설계 근거는 [`SPEC.md`](SPEC.md) §10 참조.

---

## 현재 상태 (2026-05-18)

**Steps 1–4 + Step 5.1(MySQL/SQLite) + Step 7.0~7.4 + Step 8 전체 + Step 9.2(scheduler) + Step 9.3a + Step 9.3b(heartbeat + zombie reaper) 완료. 코어 344 unit + 서버 65 unit + 3 skip + 코어 119 it + 서버 223 it = 754 테스트, 24 ADR.**
**서비스화 방향 확정**: Step 7 이후로 `services/etlx-server` (FastAPI) + `services/etlx-web` (Next.js) 별도 패키지로 진행. 코어와 서비스는 단방향 의존 (서비스 → 코어). 자세한 결정은 ADR-0017.

---

## Step 1 — Foundation ✅ (2026-05-14 완료)

### 1.0 Harness 문서
- [x] `SPEC.md` 분리 (기존 CLAUDE.md → SPEC.md)
- [x] `CLAUDE.md` 작성 (세션 컨텍스트)
- [x] `ROADMAP.md` 작성
- [x] `DECISIONS.md` 작성 (ADR-0001 포함)
- [x] `DEVELOPMENT.md` 작성
- [x] `CHANGELOG.md` 초기화

### 1.1 프로젝트 스캐폴딩
- [x] `pyproject.toml` (uv, `requires-python>=3.11`, ADR-0002)
- [x] `.python-version` (3.11)
- [x] `uv.lock`, `.gitignore`, `.editorconfig`
- [x] `.pre-commit-config.yaml` + `.yamllint.yaml`
- [x] `Makefile`, `README.md`
- [x] `etl_plugins/__init__.py`, `py.typed`

### 1.2 환경 재현
- [x] `.env.example`, `scripts/bootstrap.sh`
- [x] `docker/docker-compose.dev.yml` (postgres 16, kafka 3.7 KRaft, minio, redis 7)
- [ ] `.devcontainer/devcontainer.json` *(optional, 나중에)*

### 1.3 CI
- [x] `.github/workflows/ci.yml` (lint, mypy, yamllint, detect-secrets, unit matrix py3.11/3.12, integration, build)
- [x] `.github/workflows/release.yml` (태그 트리거, PyPI Trusted Publishing)
- [x] `.secrets.baseline`, `tests/` 초기 구조, Dynamic version

### 1.4 Core 추상화
- [x] exceptions / record / schema / connector / registry / context / pipeline
- [x] 57 unit tests, mypy strict / ruff / pytest 통과

### 1.5 Config
- [x] models / secrets / loader, 49 unit tests

### 1.6 Observability 베이스
- [x] logging (structlog + secret masker) / metrics / tracing ABC + NoOp, 30 unit tests
- [ ] OTel/Prometheus 실제 백엔드 → Step 6
- [ ] Pipeline 자동 emit → Step 3.3에서 통합 완료

### 1.7 Utils
- [x] retry / chunk / async_io, 42 unit tests

### 1.8 테스트 인프라
- [x] fixtures + contracts (Batch) + InMemory 자체 검증 15 tests
- [ ] Stream contract → Step 2.3에서 추가됨

---

## Step 2 — Reference Connectors ✅ (2026-05-14 완료)

### 2.1 `postgres` (BatchSource + BatchSink) — ADR-0008
- [x] psycopg 3, server-side cursor, COPY/TRUNCATE+COPY/ON CONFLICT
- [x] 29 통합 테스트 (testcontainers postgres:16)

### 2.2 `s3` — ADR-0009
- [x] boto3 + pyarrow, jsonl/csv/parquet, MinIO 호환
- [x] 21 unit + 35 integration tests

### 2.3 `kafka` — ADR-0010
- [x] aiokafka StreamSource + StreamSink, Stream Contract 신규 도입
- [x] 16 통합 테스트 (KRaft mode)

---

## Step 3 — Pipeline 실행기 + CLI ✅ (2026-05-14 완료)

### 3.1 YAML 빌더 + Transforms + CLI (batch) — ADR-0011
- [x] runtime/transforms.py (rename / cast / filter sandbox / python)
- [x] runtime/builder.py, runtime/runner.py
- [x] Typer CLI: `version`, `list-connectors`, `validate`, `run`, `test-connection`
- [x] 38 unit tests

### 3.2 Stream runtime — ADR-0012
- [x] `Pipeline.arun_stream`, `etlx run-stream` + `--stop-after-*`
- [x] Kafka async `commit()` (ABC sync→async 보정)
- [x] Stream sink 버퍼 (`buffer.max_records` / `buffer.max_seconds`)
- [x] 17 unit + 2 Kafka integration tests

### 3.3 Retry / DLQ / Observability — ADR-0013
- [x] Retry: batch task-level, stream publish-level
- [x] DLQ: transform 실패 라우팅, best-effort
- [x] Pipeline.run 내부 자동 metrics emit
- [x] CLI global `--log-format json|console` / `--log-level`
- [x] 13 unit tests
- [ ] Checkpoint / Cursor → Step 6
- [ ] Pipeline span emit → Step 6

---

## Step 4 — Orchestrator Adapters ✅ (2026-05-14 완료) — ADR-0014

- [x] Airflow `ETLPluginsOperator`
- [x] Dagster `EtlPluginsResource` + `etl_plugins_op`
- [x] Prefect `run_etl_pipeline_flow` + `task`
- [x] PEP 562 lazy 로딩 — orchestrator 미설치 환경에서도 모듈 import 성공
- [x] 3 optional-dependencies extras + structural tests 7 + conditional tests 3

---

## Step 5 — Connector 확장 🔄 (진행 중)

### 5.1 RDBMS
- [x] MySQL/MariaDB (PyMySQL) — ADR-0015, 29 it tests
- [x] SQLite (stdlib) — ADR-0016, 32 unit tests
- [ ] MSSQL
- [ ] Oracle

### 5.2 Data Warehouse
- [ ] Snowflake (PUT/COPY INTO bulk load)
- [ ] BigQuery (load job)
- [ ] Redshift (COPY)
- [ ] ClickHouse

### 5.3 NoSQL
- [ ] MongoDB
- [ ] Redis
- [ ] DynamoDB
- [ ] Cassandra

### 5.4 Streaming
- [ ] Kinesis
- [ ] Pulsar
- [ ] RabbitMQ
- [ ] NATS

### 5.5 CDC
- [ ] Debezium (Kafka 위에 wrap)
- [ ] PostgreSQL logical replication

### 5.6 Object Storage
- [ ] GCS
- [ ] Azure Blob

### 5.7 HTTP/REST
- [ ] 일반 REST source (pagination 추상화)

---

## Step 6 — Core 강화: Asset / Lineage / Cursor 1급 모델 추가 (ADR-0024)

> 단순 "강화"가 아니라 **"데이터 통합 플랫폼"으로서의 모델 확장**.
> 버전 분할: **v0.1.0** = Cursor + 강화 일반 + 릴리스, **v0.2.0** = Asset + Lineage + Catalog API.

### 6.1 Cursor / Watermark abstraction → v0.1.0
- [ ] `etl_plugins.core.cursor`: `Cursor` protocol, `CursorState` (in-memory / file / DB-backed)
- [ ] `BatchSource.read_since(cursor=...)` 옵셔널 메서드 추가 (기본 구현은 query 그대로 — 기존 구현체 깨지지 않음)
- [ ] `Pipeline.run(cursor_from=..., cursor_to=...)` 백필 헬퍼
- [ ] postgres / mysql / sqlite 커넥터에 cursor 컬럼 활용 구현
- [ ] `tests/contracts/cursor.py` — cursor idempotency contract

### 6.2 OTel / Prometheus 실제 백엔드 → v0.1.0
- [ ] `etl_plugins.observability.metrics`의 OTel 구현 (`[observability]` extra)
- [ ] `etl_plugins.observability.tracing`의 OTel 구현 + `Pipeline.run` span emit
- [ ] Prometheus exporter (NoOp 외의 두 번째 실구현)

### 6.3 Contract test suite 완성 → v0.1.0
- [ ] 기존 Batch / Stream contract 점검
- [ ] Cursor contract 추가
- [ ] 누락된 edge case 보강 (timeout, partial failure, schema drift)

### 6.4 mkdocs 문서 사이트 → v0.1.0
- [ ] mkdocs-material 사이트 (`docs/` 디렉토리)
- [ ] API reference (mkdocstrings)
- [ ] 사용 예시 + Connector contribution 가이드 + Cursor 사용 가이드

### 6.5 v0.1.0 PyPI 릴리스 → v0.1.0
- [ ] `pyproject.toml` 버전 0.1.0 bump
- [ ] CHANGELOG 정리 (Keep a Changelog)
- [ ] `release.yml` 워크플로 점검 (PyPI Trusted Publishing)
- [ ] GitHub release + 태그 `core-v0.1.0`

### 6.6 Asset 1급 모델 → v0.2.0
- [ ] `etl_plugins.core.asset`: `Asset(name, schema, partitions, freshness_policy, deps)`, `AssetGroup`
- [ ] `@Asset.register("orders")` 데코레이터
- [ ] 기존 `Pipeline`을 "default Asset 1개를 materialize"하는 wrapper로 매핑 (backward compatible)
- [ ] Asset materialization contract

### 6.7 Lineage emit (OpenLineage) → v0.2.0
- [ ] `etl_plugins.observability.lineage` 모듈 + `openlineage-python>=1.0` (`[lineage]` extra)
- [ ] `Pipeline.run` / `arun_stream`에서 RunEvent (START/COMPLETE/FAIL/ABORT) 자동 emit
- [ ] Inputs/outputs를 source/sink connector + table/topic에서 추출
- [ ] 백엔드: NoOp 기본 / Marquez / 우리 자체 Catalog API

### 6.8 Catalog API (core) → v0.2.0
- [ ] `etl_plugins.catalog` — read-only API (`list_assets / get_asset / lineage(asset_name)`)
- [ ] In-memory 구현 + DB-backed 구현 (서비스가 메타DB로 wrap)
- [ ] Step 8의 REST API가 이 위에 얹힘

### 6.9 v0.2.0 PyPI 릴리스 → v0.2.0
- [ ] Asset/Lineage 사용 가이드 문서
- [ ] v0.2.0 bump + 릴리스

---

> **여기까지가 코어 라이브러리 (`etl_plugins`).** 아래부터는 그 위에 얹는 **별도 서비스 패키지 (`services/`)**. 코어는 서비스를 모른다 — 단방향 의존, SPEC.md §8.5 참조. 진행 전제: ADR-0017 (서비스화 전략 채택).

---

## Step 7 — Service Foundation (Metadata + Secret + YAML 양방향)

### 7.0 기술 스택 결정 (ADR 추가) ✅ (2026-05-14)
> ADR-0018은 이미 디자인 시스템에 사용되었으므로 이 그룹은 **ADR-0019~0023**으로 예약 (ADR-0017 본문과 일치).
> 프론트엔드 스택(Next.js / TypeScript / Tailwind v4 / shadcn/ui / React Flow)은 ADR-0018(디자인 시스템)에서 함께 결정되었으므로 별도 ADR을 두지 않는다.

- [x] **ADR-0019**: API 프레임워크 = FastAPI (>=0.115), Pydantic v2 통합, uvicorn+gunicorn, OpenAPI 자동
- [x] **ADR-0020**: 메타데이터 저장소 = PostgreSQL 16+ + SQLAlchemy 2.x async + Alembic, JSONB로 config 직렬화, UUID PK
- [x] **ADR-0021**: 실행 엔진 = **자체 PG-backed worker queue** (Dagster/Prefect 임베드 거부). `SKIP LOCKED` fan-out, `runs` 테이블이 큐 겸함. Step 9.1 PoC 후 본 구현.
- [x] **ADR-0022**: 모노레포 = uv workspace + pnpm workspace + CI 3분리 (`ci-core` / `ci-server` / `ci-web`) + `import-linter`로 단방향 의존 자동 검증
- [x] **ADR-0023**: 인증·인가 = OIDC 일반화(authlib) + 로컬 fallback(bcrypt) + JWT RS256(15min/7d) + PAT + 4 역할 RBAC(Owner/Editor/Runner/Viewer) + SuperAdmin + audit_log

### 7.1 모노레포 스캐폴딩 ✅ (2026-05-15)
- [x] `services/etlx-server/pyproject.toml` (uv workspace member, `etl-plugins` path dep + FastAPI/SQLAlchemy/asyncpg/Alembic/authlib/passlib/pyjwt/croniter)
- [x] `services/etlx-server/etlx_server/main.py` placeholder — `/health`, `/version` 2개 엔드포인트 + 2 unit tests (TestClient)
- [x] `services/etlx-web/package.json` (pnpm workspace, Next.js 15 + React 19 + TS 5.6) + `tsconfig.json` + `next.config.ts` + `app/{layout,page}.tsx` placeholder
- [x] 루트 `pnpm-workspace.yaml` + 루트 `pyproject.toml`에 `[tool.uv.workspace] members = ["services/etlx-server"]`
- [x] `services/docker-compose.services.yml` (etlx-server / etlx-web / metadata-db placeholder, profile별 분리)
- [x] CI 3분리: `.github/workflows/ci-core.yml` (rename from `ci.yml`, path filter + `import-linter` step) / `ci-server.yml` (lint+pytest, path filter) / `ci-web.yml` (pnpm typecheck+build, path filter)
- [x] **`.importlinter`** (ADR-0017 §6, ADR-0022 §4) — 2 contracts CI에서 자동 검증:
  - etl_plugins → services 차단
  - etl_plugins.core → connectors/adapters/runtime 차단
- [x] 루트 README CI 뱃지 3개로 분리

### 7.2 메타데이터 DB 스키마 ✅ (2026-05-16)
- [x] SQLAlchemy 2.x async 모델 정의
  - [x] `workspaces`, `users`, `memberships`, `personal_access_tokens` (ADR-0023)
  - [x] `connections` (workspace_id+name unique, type, config_json, secret_refs — 평문 금지)
  - [x] `pipelines`, `pipeline_versions` (immutable snapshot, version unique per pipeline)
  - [x] `schedules` (cron_expr nullable for stream mode, config_overrides JSONB)
  - [x] `runs`, `run_logs`, `run_metrics` — `runs`는 워커 큐 겸 결과 SSOT (ADR-0021)
  - [x] `audit_logs` (actor SET NULL, before/after JSONB, GIN 인덱스)
- [x] uuid7 PK (RFC 9562 — stdlib only, time-ordered) + TimestampMixin (server_default + onupdate)
- [x] StrEnum → Postgres native ENUM 5종 (auth_method, workspace_role, pipeline_mode, run_status, log_level)
- [x] Alembic 초기 마이그레이션 (`0001_initial_schema.py` — hand-written, 12 tables + 5 enums + indexes)
- [x] 테스트: testcontainers Postgres + per-test rollback isolation — **18 통합 테스트 통과**
  - workspace 모델 6 / connection 3 / pipeline 3 / run 4 (FOR UPDATE SKIP LOCKED 검증 포함) / audit 2
- [x] pytest-asyncio `default_fixture_loop_scope = "session"` (session-scoped 엔진 + 테스트 공유 루프)

### 7.3 YAML ↔ DB 양방향 ✅ (2026-05-16)
- [x] `services/etlx-server/etlx_server/io/yaml_sync.py` — connections.yaml + pipelines/*.yaml ↔ DB 양방향 + idempotent upsert + 변경 시에만 PipelineVersion 증가
- [x] CLI: `etlx-server import-yaml <yaml_dir> --workspace=<slug>` + `etlx-server export-yaml --workspace=<slug> --to=<dir>` (core `etlx` 와 분리 — ADR-0017 단방향 의존 유지)
- [x] Pydantic 모델 재사용: `etl_plugins.config.models`의 `ConnectionsConfig`/`ConnectionConfig`/`PipelineConfig`을 그대로 검증에 사용 (SSOT)
- [x] Secret 처리: `!secret <path>` → DB에 `${SECRET:<path>}` placeholder + `connections.secret_refs` 리스트만 저장 (평문 금지). 역방향 export에서 다시 `!secret` 태그로 복원
- [x] 9 통합 테스트 추가 (round-trip + idempotent + 버전 증가 + 스케줄 추가/삭제 + 스트림 모드)

### 7.4 Secret backend 구현 ✅ (2026-05-16)
- [x] `SecretBackend` 추상에 write 메서드 추가 — `.set(path, value)` + `.delete(path)`; read-only 백엔드는 `NotImplementedError`
- [x] 평문 0 약속: 메타데이터 DB 컬럼은 placeholder + ref 리스트만(Step 7.3에서 이미 구현). 실제 값은 항상 이 모듈의 backend가 보관/조회
- [x] `VaultSecretBackend` 실제 구현 — `hvac` KV v2, `${"value": ...}` 스키마, `url`/`token`/`mount_point` 인자 또는 `VAULT_*` env vars
- [x] `AwsSmSecretBackend` 실제 구현 — `boto3` Secrets Manager, ResourceExists면 `put_secret_value` fallback, `ForceDeleteWithoutRecovery=True`
- [x] `GcpSmSecretBackend` 실제 구현 — `google-cloud-secret-manager`, AlreadyExists면 새 version만 추가, `project_id` 인자 또는 `GCP_PROJECT`/`GOOGLE_CLOUD_PROJECT` env
- [x] `FileSecretBackend` (로컬 dev) — JSON 파일, atomic write(tmp + `os.replace`), `chmod 0600`, threading.Lock, **반드시 git-ignore 대상**
- [x] 신규 pyproject extras: `etl-plugins[vault|aws-sm|gcp-sm]`. 클라이언트 라이브러리는 lazy import — 모듈 import만으로는 무거운 SDK 안 끌어옴
- [x] 35 unit (Env read-only 검증 / Static set+delete / 9 File / GCP 풀 mock 6 / Vault constructor / AWS constructor / factory 4) + 4 Vault testcontainers it + 4 AWS SM LocalStack it

---

## Step 8 — API Server (FastAPI)

### 8.1 부트스트랩 ✅ (2026-05-16)
- [x] FastAPI 앱 — **factory 패턴** (`etlx_server.app_factory.create_app(settings)`)으로 재구성. `main.py`는 thin shim(`app = create_app()`)만 노출 → `uvicorn etlx_server.main:app` 그대로 동작
- [x] **Settings 클래스** (`etlx_server.settings.Settings`, Pydantic `BaseSettings`) — `database_url`/`environment`/`cors_origins`/`database_echo`/`service_name`. `.env` 파일 + env vars 둘 다 지원, `get_settings()` lru_cache
- [x] **라우터 분리**: `routers/health.py`(liveness+readiness), `routers/meta.py`(version). 도메인 단위로 분리해 향후 확장(`auth`, `workspaces`, ...) 시 main.py 변경 0
- [x] **Lifespan 핸들러**: 엔진 + session_factory를 `app.state`에 attach, shutdown 시 dispose. 인스턴스마다 별도 엔진(테스트별로 독립)
- [x] **DI 헬퍼**: `dependencies.py`의 `get_settings`/`get_engine`/`get_session_factory`/`get_session` — 엔드포인트가 모듈 글로벌 손대지 않고 `Depends`로 받음
- [x] `/health` (liveness, 외부 의존 없음) + `/ready` (readiness, `SELECT 1` DB ping → 실패 시 503 + `database: error` + 에러 클래스명)
- [x] `/version` (server / core / service / environment 4-tuple)
- [x] OpenAPI auto `/docs` + `/redoc` — `environment=production`이면 404로 숨김
- [x] CORS 미들웨어 — `cors_origins`가 비어있지 않으면만 attach (불필요할 땐 미들웨어 0)
- [x] 14 신규 unit(`test_health.py` 6 + `test_settings.py` 6 + factory/CORS 2) + 2 신규 it(`test_health_ready.py`, testcontainers Postgres 16)

### 8.2 인증
**원래 단일 슬라이스였으나 PR 크기 관리를 위해 a/b로 분리 (2026-05-16).**

#### 8.2a 로컬 인증 + JWT ✅ (2026-05-16)
- [x] `JwtService` 클래스 (RS256, `issue_access`/`issue_refresh`/`verify`, `Claims` dataclass, `TokenType` enum, verify-only 인스턴스 지원, extra_claims가 reserved claim 덮어쓰지 못함)
- [x] `PasswordService` 클래스 — bcrypt 직접 사용(passlib 미사용 — bcrypt 4.x 비호환), 72-byte 초과는 SHA-256 prehash + base64 후 bcrypt
- [x] `UserRepository`(get_by_email/id) — SQL 추상화, email은 lowercase로 정규화
- [x] 로컬 계정 fallback — bcrypt password_hash 검증, OIDC 사용자는 로컬 로그인 거부(401), unknown email 시 dummy hash compare로 timing leak 방지
- [x] `/auth/login`, `/auth/refresh`(access→refresh는 거부), `/auth/logout`(stateless 204), `/auth/me`(FE bootstrap)
- [x] `Depends(get_current_user)` — `HTTPBearer` + JwtService.verify + UserRepository 조회, 모든 실패 경로 401 + `WWW-Authenticate: Bearer`
- [x] Settings 확장: `auth_jwt_private_key_pem`/`auth_jwt_public_key_pem`(SecretStr) / `auth_jwt_issuer` / `auth_jwt_audience` / `auth_jwt_access_ttl_seconds`(900) / `auth_jwt_refresh_ttl_seconds`(7d) / `auth_local_enabled`(disable 시 503)
- [x] 신규 의존성: `bcrypt>=4.2`(passlib 제거), `pyjwt[crypto]>=2.9`(RS256), `email-validator>=2.2`(Pydantic EmailStr)
- [x] 15 신규 unit(`test_password_service.py` 4 + `test_jwt_service.py` 11) + 13 신규 it(`test_auth_router.py`, testcontainers PG, `app.dependency_overrides[get_session]`로 outer-trans rollback isolation 유지)

#### 8.2b OIDC ✅ (2026-05-18)
- [x] OIDC (Google / Azure AD / Okta / GitHub / generic 일반화) — `OidcService` + `OidcProviderConfig` 레지스트리. authlib는 deprecation(`authlib.jose`) 회피 위해 PyJWT + httpx 직접 사용. ADR-0023 implementation footnote에 명시.
- [x] `/auth/oidc/providers` (메타 — 시크릿 노출 0), `/auth/oidc/login`, `/auth/oidc/callback`
- [x] ID token RS256 + JWKS 검증 (kid 매칭, audience/issuer 강제, `email_verified=false` 거부, nonce 매칭). 신규 사용자 `UserRepository.provision_oidc_user`로 자동 프로비저닝 — provider명 → `AuthMethod` 매핑.
- [x] Settings 확장: `auth_oidc_enabled` / `auth_oidc_providers` (list[OidcProviderConfig] — 환경변수 JSON) / `auth_oidc_state_ttl_seconds` / `auth_oidc_http_timeout_seconds`
- [x] State CSRF + nonce: `OidcStateSigner`가 같은 RS256 키쌍으로 단명 state JWT(`token_type=oidc_state`) 서명/검증, 서버측 세션·쿠키 0
- [x] 이메일 충돌 안전성: LOCAL 계정과 동일 email 거부(409, 계정 탈취 방지), 다른 OIDC provider와 동일 email 거부(409). 동일 provider 재로그인 시 name만 갱신.
- [x] 6 신규 unit(OidcStateSigner) + 14 신규 unit(OidcService, IdP를 `httpx.MockTransport`로 모킹 — 진짜 ID token JWT 서명/검증) + 6 신규 it(provision_oidc_user) + 11 신규 it(OIDC router 전체 플로우)

### 8.3 RBAC ✅ (2026-05-18)
- [x] 역할: Owner / Editor / Runner / Viewer — `etlx_server/auth/rbac.py`가 strict hierarchy(Owner 4 > Editor 3 > Runner 2 > Viewer 1)로 모델링. `role_rank(role)` + `has_at_least(actual, required)`. Editor가 "all CRUD"이므로 trigger 포함 — Runner는 그 subset.
- [x] 의존성 주입: `Depends(get_current_workspace)`(workspace_id path param 로딩 + 404/403) → `Depends(require_workspace_role(WorkspaceRole.X))` (role 비교 + 403). SuperAdmin(`users.is_superadmin`) 무조건 bypass — 멤버십 없어도 통과(`WorkspaceContext.role = None`).
- [x] `WorkspaceContext` dataclass(user + workspace + role) + 도메인 분리(`MembershipRepository.get_role`, `WorkspaceRepository.get_by_id`).
- [x] 첫 RBAC-protected 엔드포인트 `GET /workspaces/{workspace_id}` (Viewer+ 필요) — Step 8.5에서 같은 dependency stack 위에 CRUD/list/멤버 관리 확장.
- [x] **워크스페이스 단위 격리(쿼리 자동 필터)는 Step 8.5로 이관** — 도메인 리소스 라우터가 추가되는 시점에 함께 구현해야 의미가 있음.
- [x] 15 신규 unit(rbac 9 + workspace_context 6) + 11 신규 it(membership repo 4 + workspaces router 7) — 각 역할(미인증/미존재 워크스페이스/비멤버/Viewer/Owner/SuperAdmin/malformed UUID) 경로 전부 커버.

### 8.4 Audit log ✅ (2026-05-18)
- [x] `etlx_server/audit/` 패키지: `AuditService`(세션 바운드, 호출자 트랜잭션 따라 commit/rollback — 비즈니스 mutation rollback 시 audit row도 자동 rollback) + `AuditLogRepository`(workspace/actor/resource_type/resource_id 필터 + limit/offset 페이지네이션 + created_at desc 정렬) + `RequestMeta`(ip + user_agent dataclass) + `AuditRequestMetaMiddleware`(starlette `BaseHTTPMiddleware` subclass — request.state.audit_meta에 부착) + `get_audit_service` Depends.
- [x] `/audit?workspace_id=&actor_user_id=&resource_type=&resource_id=&limit=&offset=` 조회 API — 이중 ACL: `workspace_id` 미지정 시 SuperAdmin 필수, 지정 시 해당 workspace의 Viewer+ 멤버 또는 SuperAdmin. 미존재 workspace에 비멤버가 query하면 행 존재 여부 leak 방지를 위해 403(404 아님). limit 1~200 강제(422).
- [x] **mutating endpoint에서 `audit_service.record(...)` 호출은 Step 8.5 CRUD 슬라이스와 같이 진행** — 8.4 단독으로는 적용 대상이 없어 (현재 mutating 라우터 = OIDC callback 1개) infra와 query API만 ship. Step 8.5 라우터가 같은 패턴으로 audit 호출.
- [x] 2 신규 unit(middleware — `TestClient`로 실 Starlette 앱에 부착) + 17 신규 it(audit_service 4 + audit_repository 5 + audit_router 8 — 미인증/비admin global 403/admin global/비멤버 403/Viewer/필터 조합/limit 0 422/미존재 workspace 403 안전 path).

### 8.5 CRUD 엔드포인트
**1 PR = 1 slice 원칙 유지를 위해 a~e로 분할 (2026-05-18).**

#### 8.5a Workspaces CRUD ✅ (2026-05-18)
- [x] `POST /workspaces` (인증된 사용자면 누구나, 호출자 자동 Owner 멤버십 동시 insert)
- [x] `GET /workspaces` (caller가 멤버인 워크스페이스 목록 + SuperAdmin은 전체)
- [x] `GET /workspaces/{id}` (Step 8.3에서 이미 ship)
- [x] `PATCH /workspaces/{id}` (Editor+, name/slug/color_hex 부분 갱신)
- [x] `DELETE /workspaces/{id}` (Owner only, FK cascade로 memberships 제거, audit row는 `workspace_id FK ON DELETE SET NULL`로 보존)
- [x] 각 mutation이 `audit_service.record(workspace.create / update / delete)` 호출 — 같은 트랜잭션에서 commit, 실패 시 audit row도 함께 rollback
- [x] `WorkspaceCreateRequest`/`UpdateRequest` Pydantic 검증(slug regex `^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$`, color_hex `#RRGGBB(AA)?`)
- [x] `WorkspaceSlugTakenError`(`IntegrityError` 변환)로 409 응답
- [x] 11 신규 it(POST happy + 409 + 422 invalid slug / list user memberships / list superadmin sees all / PATCH editor + audit before·after / runner 403 / empty body 400 / slug collision 409 / DELETE owner + audit + cascade / editor 403)

#### 8.5b Membership management ✅ (2026-05-18)
- [x] `GET /workspaces/{id}/memberships` (Viewer+) — User row와 join해서 email + name 포함, email 정렬
- [x] `POST /workspaces/{id}/memberships` (Owner — email + role 입력, unknown email 404, 중복 409, invalid role 422)
- [x] `PATCH /workspaces/{id}/memberships/{user_id}` (Owner — role 변경)
- [x] `DELETE /workspaces/{id}/memberships/{user_id}` (Owner)
- [x] **마지막 Owner 보호**: `MembershipRepository.update_role`/`remove`가 `count_owners(...) <= 1`이고 Owner를 demote/remove하면 `LastOwnerError` → 409. 자기 자신 mutation에도 동일 적용 — 자기 자신을 데모트/제거하더라도 다른 Owner가 남아있어야만 허용. 워크스페이스 orphan 방지.
- [x] 각 mutation이 `audit.record(membership.create / update / delete, resource_id=str(membership.id))` 호출 — DELETE는 row 삭제 전 `membership.id` 캡처 (Python 객체는 후에도 살아있음).
- [x] 15 신규 it (list 멤버/비멤버 403, POST owner-add + audit / non-owner 403 / unknown email 404 / dup 409 / invalid role 422, PATCH role 변경 + audit before·after / last-owner demote 409 / 다른 Owner 존재 시 자기 demote 허용 / unknown 404, DELETE owner-remove + audit + cascade / last-owner remove 409 + 행 그대로 유지 / 자기 제거 다른 Owner 존재 시 허용 / unknown 404).

#### 8.5c Connections CRUD ✅ (2026-05-18)
- [x] `/workspaces/{ws}/connections` 6 엔드포인트 — GET list (Viewer+), POST (Editor+), GET single (Viewer+), PATCH (Editor+), DELETE (Editor+), POST `/{id}/test` (Runner+).
- [x] **시크릿 처리 (ADR-0017 §6)**: 본문에 `{"$secret": "logical_key"}` sentinel 마커 + `secrets: {logical_key: plain_value}` 맵. 서버가 walker로 마커를 placeholder `${SECRET:etlx/{ws_id}/{conn_id}/{key}}`로 치환, 같은 트랜잭션에서 secret backend(Vault/AWS SM/GCP SM/File/Static)에 set(). DB에는 ref만 — 평문은 한 번도 저장 안 함. Orphan 마커/값 → 422. read-only backend(`EnvSecretBackend`) + secrets → 503. POST는 connection UUID를 backend write *전*에 mint하여 path가 row 평생 안정. PATCH는 secret 동기화 — 새 config에 없는 path 자동 backend.delete, 새 path는 set(). DELETE는 row 삭제 후 backend.delete_paths best-effort. 응답에는 placeholder만 — plain value 절대 안 떠남. <!-- pragma: allowlist secret -->
- [x] **`POST /{conn_id}/test`**: `ConnectionTester`가 placeholder들 backend.get으로 resolve → `ConnectorRegistry.get(type)` → blocking `connect()`/`health_check()`/`close()`를 `asyncio.to_thread`로 wrap. 응답 `{ok: bool, error: str|None}`. unknown connector type / secret resolution 실패 / health_check False 모두 `ok=False` + error 메시지.
- [x] 각 mutation이 `audit.record(connection.{create,update,delete})` + commit. PATCH/DELETE는 backend write 실패 시 best-effort 정리(`delete_paths`로 방금 쓴 path 복원) — DB 외부 부작용도 일관되게 처리.
- [x] 신규 `etlx_server/connections/` 패키지: `repository.py`(`ConnectionRepository` + `ConnectionNameTakenError`), `secrets.py`(`SecretWalker` + `SecretMarkerError` + `SecretBackendReadOnlyError`), `tester.py`(`ConnectionTester` + `ConnectionTestOutcome` + `SecretResolutionError`).
- [x] `SecretBackend`가 `app.state.secret_backend`에 attach (`Settings.secret_backend`/`secret_backend_file_path` 신규 필드, lifespan에서 `get_secret_backend(name, **opts)` 호출). 새 Depends `get_secret_backend_dep`.
- [x] 18 신규 it (POST happy + audit + secret backend write at expected path / orphan marker 422 / orphan value 422 / EnvSecretBackend 503 / dup name 409 / Viewer 403 / list workspace 단위 / cross-workspace GET 404 / PATCH rename only / PATCH secret rotation backend delete-then-set / PATCH empty 400 / PATCH secrets without config 400 / DELETE row + backend + audit / DELETE unknown 404 / test endpoint sqlite `:memory:` ok=True / unknown connector type ok=False + 에러 / 시크릿 resolve 후 test ok=True / Viewer test 403).

#### 8.5d Pipelines CRUD + 버전 관리 ✅ (2026-05-18)
- [x] `/workspaces/{ws}/pipelines` 6 엔드포인트 — GET list (Viewer+, 각 행에 current_version + config 포함), POST (Editor+, 201, Pipeline + PipelineVersion v1 원자적 insert), GET single (Viewer+), PATCH (Editor+, 부분 갱신), DELETE (Editor+, FK cascade로 versions+schedules 제거), GET `/{pid}/versions` (Viewer+, 버전 오름차순 + is_current 표시).
- [x] **버전 관리 idempotency** — `PipelineRepository.ensure_version`이 Step 7.3 yaml_sync 패턴 재사용: 새 `config_json`이 현재 is_current 버전과 동일하면 **새 row 안 만듦**(no-op), 다르면 prior `is_current=False` flip + 신규 `version=prev+1` insert. PATCH가 metadata만 보내면 버전 안 올라감, config 보내면 비교 후 결정. 응답의 `current_version` + audit의 `version_created` 플래그가 실제 변경 여부 노출.
- [x] **config 검증** — 코어의 `PipelineConfig.model_validate`로 구조 검증 (source/sink/transforms/...) — Pydantic ValidationError → 422 + 에러 체인. 서버가 `config["name"] = body.name` 주입해서 사용자가 두 곳에 name 안 써도 됨 (canonical JSON dump가 DB에 저장 — re-importable as YAML).
- [x] **신규 `etlx_server/pipelines/` 패키지**: `PipelineRepository`(list_for_workspace/get/get_current_version/list_versions/add[Pipeline+v1 원자적]/update_metadata[whitelist]/ensure_version[idempotency]/delete + snapshot 헬퍼) + `PipelineNameTakenError`.
- [x] 각 mutation은 `audit.record(pipeline.{create,update,delete})` + commit. PATCH의 audit.after_json에 `version_created` 플래그 포함 — 실제 config 변경된 PATCH와 metadata만 바꾼 PATCH를 forensics에서 구분.
- [x] 14 신규 it (POST happy + v1 + audit + config.name 서버 주입 / invalid config 422 / dup name 409 / Viewer 403 / list with current_version / GET 404 / PATCH metadata only — 버전 안 올라감 / PATCH config diff — 버전 증가 + prior is_current=False / **PATCH config identical — no-op + same version 유지** / 빈 PATCH 400 / name collision 409 / DELETE cascade + audit / GET /versions 다중 버전 history + is_current / versions 404).

#### 8.5e Schedules + Runs (read-only) ✅ (2026-05-18)
- [x] `/workspaces/{ws}/pipelines/{pid}/schedules` 6 엔드포인트 — GET list (Viewer+), POST (Editor+, 201, cron 검증), GET single (Viewer+), PATCH (Editor+, 부분 갱신, cron 재검증), DELETE (Editor+), POST `/{sid}/toggle` (Editor+, `is_active` flip). 스케줄은 pipeline 하위에 nest — schema가 1-pipeline-many-schedules + worker가 특정 pipeline_version에 정책 attach.
- [x] **Cron 검증** — `croniter.is_valid`로 만든 `validate_cron_for_mode(mode, cron_expr)`: batch는 cron_expr 필수, stream은 NULL 허용(연속 활성) — 둘 다 supplied 시 validate. 워커(Step 9)가 다음 firing time 계산할 때 같은 라이브러리 사용 — "저장된 schedule = 워커가 실행할 schedule" 보장.
- [x] **모드 immutable** — `ScheduleUpdateRequest`는 mode 없음. batch ↔ stream 전환 시 삭제 후 재생성. `ScheduleRepository.update`는 화이트리스트(name/cron_expr/is_active/config_overrides)로 unknown 필드 즉시 거부.
- [x] **Pipeline-workspace 경계** — 라우터의 `_resolve_pipeline_or_404`가 항상 pipeline.workspace_id == ctx.workspace.id를 검증 후 통과. 다른 워크스페이스 pipeline id로 접근 시 404 (정보 누설 방지 — "not found" / "not in your workspace" 동일 응답).
- [x] `/workspaces/{ws}/runs` 4 read-only 엔드포인트 — GET list (status/pipeline_id/schedule_id 필터 + limit/offset 페이지네이션, created_at desc), GET single (RunDetail = worker_id + heartbeat_at + error_message + result_json), GET `/{rid}/logs` (시계열 정렬), GET `/{rid}/metrics`. 모두 Viewer+. **mutation 엔드포인트 없음** — runs는 워커(Step 9)만 쓴다.
- [x] **No audit on runs** — read-only이므로 audit 없음. runs 자체 + run_logs 시계열이 "워커가 무엇을 했나"의 audit trail. 워크스페이스 boundary는 모든 GET에서 검증 — 다른 워크스페이스 run UUID 추측해도 404 (logs/metrics 포함).
- [x] **신규 패키지 둘**: `etlx_server/schedules/`(repository.py + `InvalidCronError`), `etlx_server/runs/`(repository.py — read-only, list/get/list_logs/list_metrics만). `ScheduleSummary`/`ScheduleCreateRequest`/`ScheduleUpdateRequest`/`RunSummary`/`RunDetail`/`RunLogEntry`/`RunMetricEntry` Pydantic.
- [x] 26 신규 it (스케줄 15 — batch+valid cron / stream+null cron / batch missing cron 422 / 잘못된 cron 422 / Viewer 403 / cross-ws pipeline 404 / list 정렬 / single 404 / PATCH cron + audit / PATCH invalid cron 422 / 빈 PATCH 400 / DELETE + audit / toggle ON↔OFF + audit / repository.update unknown 필드 ValueError / 다른 pipeline 하위 schedule 404 ; runs 11 — list / status 필터 / pipeline_id 필터 / 워크스페이스 경계(list + GET) / limit+offset / RunDetail / 404 / logs 시계열 / metrics / 다른 ws run logs 404 / mutation 메서드(POST/PATCH/DELETE) 404/405).

### 8.6 Action 엔드포인트 ✅ (2026-05-18)
- [x] `POST /workspaces/{ws}/pipelines/{pid}/dry-run` (Runner+) — 워크스페이스의 connection을 resolve → secret backend로 placeholder 풀이 → 코어 `build_pipeline` → 각 connector `connect/health_check/close` (parallel `asyncio.to_thread`). `DryRunResponse{ok, errors, connectors[{name,type,ok,error}]}` 반환. **첫 100 record 샘플은 의도적으로 미포함** — 실행 경로(retry/timeout/back-pressure)는 worker(Step 9)에 위임, 필요해지면 후속 슬라이스에서 worker-side `stop_after_records=N` 위에 얹는다.
- [x] `POST /workspaces/{ws}/pipelines/{pid}/trigger` (Runner+) — 현재 PipelineVersion으로 pending Run row 1개 enqueue (`schedule_id=NULL`, `triggered_by_user_id=ctx.user.id`). 202 Accepted + `RunSummary`. audit `run.trigger`(source=manual + version). 현재 버전 없는 파이프라인 → 409. ADR-0021 worker queue = runs 테이블 자체 — 별도 메시지 인프라 없음.
- [x] `POST /workspaces/{ws}/runs/{rid}/retry` (Runner+) — failed/cancelled run만 retry 허용(`RunNotRetryableError` → 409). 새 row가 원본의 `pipeline_version_id` + `schedule_id` 그대로 상속(retry는 "다시 실행", 최신 config로 trigger 아님), `result_json={"retry_of": original.id}`로 lineage 보존. **원본 row 절대 손대지 않음** — worker가 status 전이의 단일 writer. 202 Accepted. audit `run.retry`.
- [x] `GET /workspaces/{ws}/runs/{rid}/logs/stream` (Viewer+) — Server-Sent Events(SSE) `text/event-stream`. async generator가 fresh session per poll(`get_session_factory` Depends, 긴 트랜잭션 hold 방지)로 ~500ms마다 새 run_logs를 frame당 `event: log\\ndata: {...}` JSON으로 emit. run terminal + 2s idle 또는 client disconnect 시 graceful close. `Cache-Control: no-cache`, `X-Accel-Buffering: no` 헤더로 Nginx/CDN 버퍼링 방지.
- [x] 19 신규 it (pipeline_actions 11 + run_actions 8): dry-run happy(sqlite :memory:) / 누락 connection / unknown connector type per-connector 보고 / secret backend placeholder 풀이 / Viewer 403 / 404 / trigger happy + audit + 202 + Run row 검증 / Viewer 403 / 현재 버전 없음 409 / 404 + cross-ws 404; retry happy + audit + 원본 unchanged + retry_of lineage / succeeded 409 / running 409 / cancelled OK / Viewer 403 / cross-ws 404 ; SSE drain 3 frames + terminal+idle 자동 close / unknown run 404.

### 8.7 테스트 ✅ (2026-05-18)
- [x] pytest + httpx async client — Step 8.1부터 패턴 정착 (모든 라우터 테스트가 `httpx.AsyncClient` + `ASGITransport`)
- [x] testcontainers Postgres + fixtures — Step 7.2에서 도입(`metadata_db_container` 세션 fixture + per-test outer-transaction rollback)
- [x] **시나리오 테스트**(`tests/db/test_scenario_e2e.py`, 3 신규 it): ① **happy path** — login → POST workspace(Owner 자동) → POST 2 connections(src/dst sqlite :memory:) → POST pipeline(config) → POST schedule → POST dry-run(ok=true + connector 둘 다 health ok) → POST trigger(202) → GET runs list + single(status=pending, worker_id/heartbeat_at NULL — 워커 미존재 확인) → GET audit workspace_id 필터(`workspace.create` → `connection.create`×2 → `pipeline.create` → `schedule.create` → `run.trigger` 정확한 순서 검증, dry-run·GET은 audit 없음 확인). ② **retry-after-simulated-worker-failure** — trigger 후 ORM으로 Run.status = FAILED 강제(워커 simulate) → HTTP retry → 새 row의 `result_json.retry_of` lineage + 원본 row unchanged(status=FAILED, error_message 그대로) 둘 다 검증. ③ **non-member boundary** — Alice 워크스페이스에 Bob이 GET/list/dry-run/trigger 모두 403. **회원가입 엔드포인트는 아직 없음** — User 시드만 ORM 직접 insert(나머지 모든 step은 HTTP), 시나리오는 가입 이후 워크플로우만 검증. 시드된 user가 HTTP 측에서 보이도록 `await session.commit()` 후 login.

---

## Step 9 — Execution Engine 통합

### 9.1 엔진 선택 확정 ✅ (ADR-0021)
- [x] **결정**: Dagster/Prefect 임베드 대신 **자체 PG worker queue** — `runs` 테이블 자체가 큐, `FOR UPDATE SKIP LOCKED`로 다중 워커 안전 claim. 코어 어댑터 재활용은 Step 10 후 검토.

### 9.2 스케줄러 ✅ (2026-05-18)
- [x] DB `schedules` 테이블 → tick 마다 활성 schedule 평가, 다음 firing time에 pending Run row 생성 (worker가 그 후 claim). 새 `etlx_server/scheduler/` 패키지 — `Scheduler(factory, *, tick_interval_seconds=10.0)`, `tick_once()` 단일 패스 + `run()` poll 루프(`asyncio.Event` 기반 graceful stop). 새 `etlx-server scheduler run` CLI(SIGTERM/SIGINT graceful).
- [x] cron 표현식 검증 (`croniter`) — Step 8.5e에서 `validate_cron_for_mode` 구현 완료.
- [x] 일시정지 / 재개 — `is_active=False`는 `_load_due_schedules`에서 자동 필터. Step 8.5e `POST /schedules/{sid}/toggle`과 자연스럽게 연동.
- [x] no-migration 디자인 — "last firing"을 schedules row에 저장 않고 `MAX(runs.scheduled_at) WHERE runs.schedule_id=...`로 derive. `schedule.created_at`을 fallback base로 사용해 epoch backfill 방지.
- [x] safety: 인액티브 / stream(`cron_expr IS NULL`) / no-current-version / invalid cron 전부 skip-and-log (loop crash 안 함). 8 신규 it 테스트.

### 9.3 Run 라이프사이클
- [x] **9.3a (worker batch lifecycle, 2026-05-18) 완료** — 신규 `etlx_server/worker/` 패키지 + `etlx-server worker run` CLI. `claim_pending_run`(SQLAlchemy `with_for_update(skip_locked=True)` + ORDER BY scheduled_at, created_at + LIMIT 1, 같은 트랜잭션에서 status=running + worker_id + started_at + heartbeat_at 전이) + `RunExecutor`(fresh session per run, `_build`로 PipelineConfig 재검증 + connection 이름 resolve + `${SECRET:...}` 풀이 + 코어 `build_connector`/`build_pipeline`, 단일 `asyncio.to_thread`로 connect+run+close 일관 thread — sqlite3 같은 thread-bound driver 호환, 성공 시 records_read/written/duration_seconds/`core_run_id` 기록, 실패 시 error_class/message[2000자 trim]) + `RunWorker` 폴 루프(`asyncio.Event` stop, exception은 log + 계속, empty queue는 `contextlib.suppress(TimeoutError)` + `asyncio.wait_for`로 stop 또는 poll_interval 중 먼저). CLI: `etlx-server worker run --poll-interval / --worker-id / --secret-backend / --secret-file-path / --log-level`, SIGTERM/SIGINT 그레이스풀 종료. **현재 batch only** — `Pipeline.run` 호출이라 stream pipeline은 즉시 `_PipelineBuildError`로 failed. **헬퍼 공유 모듈** `etlx_server/pipelines/runtime.py`(`resolve_placeholders`/`referenced_connection_names`/`load_connections_by_name`) — DryRunService와 worker가 같은 빌드 경로를 공유해 "dry-run ok면 worker run도 build 단계는 통과" 보장. **코어 버그 수정**: `Pipeline._run_task`가 `task.sink_table`을 sink.write에 forwarding 안 하던 문제 발견 + 수정 — RDBMS sink(sqlite/postgres/mysql)의 batch 경로가 처음으로 동작. InMemorySink/S3 sink는 `**options`로 흡수해서 비영향. **Audit 정렬 안정화**: `AuditLogRepository.query`에 secondary sort `id.desc()` 추가(UUIDv7 시간 순서) — 같은 microsecond 내 audit row 정렬 안정. 11 신규 worker it (claim 4 + executor 5 + worker loop 2) + sqlite tmp_path fixture 활용한 실제 read→write 검증 (`records_read=records_written=3`).
- [x] **9.3b (heartbeat + zombie reaper, 2026-05-18) 완료** — 새 `etlx_server/worker/heartbeat.py`(`heartbeat_loop` async fn, asyncio main loop에서 별도 session으로 ~10s마다 `UPDATE runs SET heartbeat_at=now()`, exception은 log + 계속). `RunExecutor.execute`가 `pipeline.run`을 `asyncio.to_thread` 호출 *전에* heartbeat task spawn → finally에서 `stop_event.set()` + `await task` (CancelledError suppress). 새 `etlx_server/worker/reaper.py` `ZombieReaper(factory, *, heartbeat_timeout_seconds=60, scan_interval_seconds=30, batch_limit=100)` — `reap_once()`이 `SELECT ... WHERE status='running' AND heartbeat_at IS NOT NULL AND heartbeat_at < cutoff ORDER BY heartbeat_at LIMIT N FOR UPDATE SKIP LOCKED` → 각 행에 `status=failed` + `error_class='ZombieReaped'` + `error_message='worker X stopped heartbeating Ys ago (threshold Zs)'` + `finished_at`. **Auto-resubmit 의도적 안 함** — poison row가 N worker 죽이면 thundering herd. Step 8.6 retry 엔드포인트로 명시적 retry 가능. `heartbeat_at IS NOT NULL` 가드: 방금 claim된 row(stamp 이전 microsecond)는 보호. 새 `etlx-server reaper run` CLI(별도 process, `--heartbeat-timeout` / `--scan-interval` / `--batch-limit`, SIGTERM/SIGINT 그레이스풀). 11 신규 it (reaper 8 + heartbeat 3): stale → failed + msg에 worker_id 포함 / fresh untouched / NULL heartbeat untouched / terminal 3종 untouched / idempotent(두 번째 reap_once는 0) / batch_limit 준수 / run loop stop 응답 / heartbeat loop 시간 진행 / pre-set stop 시 stamp 없음 / DB hiccup 통과.
- [x] **9.3c (log/metric 수집, 2026-05-18) 완료** — 새 `etlx_server/worker/recorder.py` 패키지. `RunRecorder` 비동기 컨텍스트 매니저가 활성 run 별로 등록되고, `RecordingMetrics`(코어 `Metrics` ABC 구현)로 글로벌 metrics 백엔드 교체 + structlog `log_processor` 필터(event_dict의 `run_id`가 활성 recorder와 일치하면 enqueue). 두 producer 모두 `queue.SimpleQueue`(스레드 안전) — `asyncio.to_thread`에서 emit해도 안전. `__aexit__`에서 최종 한 번 flush (활성 맵에서 제거 → metrics 백엔드 복원 → run_logs/run_metrics 일괄 commit). 단일-flush 디자인 결정 사유: 주기적 drain은 executor의 메인 코루틴과 같은 `AsyncSession`을 동시에 commit 해야 하는데, 공유 세션을 쓰는 테스트 픽스처에서 동시 사용 충돌이 발생. live-tail SSE는 `/runs/{id}/logs/stream` 폴링으로 대체. **Executor 통합**: `RunExecutor.execute`가 4 lifecycle 이벤트(`run.build_started`/`build_failed`/`pipeline_started`/`pipeline_succeeded`/`pipeline_failed`)를 emit해 조용한 connector도 최소한의 trail 보장. **CLI 통합**: `etlx-server worker run` 시작 시 `configure_logging(extra_processors=[log_processor])`. **테스트**: 5 it (`test_worker_recorder.py`) — 매칭 run_id 이벤트 capture / metric capture / exit 후 stale 이벤트 drop / e2e 성공 시 lifecycle + RECORDS_READ/WRITTEN/DURATION 메트릭 / build 실패 시 `run.build_failed` 로그 + error_class.
- [ ] **메트릭 수집: 코어가 emit하는 OTel 메트릭을 Prometheus로** — Step 6 OTel 백엔드와 묶일 가능성.

### 9.4 Stream worker manager ✅ (2026-05-18, 단일 replica)
- [x] **`etlx_server/worker/stream.py` `StreamWorker`** + `etlx-server stream-worker run` CLI. tick loop이 active stream schedule을 스캔, 누락된 schedule마다 Run row(`running` status, started_at/heartbeat_at stamped)를 생성하고 `asyncio.Task`로 `Pipeline.arun_stream()` 실행. heartbeat + RunRecorder(periodic drain)도 동일하게 부착.
- [x] **종료 정책**: schedule이 비활성화/삭제되면 task cancel → Run row `status=cancelled` + `error_class='StreamCancelled'`. worker shutdown 시 in-flight 전체 cancel. CancelledError는 다시 raise해서 호출 컨텍스트가 cleanup 가능.
- [x] **No re-spawn loop**: build 실패(모드 mismatch, 누락 connection 등)로 task가 즉시 종료된 경우, 동일 schedule을 다음 tick에서 재시도하지 않음. user가 schedule을 수정(`updated_at` 변경)해야만 retry — poison schedule이 매 tick마다 Run row 양산하는 thundering herd 방지.
- [x] **Single-replica** — 다중 replica는 schedule row에 `FOR UPDATE SKIP LOCKED` claim 필요(Step 11 운영 강화로 미룸). 현재는 deployment에서 replica=1로 강제.
- [x] **Stream 모드 enforcement**: pipeline version의 `mode`가 `stream`이 아니면 `error_class='ModeMismatch'`로 즉시 stamp + skip.
- [x] 6 신규 it(스트림 spawn / inactive·batch ignored / mode mismatch / deactivation cancels / pre-set stop / pipeline without current version). 코어 Kafka 없이 monkeypatched `build_pipeline` + `_FakeStreamPipeline`(arun_stream = `asyncio.sleep` respecting cancellation)으로 worker lifecycle만 검증.
- [ ] K8s 모드: `Deployment` per pipeline, healthcheck — Step 11.
- [ ] Docker Compose 모드 supervisor — Step 11.

### 9.5 정책 UI 노출
- [ ] retry / DLQ / 타임아웃 / 동시성 정책을 메타데이터 DB → UI에서 편집 가능

---

## Step 10 — Web UI (Next.js)

> 디자인 단일 진실은 `DESIGN.md`. 토큰 외 임의 값 금지. 진행 순서는 `DESIGN.md` §13.

### 10.0 디자인 토큰 구현 ✅ (2026-05-18, UI 1 slice)
- [x] `services/etlx-web/app/globals.css` — DESIGN.md §11.1 토큰 전부 (`@theme` 블록을 통한 Tailwind v4 inline 매핑). dark default + `[data-theme="light"]` 분기. focus-visible / scrollbar / pulse keyframe 포함.
- [x] PostCSS 설정 (`postcss.config.mjs` → `@tailwindcss/postcss`). 별도 `tailwind.config.ts` 없음 — Tailwind v4 CSS-first 구성.
- [ ] 폰트 self-host (Inter Variable + Pretendard Variable + JetBrains Mono) — 시스템 폴백 fallback chain만 적용. self-host는 10.8 품질 게이트로 미루기.
- [ ] Storybook + Chromatic — 10.8 품질 게이트로 묶음.

### 10.1 기초 컴포넌트 ✅ (2026-05-18, UI 1 slice — partial)
- [x] Button / Input / Card / DataTable / StatusBadge / EmptyState — 모두 토큰 기반, shadcn 없이 직접 작성 (Tailwind v4 + 디자인 토큰 안전성). variant 5/size 3 Button(primary gradient/secondary/ghost/destructive/outline).
- [x] Sidebar (Arc-style, workspace 4px 컬러 인디케이터 via `box-shadow inset 4px 0`, 워크스페이스 picker dropdown, 활성 nav 핑크 강조).
- [x] Header (페이지 title/subtitle + theme toggle + user avatar + sign-out).
- [x] Toast (Sonner, top-right, 토큰 적용 className).
- [ ] Badge (general-purpose) / Tooltip / Tabs / Select / Dropdown / Separator / Command Palette — 10.6 관리자 화면 또는 후속 슬라이스에서 필요할 때 추가.
- [ ] Storybook story — 10.8.

### 10.2 레이아웃 셸 ✅ (2026-05-18, UI 1 slice)
- [x] `AppShell` — signed-in이면 Sidebar + 메인 area, signed-out / `/login`은 중앙 정렬 카드.
- [x] 워크스페이스 전환 — Sidebar dropdown + 4px 컬러 bar가 현재 선택을 반영. URL 변경 시 `useWorkspaceFromSlug` 훅이 자동 동기화.
- [x] 다크/라이트 토글 — `ThemeProvider` + `localStorage["etlx.theme"]` (dark default).
- [x] Auth 흐름 — `AuthProvider`가 `/auth/me` ping으로 현재 사용자 판정, 401 시 `etlx:unauthorized` 이벤트 + `/login` 리다이렉트, `next` 쿼리 보존.
- [ ] 페이지 전환 motion preset — 작업 안 함, 후속.

### 10.3 Connection 관리 ✅ (2026-05-18, edit는 rename만 — config 변경은 delete+recreate)
- [x] 목록 (`/w/[slug]/connections`) — `DataTable` + `Test` 버튼이 `POST /connections/{id}/test` 호출.
- [x] **생성 / 편집 / 삭제 UI** (2026-05-18) — Per-row Edit + Delete 버튼 + `ConfirmDialog` 모달. 생성 폼은 connector type pill picker(5종) + `lib/connector-schemas.ts`로 type별 필드 자동 렌더. Edit은 **이름 변경만** — config 변경은 secret 재입력이 필요하므로 "delete + recreate" 권장 메시지로 안내.
- [x] **시크릿 입력 폼** — `isSecret` 필드(postgres/mysql password, s3 access_key/secret_key, kafka sasl_password)는 type=password input으로 표시. 제출 시 `config`에는 `{"$secret": "<field_key>"}` 마커, `secrets`에는 plaintext를 담아 POST → 서버가 SecretBackend에 즉시 위임. 평문은 metadata DB에 절대 들어가지 않음(`SecretWalker` 검증). <!-- pragma: allowlist secret -->

### 10.4 Pipeline Builder ✅ (2026-05-18, UI 2 slice — initial)
- [x] React Flow(`@xyflow/react`) + 커스텀 `PipelineNode` (DESIGN.md §7.6 사양 충실 — bg-elevated/border-subtle/14 radius/accent handle, selected는 ring-accent).
- [x] 노드 타입: source(`postgres`/`mysql`/`sqlite`/`s3`/`kafka`) / transform(`rename`/`cast`/`filter`/`python`) / sink(같은 connector 5종). DESIGN.md §3.5 컬러 톤별로 accent 다르게.
- [x] 좌측 Palette — 그룹별 operator 카탈로그(클릭 add). 우측 Properties 패널 — 선택된 노드의 필드 자동 폼(connection은 workspace의 같은 type connection만 select, JSON 필드는 inline validation).
- [x] 저장 = `PATCH /pipelines/{id}` body.config → 코어 `PipelineConfig.model_validate` 통과 시 새 PipelineVersion(idempotent).
- [x] 신규 파이프라인 생성 — list page의 "New pipeline" 버튼이 blank `PipelineConfig`(default postgres source+sink, empty connection)로 POST 후 즉시 editor로 이동.
- [x] **Dry Run** — editor header에 `Dry run` 버튼. `POST /pipelines/{id}/dry-run`(server-side build + 병렬 `connector.health_check()`) 호출 후 canvas 아래 패널에 결과 표시. top-level config errors + per-connector check(ok/error 메시지 trace). Trigger 전에 안전하게 검증 가능.
- [x] **Pipeline-level retry + DLQ 설정** — `PipelineSettingsPanel` (canvas 빈 영역 클릭 시 우측 panel). 코어 `RetryConfig`(max_attempts/backoff/initial_delay) + `DlqConfig`(connection picker/table/topic/mode) 노출. Enable 토글로 비활성화 시 PipelineConfig JSON에서 키 자체 생략 — 빈 객체 오염 방지. Step 9.5의 일부 (retry/DLQ 정책 UI 노출).
- [ ] DLQ 노드 — 아직 (core PipelineConfig는 단일 dlq optional, builder UI에서 노출 안 함).
- [ ] Multi-branch / fan-out — core가 linear pipeline이라 의도적으로 미지원. 향후 graph 지원은 core 확장 필요.

### 10.5 Schedule + Run 모니터링 (← 작업 중, 2026-05-18 Schedule CRUD + Run 상세까지 완료)
- [x] Run 목록 (Data Table, StatusBadge, 5s polling) — `/w/[slug]/runs`. Row 클릭 시 상세 페이지로 이동.
- [x] Schedule 목록 — `/w/[slug]/schedules` (across pipelines flattened, cron/mode 표시).
- [x] **Run 상세** (2026-05-18) — `/w/[slug]/runs/[id]`. 2-col 레이아웃: 좌측은 mono 로그 viewer(레벨별 색, context_json inline), 우측은 Summary(상태/시간/duration/records/worker/heartbeat) + Metrics(name별 누적 + point 개수) + Error(error_class + error_message pre). 비-terminal status면 2초 polling, terminal이 되면 1회 더 fetch 후 정지. failed/cancelled에 Retry 버튼 → `POST /runs/{id}/retry`.
- [x] **Schedule CRUD + cron builder** (2026-05-18) — 새 `components/schedules/{cron-input,schedule-form}.tsx`. Create form: 이름/mode picker(batch/stream)/CronInput(6 preset chips + cronstrue 자연어 해설 inline + 5-field validation)/active checkbox. Edit form: 이름 + cron만(mode immutable). Per-row 액션: toggle(active ↔ paused via `POST /toggle`) / edit / delete(ConfirmDialog). 의존성 추가: `cronstrue ^3.4.x`(human-readable cron).
- [x] **다음 N회 실행 예상 시각 미리보기** (2026-05-18) — CronInput에 `cron-parser` 통합. 유효한 cron이면 다음 3 firing을 사용자 로컬 타임존 + 상대 시간(`in 5h 23m`)으로 표시. 미드-편집 invalid 상태에서는 조용히 비활성화. 의존성 `cron-parser ^5.x`(Luxon 포함, schedules 라우트 12.7→42.6 kB).
- [x] **Live mid-run logs via recorder periodic drain** (2026-05-18) — `RunRecorder`에 opt-in `flush_interval_seconds` 추가, 백그라운드 task가 N초마다 fresh session으로 commit. Default `None`(테스트 호환), `etlx-server worker run --log-flush-interval`(default 2.0s)로 production 활성화. Run 상세 페이지의 2s polling이 별도 변경 없이 live tail로 동작. 새 it 1개(periodic drain check + 명시적 cleanup). 진정한 SSE live-tail(EventSource via 토큰)은 별도 슬라이스(헤더-기반 인증을 query param으로 옮기거나 미들웨어로 sniff 필요).
- [ ] DLQ 조회 + 재처리.

### 10.6 관리자 화면 (← 작업 중, 2026-05-18 멤버 + 감사 로그까지 완료)
- [x] Workspace 목록 + 생성 — `/workspaces` (auto-Owner). 8 preset color picker.
- [x] Workspace 상세 메타 — `/w/[slug]/settings`. Owner는 name/slug/color rename 가능(slug 변경 시 자동 hot-redirect), SuperAdmin bypass도 동일 권한. Danger Zone에서 workspace 삭제(`ConfirmDialog`). Non-Owner는 read-only.
- [x] **Pipeline 삭제** UI — `/w/[slug]/pipelines` 각 row에 Delete 버튼(`ConfirmDialog`로 "all versions/schedules removed; runs stay for audit" 경고). `DELETE /pipelines/{id}`.
- [x] **멤버 + 역할 관리 UI** (2026-05-18) — `/w/[slug]/members`. 이메일로 추가(`POST /memberships`), 역할 인라인 변경(`PATCH /memberships/{user_id}` — owner/editor/runner/viewer select), 제거(`DELETE`). RBAC role별 색 칠한 badge + 역할 설명 legend. Owner 아닌 사용자는 read-only view. 자기 자신의 행은 변경/제거 비활성화 (서버는 LastOwnerError 409로도 보호). Sidebar에 Members nav 추가.
- [x] **Audit log 뷰어** (2026-05-18) — `/w/[slug]/audit`. `GET /audit?workspace_id=…` 호출(서버는 멤버이면 자동 ACL, SuperAdmin bypass). 필터: resource_type select(workspace/membership/connection/pipeline/schedule/run/all) + resource_id 정확 매치 input. row expand → before_json / after_json side-by-side pre 패널. Pagination via offset(100/page).

### 10.7 빈 상태 / 에러 / 로딩 패스 점검
- [ ] 전 페이지 empty state (DESIGN.md §7.11)
- [ ] 전역 error boundary
- [ ] Skeleton + 핑크 shimmer

### 10.8 품질 게이트
- [ ] axe-core a11y 검사 (AA 통과)
- [ ] Chromatic/Playwright visual regression baseline 5개 페이지
- [ ] keyboard-only 시나리오 테스트

---

## Step 11 — 운영 강화

- [ ] 멀티 테넌시 격리 부담 테스트 (workspace 100개 시뮬레이션)
- [ ] Helm chart (`services/charts/etlx/`) — postgres, vault, server, web, prometheus 일괄
- [ ] `services/docker-compose.services.yml` — 로컬 풀스택 실행
- [ ] 백업 가이드: metadata DB dump + 시크릿 백엔드 export
- [ ] 운영 메트릭 Grafana 대시보드 템플릿
- [ ] 첫 서비스 릴리스 (v1.0 — 코어는 0.x여도 서비스는 별도 SemVer)

---

## 변경 이력

- 2026-05-14: 최초 작성. Step 1 착수.
- 2026-05-14: Step 1.0 (Harness 문서), 1.1 (스캐폴딩), 1.2 (환경 재현) 완료.
- 2026-05-14: Step 1.3 (CI) 완료.
- 2026-05-14: Step 1.4 (Core 추상화) 완료. ADR-0003.
- 2026-05-14: Step 1.5 (Config 로더) 완료. ADR-0004.
- 2026-05-14: Step 1.6 (Observability 베이스) 완료. ADR-0005.
- 2026-05-14: Step 1.7 (Utils) 완료. ADR-0006.
- 2026-05-14: **Step 1.8 (테스트 인프라) + Step 1 전체 완료.** ADR-0007.
- 2026-05-14: **Step 2.1 (PostgreSQL) 완료.** ADR-0008.
- 2026-05-14: **Step 2.2 (S3) 완료.** ADR-0009.
- 2026-05-14: **Step 2.3 (Kafka) + Step 2 전체 완료.** ADR-0010.
- 2026-05-14: **Step 3.1 (YAML 빌더 + Transforms + CLI) 완료.** ADR-0011.
- 2026-05-14: **Step 3.2 (Stream runtime) 완료.** ADR-0012.
- 2026-05-14: **Step 3.3 (Retry/DLQ/메트릭) + Step 3 전체 완료.** ADR-0013.
- 2026-05-14: **Step 4 (Orchestrator Adapters) 완료.** ADR-0014.
- 2026-05-14: **Step 5.1 (MySQL) 완료.** ADR-0015.
- 2026-05-14: **Step 5.1b (SQLite) 완료.** ADR-0016.
- 2026-05-14: **서비스화 방향 확정 — ADR-0017.** Step 7~11 추가 (Service Foundation / API Server / Execution Engine / Web UI / 운영 강화). 코어와 서비스는 단방향 의존, 모노레포 구조 (`services/etlx-server`, `services/etlx-web`). Step 7.0에서 세부 기술 스택 ADR(0019~0023) 확정 예정. 다음 작업은 Step 5 계속 또는 Step 7.0 착수 — 우선순위 선택 필요.
- 2026-05-14: **디자인 시스템 채택 — ADR-0018.** `DESIGN.md` 신규 (Arc 영감, 네이비 베이스 + 팝핑크 강조, shadcn/ui + Tailwind v4 + Storybook). Step 10이 디자인 토큰 우선 순서(10.0)로 세부화됨. SPEC.md §3/§9.7/§10 패치. 다음 작업은 Step 5 계속 또는 Step 7.0 착수.
- 2026-05-14: **Step 7.0 (서비스 기술 스택 ADR) 완료.** ADR-0019(FastAPI) / ADR-0020(PostgreSQL+SQLAlchemy async+Alembic) / ADR-0021(자체 PG-backed worker queue) / ADR-0022(uv+pnpm workspace, CI 3분리, import-linter) / ADR-0023(OIDC+로컬 fallback+JWT RS256+4-role RBAC). 코드 변경 없음, 결정 문서만. 다음은 Step 7.1 모노레포 스캐폴딩.
- 2026-05-14: **ADR-0021 본문 강화.** Considered alternatives 표 + Operating envelope(~50 runs/sec, ~10k runs/day, p95 <2s) + Exit ramp(큐 폴링 코드를 한 파일에 격리) + PoC 합격 기준 4개 추가. 결정 자체는 유지 (자체 PG queue). 코드 변경 없음.
- 2026-05-14: **Step 6 격상 — ADR-0024.** "강화" 일반 항목을 "Asset/Lineage/Cursor 1급 모델 코어 추가"로 재구성. 9 서브슬라이스(6.1~6.9)로 분할, v0.1.0(Cursor+릴리스)과 v0.2.0(Asset+Lineage+Catalog) 두 단계로 쪼갬. 코드 변경 없음.
- 2026-05-15: **Step 7.1 (모노레포 스캐폴딩) 완료.** `services/etlx-server`(uv workspace member, FastAPI `/health`+`/version` placeholder, 2 unit tests) + `services/etlx-web`(pnpm workspace, Next.js 15 placeholder) + `pnpm-workspace.yaml` + 루트 `[tool.uv.workspace]` + CI 3분리(`ci-core.yml`/`ci-server.yml`/`ci-web.yml`) + `.importlinter` 2 contracts(CI 자동 검증, KEPT). 코어 코드 변경 0. 다음은 Step 7.2 메타DB 스키마.
- 2026-05-16: **Step 7.2 (메타데이터 DB 스키마) 완료.** `etlx_server/db/` 모듈 (uuid7 PK, TimestampMixin, 5종 PG ENUM, 12 테이블 모델: workspace/user/membership/PAT/connection/pipeline/pipelineVersion/schedule/run/runLog/runMetric/auditLog). Alembic 초기 마이그레이션 0001 (hand-written, 5 enum 타입 + 12 테이블 + 인덱스/UNIQUE). testcontainers Postgres + per-test rollback isolation + `default_fixture_loop_scope=session` 으로 **18 통합 테스트** 통과 — 워커 큐 `FOR UPDATE SKIP LOCKED` 패턴, 좀비 회수 인덱스(`ix_runs_heartbeat`), cascade/SET NULL FK 동작, JSONB 라운드트립 모두 검증. ADR-0020/0021/0023 결정 구현체 (신규 ADR 없음). 다음은 Step 7.3 YAML↔DB 동기화.
- 2026-05-16: **Step 7.3 (YAML ↔ DB 양방향) 완료.** `etlx_server/io/yaml_sync.py` + `etlx-server` 새 console script (`import-yaml`/`export-yaml`). 코어 `etl_plugins.config.models`를 SSOT로 재사용. `!secret <path>` 태그는 DB에 `${SECRET:<path>}` placeholder + `secret_refs` 리스트만 저장(평문 0); export 시 다시 `!secret` 로 복원되어 round-trip. `${VAR}` env-var placeholder는 import 시점에 절대 expand 하지 않고 그대로 보관. PipelineVersion은 config_json diff 시에만 증가(idempotent). 9 신규 통합 테스트 — 누적 27 server it + 변동 없는 core 321 unit + 2 server unit = **350 테스트** all green. ADR-0017 단방향 의존 유지(`etlx`는 core, `etlx-server`는 service). 다음은 Step 7.4 secret backend 구현.
- 2026-05-16: **Step 7.4 (Secret backend 구현) 완료.** `SecretBackend`에 write API 추가(`set`/`delete`, read-only면 NotImplementedError). 4종 실제 구현: `FileSecretBackend`(JSON, atomic write, 0600), `VaultSecretBackend`(hvac KV v2), `AwsSmSecretBackend`(boto3 SM, ResourceExists handling, ForceDelete), `GcpSmSecretBackend`(google-cloud-secret-manager, AlreadyExists handling). pyproject extras 3종(`vault`/`aws-sm`/`gcp-sm`) — 클라이언트 라이브러리는 lazy import. 35 신규 unit(File 9, GCP 풀-mock 6, Env/Static 확장, factory 4) + 4 Vault testcontainers it + 4 AWS SM LocalStack it. 누적 **코어 344 unit + 2 server unit + 코어 119 it + 27 server it = 492 테스트** all green. 신규 ADR 없음 — 기존 SecretBackend ABC를 확장한 것. 다음은 Step 8 (API Server / FastAPI) 또는 Step 5 코어 확장(MSSQL/Oracle/HTTP 등).
- 2026-05-16: **Step 8.1 (API Server 부트스트랩 / OOP 재구성) 완료.** `services/etlx-server`를 정규화된 OOP 구조로 재배치 — **factory 패턴** `app_factory.create_app(settings)`, **Settings** 클래스(`pydantic-settings`), **routers** 도메인 분리(`health`/`meta`), **DI 헬퍼** `dependencies.py`(`get_engine`/`get_session_factory`/`get_session`), **lifespan-managed engine**(인스턴스마다 별도 엔진, shutdown dispose). `/ready`는 진짜 DB `SELECT 1` ping (실패 시 503 + degraded). 14 신규 server unit + 2 readiness it(testcontainers PG 16). 누적 **코어 344 unit + 서버 **8 unit** + 코어 119 it + 서버 **29 it** = 500 테스트** all green. 신규 dep `pydantic-settings>=2.5`. 신규 ADR 없음 — ADR-0019(FastAPI) 구현체. 다음은 Step 8.2 (인증 — OIDC + JWT + bcrypt fallback, ADR-0023 구현).
- 2026-05-16: **Step 8.2 a/b 분리.** 단일 슬라이스가 너무 커져서 a(로컬 + JWT) / b(OIDC) 로 분할. 1 PR = 1 slice 원칙 유지.
- 2026-05-16: **Step 8.2a (로컬 인증 + JWT) 완료.** `auth/` 하위 패키지 신설 — `JwtService`(RS256, access/refresh + verify-only 모드), `PasswordService`(bcrypt 직접 — passlib는 bcrypt 4.x와 비호환), `UserRepository`(email/id 조회), `current_user.py` DI 헬퍼 + `get_current_user` Depends, Pydantic schemas. `routers/auth.py` 4 엔드포인트(login/refresh/logout/me). app_factory가 JwtService + PasswordService를 lifespan에서 `app.state`에 attach. 15 신규 unit(JwtService 11 + PasswordService 4) + 13 신규 it(`test_auth_router.py`, `app.dependency_overrides[get_session]`로 outer-trans rollback isolation 유지). 누적 **코어 344 unit + 서버 23 unit + 코어 119 it + 서버 47 it = 533 테스트** all green. 신규 deps `bcrypt>=4.2` + `pyjwt[crypto]>=2.9` + `email-validator>=2.2`. ADR-0023 로컬 fallback + JWT 부분 구현체. 다음은 Step 8.2b (OIDC, authlib).
- 2026-05-18: **Step 8.2b (OIDC) 완료.** `auth/` 확장 — `OidcProviderConfig`(Pydantic, provider명→`AuthMethod` 매핑), `OidcStateSigner`(RS256 키쌍 재사용, `token_type=oidc_state` 단명 JWT로 CSRF+nonce+return_to 무상태 운반), `OidcService`(provider 레지스트리, 디스커버리/JWKS 캐시, `build_authorize_url`+`handle_callback`, `httpx.AsyncClient` factory 주입 가능, PyJWT로 ID 토큰 RS256+JWKS 검증 — `authlib.jose` deprecated 회피), `UserRepository.provision_oidc_user`(LOCAL 계정/다른 OIDC provider와 email 충돌 시 `OidcEmailCollisionError`로 거부, 동일 provider 재로그인 시 name만 갱신). `routers/oidc.py` 3 엔드포인트(`/auth/oidc/providers`+`/login`+`/callback`) — 시크릿 노출 0, OIDC 미설정 시 503. app_factory가 `_build_oidc_service` 헬퍼로 lifespan에서 attach. 20 신규 unit(OidcStateSigner 6 + OidcService 14, IdP를 `httpx.MockTransport`로 모킹 + 진짜 ID token JWT 서명/검증으로 nonce/aud/iss/email_verified 경로 전부 커버) + 17 신규 it(provision_oidc_user 6 + OIDC 라우터 end-to-end 11). 누적 **코어 344 unit + 서버 48 unit + 코어 119 it + 서버 59 it = 570 테스트** all green, mypy strict 코어 39 + 서버 33 src files OK, lint-imports 2 KEPT. ADR-0023 OIDC 부분 구현체 — 신규 ADR 없음. 다음은 Step 8.3 (RBAC — workspace 단위 역할 + `Depends(require_role(...))`).
- 2026-05-18: **Step 8.3 (RBAC) 완료.** `auth/` 확장 — `rbac.py`(strict hierarchy Owner>Editor>Runner>Viewer, `role_rank`/`has_at_least`), `MembershipRepository.get_role`, `WorkspaceRepository.get_by_id`, `workspace_context.py`(`WorkspaceContext` frozen dataclass + `get_current_workspace` Depends [path param `workspace_id` 자동 추출, 404/403 일관 처리, SuperAdmin bypass 시 `role=None`] + `require_workspace_role(min_role)` factory). 첫 RBAC-protected 라우터 `routers/workspaces.py` (`GET /workspaces/{workspace_id}` Viewer+) — 모듈 레벨 `_require_viewer = Depends(...)`로 ruff B008 회피. app_factory에 router 추가. `WorkspaceSummary` Pydantic 스키마(`role: str | None`로 SuperAdmin 표현). 워크스페이스 단위 격리(쿼리 자동 필터)는 도메인 리소스 라우터가 추가되는 Step 8.5로 자연 이관. 15 신규 unit(rbac 9 + workspace_context 6) + 11 신규 it(membership repo 4 + workspaces router 7 — 미인증/미존재/비멤버/Viewer/Owner/SuperAdmin/malformed UUID). 누적 **코어 344 unit + 서버 63 unit + 코어 119 it + 서버 70 it = 596 테스트** all green, mypy strict 코어 39 + 서버 38 src files OK, lint-imports 2 KEPT. ADR-0023 RBAC 부분 구현체 — 신규 ADR 없음. 다음은 Step 8.4 (Audit log — 미들웨어 + `/audit` 조회).
- 2026-05-18: **Step 8.4 (Audit log) 완료.** 새 `etlx_server/audit/` 패키지 — `AuditService`(세션 바운드 recorder, 호출자 트랜잭션 따라 자동 rollback으로 false-positive 방지) + `AuditLogRepository.query`(workspace/actor/resource_type/resource_id 필터 + limit/offset + created_at desc) + `RequestMeta`(ip/user_agent dataclass) + `AuditRequestMetaMiddleware`(starlette `BaseHTTPMiddleware` subclass, request.state.audit_meta 부착) + `get_audit_service` Depends(meta 없으면 기본 RequestMeta() fallback). `routers/audit.py` `GET /audit` 이중 ACL — `workspace_id` 미지정 시 SuperAdmin 필수, 지정 시 Viewer+ 멤버 또는 SuperAdmin (비멤버는 미존재 workspace UUID로도 403 — enumeration leak 방지). limit 1~200 강제. `AuditLogEntry` Pydantic 응답(`from_attributes=True`로 ORM row → API mapping). app_factory에 middleware + router 추가. mutating 라우터의 `audit.record(...)` 호출은 도메인 mutation 라우터가 들어오는 Step 8.5에서 일괄 wire — 8.4 단독 적용 대상이 없어 infra + query API만 ship. 2 신규 unit(middleware) + 17 신규 it(audit_service 4 + repository 5 + router 8). 누적 **코어 344 unit + 서버 65 unit + 코어 119 it + 서버 87 it = 615 테스트** all green, mypy strict 코어 39 + 서버 44 src files OK, lint-imports 2 KEPT. ADR-0023 §9 audit 부분 구현체 — 신규 ADR 없음. 다음은 Step 8.5 (CRUD 엔드포인트 — `/connections`/`/pipelines`/`/schedules`/`/runs`, 각 mutation에서 `audit.record` 호출 + workspace_id 쿼리 자동 필터).
- 2026-05-18: **Step 8.5 a/b/c/d/e 분리.** Step 8.5의 도메인이 너무 넓어(workspaces+members+connections+pipelines+schedules+runs) 1 PR = 1 slice 원칙 유지를 위해 5개 슬라이스로 분할. 8.5a부터 진행.
- 2026-05-18: **Step 8.5a (Workspaces CRUD) 완료.** `WorkspaceRepository`에 `create`(워크스페이스 + Owner 멤버십 원자적 insert) / `list_for_user`(SuperAdmin은 include_all=True로 전체) / `update`(허용된 필드 외 거부 ValueError) / `delete`(FK cascade로 memberships 제거) + `snapshot()` 헬퍼(audit before/after용 dict) 추가. `WorkspaceSlugTakenError`로 `IntegrityError` 추상화. `WorkspaceCreateRequest`/`WorkspaceUpdateRequest` Pydantic 스키마 — slug regex + color_hex regex 검증. `routers/workspaces.py` 4 신규 엔드포인트 (POST 201 + Owner 자동 / GET list / PATCH Editor+ / DELETE Owner only) + 기존 GET/{id} 유지. 각 mutation이 audit.record + session.commit (`workspace.create` / `workspace.update` / `workspace.delete`) — 모듈 레벨 `_require_viewer`/`_require_editor`/`_require_owner` Depends 싱글톤으로 ruff B008 회피. **테스트 패턴 확립**: 호출자 commit이 conftest의 outer-trans rollback 안에서 savepoint release로 동작 (`expire_on_commit=False`), 테스트가 `session.commit()` 호출로 router 쓴 데이터를 자체 식별맵 갱신해서 audit row 조회. 11 신규 it (POST happy + audit + 멤버십 확인 / 409 slug 중복 / 422 invalid slug / list user 멤버십만 / list superadmin 전체 / PATCH editor + audit before·after / runner 403 / empty body 400 / slug 충돌 409 / DELETE owner + audit + cascade + workspace_id SET NULL / editor 403). 누적 **코어 344 unit + 서버 65 unit + 코어 119 it + 서버 98 it = 626 테스트** all green, mypy strict 코어 39 + 서버 44 src files OK, lint-imports 2 KEPT. ADR-0023 §5 RBAC + §9 audit 구현 적용 — 신규 ADR 없음. 다음은 Step 8.5b (Membership management — list/add/change-role/remove, 마지막 Owner 제거 보호).
- 2026-05-18: **Step 8.5b (Membership management) 완료.** `MembershipRepository`에 `get`(full row), `list_for_workspace`(User join + email 정렬), `count_owners`(워크스페이스 단위), `add`(UNIQUE 위반 → `MembershipExistsError`), `update_role`(마지막 Owner demote 시 `LastOwnerError`), `remove`(마지막 Owner remove 시 `LastOwnerError`) 추가. 새 `routers/memberships.py` 4 엔드포인트 (`/workspaces/{ws}/memberships` GET Viewer+/POST Owner, `/{user_id}` PATCH Owner/DELETE Owner). `MembershipSummary`/`MembershipCreateRequest`(email 입력)/`MembershipUpdateRequest` Pydantic — role은 `Literal["owner","editor","runner","viewer"]`로 422 강제. **마지막 Owner 보호**가 핵심: `update_role`/`remove`가 `OWNER` + `count_owners <= 1`이면 LastOwnerError로 409 — 자기 자신 mutation에도 동일 적용. DELETE는 row 삭제 전 `membership.id`를 Python 변수에 캡처하여 삭제 후 audit row에서 `resource_id`로 참조. 각 mutation은 `audit.record(membership.{create,update,delete})` + commit. 15 신규 it (list 멤버 + email 정렬 / 비멤버 403 / POST owner-add + audit + non-owner 403 + unknown email 404 + dup 409 + invalid role 422 / PATCH role 변경 + before·after audit + last-owner demote 409 + 다른 Owner 존재 시 자기 demote 허용 + unknown 404 / DELETE happy + audit + cascade + last-owner 409 + 다른 Owner 시 자기 제거 허용 + unknown 404). 누적 **코어 344 unit + 서버 65 unit + 코어 119 it + 서버 113 it = 641 테스트** all green, mypy strict 코어 39 + 서버 45 src files OK, lint-imports 2 KEPT. ADR-0023 §5 RBAC + §9 audit 구현 적용 — 신규 ADR 없음. 다음은 Step 8.5c (Connections CRUD — workspace-scoped + Secret backend 위임 + `POST /{id}/test`).
- 2026-05-18: **Step 8.5c (Connections CRUD + test) 완료.** 새 `etlx_server/connections/` 패키지 — `ConnectionRepository`(list/get/add/update/delete, 호출자 트랜잭션, UNIQUE → `ConnectionNameTakenError`), `SecretWalker`(`{"$secret": "key"}` sentinel → `${SECRET:etlx/{ws}/{conn}/{key}}` placeholder + orphan 마커/값 검출 + backend.set/delete 헬퍼, read-only backend → `SecretBackendReadOnlyError`), `ConnectionTester`(placeholder resolve → `ConnectorRegistry.get(type)` → `asyncio.to_thread`로 blocking `connect()`/`health_check()`/`close()` 실행, RegistryError/ConfigError/health_check False 모두 `ok=False` + error 메시지). 6 신규 엔드포인트 `/workspaces/{ws}/connections` (GET list Viewer+ / POST 201 Editor+ / GET single Viewer+ / PATCH Editor+ / DELETE 204 Editor+ / POST `/{id}/test` Runner+). 신규 `Settings.secret_backend`/`secret_backend_file_path` + lifespan이 `app.state.secret_backend` attach + `dependencies.get_secret_backend_dep` Depends. **연결 UUID를 backend write 전에 mint**해서 path가 row 평생 안정 — slug rename에도 secret 안 옮겨감. PATCH는 secret 라이프사이클 동기화 (added paths: backend.set, removed paths: backend.delete best-effort). DELETE는 row 삭제 후 backend.delete_paths. POST/PATCH 실패 시 방금 쓴 secret을 best-effort 정리. **평문 절대 노출 안 함** — 응답에는 placeholder만. ADR-0017 §6 시크릿 처리 완전 준수. 각 mutation은 `audit.record(connection.{create,update,delete})` + commit. 18 신규 it (POST happy + audit + 정확한 backend path 검증 / orphan 422 두 종류 / EnvSecretBackend 503 / dup 409 / Viewer 403 / list / cross-ws GET 404 / PATCH rename / PATCH secret 회전 backend delete+set / 빈 PATCH 400 / secrets-without-config 400 / DELETE cascade backend / unknown 404 / sqlite :memory: test ok=True / unknown connector type ok=False + 에러 / 시크릿 resolve 후 test ok=True / Viewer test 403). 누적 **코어 344 unit + 서버 65 unit + 코어 119 it + 서버 131 it = 659 테스트** all green, mypy strict 코어 39 + 서버 50 src files OK, lint-imports 2 KEPT. ADR-0017 §6 + ADR-0023 §5/§9 구현 적용 — 신규 ADR 없음. 다음은 Step 8.5d (Pipelines CRUD + 버전 관리, Step 7.3 yaml_sync의 PipelineVersion idempotency 패턴 재사용). <!-- pragma: allowlist secret -->
- 2026-05-18: **Step 8.5d (Pipelines CRUD + 버전 관리) 완료.** 새 `etlx_server/pipelines/` 패키지 — `PipelineRepository`(list_for_workspace/get/get_current_version/list_versions/add[Pipeline + PipelineVersion v1 원자적 insert]/update_metadata[name/description 화이트리스트]/ensure_version[Step 7.3 yaml_sync와 동일한 idempotency — current와 config_json 동일 시 no-op, 다르면 prior is_current=False + 신규 version=prev+1]/delete + snapshot 헬퍼) + `PipelineNameTakenError`. `routers/pipelines.py` 6 엔드포인트 — GET list (각 행에 current_version + config 포함), POST (Editor+ 201), GET single, PATCH (Editor+, 부분 갱신 — metadata만, config만, 또는 둘 다), DELETE (Editor+, FK cascade), GET /{pid}/versions (Viewer+, 오름차순 + is_current). **서버가 `config["name"] = body.name` 주입**해서 사용자 중복 입력 회피 — canonical JSON dump가 저장돼 YAML re-export 가능. core의 `PipelineConfig.model_validate`로 구조 검증 (Pydantic ValidationError → 422). 각 mutation은 audit.record + commit, PATCH의 audit.after_json에 **`version_created` 플래그** 포함 — 실제 config 변경된 PATCH와 metadata-only PATCH를 forensics에서 구분. `PipelineCreateRequest`/`PipelineUpdateRequest`/`PipelineSummary`(현재 버전 flatten)/`PipelineVersionEntry` Pydantic. 14 신규 it (POST + v1 + audit + name 주입 / invalid config 422 / dup 409 / Viewer 403 / list / GET 404 / PATCH metadata only no bump / **PATCH config diff bumps version + prior flipped** / **PATCH config identical no-op** / 빈 400 / name collision 409 / DELETE cascade / GET /versions multi-version / 404). 누적 **코어 344 unit + 서버 65 unit + 코어 119 it + 서버 145 it = 673 테스트** all green, mypy strict 코어 39 + 서버 53 src files OK, lint-imports 2 KEPT. ADR-0023 §5/§9 + Step 7.3 idempotency 패턴 재사용 — 신규 ADR 없음. 다음은 Step 8.5e (Schedules CRUD + 활성화 토글 + Runs 읽기 — 실행 트리거링은 Step 9).
- 2026-05-18: **Step 8.5e (Schedules CRUD + Runs read-only) 완료.** 새 두 패키지: `etlx_server/schedules/`(repository.py + `InvalidCronError` + `validate_cron_for_mode`), `etlx_server/runs/`(repository.py — list_for_workspace/get/list_logs/list_metrics, write 없음 — 워커(Step 9) 전용). `routers/schedules.py` 6 엔드포인트 — `/workspaces/{ws}/pipelines/{pid}/schedules` 하위에 nest (schema가 1-pipeline-many-schedules + worker가 PipelineVersion에 policy attach). GET list (Viewer+), POST 201 (Editor+, cron 검증), GET single (Viewer+), PATCH (Editor+, mode immutable, cron 재검증), DELETE 204 (Editor+), POST `/{sid}/toggle` (Editor+, is_active flip). 매 mutation `audit.record(schedule.{create,update,delete,toggle})` + commit. **Cron 검증**: `croniter.is_valid`로 만든 `validate_cron_for_mode` — batch는 cron_expr 필수, stream은 NULL 허용; 둘 다 supplied 시 validate. 워커가 다음 firing time 계산에 같은 croniter 쓸 예정 — "저장된 schedule = 워커가 실행할 schedule" 보장. **Pipeline-workspace 경계**: 라우터의 `_resolve_pipeline_or_404`가 항상 pipeline.workspace_id == ctx.workspace.id 검증 후 통과; cross-ws pipeline id 또는 다른 pipeline 하위 schedule id 모두 404로 collapse (enumeration leak 방지). `routers/runs.py` 4 read-only — `/workspaces/{ws}/runs` GET list (status/pipeline_id/schedule_id 필터 + limit/offset, created_at desc), GET single (`RunDetail` = worker_id + heartbeat_at + error_message + result_json 포함), GET `/{rid}/logs` (시계열), GET `/{rid}/metrics`. 모두 Viewer+. **No audit on runs** — read-only이므로 audit 없음, runs 자체 + run_logs 시계열이 워커 트레일. 워크스페이스 boundary는 logs/metrics 포함 전부 단일 run row 조회 후 통과 — 다른 ws run UUID 추측 404. `ScheduleSummary`/`ScheduleCreateRequest`/`ScheduleUpdateRequest`/`RunSummary`/`RunDetail`/`RunLogEntry`/`RunMetricEntry` Pydantic. **Schedule name 비유일** — Pipelines/Connections/Workspaces는 YAML(Step 7.3 yaml_sync)에서 name으로 addressing이라 unique이지만 schedule은 id로만 — 가벼운 자유로 유지. 26 신규 it (schedules 15 + runs 11). 누적 **코어 344 unit + 서버 65 unit + 코어 119 it + 서버 171 it = 699 테스트** all green, mypy strict 코어 39 + 서버 55 src files OK, lint-imports 2 KEPT. ADR-0023 §5/§9 + worker queue ADR-0021 의존성 분리(write 없음) — 신규 ADR 없음. 다음은 Step 8.6 (Action 엔드포인트 — dry-run/trigger/retry/logs streaming).
- 2026-05-18: **Step 8.6 (Action 엔드포인트) 완료.** 새 `etlx_server/pipelines/dry_run.py` — `DryRunService(session, backend).run(pipeline, current_version)`: 워크스페이스 connection 이름 resolve → `${SECRET:...}` placeholder backend로 풀이 → `ConnectionConfig` 검증 → 코어 `build_connector`/`build_pipeline` → 각 connector `connect`/`health_check`/`close`(parallel `asyncio.to_thread`). 단일 connector 실패 시 per-row 보고(`ConnectorCheck`), pipeline build 실패는 top-level errors. **첫 100 record 샘플은 의도적 미포함** — bounded read는 worker(Step 9)의 retry/timeout/back-pressure 정책과 묶여야 안전. `routers/pipelines.py`에 2 신규: `POST /workspaces/{ws}/pipelines/{pid}/dry-run`(Runner+, audit 없음 — read-only 검증) + `POST /{pid}/trigger`(Runner+, 202, `RunRepository.add_manual`로 pending row enqueue, audit `run.trigger`). `routers/runs.py`에 2 신규: `POST /workspaces/{ws}/runs/{rid}/retry`(Runner+, 202, `RunRepository.add_retry` — failed/cancelled만 허용 `RunNotRetryableError` → 409, 원본 row 절대 손 안 댐, 새 row가 `pipeline_version_id`+`schedule_id` 상속 + `result_json.retry_of=original.id` lineage, audit `run.retry`) + `GET /{rid}/logs/stream`(Viewer+, SSE). **SSE 디자인**: `StreamingResponse`(media_type=`text/event-stream`), `get_session_factory` Depends로 fresh session per ~500ms poll(긴 트랜잭션 hold 방지), frame당 `event: log\ndata: {RunLogEntry JSON}`, run terminal + 2s idle 또는 client disconnect 시 graceful close, `Cache-Control: no-cache` + `X-Accel-Buffering: no`로 Nginx/CDN 버퍼링 방지. **워커 단일 writer 유지** — HTTP는 retry로 새 row 만들 뿐 status 전이는 ADR-0021 worker만. `DryRunConnectorCheck`/`DryRunResponse`/`RunTriggerRequest` Pydantic. `RunRepository`에 `add_manual`/`add_retry` 추가 + `RunNotRetryableError`. 19 신규 it (pipeline_actions 11 + run_actions 8 + SSE drain test). 누적 **코어 344 unit + 서버 65 unit + 코어 119 it + 서버 190 it = 718 테스트** all green, mypy strict 코어 39 + 서버 60 src files OK, lint-imports 2 KEPT. ADR-0021 worker queue + ADR-0023 §5 RBAC 적용 — 신규 ADR 없음. 다음은 Step 8.7 (테스트 — 시나리오: 가입→워크스페이스→연결→파이프라인→실행→결과)와 **Step 9 (Execution Engine 통합)** — runs 큐를 실제 워커가 claim해서 코어 `run_pipeline_yaml` 호출 + heartbeat + zombie 회수.
- 2026-05-18: **Step 8.7 (시나리오 테스트) 완료.** 새 `services/etlx-server/tests/db/test_scenario_e2e.py` — 3 e2e 시나리오: (1) full happy path (login → workspace 생성 → 2 connection → pipeline + schedule → dry-run → trigger → run list/single → audit 정확한 순서 검증 `workspace.create→connection.create×2→pipeline.create→schedule.create→run.trigger`); (2) retry-after-simulated-failure (trigger → ORM으로 Run.status=FAILED 직접 set으로 워커 simulate → HTTP retry → 새 row의 `result_json.retry_of` lineage + 원본 unchanged 검증); (3) non-member boundary (Bob이 Alice 워크스페이스에 GET/list/dry-run/trigger 모두 403). 회원가입 엔드포인트 미존재 — User 시드만 ORM(commit 후 login), 나머지 모든 step은 public HTTP API로. **Step 8 (API Server) 완전 종료** — auth(local+OIDC) / RBAC(workspace-scoped) / audit(중간middleware + 이중 ACL `/audit`) / CRUD(workspaces+memberships+connections+pipelines+schedules) / runs read-mostly / actions(dry-run+trigger+retry+SSE logs) / e2e 시나리오 검증 모두 ship. 누적 **코어 344 unit + 서버 65 unit + 코어 119 it + 서버 193 it = 721 테스트** all green, mypy strict 코어 39 + 서버 60 src files OK, lint-imports 2 KEPT. 신규 ADR 없음. 다음은 **Step 9 (Execution Engine)** — 워커가 runs 큐 claim → 코어 `run_pipeline_yaml` 호출 + heartbeat + zombie 회수. Step 8.6에서 enqueue까지 끝났으니 9는 큐 consumer + status writer만 추가.
- 2026-05-18: **Step 9.3a (Worker batch lifecycle) 완료.** 새 `etlx_server/worker/` 패키지 — `claim_pending_run`(SKIP LOCKED 단일 row claim + status 전이) + `RunExecutor`(fresh session per run, build → 단일 thread connect+run+close, 성공/실패에 따라 records_*+duration / error_class+message 기록) + `RunWorker`(asyncio.Event-driven poll loop, exception은 log + 계속, empty queue idle은 stop_event.wait 우선). 새 `etlx-server worker run` CLI(typer subcommand, SIGTERM/SIGINT 그레이스풀). 신규 공유 모듈 `etlx_server/pipelines/runtime.py`(resolve_placeholders / referenced_connection_names / load_connections_by_name) — DryRunService와 worker가 빌드 경로 공유해 dry-run-passes-but-worker-fails 발산 방지. **코어 버그 수정**: `Pipeline._run_task`가 `task.sink_table`을 `sink.write`에 forwarding 안 하던 문제 발견 + 수정. RDBMS sink(sqlite/postgres/mysql)의 batch 경로가 처음으로 동작 — 기존 unit 테스트는 `InMemorySink`(`**options` 흡수)라 누락된 채로 통과되어 있었다. S3 sink도 `**options`이므로 영향 없음. **Audit 정렬 안정화**: `AuditLogRepository.query`에 secondary `id.desc()` 추가(UUIDv7 시간 순서) — 같은 microsecond 내 audit row의 ORDER BY 안정. 시나리오 e2e 테스트가 다른 테스트와 함께 실행될 때 발생하던 비결정성 해소. **OIDC state test 비결정성 수정**: `test_verify_rejects_tampered_token`이 base64 마지막 char 1bit flip으로는 underlying signature 동일할 수 있어 ~40% 실패. signature 전체를 명백히 wrong한 'AAA...'로 치환. 11 신규 worker it(claim 4 + executor 5 + worker loop 2) — sqlite tmp_path fixture로 실제 read→write 검증. 누적 **코어 344 unit + 서버 65 unit + 코어 119 it + 서버 204 it = 732 테스트** all green(269 server tests in suite, no skips), mypy strict 코어 39 + 서버 65 src files OK, lint-imports 2 KEPT. ADR-0021 worker queue 첫 구현 — 신규 ADR 없음. 다음은 Step 9.2 (스케줄러 — schedules 테이블 tick → pending Run 생성) 또는 9.3b (heartbeat + zombie reaper).
- 2026-05-18: **Step 9.3b (Heartbeat + ZombieReaper) 완료.** 새 `etlx_server/worker/heartbeat.py` — `heartbeat_loop(factory, run_id, *, stop_event, interval_seconds)`이 asyncio main loop에서 별도 fresh session으로 주기적 `UPDATE runs SET heartbeat_at=now()`. `RunExecutor.execute`가 `asyncio.to_thread(pipeline.run, ...)` 전에 heartbeat task spawn, finally에서 `stop_event.set() + await task` (CancelledError suppress). 새 `etlx_server/worker/reaper.py` `ZombieReaper(factory, *, heartbeat_timeout_seconds=60, scan_interval_seconds=30, batch_limit=100)` — `reap_once()`이 SKIP LOCKED로 stale running 일괄 claim → `status=failed` + `error_class='ZombieReaped'` + `error_message="worker {id} stopped heartbeating {s}s ago"` + `finished_at`. **Auto-resubmit 의도적 안 함** — poison row가 모든 worker 죽이면 thundering herd 발생, 명시적 retry는 Step 8.6 `POST /runs/{id}/retry`로 해결. `heartbeat_at IS NOT NULL` 가드로 방금 claim된 row 보호. 새 `etlx-server reaper run` CLI 서브커맨드(별도 process, `--heartbeat-timeout` / `--scan-interval` / `--batch-limit` / `--log-level`, SIGTERM/SIGINT 그레이스풀). 11 신규 it (reaper 8 + heartbeat 3). 누적 **코어 344 unit + 서버 65 unit + 코어 119 it + 서버 215 it = 743 테스트** all green(280 server tests in suite, no flakes), mypy strict 코어 39 + 서버 67 src files OK, lint-imports 2 KEPT. ADR-0021 worker queue 운영성 보강 — 신규 ADR 없음. 다음은 Step 9.2 (스케줄러 — schedules 테이블 tick → pending Run 생성) 또는 9.3c (log/metric forwarding — 코어 structlog → run_logs, metrics ABC → run_metrics).
