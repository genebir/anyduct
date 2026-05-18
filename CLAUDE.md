# CLAUDE.md — ETL Plugins 세션 컨텍스트

> Claude Code가 매 세션 시작 시 읽는 요약 컨텍스트. **전체 설계 명세는 [`SPEC.md`](SPEC.md)** 에 있다.
> 세션 시작 순서: ① 이 파일 → ② [`ROADMAP.md`](ROADMAP.md) → ③ 최근 커밋 로그.

---

## 1. 프로젝트 목적

**두 단계로 진화하는 프로젝트**:

1. **코어 라이브러리 (`etl_plugins`)** — 모든 데이터 소스/싱크(RDBMS, NoSQL, DW, Stream, Object Storage)에 대해 **통일된 인터페이스**를 제공하고, **어떤 오케스트레이터(Airflow/Dagster/Prefect/사내 워크플로우) 위에서도 재사용 가능한 Python ETL 플러그인 라이브러리**. Steps 1~6.

2. **서비스 패키지 (`services/etlx-server` + `services/etlx-web`)** — 코어 위에 얹는 **웹 UI 기반 ETL 서비스**. 파이프라인·연결·스케줄을 시각적으로 만들고 실행·모니터링. 비개발자도 사용 가능. Steps 7~11. **코어는 서비스를 모른다 (단방향 의존)** — 서비스 패키지가 통째로 사라져도 라이브러리는 그대로 동작.

핵심 가치:
- **Backend-agnostic** + **Orchestrator-agnostic** + **Config-driven** + **Batch/Stream 통합**
- **Service-layer optional**: 서비스 없이도 라이브러리 단독으로 완전 동작
- **Harness 코딩**: 어느 환경/누가 이어받아도 동일하게 재현·이어작업 가능

---

## 2. 아키텍처 요약

```
Web UI (Next.js)               ← Step 10 (서비스)
        ↑
REST API (FastAPI)             ← Step 8  (서비스)
        ↑
Metadata + Secret + Execution  ← Steps 7, 9 (서비스)
==================================== 서비스/코어 경계
Orchestrator Adapters (Airflow / Dagster / Prefect / etlx CLI)
        ↑
Pipeline Core (Pipeline / Task / Context / Hooks)
        ↑
Connector Abstraction (BatchSource/Sink, StreamSource/Sink, Record)
        ↑
Connectors (plugins) (postgres, mysql, sqlite, s3, kafka, ...)
        ↑
Foundation (Config / Secrets / Logging / Metrics / Retry)
```

상하 단방향 의존. **위 층이 아래 층을 import할 뿐 그 반대는 절대 금지.** 상세 다이어그램과 인터페이스 정의는 `SPEC.md` §2, §4 참조.

---

## 3. 디렉토리 규약 (목표 구조)

```
etl-plugins/                    # 리포 루트 (모노레포)
├── CLAUDE.md / SPEC.md / DESIGN.md / ROADMAP.md / DECISIONS.md / DEVELOPMENT.md / CHANGELOG.md
├── pyproject.toml / uv.lock / .env.example / .pre-commit-config.yaml / Makefile
├── configs/{connections.yaml, pipelines/*.yaml, logging.yaml}
├── docker/docker-compose.dev.yml
├── scripts/bootstrap.sh
│
├── etl_plugins/                # ─── 코어 라이브러리 (Steps 1~6)
│   ├── core/                   # connector, record, schema, pipeline, context, registry
│   ├── config/                 # loader, models, secrets
│   ├── connectors/             # rdbms / nosql / warehouse / stream / object_storage
│   ├── adapters/               # airflow / dagster / prefect (PEP 562 lazy)
│   ├── observability/          # logging / metrics / tracing
│   ├── runtime/                # transforms / builder / runner
│   ├── utils/                  # retry / chunk / async_io
│   └── cli.py
│
├── services/                   # ─── 서비스 패키지 (Steps 7~11, 별도 pyproject)
│   ├── etlx-server/            # FastAPI: api / db / auth / scheduler / workers / execution
│   ├── etlx-web/               # Next.js: app / components / lib
│   └── docker-compose.services.yml
│
└── tests/{unit, integration, contracts, fixtures}
```

