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

> **최신 마일스톤 (2026-05-29): Migration 폼을 마이그레이션-shaped으로 재설계 (Phase AAN3).** 사용자 *"명확하게 마이그레이션 같이 만들어줘. 지금 형태는 ETL과 별 차이가 없잖아"*. ① 테이블 picker(쿼리 textarea 제거) + connectionsApi.tables datalist. ② Source→Destination 카드 좌우 + ▶ 화살표. ③ Strategy radio 3가지(Full snapshot=overwrite+drop / Append new rows=append+cursor / Live mirror=upsert+keys) — mode/if_exists 기술 라벨 제거. ④ 소스 스키마 미리보기 + (mirror) PK 컬럼 배지. ⑤ migration-config.ts 재설계: MigrationStrategy enum + strategyToSinkShape + parseSelectStarTable. ⑥ list page: From→To 화살표 + Strategy chip. ⑦ safe-exit: 복잡 쿼리는 빌더로. ⑧ i18n en/ko 각 22 추가 키. 신규 시각 컴포넌트 0. 코어/서버 변화 0. web tsc clean.
>
> **이전 마일스톤 (2026-05-29): Migration 자체를 빌더와 분리 — 전용 form + new/edit 페이지 (Phase AAN2).** 사용자 추가 요청 *"빌더로 연결되는 게 아니라 마이그레이션 자체를 따로 빼줘"*. ① `/w/[slug]/migrations/new` 빌더 우회 단순 폼(source connection+query / sink connection+table+mode+if_exists / upsert 시 key_columns). ② `/w/[slug]/migrations/[id]` 전용 detail/edit/delete. ③ 신규 `MigrationForm` 컴포넌트 — RDBMS connection만 dropdown, graph canvas 없음. ④ 신규 `lib/migration-config.ts` — build/parse/validate pure functions. ⑤ safe-exit: 마이그레이션 shape 아닌 파이프라인은 안내 + 빌더 routing. ⑥ list page CTA reroute. ⑦ i18n en/ko 각 14 추가 키. 신규 시각 컴포넌트 0. 코어/서버 변화 0. web tsc clean.
>
> **이전 마일스톤 (2026-05-29): 사이드바 "Migrations" 메뉴 분리 + `/w/[slug]/migrations` 전용 페이지 (Phase AAN).** 사용자 요청 *"마이그레이션 메뉴를 따로 빼서 관리해줘"*. ① 사이드바 Pipelines와 Schedules 사이에 `Migrations`(ArrowRightLeftIcon) nav 추가. ② `/w/[slug]/migrations` 페이지 — `auto_create_table=true` 파이프라인만 client-side 필터(server endpoint 0). 컬럼 이름 / 도착지 / 모드 / 존재 시 동작(tone-aware). "+ New migration" CTA가 `db-migrate-cross` 템플릿(AAI)으로 빌더 진입. ③ 신규 `lib/migration-utils.ts` — `migrationSummaryOf(config)` linear/fan-out/graph 3 shape walk + pure 함수. ④ i18n en/ko 각 14 키. 신규 시각 컴포넌트 0. 코어/서버 변화 0. web tsc clean.
>
> **이전 마일스톤 (2026-05-29): examples/ 확장 — snapshot rebuild + upsert merge 예제 (Phase AAM).** Phase AAH의 cross_db_migration.yaml 옆에 두 운영 시나리오 추가: `cross_db_snapshot.yaml`(drop + overwrite, 일일 schema-drift-tolerant 재구축) + `cross_db_upsert.yaml`(upsert + key_columns + auto_create_table, PRIMARY KEY 자동 emit + live cache 패턴). 헤더 코멘트에 ADR-0071/0072 운영 trade-off 명시. `etlx validate` 둘 다 직접 검증 ✓. lock-in test 2개 추가(코어 unit 810→812).
>
> **이전 마일스톤 (2026-05-29): Workspace Variables 페이지 type badge — 빌더와 일관성 (Phase AAL).** `/w/[slug]/variables` 리스트에 string/number/boolean/JSON 타입 배지 추가. 빌더 pipeline-settings-panel(L1)이 이미 갖고 있던 vocabulary와 합치. ① 신규 `lib/variable-types.ts` (`VarType` + `inferType`) — 두 surface가 같은 추론 함수 공유. ② 빌더 inline 선언 → import로 교체(코드 중복 0). ③ workspace 변수 리스트에 `<span>` badge(uppercase, `bg-overlay`). 신규 시각 컴포넌트 0. 코어/서버 변화 0. web tsc clean.
>
> **이전 마일스톤 (2026-05-29): `auto_create_table_planned` 린트 규칙 — dry-run 안내 (Phase AAK).** Phase DD 패턴 확장. 각 sink에 `auto_create_table=true`가 있으면 dry-run에서 "sink will create table 't2' on first run if it's missing" 안내. `auto_create_if_exists='drop'`은 "rebuild" / `'error'`는 "fail next run" 문구. linear / fan-out / graph 3 shape 모두 walk. **운영 UX**: 사용자가 Trigger 누르기 전 Dry Run에서 정확히 미리 봄. 코어 unit 805→810(+5). 신규 시각 컴포넌트 0(기존 DryRunPanel warnings 영역 자동 노출).
>
> **이전 마일스톤 (2026-05-29): Cross-DB `auto_create_table` → 카탈로그 REST 자동 등록 dogfood (Phase AAJ).** auto-created sink가 catalog REST에서 first-class 자산으로 보임을 분석가 페르소나 e2e: products → products_cache(auto_create) run → `GET /assets`에서 src/products + dst/products_cache 둘 다 노출 + upstream lineage + materializations 모두 정확. **운영 보장**: auto-created 테이블이 runtime-only 사이드 이펙트가 아니라 카탈로그 first-class 시민(분석가 dashboard 빌드 시 lineage 그래프 자연 노출). 서버 it 481→482(+1). 코어 805 unchanged. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): 빌더 "Cross-DB Migration" 스타터 템플릿 (Phase AAI).** Phases AAA→AAH의 cross-DB 기능을 가장 짧은 path로 노출. ① 새 템플릿 `db-migrate-cross`(postgres source + sqlite sink, `auto_create_table=true` 사전 설정 → 사용자가 토글 발견할 필요 0). ② `state()` helper에 per-node `overrides` 인자(노드별 field defaults). ③ i18n 4 신규 키 en/ko(`tpl.dbMigrateCross/Desc`). 신규 시각 컴포넌트 0. web tsc clean. 코어/서버 변화 0.
>
> **이전 마일스톤 (2026-05-29): `examples/cross_db_migration.yaml` + CLI validate 회귀 가드 (Phase AAH).** Phases AAA→AAG의 cross-DB migration 기능을 카피·페이스트 가능한 한 페이지 사용자용 예제 + 회귀 가드. `auto_create_table` + `auto_create_if_exists` 코멘트 가이드 + `etlx validate` 사용법 + `connections.example.yaml` 동반(secret marker pattern). 신규 unit `test_example_yaml_loads.py`로 future config-schema 드리프트 방지. CLI 검증: "pipeline: orders_replication (mode=batch) ✓". 코어 unit 804→805(+1). ruff clean.
>
> **이전 마일스톤 (2026-05-29): `SinkConfig.auto_create_if_exists`를 `Literal["skip","drop","error"]`로 좁힘 (Phase AAG).** typo (`"DROP"`/`"replace"`/`"Skip"`)가 config-validation 시점에서 즉시 422 거부. 기존 plain str은 connector 깊은 곳의 unknown branch에서만 실패해 운영자 디버그 비용 컸음. 2 신규 unit(canonical 3종 accept + typo 3종 reject). 코어 unit 802→804(+2). mypy/ruff clean. backward-compat 완전.
>
> **이전 마일스톤 (2026-05-29): 빌더 FieldDef.showWhen — dependent field visibility (Phase AAF).** `auto_create_table` default OFF인데 `auto_create_if_exists` select 항상 노출 = 99% 사용자에게 시각적 clutter. ① `FieldBase`에 `showWhen?: { field, equals }` 추가. ② Properties panel이 노드 데이터 보고 conditional 렌더(필터 한 줄). ③ 3 RDBMS sink의 `auto_create_if_exists`에 `showWhen: { field: "auto_create_table", equals: true }` + help text 잉여 가이드 제거. **결과**: 기본 sink 패널에서 if_exists 옵션 자연히 사라짐 + 사용자가 토글하는 순간 출현. 신규 시각 컴포넌트 0. web tsc clean. 코어/서버 변화 0.
>
> **이전 마일스톤 (2026-05-29): Cross-DB cross-vendor testcontainers — postgres↔sqlite upsert + drop (Phase AAE).** 실제 Docker postgres로 AAA/AAC 회귀 가드: ① AAE1 postgres→sqlite upsert(BIGINT id + VARCHAR(64) name → ensure_table(primary_key=['id']) → 2-pass upsert idempotent merge) — AAC가 sqlite-only 검증이었던 것을 cross-vendor로 확장. ② AAE2 sqlite→postgres drop — schema drift(legacy_col→current_col)에 if_exists='drop' 재구축 + day-1 컬럼 사라짐. Phase VV 옆에 +2 = cross-vendor 통합 it 4종 회귀 가드. 이번 슬라이스 코어 변화 0. 코어 unit 802 unchanged. mypy/ruff clean.
>
> **이전 마일스톤 (2026-05-29): Cross-DB overwrite + auto_create — idempotent re-run dogfood (Phase AAD).** 분석가 페르소나(매시간 dashboard latest snapshot 패턴): `mode=overwrite` + `auto_create_table` 조합 2-pass 실행. pass-1 sink 자동 생성 + write, pass-2 sink 존재(skip 분기) + overwrite가 truncate+insert로 idempotent 재현. 동일 (region, value) 3 rows, 중복 0, 스키마 drift 0. 운영 보장: "동일 시간 두 번 run → dashboard 영향 없다". 이번 슬라이스 코어 변화 0 — 기존 SinkConfig + ensure_table(skip) + overwrite mode 조합이 sample-data로 명문화. 서버 it 480→481(+1). 코어 802 unchanged. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): Cross-DB upsert + auto_create_table — PRIMARY KEY emit (Phase AAC, ADR-0072, 8번째 silent miss).** 사용자 페르소나 dogfood가 catch: `auto_create_table=true` + `mode=upsert` + `key_columns=[id]` 조합으로 fresh sink 생성 시 PK 없는 plain CREATE TABLE → 첫 ON CONFLICT가 *"does not match any PRIMARY KEY or UNIQUE constraint"* 실패. 보완: `SchemaWriter.ensure_table`에 `primary_key: list[str] | None` 추가 + 런타임이 `mode='upsert'` 시 자동 forwarding + 3 RDBMS connector(sqlite/postgres/mysql) PK constraint emit + 안전 검증. AAC1 페르소나 e2e (day-1 fresh sink + day-2 upsert merge: Alice updated/Carol joined/Bob unchanged, total 3 rows) green. 이번 세션 8번째 silent miss(Z/BB/II×2/MM/UU/XX/AAC). 코어 unit 799→802(+3). 서버 it 479→480(+1). DB 마이그레이션 0. backward-compat 완전(append/overwrite 영향 0).
>
> **이전 마일스톤 (2026-05-29): 빌더 UI `auto_create_if_exists` select + nightly-snapshot 페르소나 e2e (Phase AAB).** Phase AAA의 UX 닫힘. ① operators.ts 3 RDBMS sink(postgres/mysql/sqlite)에 `auto_create_if_exists` select 옵션 추가 — skip/drop/error + help text가 nightly snapshot 가이드. ② AAB1 엔지니어 nightly-snapshot 페르소나 e2e — day-1 소스 `(id, name)` → 싱크 자동 생성. day-2 소스 schema drift `(id, email)` → drop이 day-1 테이블·데이터 모두 제거 + 새 schema 재구축. **사용자 promise 직접 검증**: 매일 schema가 바뀌어도 운영자 손대지 않아도 됨. 신규 시각 컴포넌트 0(기존 select renderer 재사용). 서버 it 478→479(+1). 코어 799 unchanged. web tsc clean.
>
> **이전 마일스톤 (2026-05-29): `auto_create_if_exists` — sink 자동 생성 충돌 정책 (Phase AAA, ADR-0071).** `SinkConfig.auto_create_if_exists`(str, default `"skip"`) — skip/drop/error. 빌더 3 path(single-sink/fan-out/graph) 모두 forwarding. 3 RDBMS connector `ensure_table`가 이미 if_exists 지원(ADR-0066) — 런타임만 wire. AAA1 drop은 nightly snapshot replication 시나리오(source 스키마 매일 진화 → sink 재구축, stale `batch` 컬럼/데이터 자동 정리). AAA2 error는 런타임 best-effort 시맨틱 명문화(`_auto_create_sink_tables`가 `contextlib.suppress`로 감싸 원본 보호). 서버 it 476→478(+2). 코어 799 unchanged. mypy/ruff clean. DB 마이그레이션 0. backward-compat 완전 유지. **다음(AAB)**: operators.ts에 `auto_create_if_exists` select 노출.
>
> **이전 마일스톤 (2026-05-29): Cross-DB fan-out — sink별 `auto_create_table` 독립 동작 (Phase ZZ, ADR-0070).** ZZ1: 동일 source orders → 2 sinks(analytics with auto_create / warehouse 기존 테이블). analytics는 자동 생성(id, amount), warehouse는 기존 (id, amount, batch) 그대로 untouched. Sink별 flag 독립 적용 + 기존 테이블 보호 확인. 서버 it 475→476(+1 ZZ1). 코어 799 unchanged. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): 빌더 UI에 `auto_create_table` 토글 노출 + boolean FieldKind (Phase YY, ADR-0069).** Cross-DB migration UX 마지막 1마일. FieldDef에 `kind: "boolean"` 신규(defaultValue 옵셔널, wire shape true/false, false는 serializer drop). Properties panel renderer가 checkbox + label로 렌더. postgres/mysql/sqlite sink operators에 `auto_create_table` 필드 추가("Create table if missing" + cross-DB 타입 변환 help text). 빌더 사용자가 한 클릭으로 cross-DB migration 활성화 — postgres→sqlite/mysql/postgres 등 일반화. web tsc clean. 코어 799 unchanged. backward-compat 완전(false는 config에서 누락 → 기본값과 정합).
>
> **이전 마일스톤 (2026-05-29): Transform-aware `auto_create_table` — 7번째 silent miss 보완 (Phase XX, ADR-0068).** 사용자 페르소나 dogfood(SCD Type 2 cross-DB) 발견: `auto_create_table`이 source 원본 schema 사용 → transform 후 rename/add column이 sink와 불일치 → write 실패. 보완: `Task.transform_specs`(builder가 raw TransformConfig 보관) + `_project_columns_through_transforms`(rename/add_constant/cast/drop/select/filter/dedupe/assert 시뮬, opaque transform은 source verbatim fallback). 결과: SCD Type 2 migration(source country → sink region + effective_from/is_current 추가) 정상 동작. 이번 세션 7번째 silent miss(Z/BB/II×2/MM/UU/XX). 코어 unit 799 unchanged. 서버 it 474→475(+1 XX1). mypy 코어 62 + 서버 100 OK. ruff clean. DB 마이그레이션 0. backward-compat 완전 유지.
>
> **이전 마일스톤 (2026-05-29): Engineer cross-DB onboarding journey — REST UX 검증 (Phase WW, ADR-0067).** 사용자 페르소나 시리즈 4번째(데이터 엔지니어 cross-DB). WW1: 전체 REST 흐름 dogfood — login → workspace → connections × 2 → pipelines(sink.auto_create_table=true) → dry-run → trigger → drain → run 성공 + sqlite PRAGMA 타입 affinity(BIGINT→INTEGER, NUMERIC(10,2) 보존, TIMESTAMPTZ/JSONB/VARCHAR(64)→TEXT) + 카탈로그 asset_key 확인. 3-layer 검증(REST/sink DDL/카탈로그). REST schema 변경 0(auto_create_table는 PipelineConfig 옵셔널 필드). 서버 it 473→474(+1 WW1). 코어 799 unchanged. mypy 코어 62 + 서버 100 OK. ruff clean. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): Cross-DB replication — type mapping + 자동 sink table 생성 (Phase VV, ADR-0066).** 사용자 *"DB 간 마이그레이션 + 테이블 복제 + 데이터 타입 DB별 매핑"* 요청. 새 코어 기능: `etl_plugins/core/type_mapping.py`(13 CanonicalType + dialect 매핑 sqlite/postgres/mysql) + `SchemaWriter` Protocol(SchemaInspector의 dual) + `SinkConfig.auto_create_table` + `Pipeline._auto_create_sink_tables`(`_run_pre_sql` 직전 best-effort) + 3 RDBMS connector의 `ensure_table` 구현 + `postgres.list_columns`에 precision/scale/length 보강. 타입 매핑 핵심: BIGINT↔INTEGER(sqlite 합치기) / TIMESTAMPTZ↔DATETIME↔TEXT / JSONB↔JSON↔TEXT / BOOLEAN↔TINYINT(1)↔INTEGER / VARCHAR(n) length-aware. 코어 unit 738→799(+61) + 서버 it 472→473(+1 sqlite→sqlite e2e) + 통합 it 2 신규(postgres↔sqlite 양방향 testcontainers). mypy 코어 62 + 서버 100 OK. ruff clean. **DB 마이그레이션 0**(metadata DB 무변경) · backward-compat 완전 유지(auto_create_table 기본값 False).
>
> **이전 마일스톤 (2026-05-29): Analyst exploration journey + 6번째 silent UX miss 보완 (Phase UU, ADR-0065).** 두 번째 사용자 페르소나 — 분석가. UU1(Viewer 권한 catalog 탐색 5-step: list → lineage → column drill-down → materializations → forbidden action 403). **Dogfooding 발견**: `AssetSummary` 응답에 `column_lineage_opaque` 필드 누락 → 분석가가 목록에서 traceability badge 한눈에 못 보고 매 자산마다 column-lineage 엔드포인트 호출 필요(bad UX). ADR-0041 J2가 컬럼은 추가했지만 schema 안 노출. 보완: `AssetSummary.column_lineage_opaque: bool = False` 추가, `from_attributes=True`로 자동 populate. 이번 세션 6번째 silent miss(Z COUNT(*) / BB sink-only / II DLQ × 2 / MM lineage var / UU AssetSummary). 사용자 페르소나 e2e 패턴(TT 운영자 + UU 분석가)이 향후 페르소나 시리즈 템플릿. 서버 it 471→472(+1). 코어 738 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0. backward-compat.
>
> **이전 마일스톤 (2026-05-29): Operator onboarding journey — UX-first 전체 흐름 e2e (Phase TT, ADR-0064).** 사용자 *"UX 고려, 사용자 페르소나로 전체 점검"* 요청 적용. TT1(운영자 day-1: /auth/login → /workspaces → /connections × 2 → /connections/test → /pipelines → /dry-run → /trigger → drain → /runs/{rid} → /assets → /audit, 10-step REST journey 자연 흐름 + 응답 shape 검증), TT2(데이터 엔지니어 페르소나 — custom_python 첫 사용 시 Phase DD `column_mapping_recommended` warning 적시 표시). 검증 3 axis: status code + 응답 shape(id/records_written/asset_key 등 UI load-bearing) + 흐름 일관성(ID carry + token 재사용). UX 회귀 가드: 응답 shape 변경 즉시 catch. 서버 it 469→471(+2). 코어 738 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): Cron schedule 시나리오 — 시간 기반 trigger family 3축 완성 (Phase SS, ADR-0063).** Step 9.2 cron scheduler를 sample-data + 시간 시뮬: SS1(cron='* * * * *' + created_at 2분 전 강제 → tick_once fired=1 + drain → sink rows + run.schedule_id), SS2(is_active=False → fired=0). 시간 기반 trigger 3축 명문화: pipeline-axis(NN/ADR-0038, producer SLA) + asset-axis(RR/K3 sensor, consumer declare) + operator-axis(SS/cron, operator 정시). schedules.created_at 직접 UPDATE 패턴(NN의 last_materialized_at 확장). 서버 it 467→469(+2). 코어 738 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0. 운영 자동화 핵심이 모두 sample-data e2e 통합 입증.
>
> **이전 마일스톤 (2026-05-29): K3 Sensor framework 통합 시나리오 — `asset_freshness` builtin (Phase RR, ADR-0062).** ADR-0041 K3 sensor framework를 real catalog + real sensor row + SensorScheduler.tick_once 통합 검증: RR1(stale catalog → tick fired=1 + Sensor.last_triggered_at/last_result_json 정확 + PENDING run enqueue), RR2(fresh within budget → fired=0 + last_triggered_at None). asset-axis(K3 sensor: consumer 입장 declare) vs pipeline-axis(NN/ADR-0038 Schedule.freshness_sla_minutes: producer 입장) 구분 명문화. 시간 시뮬 NN 패턴 재사용. 서버 it 465→467(+2). 코어 738 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): Audit trail 통합 시나리오 — data-plane events 운영 검증 (Phase QQ, ADR-0061).** Phase U/W의 run.sql_read/run.python_executed/run.sql_executed를 real run + sample data 통합 검증: QQ1(single run에서 count + payload 정확 — connection_type/kind/query/code_hash 모두), QQ2(multi-run isolation — resource_id 필터로 자기 audit만, cross-run leakage 0). 3차원 검증: count + payload + isolation. GDPR/SOX compliance 운영 워크플로(audit_log scan만으로 'run에서 무슨 query/code 돌았나' 답) 확인. 서버 it 463→465(+2). 코어 738 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): Pipeline evolution 시나리오 — config 변경 시 catalog 동작 명문화 (Phase PP, ADR-0060).** 운영 흔한 패턴 검증: PP1(v1 sink:stage_v1 → v2 sink:stage_v2, catalog에 둘 다 보유 + 각자 materialization 1개 + v1 자연스럽게 stale), PP2(v1 raw → v2 raw JOIN dim_country, src/dim_country 자동 등록 + edge 추가 + sink materialization 1→2). Catalog evolution 정책 명문화: 자산 immutable/append-only + 새 자산 자동 등록(Phase X 후속의 extract_referenced_tables) + edge upsert + materialization run-per-row. 서버 it 461→463(+2). 코어 738 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): Connection 실패 시나리오 — catalog clean 정책 보장 (Phase OO, ADR-0059).** 운영 흔한 실패 2종: OO1(invalid db path → connect 실패), OO2(source table missing → read 실패). 둘 다 run.status=FAILED + error_class/error_message 채워짐 + 카탈로그 empty. transform fail(FF/F) + DLQ fail(II) + connect fail(OO1) + read fail(OO2) 4-stage 모두 같은 "no half-written catalog rows" posture 통합 검증. 서버 it 459→461(+2). 코어 738 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): Sensors freshness 시나리오 — 시간 기반 auto-materialize sample-data e2e (Phase NN, ADR-0058).** ADR-0038의 `freshness_sla_minutes` 동작 검증: NN1(SLA 60분 + last_materialized_at 2시간 전 강제 → `_tick_freshness` fired=1 + PENDING run에 `triggered_by: freshness` stamp + drain 시 asset refresh), NN2(SLA 60분 + 최근 materialized → fired=0, 건강한 pipeline 큐 storm 안 함). 시간 시뮬 패턴(`Asset.last_materialized_at` + `Run.created_at` 직접 UPDATE)으로 외부 wall-clock mock 불필요. 향후 시간 기반 e2e에서 재사용 가능. 서버 it 457→459(+2). 코어 738 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): Workspace variables 시나리오 + lineage emit silent bug 보완 (Phase MM, ADR-0057).** sample-data dogfood 중 발견: `_lineage_for`가 `version.config_json` 그대로 사용 → catalog asset_key가 unresolved `${var.target_table}`로 저장. Pipeline은 정상 실행되나 운영자가 catalog에서 자산 못 찾음. 보완: 새 `_lineage_for_resolved(session, run, version, log)` — workspace vars resolve 후 derive_lineage. `_persist_column_lineage(..., *, resolved_cfg=None)` 옵셔널 인자로 column lineage도 동일 resolved cfg. MM1 시나리오: 두 ws(prod/dev) 동일 config + 다른 var값 → 각자 resolved key catalog. **이번 세션 4번째 silent bug** dogfood가 catch. 서버 it 456→457(+1). 코어 738 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0. backward-compat 유지(`_lineage_for` 그대로 + column_lineage 추가 인자 옵셔널).
>
> **이전 마일스톤 (2026-05-29): Auto-materialize cycle 차단 시나리오 — `trigger_chain` 안전성 (Phase LL, ADR-0056).** A(staging→mart) + B(mart→staging) 둘 다 auto_materialize. A 트리거 → drain → 정확히 A 1 run + B 1 run (무한 loop 없음). B의 result_json.trigger_chain == [A.id]로 cycle 차단 메커니즘 audit lineage 보존. 운영 안전성 확인 — 잘못된 wiring이 무한 큐 못 만듦. 서버 it 455→456(+1). 코어 738 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): Catalog REST API 시나리오 — UI 경로 e2e 검증 (Phase KK, ADR-0055).** worker가 쓴 카탈로그를 UI가 호출하는 REST endpoint로 조회 정확성 검증: KK1(2-pipeline chain → list+lineage 응답 정확), KK2(2 runs → materializations 2 entries + column-lineage opaque=false), KK3(cross-ws 404 + non-member 403). `_build_app(session)`이 outer-transaction session 공유로 worker write/REST read atomicity. 서버 it 452→455(+3). 코어 738 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0. UI ↔ worker shape drift 회귀 가드.
>
> **이전 마일스톤 (2026-05-29): Cursor backfill 시나리오 — incremental sync semantics 깊은 검증 (Phase JJ, ADR-0054).** ADR-0039 backfill의 cursor range semantics(`(cursor_from, cursor_to]`)와 catalog materialization audit trail을 sample-data e2e로: JJ1(10 rows + 3-day window → 정확히 3 rows + backfill marker survive), JJ2(full+backfill → 같은 asset row + 2 materialization entries). 서버 it 450→452(+2). 코어 738 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.
>
> **이전 마일스톤 (2026-05-29): DLQ 시나리오 검증 + 두 코어 버그 보완 (Phase II, ADR-0053).** dogfood 시나리오 작성 중 두 silent 미스 동시 발견: (1) `_dlq_route_batch`가 `table` kwarg 전달 안 함 → sqlite sink가 WriteError 발생 → `contextlib.suppress`가 삼킴 → DLQ가 실제로 *동작 안 함*. (2) `derive_lineage`가 DLQ를 outputs로 안 추가 → 카탈로그에 "실패 record 어디 갔지?" 답 없음. 둘 다 코어에서 보완. e2e 시나리오 2종(II1 record-level partial success — clean 3 row/bad 2 row, II2 catalog에 DLQ asset 등록). 코어 단위 1 신규. 코어 unit 737→738(+1) + 서버 it 448→450(+2). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0. **단위로는 catch 못 했을 미스를 sample-data e2e가 catch — 사용자 요청의 가치 입증**.
>
> **이전 마일스톤 (2026-05-29): Multi-workspace 격리 시나리오 — 멀티 테넌트 신뢰성 (Phase HH, ADR-0052).** 같은 connection name + 같은 table name이 두 ws에 공존해도 격리 보장. HH1(auto-materialize trigger가 ws 경계 안 넘음 — ws_a producer만 큐 → ws_a 2 runs/ws_b 0 runs/data 격리) + HH2(catalog row가 ws-scoped — 같은 asset_key 문자열이지만 row id 분리, upstream disjoint). 3-layer 검증(data/run/catalog). 서버 it 446→448(+2). 코어 737 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0. SaaS 멀티 테넌트 운영 신뢰성 확인.
>
> **이전 마일스톤 (2026-05-29): Auto-materialize 시나리오 corpus — chain/fan-out/실패 차단 깊은 검증 (Phase GG, ADR-0051).** ADR-0037 D1 메커니즘의 interplay(catalog 정합성 + chain 동작 + 실패 차단)를 sample data e2e로 깊게: GG1 happy chain(A→B 자동 trigger, catalog 전체 chain), GG2 fan-out(A→B+C 둘 다 자동), GG3 failure blocks(A 실패 → B 큐 empty + catalog dirty 안 됨). 3-layer 검증(record/asset/run lineage). `_drain_pending_runs` helper로 큐 끝까지. Asset key `connection/table` 디자인이 chain에서 표면화되는 점을 코멘트로 명문화. 서버 it 443→446(+3 시나리오). 코어 737 unchanged. mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.
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
