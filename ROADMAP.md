# ROADMAP

> Step 단위 진행 상태. 체크박스로 관리. 작업 중단 시 해당 항목에 `← 작업 중 (YYYY-MM-DD, 다음 할 일: ...)` 표시.
> 모든 커밋 메시지에 Step 번호 포함 (예: `feat(connector): add postgres source [Step 2.1]`).
> 전체 설계 근거는 [`SPEC.md`](SPEC.md) §10 참조.

---

## 현재 상태 (2026-05-14)

**Steps 1–4 완료 + Step 5.1 RDBMS 일부(MySQL, SQLite). 321 단위 + 3 skip + 111 통합 = 435 테스트, 16 ADR.**
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

## Step 6 — 강화

- [ ] OpenLineage 이벤트 emit
- [ ] OTel / Prometheus 실제 백엔드 구현
- [ ] Checkpoint / Cursor abstraction + Pipeline span emit
- [ ] Contract test suite 완성
- [ ] mkdocs 문서 사이트
- [ ] v0.1.0 PyPI 릴리스 (CHANGELOG + GitHub release workflow)

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

### 7.1 모노레포 스캐폴딩
- [ ] `services/etlx-server/pyproject.toml` (코어 의존: `etl-plugins>=X.Y,<X+1`)
- [ ] `services/etlx-web/package.json` (Next.js + TS)
- [ ] 루트 README/CONTRIBUTING에 서비스 디렉토리 안내
- [ ] CI 매트릭스 분리: `ci-core.yml` / `ci-server.yml` / `ci-web.yml`
- [ ] **import-graph 검사**: `etl_plugins/*`가 `services/*`를 import하지 않음을 자동 검증 (CI에 lint step)

### 7.2 메타데이터 DB 스키마
- [ ] SQLAlchemy 2.x async 모델 정의
  - `workspaces`, `users`, `roles`, `memberships`
  - `connections` (workspace_id, type, config_json, secret_refs)
  - `pipelines`, `pipeline_versions` (이력)
  - `schedules` (cron / stream-active)
  - `runs`, `run_logs`, `run_metrics`
  - `audit_log` (actor + before/after JSON)
- [ ] Alembic 초기 마이그레이션 (`alembic init`, `alembic revision --autogenerate`)
- [ ] 테스트: testcontainers Postgres + factory_boy fixture

### 7.3 YAML ↔ DB 양방향
- [ ] `services/etlx-server/etlx_server/io/yaml_sync.py` — `configs/*.yaml` 읽어 DB upsert, 역방향 export
- [ ] CLI 확장: `etlx import <yaml_dir> --workspace=<id>`, `etlx export --workspace=<id> --to=<dir>`
- [ ] Pydantic 모델 재사용: `etl_plugins.config.models`의 `ConnectionConfig` / `PipelineConfig`을 그대로 DB 컬럼 검증에 사용

### 7.4 Secret backend UI 통합
- [ ] 입력 즉시 metadata DB에는 평문 저장 금지 — 시크릿 백엔드(Vault / AWS SM / GCP SM)에 저장하고 ref만 보관
- [ ] `EnvSecretBackend`의 NotImplemented 스텁 3종(`VaultSecretBackend`, `AwsSmSecretBackend`, `GcpSmSecretBackend`) 실제 구현
- [ ] 로컬 dev용 `FileSecretBackend` (path: file) — 평문 파일이지만 git 무시

---

## Step 8 — API Server (FastAPI)

### 8.1 부트스트랩
- [ ] FastAPI 앱 (`services/etlx-server/etlx_server/main.py`)
- [ ] uvicorn / gunicorn 진입점
- [ ] OpenAPI 자동 생성 + `/docs` / `/redoc`
- [ ] 헬스체크 `/health`, `/ready`

### 8.2 인증
- [ ] OIDC (Google / Azure AD / Okta 일반화) — authlib
- [ ] 로컬 계정 fallback (email + password, bcrypt)
- [ ] JWT 발급/검증 (RS256)
- [ ] `/auth/login`, `/auth/refresh`, `/auth/logout`

### 8.3 RBAC
- [ ] 역할: Owner / Editor / Viewer / Runner
- [ ] 워크스페이스 단위 격리: 모든 리소스에 workspace_id, 쿼리 자동 필터
- [ ] 의존성 주입: `Depends(require_role("editor"))`

### 8.4 Audit log
- [ ] 미들웨어: 모든 mutating 요청에 actor + path + before/after JSON 저장
- [ ] `/audit?resource=...&actor=...` 조회 API

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