**의존 규칙 (CI에서 자동 검증)**:
- `etl_plugins/` → `services/` import **금지**
- `services/etlx-server/` → `etl-plugins`(PyPI) **허용**
- `services/etlx-web/` ↔ `etlx-server/` 는 HTTP REST로만 통신

전체 트리는 `SPEC.md` §3.

---

## 4. 자주 쓰는 명령어

```bash
# 환경 셋업
./scripts/bootstrap.sh           # 원커맨드: uv sync + pre-commit + docker compose up
make setup / test / lint / fmt / up / down / clean

# 코어 실행
uv run etlx run        configs/pipelines/<x>.yaml --connections configs/connections.yaml
uv run etlx run-stream configs/pipelines/<x>.yaml --connections configs/connections.yaml --stop-after-records 1000
uv run etlx test-connection <name|--all> --connections configs/connections.yaml
uv run etlx list-connectors
uv run etlx validate <config.yaml>
uv run etlx --log-format console --log-level DEBUG run ...

# 품질 게이트
uv run pytest                            # unit
uv run pytest tests/integration -m it    # testcontainers
uv run ruff check . && uv run ruff format .
uv run mypy etl_plugins
```

> **서비스 패키지가 추가되면(Step 7~)** 다음이 추가될 예정:
> ```bash
> # services/etlx-server
> uv run --package etlx-server uvicorn etlx_server.main:app --reload
> uv run --package etlx-server alembic upgrade head
>
> # services/etlx-web
> pnpm --filter etlx-web dev
> ```

---

## 5. 현재 단계

**Step 9.3c(log/metric forwarding) + Step 10.5 Run 상세 페이지 완료 → 다음은 cron builder / 멤버 관리 / 감사 UI 또는 Connection 생성·편집 폼** (2026-05-18 기준). Steps 1–4 + Step 5.1 + Step 7.0~7.4 + Step 8 전체 + **Step 9.2(cron scheduler) + 9.3a(worker claim+execute) + 9.3b(heartbeat-during-execution + ZombieReaper) + 9.3c(log/metric → run_logs/run_metrics)** + **Step 10 UI 1(`services/etlx-web` foundation) + UI 2(`@xyflow/react` Pipeline Builder) + 10.5 Run 상세 페이지** 완료. **344 코어 단위 + 65 etlx-server 단위 + 3 skip + 119 코어 통합 + 228 서버 통합 = 759 테스트** all green, **24 ADR**. **etlx-web**: Next 15 + React 19 + Tailwind v4 (CSS-first `@theme`), DESIGN.md §11.1 토큰, Arc-style 사이드바(워크스페이스 4px 컬러 bar), Auth/Theme/Workspace Provider, REST 클라이언트 + 운영자 카탈로그(`lib/operators.ts` 14종), PipelineConfig serializer(`lib/pipeline-config.ts`), `/w/[slug]/pipelines/[id]/edit` Pipeline Builder(좌 Palette / 중앙 React Flow canvas / 우 Properties panel). `pnpm --filter @etlx/web build` 통과(8 routes, max 187kB first-load JS — editor). `mypy strict` 코어 39 + 서버 67 src files OK, `lint-imports` 2 contracts KEPT. **OOP 정규화 일관 적용**: services(`JwtService`/`PasswordService`/`OidcService`/`OidcStateSigner`/`AuditService`/`SecretWalker`/`ConnectionTester`/`DryRunService`) + repository(User/Workspace/Membership/Connection/Pipeline/Schedule/Run/AuditLog) + 도메인 라우터(`auth.py`/`oidc.py`/`workspaces.py`/`memberships.py`/`connections.py`/`pipelines.py`/`schedules.py`/`runs.py`/`audit.py`) + `Depends`-기반 DI + 모듈 레벨 `_require_*` Depends 싱글톤(ruff B008 회피) + 도메인 예외(`WorkspaceSlugTakenError`/`MembershipExistsError`/`LastOwnerError`/`ConnectionNameTakenError`/`SecretMarkerError`/`SecretBackendReadOnlyError`/`PipelineNameTakenError`/`InvalidCronError`/`RunNotRetryableError`)로 라우터에서 깨끗한 4xx/5xx 매핑.

