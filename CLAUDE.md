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

> **최신 마일스톤 (2026-05-29): Auto-materialize 시나리오 corpus — chain/fan-out/실패 차단 깊은 검증 (Phase GG, ADR-0051).** ADR-0037 D1 메커니즘의 interplay(catalog 정합성 + chain 동작 + 실패 차단)를 sample data e2e로 깊게: GG1 happy chain(A→B 자동 trigger, catalog 전체 chain), GG2 fan-out(A→B+C 둘 다 자동), GG3 failure blocks(A 실패 → B 큐 empty + catalog dirty 안 됨). 3-layer 검증(record/asset/run lineage). `_drain_pending_runs` helper로 큐 끝까지. Asset key `connection/table` 디자인이 chain에서 표면화되는 점을 코멘트로 명문화. 서버 it 443→446(+3 시나리오). 코어 737 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): column_mapping typo 검출 lint + 실패/재시도 시나리오 검증 (Phase FF, ADR-0050).** 신뢰성의 두 차원 추가: (a) 사용자 실수 catch — 새 lint 규칙 `column_mapping_unknown_source_column`이 declaration의 source col이 transform 시점 upstream mapping에 없으면 dry-run에서 안내. 이전 rename도 정확히 반영. (b) 비정상 경로 카탈로그 정책 명문화 — 시나리오 F(failed run → catalog untouched), 시나리오 G(rerun idempotent — asset/edge/column lineage 모두 dedup, materialization만 +1). 기존 e2e 1개 narrow assertion으로 보강. 코어 732→737(+5 lint) + 서버 441→443(+2 시나리오) + 1 기존 보강 green. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): 확장 dogfood 시나리오 corpus — 실전 패턴 5종 sample-data e2e (Phase EE, ADR-0049).** 사용자 *"테스트 중점, 샘플 데이터로 깊게"* 요청. `test_extended_workflow_scenarios.py`: A) SCD Type 2(literal column empty upstream / alias trace) B) Fan-out by `when`(ADR-0027 multi-sink) C) Aggregation chain(`SUM` GROUP BY → base column trace) D) Self-join(같은 base의 두 alias) E) Schema evolution(ALTER ADD COLUMN → passthrough sink-only emission). 3-layer 검증(record-level 실제 row 비교 + asset-level edge + column-level upstream). 5-20 rows per 시나리오로 디버그 친화. Dogfooding 발견: Scenario B routing-key sink schema 누락 → best practice 코멘트. 서버 it 436→441(+5). 코어 732 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0. 모든 Phase X/Z/AA/CC 메커니즘 실전 깊이 검증.
>
> **이전 마일스톤 (2026-05-29): Pipeline lint — dry-run에서 column_mapping 권장 advisory warning 노출 (Phase DD, ADR-0048).** Phase CC의 `column_mapping`은 opt-in이라 사용자가 알아야 채택률 올라감. dry-run 단계가 자연 통과 지점이라 자동 안내: `etl_plugins/runtime/lint.py` `lint_pipeline(cfg)`(pure, 모든 shape 순회) + `LintWarning(code/message/location)`. 첫 규칙 `column_mapping_recommended`: python/custom_python/sql_exec + column_mapping 없으면 발동. `DryRunResult.warnings` + 모든 return path lint 호출 — advisory(차단 안 함). `DryRunResponse` Pydantic schema에 `warnings` 필드 추가(하위 호환). 코어 732(+9) + 서버 436(+2 e2e) green. DB 마이그레이션 0. Builder UI 통합은 별개 슬라이스.
>
> **이전 마일스톤 (2026-05-29): 사용자-명시 `column_mapping` — 트랜스폼에 직접 의도 박기 (Phase CC, ADR-0047).** Phase AA의 schema-passthrough가 못 잡는 케이스(컬럼명 rename, a→b 데이터 이동)를 위한 빠져나갈 길. `TransformConfig`이 `extra="allow"`이라 모델 변경 0 + DB 마이그레이션 0. `column_mapping: {output_col: [source_col, ...]}` — 빈 리스트 = 새 컬럼. `_apply_transform`이 type 분기 *전*에 우선 처리, 모든 트랜스폼 타입(rename/cast/python/custom_python/sql_exec) 적용 가능. Replace 모드(merge 아님) — deterministic + 사용자 의도 명확. Malformed → None 시그널 → type fallback. column_mapping 있으면 opaque 안 되니 Phase AA fallback 안 발동(둘 다 안 발동, 충돌 없음). 코어 unit 723(+5) + 서버 it 434(+1 e2e — sqlite custom_python `name→display_name` rename + column_mapping → catalog 정확) green. **사용자가 의도를 박으면 100% 정확**.
>
> **이전 마일스톤 (2026-05-29): 4-stage e-commerce dogfooding 시나리오 — 모든 Phase 기능 실전 검증 (Phase BB).** `test_complex_workflow_catalog.py`: Stage 1(SQL JOIN+CTE) → Stage 2(CTE+ROW_NUMBER window) → Stage 3(custom_python passthrough) → Stage 4(SELECT * inspector). 9 asset row + DAG edge + 4 sink 모두 `column_lineage_opaque=False` + per-stage column attribution 정확. Dogfooding 발견 1개: passthrough fallback이 sink-only 컬럼(python이 추가한 `tier`)에 row 자체를 안 만들던 미스 → sink schema 모든 컬럼에 row 생성(intersection은 upstream, sink-only는 빈 upstream)으로 보완. 서버 it 433 + 코어 718 green. ADR-0046 보완 노트. 카탈로그가 sink의 물리 schema와 항상 일치. **모든 Phase X/Z/AA 기능이 실전에서 한 번에 동작 확인됨**.
>
> **이전 마일스톤 (2026-05-29): Schema-passthrough fallback — 어떤 트랜스폼이든 column edge 자동 생성 (Phase AA, ADR-0046).** 사용자 *"어떤 소스던 간에 소스/타겟 있잖아, 전부 자동생성"* 요청. python/custom_python/sql_exec 트랜스폼이 끼어 opaque로 떨어진 sink에 대해 `RunExecutor._augment_opaque_with_schema_passthrough`가 발동: source + sink schema fetch → 컬럼명 intersection 각 컬럼에 1:1 ColumnEdge. 컬럼명이 다르면 mapping 없음(거짓 양성 0). 본질 opaque(Kafka/HTTP sink, 컬럼명 완전 disjoint)는 정직하게 유지. e2e 1개(sqlite + custom_python + sqlite → column_lineage_opaque=False + 자동 id/name edge). 서버 432 + 코어 718 green. 카탈로그 자동 생성률 사실상 100%(SQL-introspectable connector 부류 안에서) — **사용자 "전부 자동" 요구 완전 충족**.
>
> **이전 마일스톤 (2026-05-28): 2000-쿼리 fuzz harness + SchemaInspector inject로 `SELECT *` 실행 시점 해결 (Phase Z, ADR-0045).** `tests/unit/runtime/sql_corpus_generator.py` 22 generator(L1 baseline → L9 combined)가 단계적 합성 + ground-truth 동시 build. `test_sql_lineage_fuzz.py`가 2002 쿼리(22×91) round-robin 실행. **첫 1820/2002 (91%)에서 `COUNT(*)` leaf 컬럼명이 alias로 잘못 들어가는 회귀 catch → `_column_from_expression`으로 보완 → 2002/2002 (100%)** 모든 22 shape. **`SELECT *`**: 코어 `derive_column_lineage(cfg, *, schemas=None)` 옵셔널 schemas. 서버 `RunExecutor._build_schemas_for_star_queries`가 `"*" in query`인 source만 `ConnectionInspector.list_columns`로 schema fetch + inject. best-effort, query에 `*` 없으면 overhead 0. 코어 718(+3) + 서버 431(+1 e2e sqlite SELECT*) + 2002 fuzz green. 카탈로그 column lineage의 마지막 opaque case 해소.
>
> **이전 마일스톤 (2026-05-28): Static lineage emit이 JOIN/CTE/UNION의 모든 base 테이블을 input asset으로 자동 등록 (Phase X 후속, ADR-0044).** Phase X가 만든 multi-upstream column edge가 실제 catalog row를 만들려면 asset-axis도 query의 모든 referenced 테이블을 등록해야 했음(옛 동작은 첫 FROM만). `extract_referenced_tables(query)`(sqlglot AST 전체에서 `exp.Table` 모음, CTE 제외, fq 이름) + `derive_lineage`에서 linear/graph 양 경로 primary + extras + fan-out edges 등록. `_table_of_simple`/`_single_base_table`/`_build_alias_table_map`도 fq 이름 통일(`_table_fq_name`) → column leaf와 asset key 정합. 비-SQL source는 자동 fanout 없음(Mongo/Kafka/S3 영향 0). 12 신규 테스트(extract_referenced_tables 6 + derive_lineage 5 + e2e sqlite JOIN 1). DB 마이그레이션 없음. 코어 714 + 서버 430 green. 다음 자연 보완: SchemaInspector(ADR-0033)를 lineage 호출에 inject해서 `SELECT *` 실행 시점에 해결.
>
> **이전 마일스톤 (2026-05-28): SQL 컬럼 리니지 풀-AST 워커 (Phase X, ADR-0043).** `etl_plugins/runtime/sql_lineage.py`(신규) `extract_sql_lineage`가 `sqlglot.lineage`로 JOIN(모든 종류)/CTE chain·재귀/서브쿼리/UNION/윈도우/CASE/COALESCE/LATERAL 정확 파싱. Placeholder leaf 복구(correlated/LATERAL alias→table) + Aggregate fallback(`COUNT(*)`→base table) + `SELECT *` w/schema 지원. `_Mapping`을 `tuple[ColumnRef, ...]`로 widen(데이터 모델 무변경, DB 마이그레이션 없음). 32 신규 테스트(corpus 25 + stress 4: 200줄 dbt-style + 100-layer CTE 2000줄+ <5s + 50-branch UNION). 옛 "JOIN→opaque" 기대 2개는 새 정확한 결과로 갱신. 코어 703 + 서버 429 green.
>
> **이전 마일스톤 (2026-05-21): Asset/Lineage 축 = "Airflow 오케스트레이션 + Dagster 리니지" 비전 핵심 골격 완성 (A→B→C→D1→D2).** 자세한 진행은 메모리 [[ultimate-vision]] + ROADMAP §6(6.6~6.8) + ADR-0036/0037/0038 참조. 요약:
> - **A** 코어 모델/정적파생/런타임 emit/카탈로그(`etl_plugins/core/asset.py`, `runtime/lineage.py`, `observability/lineage.py`, `etl_plugins/catalog.py`) — derived-first(zero-config) 리니지, SQL `FROM` 파싱으로 source↔sink 키 일치.
> - **B** 메타DB 영속화(assets/asset_edges/asset_materializations, Alembic 0004) + `AssetRepository` + 워커 hook + 카탈로그 REST.
> - **C** 웹 카탈로그(`/w/[slug]/assets`) + `LineageGraph`(@xyflow/react).
> - **D1** 이벤트 기반 auto-materialize(`auto_materialize` opt-in, 워커 `_trigger_asset_consumers`). **D2** 시간 기반 freshness(`freshness_sla_minutes`, 스케줄러 `_tick_freshness`). **D3** 백필(`SourceConfig.cursor_column` 배선 + `POST .../backfill` → `result_json.backfill` cursor 범위를 워커가 `pipeline.run`에 전달 + 빌더 Backfill 다이얼로그, ADR-0039). 라이브 e2e 검증 완료.
> - **start.sh/stop.sh가 worker+reaper+scheduler 관리**(이전엔 스케줄러 미기동 → cron도 안 돌았음). 멱등성: sink `pre_sql`(원자) + "Run SQL" transform(ADR-0035).
> - **다음 방향(2026-05-21 사용자 지정)**: **pipeline 전면 개선 — 오케스트레이션 위주**. 첫 슬라이스 = **Spark 백엔드 + ExecutionBackend 추상화 + `engine` 필드 전면 제거(ADR-0040)** — 실행 모델을 로컬 인프로세스 단일 경로(linear/task-DAG/dataflow graph)로 단순화해 데크 정리 완료. 다음은 오케스트레이션 본 작업(범위는 다음 세션에 정렬). [[pipeline-overhaul-intent]] 참조.
> - **미착수(E)**: 컬럼 레벨 리니지 · OpenLineage export · asset→consumer 인덱스 · multi-replica 스케줄러 락. v0.1.0 PyPI는 user-gated.

