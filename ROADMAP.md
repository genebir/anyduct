# ROADMAP

> Step 단위 진행 상태. 체크박스로 관리. 작업 중단 시 해당 항목에 `← 작업 중 (YYYY-MM-DD, 다음 할 일: ...)` 표시.
> 모든 커밋 메시지에 Step 번호 포함 (예: `feat(connector): add postgres source [Step 2.1]`).
> 전체 설계 근거는 [`SPEC.md`](SPEC.md) §10 참조.

---

## 현재 상태 (2026-05-18)

**Steps 1–4 + Step 5.1(MySQL/SQLite) + Step 7.0~7.4 + Step 8.1~8.4 완료. 코어 344 unit + 서버 65 unit + 3 skip + 코어 119 it + 서버 87 it = 618 테스트, 24 ADR.**
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
- [ ] `/workspaces` `/users` `/roles`
- [ ] `/connections` (CRUD + `POST /{id}/test`)
- [ ] `/pipelines` (CRUD + 버전 관리, `GET /{id}/versions`)
- [ ] `/schedules` (CRUD + 활성화 토글)
- [ ] `/runs` (목록/상세/로그/메트릭 조회)

### 8.6 Action 엔드포인트
- [ ] `POST /pipelines/{id}/dry-run` — 파이프라인 빌드 검증 + 첫 100 record 샘플
- [ ] `POST /pipelines/{id}/trigger` — 즉시 실행 큐잉
- [ ] `POST /runs/{id}/retry` — 실패 run 재시도
- [ ] `GET /runs/{id}/logs` — 스트리밍 로그

### 8.7 테스트
- [ ] pytest + httpx async client
- [ ] testcontainers Postgres + fixtures
- [ ] 시나리오 테스트: 회원가입 → 워크스페이스 생성 → 연결 등록 → 파이프라인 생성 → 실행 → 결과 조회

---

## Step 9 — Execution Engine 통합

### 9.1 엔진 선택 확정 (ADR-0021)
- [ ] Dagster 임베드 / Prefect 임베드 / 자체 구현 비교 PoC
- [ ] 결정 후 의존성 추가, 코어의 기존 adapter 재활용 검증

### 9.2 스케줄러
- [ ] DB `schedules` 테이블 → 실행엔진의 schedule API로 동기화 (CRUD 시 트리거)
- [ ] cron 표현식 검증 (`croniter`)
- [ ] 일시정지 / 재개

### 9.3 Run 라이프사이클
- [ ] 트리거 → 실행엔진에 작업 제출
- [ ] 상태 변화 webhook 또는 polling → `runs` 테이블 갱신
- [ ] 로그 수집: 표준 출력 → `run_logs` (또는 Loki) 적재
- [ ] 메트릭 수집: 코어가 emit하는 OTel 메트릭을 Prometheus로

### 9.4 Stream worker manager
- [ ] long-running stream pipeline은 별도 process/Pod로 관리
- [ ] K8s 모드: `Deployment` per pipeline, healthcheck, graceful shutdown
- [ ] Docker Compose 모드 (로컬 dev): supervisor

### 9.5 정책 UI 노출
- [ ] retry / DLQ / 타임아웃 / 동시성 정책을 메타데이터 DB → UI에서 편집 가능

---

## Step 10 — Web UI (Next.js)

> 디자인 단일 진실은 `DESIGN.md`. 토큰 외 임의 값 금지. 진행 순서는 `DESIGN.md` §13.

### 10.0 디자인 토큰 구현
- [ ] `services/etlx-web/styles/globals.css` (CSS variables, DESIGN.md §11.1)
- [ ] `services/etlx-web/tailwind.config.ts` (DESIGN.md §11.2)
- [ ] 폰트 self-host (Inter Variable + Pretendard Variable + JetBrains Mono)
- [ ] Storybook 초기화 + 토큰 sanity stories (Colors / Typography / Spacing / Radius / Motion)
- [ ] Chromatic 또는 Playwright visual baseline

### 10.1 기초 컴포넌트 (shadcn/ui → 토큰 치환)
- [ ] Button / Input / Card / Badge / Tooltip / Tabs / Select / Dropdown / Separator
- [ ] Toast (Sonner)
- [ ] Sidebar (Arc-style, workspace 4px 컬러 bar)
- [ ] Header (검색 진입 + 액션 + theme toggle)
- [ ] Command Palette (cmdk, Cmd+K — DESIGN.md §7.5)
- [ ] 모든 컴포넌트 Storybook story 작성

### 10.2 레이아웃 셸
- [ ] Sidebar + Header + Content slot
- [ ] 워크스페이스 전환 (컬러 인디케이터 슬라이드)
- [ ] 다크/라이트 토글 + system preference + 로컬 저장
- [ ] 페이지 전환 motion preset 적용

### 10.3 Connection 관리
- [ ] 목록 / 생성 / 편집 / 삭제
- [ ] 시크릿 입력 폼 (값은 즉시 backend, DB에 평문 저장 금지)
- [ ] Test connection 버튼 + 결과 인디케이터

### 10.4 Pipeline Builder
- [ ] React Flow + 커스텀 노드 테마 (DESIGN.md §7.6, §11.4)
- [ ] 노드 타입: source / transform / sink / dlq
- [ ] Properties 패널 (Pydantic schema → 자동 폼)
- [ ] 저장 = metadata DB의 PipelineConfig JSON

### 10.5 Schedule + Run 모니터링
- [ ] cron 표현식 빌더 + cronstrue 자연어 해설
- [ ] 다음 N회 실행 예상 시각 미리보기
- [ ] Run 목록 (Data Table, status badge)
- [ ] Run 상세 (timeline + 로그 + 메트릭 차트)
- [ ] DLQ 조회 + 재처리

### 10.6 관리자 화면
- [ ] Workspace 관리 (생성/멤버/색)
- [ ] 멤버 + 역할 (Owner / Editor / Viewer / Runner)
- [ ] Audit log 뷰어 (필터, 시간 범위)

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