추가로 **서비스화 방향 확정**(ADR-0017): `services/etlx-server`(FastAPI) + `services/etlx-web`(Next.js)을 별도 패키지로 Step 7부터 진행. 코어와 서비스는 단방향 의존.

다음 할 일은 항상 `ROADMAP.md`의 첫 번째 **미체크 + "← 작업 중" 표시** 항목.

Step별 산출물 요약:

- ✅ Step 1 (Foundation): 스캐폴딩, Harness 문서, Core/Config/Observability/Utils/테스트 인프라 — ADR-0001~0007
- ✅ Step 2 (Reference Connectors): `postgres`(psycopg3), `s3`(boto3+pyarrow, MinIO 호환), `kafka`(aiokafka, Stream Contract) — ADR-0008~0010
- ✅ Step 3 (Pipeline 실행기 + CLI): YAML 빌더 + 5 CLI 서브커맨드, Stream runtime, Retry+DLQ+자동 메트릭 — ADR-0011~0013
- ✅ Step 4 (Orchestrator Adapters): Airflow/Dagster/Prefect PEP 562 lazy — ADR-0014
- 🔄 Step 5 (Connector Expansion):
  - 5.1 RDBMS: ✅ MySQL(ADR-0015) ✅ SQLite(ADR-0016). MSSQL/Oracle 남음
  - 5.2 DW / 5.3 NoSQL / 5.4 Streaming / 5.5 CDC / 5.6 Object / 5.7 HTTP — 미착수
- ⏸ Step 6 (Core 강화 — **Asset/Lineage/Cursor 1급 모델 추가**, ADR-0024): 9 서브슬라이스. v0.1.0=Cursor+OTel+mkdocs+PyPI / v0.2.0=Asset+Lineage+Catalog. 미착수
- 🔄 Step 7 (Service Foundation):
  - 7.0 ✅ 기술 스택 ADR-0019~0023 작성 완료 — FastAPI / PostgreSQL+async+Alembic / 자체 PG worker queue / uv+pnpm 모노레포+CI 3분리 / OIDC+RBAC
  - 7.1 ✅ 모노레포 스캐폴딩 완료 — `services/etlx-server`(uv workspace + FastAPI placeholder) + `services/etlx-web`(pnpm + Next.js placeholder) + CI 3분리 + `.importlinter`
  - 7.2 ✅ 메타데이터 DB 스키마 완료 — 12 테이블 + 5 PG enum + Alembic 초기 마이그레이션 + uuid7 PK + 18 testcontainers 통합 테스트(ADR-0020/0021/0023 구현)
  - 7.3 ✅ YAML↔DB 양방향 완료 — `etlx_server/io/yaml_sync.py` + `etlx-server` CLI(`import-yaml`/`export-yaml`). `!secret` 평문 0 약속 유지, idempotent PipelineVersion, 9 신규 it 테스트
  - 7.4 ✅ Secret backend 구현 완료 — `SecretBackend`에 write API(set/delete) + 4 구현체(File/Vault/AWS SM/GCP SM). lazy import + 3 pyproject extras. 35 unit + 8 testcontainers it(Vault + LocalStack)