**(아래는 이전 누적 컨텍스트)** **Step 5.3 MongoDB(첫 NoSQL) + Step 5.7 HTTP source + Step 6.1 전체(`etl_plugins.core.cursor` + 4 connector `read_since`(sqlite/postgres/mysql/mongodb, contract 통과) + `Pipeline.run(cursor_from/cursor_to)` 백필 헬퍼 + DB-backed `CursorState`(etlx_server.cursors, cursors 테이블, psycopg-v3 sync engine)) + Step 6.2 전체(OTel `configure_otel` 어댑터 + Pipeline span emit `pipeline.run`/`pipeline.task` + Prometheus exporter `prometheus_port` — InMemory/OTLP/Prometheus 3 reader 자유 조합) + Step 6.3 contract suite 보강(5 신규 invariants — idempotent close × 2 / reconnect-after-close × 2 / cursor metadata stamp) + Step 6.4 mkdocs 문서 사이트(mkdocs-material + mkdocstrings, `make docs` strict 빌드, 9 페이지) + **Step 6.5 v0.1.0 릴리스 로컬 prep(__version__ 0.1.0, CHANGELOG [0.1.0] section, `uv build` clean — git tag/PyPI publish는 사용자 승인 대기)** + Step 9.4 Stream worker + Step 10 UI 광범위 + Step 10.8 Storybook foundation 완료 → ADR-0024 v0.1.0 critical path 닫힘 직전(릴리스만 user-gated). 이후는 v0.2.0(Asset/Lineage) 또는 Step 5.x 추가 커넥터(Redis NoSQL, MSSQL/Oracle/DW)** (2026-05-19 기준). Steps 1–4 + Step 5.1(MySQL/SQLite) + **Step 5.3(MongoDB)** + **Step 5.7(HTTP/REST source)** + Step 7.0~7.4 + Step 8 전체 + **Step 9.2(cron scheduler) + 9.3a(worker claim+execute) + 9.3b(heartbeat + ZombieReaper) + 9.3c(log/metric → run_logs/run_metrics, periodic drain via `--log-flush-interval`) + 9.4(StreamWorker — `etlx-server stream-worker run`, in-flight task당 cancel/heartbeat/recorder, no-respawn-on-stable-updated_at, single-replica)** + **Step 10 UI 1(`services/etlx-web` foundation) + UI 2(`@xyflow/react` Pipeline Builder + Dry Run + Retry/DLQ panel) + 10.3 Connection CRUD + 10.4 Dry Run + 10.5 Run 상세 + Schedule CRUD + cron builder + next-firing 미리보기 + 10.6 멤버 관리 + Audit log 뷰어 + Workspace 편집/삭제 + 대시보드** 완료. **470 코어 단위 + 318 etlx-server (unit+it) + 3 skip + 192 코어 통합 = 983 테스트** all green, **24 ADR**. **etlx-web**: Next 15 + React 19 + Tailwind v4 (CSS-first `@theme`), DESIGN.md §11.1 토큰, Arc-style 사이드바(워크스페이스 4px 컬러 bar), Auth/Theme/Workspace Provider, REST 클라이언트 + 운영자 카탈로그(`lib/operators.ts` 17종), PipelineConfig serializer(`lib/pipeline-config.ts`), `/w/[slug]/pipelines/[id]/edit` Pipeline Builder(좌 Palette / 중앙 React Flow canvas / 우 Properties panel). `pnpm --filter @etlx/web build` 통과(8 routes, max 187kB first-load JS — editor). `mypy strict` 코어 39 + 서버 67 src files OK, `lint-imports` 2 contracts KEPT. **OOP 정규화 일관 적용**: services(`JwtService`/`PasswordService`/`OidcService`/`OidcStateSigner`/`AuditService`/`SecretWalker`/`ConnectionTester`/`DryRunService`) + repository(User/Workspace/Membership/Connection/Pipeline/Schedule/Run/AuditLog) + 도메인 라우터(`auth.py`/`oidc.py`/`workspaces.py`/`memberships.py`/`connections.py`/`pipelines.py`/`schedules.py`/`runs.py`/`audit.py`) + `Depends`-기반 DI + 모듈 레벨 `_require_*` Depends 싱글톤(ruff B008 회피) + 도메인 예외(`WorkspaceSlugTakenError`/`MembershipExistsError`/`LastOwnerError`/`ConnectionNameTakenError`/`SecretMarkerError`/`SecretBackendReadOnlyError`/`PipelineNameTakenError`/`InvalidCronError`/`RunNotRetryableError`)로 라우터에서 깨끗한 4xx/5xx 매핑.

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
  - 9.3c ✅ Log/metric forwarding — `worker/recorder.py` `RunRecorder` 비동기 컨텍스트 매니저가 활성 run을 모듈-레벨 dict에 등록 + `RecordingMetrics`(코어 Metrics ABC 구현) 글로벌 백엔드 교체 + structlog `log_processor`로 matching `run_id` 이벤트만 capture. 둘 다 `queue.SimpleQueue` (스레드 안전) → `asyncio.to_thread`에서 emit해도 안전. opt-in `flush_interval_seconds`로 주기적 drain 활성화(production worker `--log-flush-interval` default 2s) — Run 상세 페이지의 polling이 그대로 live tail로 동작. `RunExecutor`에 5개 lifecycle structlog 이벤트(build_started/build_failed/pipeline_started/pipeline_succeeded/pipeline_failed) emit해 조용한 connector도 trail 보장. `etlx-server worker run`이 시작 시 `configure_logging(extra_processors=[log_processor])`. 6 신규 it.
  - 9.4 ✅ Stream worker — `etlx_server/worker/stream.py` `StreamWorker` + `etlx-server stream-worker run` CLI. tick 루프가 active stream schedule 스캔해서 `asyncio.Task`로 `Pipeline.arun_stream()` 실행, heartbeat + RunRecorder 동일 패턴 부착. cancel-on-deactivate + cancel-on-shutdown으로 ghost running 행 방지. **No-respawn-on-stable-updated_at**: build/모드 실패한 schedule은 user가 schedule 수정해야만 재시도(thundering herd 방지). 단일 replica(multi-replica는 `FOR UPDATE SKIP LOCKED` 필요, Step 11). 6 신규 it (스트림 spawn/inactive·batch ignored/mode mismatch/deactivation cancels/pre-set stop/no-current-version skip) — monkeypatched build + `_FakeStreamPipeline`(arun_stream=`asyncio.sleep`)로 Kafka 없이 검증.
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
