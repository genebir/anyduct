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

**Step 8.2b 완료 → 8.3 RBAC 차례** (2026-05-18 기준). Steps 1–4 + Step 5.1 + Step 7.0~7.4 + Step 8.1 + Step 8.2a(로컬 인증 + JWT RS256) + **Step 8.2b(OIDC SSO — Google/Azure/Okta/GitHub/generic 공통화, RS256 JWKS 검증, 무상태 state JWT, provision_oidc_user)** 완료. **344 코어 단위 + 48 etlx-server 단위 + 3 skip + 119 코어 통합(testcontainers PG/MinIO/Kafka/MySQL/Vault/LocalStack) + 59 서버 통합 = 570 테스트** all green, **24 ADR**. `mypy strict` 코어 39 + 서버 33 src files OK, `lint-imports` 2 contracts KEPT. **OOP 정규화 일관 적용**: services(`JwtService`/`PasswordService`/`OidcService`/`OidcStateSigner`) + repository(`UserRepository.provision_oidc_user`) + 도메인 라우터(`auth.py`/`oidc.py`) + `Depends`-기반 DI(`get_oidc_service`) + lifespan-managed `app.state` 싱글톤 + 테스트 시 `http_client_factory` 주입으로 `httpx.MockTransport` IdP 모킹.

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
  - 8.3~8.7 미착수 — 다음은 RBAC(workspace 단위 4역할 + Depends 게이트)
- 📋 Step 9 (Execution Engine 통합) — 미착수
- 📋 Step 10 (Web UI / Next.js) — 미착수
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