- 🔄 Step 8 (API Server / FastAPI):
  - 8.1 ✅ 부트스트랩 + OOP 재구성 — factory 패턴, Settings, routers 분리, lifespan engine, /ready DB ping
  - 8.2a ✅ 로컬 인증 + JWT — JwtService(RS256) + PasswordService(bcrypt) + UserRepository + /auth/login/refresh/logout/me + Depends(get_current_user)
  - 8.2b ✅ OIDC — OidcService(provider 레지스트리, RS256 JWKS 검증, nonce/email_verified 강제) + OidcStateSigner(무상태 state JWT) + UserRepository.provision_oidc_user(LOCAL/다른 provider 충돌 거부) + /auth/oidc/{providers,login,callback}
  - 8.3 ✅ RBAC — `rbac.py`(strict hierarchy Owner>Editor>Runner>Viewer, `has_at_least`) + MembershipRepository + WorkspaceRepository + WorkspaceContext + `Depends(get_current_workspace)` + `require_workspace_role(...)` factory(SuperAdmin bypass) + 첫 보호 라우터 `GET /workspaces/{id}` (Viewer+)
  - 8.4 ✅ Audit log — `AuditService`(세션 바운드, 호출자 트랜잭션 따라 자동 rollback) + `AuditLogRepository.query` + `AuditRequestMetaMiddleware`(ASGI BaseHTTPMiddleware → request.state.audit_meta) + `get_audit_service` Depends + `GET /audit` 이중 ACL(no workspace_id → SuperAdmin only / with workspace_id → Viewer+ or SuperAdmin)
  - 8.5 a~e 분리 (도메인이 넓어 1 PR = 1 slice 유지)
    - 8.5a ✅ Workspaces CRUD — POST(auto-Owner) / GET list(SuperAdmin bypass) / PATCH(Editor+) / DELETE(Owner) + audit pairing
    - 8.5b ✅ Membership management — list(Viewer+) / POST add-by-email(Owner) / PATCH role(Owner) / DELETE(Owner) + 마지막 Owner 보호(`LastOwnerError` → 409) + audit pairing
    - 8.5c ✅ Connections CRUD + test — 6 엔드포인트(`/workspaces/{ws}/connections` + `/{id}/test`) + `etlx_server/connections/` 패키지(`ConnectionRepository`/`SecretWalker`/`ConnectionTester`) + secret marker `{"$secret": "key"}` + secret backend `app.state` lifecycle + `ConnectorRegistry`로 sqlite 등 실 connector 호출 + 평문 0 보장 <!-- pragma: allowlist secret -->
    - 8.5d ✅ Pipelines CRUD + 버전 관리 — 6 엔드포인트(`/workspaces/{ws}/pipelines` + `/{pid}/versions`) + `etlx_server/pipelines/` 패키지(`PipelineRepository.ensure_version` idempotency 패턴) + core `PipelineConfig` 검증 + 서버측 name 주입 + audit `version_created` 플래그
    - 8.5e ✅ Schedules CRUD + Runs read-only — 6 schedule 엔드포인트(`/workspaces/{ws}/pipelines/{pid}/schedules` nested + `/{sid}/toggle`) + 4 run read-only 엔드포인트(`/workspaces/{ws}/runs` + `/{rid}/{logs,metrics}`) + `etlx_server/schedules/` + `etlx_server/runs/` 패키지 + croniter mode-aware 검증(batch 필수 / stream NULL 허용) + mode immutable + pipeline-workspace 경계 강제(cross-ws 404) + runs write 도메인 격리(POST 404 / PATCH·DELETE 405, audit 없음)
  - 8.6 ✅ Action 엔드포인트 — `POST /workspaces/{ws}/pipelines/{pid}/{dry-run,trigger}` + `POST /workspaces/{ws}/runs/{rid}/retry` + `GET /workspaces/{ws}/runs/{rid}/logs/stream` (SSE). `DryRunService`(connection resolve + secret 풀이 + 코어 `build_pipeline` + 병렬 health_check) — read-only, audit 없음. `RunRepository.add_manual`(trigger: pending row enqueue, schedule_id=NULL)/`add_retry`(failed·cancelled만 허용 `RunNotRetryableError`, `pipeline_version_id`+`schedule_id` 상속, `result_json.retry_of` lineage, 원본 row unchanged). 워커(Step 9)가 status 전이의 단일 writer 원칙 유지. SSE: `StreamingResponse` text/event-stream + `get_session_factory` Depends로 fresh session per ~500ms poll, run terminal + 2s idle 또는 disconnect 시 graceful close.
  - 8.7 ✅ 시나리오 테스트 — 3 e2e it: ① full happy path(login → workspace → 2 connections → pipeline + schedule → dry-run → trigger → run list/single → **audit 정확한 순서 검증** `workspace.create→connection.create×2→pipeline.create→schedule.create→run.trigger`); ② retry-after-simulated-worker-failure(워커 simulate로 Run.status=FAILED 직접 set → HTTP retry → 새 row.result_json.retry_of 검증 + 원본 unchanged); ③ non-member boundary(다른 user가 GET/list/dry-run/trigger 모두 403). 회원가입 엔드포인트 미존재 — User 시드만 ORM, 나머지 모든 step은 public HTTP API.
- 🔄 Step 9 (Execution Engine 통합):
  - 9.1 ✅ 엔진 선택 — ADR-0021의 자체 PG worker queue 채택, runs 테이블이 큐 자체 + SKIP LOCKED
  - 9.2 ✅ Cron 스케줄러 — `etlx_server/scheduler/`(`Scheduler.tick_once()` + `run()` 폴 루프) + `etlx-server scheduler run` CLI(별도 process, SIGTERM/SIGINT graceful). active batch schedule만 처리, cron 다음 firing 도래한 행에 PENDING Run enqueue. **No-migration**: "last fire" = `MAX(runs.scheduled_at) WHERE schedule_id=…`, fallback base = `schedule.created_at` (epoch backfill 방지). **방어적 skip+log**: inactive / stream(`cron_expr IS NULL`) / no current version / invalid cron 5종. **No-catchup-ish**: tick당 미스 1개씩 enqueue (진정 no-catchup은 새 schedule이 첫 tick 안 firing — trade-off 결정 보류). 단일 replica 안전, multi-replica는 `FOR UPDATE SKIP LOCKED` schedule lock 필요. 8 신규 it.
  - 9.3a ✅ Worker batch lifecycle — `etlx_server/worker/`(claim/executor/runner) + `etlx-server worker run` CLI. `claim_pending_run`(FOR UPDATE SKIP LOCKED + status 전이) + `RunExecutor`(fresh session per run, 단일 to_thread로 connect+run+close — sqlite thread-bound driver 호환, build 실패는 _PipelineBuildError로 status=failed 기록, build → run 둘 다 status에 반영) + `RunWorker`(asyncio.Event stop, exception은 log + 계속). 신규 공유 모듈 `pipelines/runtime.py`로 DryRunService와 빌드 경로 통일. **코어 버그 수정**: `Pipeline._run_task`가 `task.sink_table`을 `sink.write`에 forward 안 하던 문제(RDBMS sink batch 미사용으로 가려졌던 잠재 버그). **Audit 정렬 안정화**: 같은 microsecond 내 audit row 정렬을 위해 secondary `id.desc()` 추가. 11 신규 worker it.
  - 9.3b ✅ Heartbeat + ZombieReaper — `worker/heartbeat.py`(`heartbeat_loop` async fn, ~10s마다 fresh session으로 UPDATE), `RunExecutor`가 `pipeline.run` to_thread *전에* heartbeat task spawn + finally cleanup. `worker/reaper.py` `ZombieReaper`(SKIP LOCKED + stale → status=failed/error_class=ZombieReaped, **auto-resubmit 의도적 안 함** — poison row thundering herd 방지, 명시적 retry는 8.6). `etlx-server reaper run` CLI 별도 process. 11 신규 it (reaper 8 + heartbeat 3).
  - 9.3c ✅ Log/metric forwarding — `worker/recorder.py` `RunRecorder` 비동기 컨텍스트 매니저가 활성 run을 모듈-레벨 dict에 등록 + `RecordingMetrics`(코어 Metrics ABC 구현) 글로벌 백엔드 교체 + structlog `log_processor`로 matching `run_id` 이벤트만 capture. 둘 다 `queue.SimpleQueue` (스레드 안전) → `asyncio.to_thread`에서 emit해도 안전. **`__aexit__` 단일 flush** (주기적 drain은 공유 세션과 충돌 → live-tail은 기존 SSE 폴링으로 대체). `RunExecutor`에 5개 lifecycle structlog 이벤트(build_started/build_failed/pipeline_started/pipeline_succeeded/pipeline_failed) emit해 조용한 connector도 trail 보장. `etlx-server worker run`이 시작 시 `configure_logging(extra_processors=[log_processor])`. 5 신규 it.
  - 9.4 미착수 — Stream worker manager(별도 process/Deployment)
  - 9.5 미착수 — retry/DLQ/timeout 정책 UI 노출
- 🔄 Step 10 (Web UI / Next.js):
  - 10.0~10.2 ✅ UI 1 foundation (2026-05-18) — design tokens via Tailwind v4 `@theme`, primitives(Button/Input/Card/DataTable/StatusBadge/EmptyState), Arc-style shell(Sidebar 4px accent + Header theme/avatar), AuthProvider(localStorage JWT + `etlx:unauthorized` 핸들러) + ThemeProvider + WorkspaceProvider, REST API client(DTOs mirror `etlx_server.auth.schemas`).
  - 10.3~10.6 부분 (read-only 목록만): `/workspaces` 생성 + list, `/w/[slug]/{connections,pipelines,schedules,runs,settings}` 페이지(Connections `Test` + Pipelines `Trigger` 액션 동작, Runs 5s polling). 생성/편집 UI / 시크릿 폼 / 멤버 관리 / 감사 로그 / cron 빌더 / Run 상세 / DLQ 미작업.
  - 10.4 ✅ Pipeline Builder(UI 2, 2026-05-18) — `@xyflow/react` 기반 visual editor. Palette → canvas → Properties panel 3-pane. 14 operators(sources/transforms/sinks), connection dropdown은 같은 type 한정 filter, JSON 필드 inline validation, save = PATCH /pipelines/{id} → idempotent PipelineVersion 생성. Multi-branch DAG는 core가 linear pipeline이라 미지원(향후 core 확장 필요).
  - 10.7~10.8 미착수 — empty/error/loading 정밀화 + a11y AA(axe-core) + Storybook/Chromatic baseline.
- 📋 Step 11 (운영 강화) — 미착수

**Step 5/6 (코어 강화)와 Step 7 (서비스 착수) 사이의 순서는 작업자 결정**. 일반적으로 코어를 어느 정도 안정화한 뒤 서비스에 들어가는 것이 안전.

---

## 6. 금기사항 (Hard Rules)

### 코어 라이브러리 일반
- ❌ `.env`에 들어갈 값(시크릿)을 **코드/YAML/커밋 메시지/로그/예외 메시지**에 하드코딩·노출 금지. 로깅은 반드시 마스킹 필터 거칠 것.
- ❌ `.env`는 절대 git에 커밋하지 말 것 (`.env.example`만 커밋).
- ❌ 신규 커넥터를 만들 때 **`@ConnectorRegistry.register(...)` 데코레이터 없이** 등록 금지.
- ❌ 신규 커넥터는 **공통 contract test 통과 없이** 머지 금지.
- ❌ Connector의 raw 객체(드라이버 row, Kafka msg 등)를 `Record` 정규화 없이 Pipeline 외부로 흘리지 말 것. 위치 정보(offset/lsn/seq)는 `Record.metadata`.
- ❌ 단일 PR/커밋에서 **여러 Step을 섞지 말 것**. 한 커넥터/한 기능 단위.
- ❌ 설계상 의사결정을 `DECISIONS.md` ADR 기록 없이 변경 금지. SPEC.md 변경도 ADR 동반 필수.
- ❌ Pydantic 없이 임의 dict로 설정 처리 금지.
- ❌ 패키지 매니저는 **`uv` 한 종류**만 사용. `pip install`/`poetry`/`conda` 혼용 금지.

### 서비스 패키지 격리 (Step 7 이후)
- ❌ **`etl_plugins/*` 어디서도 `services/*`를 import 금지**. 코어는 서비스를 모른다. CI의 import-graph 검사가 차단.
- ❌ 서비스 패키지가 코어 내부 모듈을 직접 손대지 말 것 (예: `etl_plugins.core._private`). public API(`etl_plugins.Pipeline`, `etl_plugins.runtime.run_pipeline_yaml` 등)만 사용.
- ❌ 코어를 서비스 편의를 위해 변경하지 말 것. 필요하면 새로운 public API 추가 + ADR.
- ❌ 시크릿을 metadata DB에 평문 저장 금지. UI에서 입력받아도 backend(Vault/AWS SM/GCP SM)에 즉시 위임, DB에는 ref만.
- ❌ 프론트(`etlx-web`)에서 Python 코어를 직접 호출하지 말 것. **반드시 REST API를 통해**.
- ❌ Step 7~11 작업 시에도 단일 PR = 단일 슬라이스 원칙 유지.
- ❌ `services/etlx-web` 작업 시 `DESIGN.md`의 토큰 외 임의 값(arbitrary color/spacing/font, 인라인 hex, 임의 px) 사용 금지. Tailwind arbitrary value(`bg-[#123456]`)도 금지.
- ❌ 새 시각 컴포넌트를 Storybook story 없이 머지 금지. visual regression baseline이 깨지면 ADR-0018 갱신 필수.
- ❌ a11y AA 위반(axe-core 실패) 머지 금지. 색만으로 의미 전달, 작은 본문에 핑크 글씨 등은 디자인 위반.

---

## 7. Claude Code 작업 규약

- **세션 시작**: `CLAUDE.md` → `ROADMAP.md` → 최근 커밋 로그 순서로 확인.
- **세션 종료**: ① `ROADMAP.md` 체크박스/"← 작업 중" 마커 갱신 ② 필요 시 `DECISIONS.md`에 ADR 추가 ③ `CHANGELOG.md`에 메모 ④ 커밋 메시지에 Step 번호 포함 (`feat(connector): add postgres source [Step 2.1]`).
- **TODO 주석**에는 Step 번호와 컨텍스트 포함: `# TODO(Step 3.2): chunked upsert via MERGE`.
- **장기 중단** 시 ROADMAP 해당 항목에 `← 작업 중 (YYYY-MM-DD, 다음 할 일: ...)` 메모.
- **모든 결정**은 `DECISIONS.md`에 ADR로 기록. ADR 번호는 0001부터 증가.
- 새 커넥터를 추가하면 **Registry 등록 + contract test 추가 + `SPEC.md` §6 지원 목록 갱신**.
- **서비스 패키지 작업 시**: import-graph 검증 step이 CI에서 자동 실행되는지 확인. Step 7.0에서 ADR-0019~0023으로 세부 기술 스택을 확정한 뒤 7.1로 진행 (프론트엔드 스택은 이미 ADR-0018에 포함).

---

## 8. 관련 문서 빠른 링크

| 문서 | 용도 |
|---|---|
| [`SPEC.md`](SPEC.md) | 마스터 설계 명세서 (원칙·아키텍처·인터페이스·설정·로드맵 전체) |
| [`ROADMAP.md`](ROADMAP.md) | Step 단위 진행 상태 (체크박스). Step 7~11은 서비스화. |
| [`DECISIONS.md`](DECISIONS.md) | ADR — 모든 설계 결정 기록. 서비스화는 ADR-0017부터. |
| [`DEVELOPMENT.md`](DEVELOPMENT.md) | 신규 환경 인계 / 부트스트랩 / 트러블슈팅 |
| [`CHANGELOG.md`](CHANGELOG.md) | Keep a Changelog 포맷 변경 이력 |
| [`DESIGN.md`](DESIGN.md) | etlx-web 디자인 시스템 (Step 10 SSOT, ADR-0018) |
