# Changelog

이 파일은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 형식을 따르며,
[Semantic Versioning](https://semver.org/spec/v2.0.0.html)을 따른다.

릴리스 시 `pyproject.toml`의 버전과 이 파일의 헤더가 일치해야 한다 (release workflow에서 검증).

---

## [Unreleased]

### Added
- **빌더 "Cross-DB Migration" 스타터 템플릿 (Phase AAI)** — 사용자가 "DB 간 마이그레이션" 패턴을 한 클릭으로 시작:
  - 새 템플릿 `db-migrate-cross`: postgres source + sqlite sink, **`auto_create_table=true` 사전 설정**(사용자가 토글 발견할 필요 0).
  - `state()` helper에 per-node `overrides` 인자 추가 — 템플릿이 노드별 field defaults 지정 가능.
  - i18n 4 신규 키 en/ko(`tpl.dbMigrateCross/Desc`). 설명: "Postgres → SQLite. 싱크 테이블을 자동 생성하고 타입을 자동 변환".
  - 사용자 promise: AAA→AAH의 cross-DB 기능을 가장 짧은 path로 다음 사용자에게 노출.
  - 검증: web tsc clean. 코어/서버 변화 0.

- **`examples/cross_db_migration.yaml` + 동반 `connections.example.yaml` (Phase AAH)** — Phases AAA→AAG의 cross-DB migration 기능을 한 페이지 분량의 카피·페이스트 가능한 사용자용 문서 + 회귀 가드. `auto_create_table` + `auto_create_if_exists` 코멘트 가이드, `etlx validate` 명령 사용법. 신규 unit `test_example_yaml_loads.py` — 예제 YAML이 향후 config-schema 변경에 드리프트 안 하도록 lock-in. 코어 unit 804→805(+1). ruff clean.

### Changed
- **`SinkConfig.auto_create_if_exists`를 `Literal["skip","drop","error"]`로 좁힘 (Phase AAG)** — typos(`"DROP"`/`"replace"`/`"Skip"`)가 config-load 시점에서 즉시 422로 거부됨. 기존엔 plain str 받아 들어가 connector 깊은 곳의 if_exists branch에서 "unknown" 메시지로만 실패해 운영자 디버그 비용 큼. 2 신규 unit(canonical 3종 accept + typo 3종 reject). 코어 unit 802→804(+2). mypy/ruff clean. backward-compat 완전(기존 값 그대로).

- **빌더 FieldDef.showWhen — dependent field visibility (Phase AAF)** — `auto_create_table` default OFF + `auto_create_if_exists` select 항상 노출 = 99% 사용자에게 clutter. ① `FieldBase`에 `showWhen?: { field, equals }` 추가. ② Properties panel이 노드 데이터를 보고 conditional 렌더. ③ 3 RDBMS sink(postgres/mysql/sqlite)의 `auto_create_if_exists` field를 `showWhen: { field: "auto_create_table", equals: true }`로 설정. ④ help text에서 "Only applied when '...' is on" 잉여 가이드 제거 — UI 자체가 사라지므로 불필요. **결과**: 기본 sink 패널에서 if_exists 옵션 사라짐 + 사용자가 `auto_create_table` 켜는 순간 자연히 출현. 신규 시각 컴포넌트 0(필터 한 줄). web tsc clean. 코어/서버 변화 0.

### Added
- **Cross-DB cross-vendor upsert/drop testcontainers e2e (Phase AAE)** — 실제 postgres↔sqlite로 AAA(drop) + AAC(upsert PK) 회귀 가드:
  - **AAE1 (postgres→sqlite upsert)**: postgres BIGINT id + VARCHAR(64) name → sqlite `ensure_table(primary_key=['id'])` → 2-pass upsert(idempotent merge). AAC가 sqlite-only 검증이었던 것을 postgres 소스 cross-vendor로 확장.
  - **AAE2 (sqlite→postgres drop)**: sqlite source의 schema drift(legacy_col → current_col)에 postgres sink가 `if_exists='drop'`로 재구축. day-1 컬럼 사라짐 + day-2 데이터 정상.
  - Phase VV의 기존 2 testcontainer 테스트 옆에 AAE 2개 추가 = 통합 it 4종 cross-vendor 회귀 가드.
  - 검증: 통합 it 2→4(+2 AAE1/AAE2 via real Docker postgres).

- **Cross-DB overwrite — idempotent re-run dogfood (Phase AAD)** — `mode=overwrite` + `auto_create_table` 조합이 분석가 시나리오(매시간 latest snapshot for dashboard)에서 idempotent 동작 확인:
  - AAD1: 동일 source 데이터로 2-pass 실행. pass-1은 sink 자동 생성 + 데이터 write. pass-2는 sink 이미 존재(skip 분기) + overwrite가 truncate+insert로 같은 shape 재현. **결과**: 동일 (region, value) 3 rows, 중복 0, 스키마 drift 0.
  - 운영 보장: 분석가가 "동일한 시간에 두 번 run해도 dashboard에 영향 없다" 직관 검증.
  - 서버 it 480→481(+1 AAD1).

### Fixed
- **Cross-DB upsert + auto_create_table — `ensure_table`가 PRIMARY KEY emit (Phase AAC, 8번째 silent miss)** [ADR-0072] — 사용자 페르소나 dogfood가 catch:
  - **Bug**: `auto_create_table=true` + `mode=upsert` + `key_columns=[id]` 조합 시 `ensure_table`이 PK 없이 plain CREATE TABLE 발행 → 첫 run의 `ON CONFLICT (id)`가 *"does not match any PRIMARY KEY or UNIQUE constraint"*로 실패. live customers cache 패턴(매 run 최신 snapshot upsert) 운영자 직관 사용 패턴.
  - **보완**: `SchemaWriter.ensure_table`에 `primary_key: list[str] | None` 추가 + 런타임이 `mode='upsert'`일 때 자동 forwarding + 3 RDBMS connector(sqlite/postgres/mysql) PK constraint emit + PK 컬럼이 columns에 없으면 WriteError.
  - **결과**: AAC1 페르소나 e2e (day-1 fresh sink 자동 PK + day-2 upsert merge, Alice updated/Carol joined/Bob unchanged, total 3 rows) green.
  - **이번 세션 8번째 silent miss**: Z/BB/II×2/MM/UU/XX/AAC. dogfood 가치 누적 입증.

### Added
- **빌더 UI에 `auto_create_if_exists` select 노출 + 엔지니어 nightly-snapshot 페르소나 (Phase AAB)** — Phase AAA의 UX 닫힘 + 페르소나 dogfood:
  - **operators.ts 3 RDBMS sink(postgres/mysql/sqlite)에 `auto_create_if_exists` select** — skip / drop / error 옵션 + help text가 "drop은 nightly snapshot에 좋다" 가이드.
  - **AAB1 페르소나 e2e** — 엔지니어 day-1: 소스 `(id, name)` → 싱크 자동 생성 + 데이터 정상. day-2: 소스 schema drift `(id, email)` (name 제거, email 추가) → drop이 day-1 테이블 + 데이터 모두 제거 + 새 schema로 재구축. **사용자 promise 직접 검증**: 매일 schema가 바뀌어도 운영자가 손대지 않아도 됨.
  - 검증: 서버 it 478→479(+1 AAB1) + 코어 unchanged + web tsc clean.

- **`auto_create_if_exists` — sink 자동 생성 충돌 정책 (Phase AAA)** [ADR-0071] — `auto_create_table`의 충돌 모드 확장:
  - **`SinkConfig.auto_create_if_exists`(str, default `"skip"`)** — skip(기본, BC) / drop(재구축) / error(connector 차원 raise).
  - **운영 시나리오 1 (nightly snapshot replication)**: source 스키마 매일 진화 → `drop`으로 sink 재구축. stale 컬럼/데이터 자동 정리.
  - **운영 시나리오 2 (strict 보호)**: `error`로 충돌 시 raw `ensure_table` 호출 차단.
  - **빌더 3 path 모두 forwarding** — single-sink / fan-out / graph.
  - **검증**: AAA1 drop은 stale `batch` 컬럼 제거 + 새 데이터 정상 + AAA2 error는 런타임 best-effort 시맨틱(원본 보호) 명문화. 서버 it 476→478(+2).

- **Cross-DB fan-out — sink별 `auto_create_table` 독립 동작 검증 (Phase ZZ)** [ADR-0070] — 운영 흔한 fan-out 패턴:
  - ZZ1: source orders → 2 sinks(analytics with auto_create + warehouse 기존 테이블). analytics는 자동 생성(id, amount), warehouse는 untouched(id, amount, batch 그대로).
  - Sink별 flag 독립 적용 + 기존 테이블 보호 확인.
  - 검증: 서버 it 475→476(+1).

- **빌더 UI에 `auto_create_table` 토글 노출 + boolean FieldKind (Phase YY)** [ADR-0069] — cross-DB migration의 UX 마지막 1마일:
  - **FieldDef에 `kind: "boolean"` 신규** — defaultValue 옵셔널. wire shape true/false, false는 serializer가 drop(`onChange(e.target.checked || undefined)`).
  - **Properties panel renderer**: `<input type="checkbox">` + label, accent color.
  - **postgres/mysql/sqlite sink operators에 `auto_create_table` 필드** — "Create table if missing" + cross-DB 타입 변환 help text.
  - **빌더 사용자가 한 클릭으로 cross-DB migration 활성화** — postgres→sqlite/mysql/postgres 등 일반화.
  - **검증**: web tsc clean. 코어 799 unchanged. backward-compat(false는 config에서 누락 → 기본값과 정합).

### Fixed
- **Transform-aware `auto_create_table` — 7번째 silent miss 보완 (Phase XX)** [ADR-0068] — 사용자 페르소나 dogfood(SCD Type 2 cross-DB)가 catch:
  - **Bug**: `auto_create_table`이 source 원본 schema로 sink 생성. transform 후 rename/add column 시 sink schema와 불일치 → write 실패('no column named region' 등).
  - **보완**: `Task.transform_specs: list[dict]` + builder가 raw TransformConfig 보관 + `_project_columns_through_transforms` helper(rename/add_constant/cast/drop/select/filter/dedupe/assert 모두 처리) + opaque transform(python 등)은 source verbatim fallback(Phase VV 동작 유지).
  - **결과**: SCD Type 2 migration 정상 동작 — source country → sink region, 새 effective_from/is_current 컬럼 자동 추가.
  - **이번 세션 7번째 silent miss**: Z/BB/II×2/MM/UU/XX. dogfood 가치 누적 입증.
  - **검증**: 코어 unit 799 unchanged. 서버 it 474→475(+1 XX1). DB 마이그레이션 0.

### Added
- **Engineer cross-DB onboarding journey — REST UX 검증 (Phase WW)** [ADR-0067] — 사용자 페르소나 시리즈 4번째(데이터 엔지니어 cross-DB):
  - **WW1**: REST 전 path로 cross-DB pipeline. Source sqlite(mixed vendor types) → POST /auth/login → /workspaces → /connections × 2 → /pipelines(sink.auto_create_table=true) → /dry-run → /trigger → drain → /runs/{rid} 성공 → sqlite PRAGMA로 타입 affinity 확인 → /assets 카탈로그 확인.
  - **3-layer 검증**: REST 응답 shape + sink 파일 실제 DDL + 카탈로그 asset_key 일관성.
  - **REST schema 변경 0**: auto_create_table는 PipelineConfig 옵셔널 필드라 응답 schema 무영향.
  - **검증**: 코어 799 unchanged. 서버 it 473→474(+1). DB 마이그레이션 0.

- **Cross-DB replication — type mapping + 자동 sink table 생성 (Phase VV)** [ADR-0066] — 사용자 요청 *"DB 간 마이그레이션 + 테이블 복제, 데이터 타입 DB별로 매핑"*. 새 기능:
  - **`etl_plugins/core/type_mapping.py`(신규)**: `CanonicalType` enum 13개 + `TypeSpec` + `normalize_db_type` + `render_canonical`. dialect 매핑 테이블(sqlite/postgres/mysql). 새 dialect는 한 entry로 추가.
  - **`SchemaWriter` Protocol** (`etl_plugins/core/inspect.py`): SchemaInspector의 dual. `ensure_table(table, columns, *, if_exists)` capability.
  - **`SinkConfig.auto_create_table: bool = False`** — Pydantic 옵셔널. True + source가 SchemaInspector + sink가 SchemaWriter → 자동 DDL.
  - **`Pipeline._auto_create_sink_tables`** — `_run_pre_sql` 직전 호출, best-effort.
  - **sqlite + postgres + mysql connector에 `ensure_table` 구현** — 각자 dialect render + identifier 화이트리스트.
  - **`postgres.list_columns` 보강** — character_maximum_length / numeric_precision / numeric_scale 활용 → `VARCHAR(64)` / `NUMERIC(10,2)` attribute 보존.
  - **타입 매핑 핵심**: `BIGINT`↔postgres `BIGINT`/mysql `BIGINT`/sqlite `INTEGER`. `TIMESTAMPTZ`↔postgres `TIMESTAMPTZ`/mysql `DATETIME`/sqlite `TEXT`. `JSONB`↔postgres `JSONB`/mysql `JSON`/sqlite `TEXT`. `BOOLEAN`↔postgres `BOOLEAN`/mysql `TINYINT(1)`/sqlite `INTEGER`. `VARCHAR(n)`↔postgres/mysql `VARCHAR(n)`/sqlite `TEXT`(length 드롭).
  - **검증**: 코어 unit 738→799(+61: type_mapping 50+ + sqlite ensure_table 6). 서버 it 472→473(+1 sqlite→sqlite e2e). 통합 it 2 신규(postgres↔sqlite 양방향 testcontainers). mypy 코어 62 + 서버 100 OK. ruff clean. DB 마이그레이션 0(metadata DB 무변경). backward-compat 완전 유지(auto_create_table 기본값 False).

### Added/Fixed
- **Analyst exploration journey + AssetSummary 누락 필드 보완 (Phase UU)** [ADR-0065] — 두 번째 사용자 페르소나(분석가). Viewer 권한으로 catalog 탐색 5-step:
  - **UU1 — Analyst catalog 탐색**: Owner가 2-stage chain 실행 → 분석가가 list → lineage → column drill-down → materializations → Viewer-금지 action 403.
  - **Dogfooding 발견 + 보완 (6번째 silent miss)**: `AssetSummary`에 `column_lineage_opaque` 필드 누락. UI 목록에서 traceability badge 한눈에 못 보고 매번 column-lineage 호출 필요 → bad UX. ADR-0041 J2가 컬럼은 추가했지만 schema 안 노출.
  - **보완**: `AssetSummary.column_lineage_opaque: bool = False` 추가. `from_attributes=True`로 자동 populate. backward-compat.
  - **검증**: 코어 738 unchanged. 서버 it 471→472(+1). DB 마이그레이션 0. **이번 세션 6번째 silent miss** catch.

- **Operator onboarding journey — UX-first 전체 흐름 e2e (Phase TT)** [ADR-0064] — 사용자 요청 "UX 고려, 사용자 페르소나로 전체 점검". 신규 운영자의 first-15-minutes path:
  - **TT1 — 운영자 신규 가입부터 첫 catalog까지 10-step REST journey**: /auth/login → /workspaces (auto-Owner) → /connections × 2 → /connections/test → /pipelines → /dry-run → /trigger → drain → /runs/{rid} → /assets → /audit. 각 단계 status code + 응답 shape(id/slug/records_written/connectors/asset_key/after_json) + 흐름 일관성(ID carry, token 재사용) 검증.
  - **TT2 — 데이터 엔지니어 페르소나, custom_python + Phase DD nudge UX**: dry-run 응답에 `column_mapping_recommended` warning + location='transforms.0' + message에 "column_mapping" 단어. run 자체는 ok 유지.
  - **사용자 페르소나 정의**: TT1(운영자 day-1 sqlite), TT2(엔지니어 python 첫 사용 → UX nudge 적시 표시).
  - **검증 3 axis**: status code + 응답 shape + 흐름 일관성.
  - **UX 회귀 가드**: 응답 shape 변경 시(예: records_written 누락) onboarding e2e가 즉시 catch.
  - **검증**: 코어 738 unchanged. 서버 it 469→471(+2). DB 마이그레이션 0.

- **Cron schedule 시나리오 — 시간 기반 trigger family 3축 완성 (Phase SS)** [ADR-0063] — Step 9.2 cron scheduler를 sample-data + 시간 시뮬:
  - **SS1 — Due cron fires + drain**: cron_expr='* * * * *' + schedules.created_at 2분 전 강제. tick_once → fired=1. drain → records sink로 + run.schedule_id 채워짐.
  - **SS2 — Inactive schedule skipped**: is_active=False → fired=0, runs 비어 있음.
  - **시간 기반 trigger 3축 명문화**: pipeline-axis(NN, producer) + asset-axis(RR/K3, consumer) + operator-axis(SS/cron, operator). 운영 자동화 핵심이 sample-data e2e로 통합 입증.
  - **시간 시뮬 패턴**: schedules.created_at 직접 UPDATE로 first-firing base 과거로. NN의 last_materialized_at 패턴 확장.
  - **검증**: 코어 738 unchanged. 서버 it 467→469(+2). DB 마이그레이션 0.

- **K3 Sensor framework 통합 시나리오 — `asset_freshness` builtin (Phase RR)** [ADR-0062] — ADR-0041 K3 sensor framework를 real catalog + real sensor row + SensorScheduler.tick_once 통합 검증:
  - **RR1 — Stale catalog triggers sensor**: pipeline run → catalog. last_materialized_at 3시간 전 강제. tick → fired=1. Sensor row의 last_check_at/last_triggered_at/last_result_json 정확. PENDING run enqueue.
  - **RR2 — Fresh within budget**: tick → fired=0. last_triggered_at None. last_result_json "fresh".
  - **asset-axis(K3) vs pipeline-axis(NN/ADR-0038)** 구분 명문화: 같은 freshness지만 다른 trigger path. 운영자가 producer/consumer 입장 선택.
  - **검증**: 코어 738 unchanged. 서버 it 465→467(+2). DB 마이그레이션 0.

- **Audit trail 통합 시나리오 — data-plane events 운영 검증 (Phase QQ)** [ADR-0061] — Phase U/W의 audit events를 real run + sample data로 통합 검증:
  - **QQ1 — Single run full audit**: sqlite source + custom_python + sink. 검증: run.sql_read 1 + run.python_executed 1 + run.sql_executed 0. after_json payload(connection_type/kind/query/code_hash) 정확.
  - **QQ2 — Multi-run isolation**: 같은 pipeline 2 run → 각 run resource_id 필터로 자기 audit만. cross-run leakage 없음.
  - **3차원 검증**: count(누락/중복 없음) + payload(field 정확) + isolation(run 격리).
  - **GDPR/SOX compliance**: audit_log scan만으로 "이 run에서 무슨 query 돌고 무슨 code 실행됐나" 정확히 답 가능.
  - **검증**: 코어 738 unchanged. 서버 it 463→465(+2). DB 마이그레이션 0.

- **Pipeline evolution 시나리오 — config 변경 시 catalog 동작 명문화 (Phase PP)** [ADR-0060] — 운영자가 pipeline config 변경 시 catalog가 어떻게 변화를 반영하는지:
  - **PP1 — Sink table rename**: v1 sink:stage_v1 → v2 sink:stage_v2. catalog에 *둘 다* 보유, 각자 materialization 1개. v1은 자연스럽게 stale(last_materialized_at 안 갱신).
  - **PP2 — Source JOIN dimension 추가**: v1 raw만 → v2 raw JOIN dim_country. v2 run 후 src/dim_country 자동 등록(Phase X 후속) + edge 자동 추가. Sink는 동일 identity라 materialization 1→2.
  - **catalog evolution 정책 명문화**: 자산 immutable/append-only / 새 자산 자동 등록 / edge upsert / materialization run-per-row.
  - **검증**: 코어 738 unchanged. 서버 it 461→463(+2). DB 마이그레이션 0.

- **Connection 실패 시나리오 — catalog clean 정책 보장 (Phase OO)** [ADR-0059] — 운영 흔한 실패 2종에서 catalog가 항상 깨끗 유지:
  - **OO1 — Invalid database path**: 잘못된 path → connect 실패 → run failed + error_class/error_message 채워짐 + 카탈로그 empty.
  - **OO2 — Source table missing**: 유효한 db이지만 raw table 없음 → read 실패 → run failed + error_message에 table 정보 + 카탈로그 empty.
  - **catalog clean 정책 통합 검증**: transform fail(FF/F) / DLQ fail(II) / connect fail(OO1) / read fail(OO2) 4가지 stage 모두 같은 posture.
  - **검증**: 코어 738 unchanged. 서버 it 459→461(+2). DB 마이그레이션 0.

- **Sensors freshness 시나리오 — 시간 기반 auto-materialize sample-data e2e (Phase NN)** [ADR-0058] — ADR-0038의 `freshness_sla_minutes` 동작을 sample-data + 시간 시뮬로:
  - **NN1 — Stale → 자동 enqueue**: SLA 60분, last_materialized_at 2시간 전 강제. `_tick_freshness` → fired=1, PENDING run에 `triggered_by: freshness` stamp, drain 시 asset refresh.
  - **NN2 — Within SLA → no fire**: SLA 60분, last_materialized_at 기본(최근). fired=0. 건강한 pipeline이 큐 storm 안 함.
  - **시간 시뮬 패턴**: `Asset.last_materialized_at` + `Run.created_at` 직접 UPDATE → 외부 wall-clock mock 불필요.
  - **검증**: 코어 738 unchanged. 서버 it 457→459(+2). DB 마이그레이션 0.

### Fixed
- **Workspace variables 시나리오 + lineage emit silent bug 보완 (Phase MM)** [ADR-0057] — sample-data dogfood 중 발견된 silent correctness bug:
  - **Bug**: `RunExecutor._lineage_for`가 `version.config_json` 그대로 사용 → catalog asset_key가 미해결 `${var.target_table}`로 저장. pipeline은 정상 실행되나 운영자가 카탈로그에서 자산 이름을 찾을 수 없음.
  - **보완**: 새 `_lineage_for_resolved(session, run, version, log)` — `WorkspaceVariableRepository.as_dict` → `resolve_config_variables` → `derive_lineage` 흐름. `_persist_column_lineage(..., *, resolved_cfg=None)` 옵셔널 인자로 column lineage도 동일 resolved cfg 사용. success branch가 두 모듈 unified path.
  - **시나리오 MM1**: 두 ws(prod/dev)에 동일 pipeline config(`table: "${var.target_table}"`) + 각자 var값 다름. 검증: 데이터/카탈로그 둘 다 환경별 resolved.
  - **이번 세션 4번째 silent bug** dogfood가 catch (Z COUNT(*) / BB sink-only / II DLQ × 2 / MM lineage var).
  - **검증**: 코어 738 unchanged. 서버 it 456→457(+1). mypy 코어 61 + 서버 100 OK. DB 마이그레이션 0.

### Added
- **Auto-materialize cycle 차단 시나리오 — `trigger_chain` 안전성 (Phase LL)** [ADR-0056] — ADR-0037의 cycle 방지가 unit으로는 mock 가능, 실제 worker drain loop에서 동작 확인은 다른 차원:
  - A(auto_materialize, staging→mart), B(auto_materialize, mart→staging). 서로 cross-wired.
  - A 트리거 → drain → 정확히 A 1 run + B 1 run. 무한 loop 없음.
  - B의 result_json.trigger_chain == [A.id] (audit lineage).
  - 운영 안전성: 잘못된 wiring이 무한 큐 못 만듦.
  - **검증**: 코어 738 unchanged. 서버 it 455→456(+1). DB 마이그레이션 0.

- **Catalog REST API 시나리오 — UI 경로 e2e 검증 (Phase KK)** [ADR-0055] — worker가 쓴 카탈로그를 UI가 호출하는 REST endpoint로 조회했을 때 정확한 응답인지 e2e:
  - **KK1 — List + Lineage REST**: 2-pipeline chain(raw→staging→mart) → GET /assets에 3 rows, GET /lineage에 upstream/downstream 정확.
  - **KK2 — Materializations + Column-lineage REST**: 같은 pipeline 2번 run → /materializations에 2 entries, /column-lineage에 opaque=false + 컬럼 upstream refs `src/raw`로.
  - **KK3 — Cross-workspace 404 + non-member 403**: ws_a 유저가 ws_b asset id로 요청 → 404. 비멤버 → 403. ACL 일관.
  - **App fixture**: `_build_app(session)`이 `get_session`을 outer-transaction session으로 override → worker write/REST read가 같은 atomicity.
  - **검증**: 코어 738 unchanged. 서버 it 452→455(+3). DB 마이그레이션 0.

- **Cursor backfill 시나리오 — incremental sync semantics 깊은 검증 (Phase JJ)** [ADR-0054] — ADR-0039 backfill의 cursor range semantics(`(cursor_from, cursor_to]`)와 catalog materialization audit trail을 sample-data e2e로:
  - **JJ1 — Backfill respects bounds**: 10 rows(day 1~10), backfill('2026-05-02', '2026-05-05') → 정확히 3 rows(day 3/4/5)만 sink로. records_read=3 + records_written=3. backfill marker가 run.result_json에 survive.
  - **JJ2 — Full run + backfill catalog**: full(no cursor bounds, 10 rows) → backfill(day 7-10, 4 rows). 결과: 14 rows append + catalog에 *같은 asset row* + 2개의 materialization entries(audit trail).
  - **`_seed_backfill_run` helper**: result_json.backfill 직접 박아 router와 동일한 worker 경로 코드 일관.
  - **검증**: 코어 738 unchanged. 서버 it 450→452(+2). DB 마이그레이션 0.

### Added/Fixed
- **DLQ 시나리오 검증 + 두 코어 버그 보완 (Phase II)** [ADR-0053] — 사용자 "sample 데이터 깊게" 요청. dogfood하다가 두 silent 미스 발견 + 동시 보완:
  - **버그 1 — DLQ silent drop**: `_dlq_route_batch`가 `sink.write([record], mode=...)`만 호출하고 `table` kwarg 전달 안 함. sqlite BatchSink가 `WriteError("requires 'table'")` 발생 → `contextlib.suppress`가 삼킴. *DLQ가 약속한 partial-success를 실제로 못 함*. 보완: `dlq.table` 명시 전달.
  - **버그 2 — DLQ catalog 미등록**: `derive_lineage`가 DLQ를 outputs로 안 추가. "실패 record 어디 갔지?" 카탈로그에서 답 없음. 보완: cfg.dlq를 outputs + 모든 inputs→dlq edge.
  - **E2E 시나리오 2종** (test_dlq_scenarios.py):
    * II1 — 5 raw(3 positive + 2 negative amount). custom_python이 negative raise. 결과: clean_orders 3 row, bad_orders 2 row, run SUCCEEDED. 데이터 측면 partial-success 동작 확인.
    * II2 — 같은 시나리오 + 카탈로그에 src/raw_orders + dst/clean_orders + dst/bad_orders 모두 등록 확인.
  - **코어 단위 1**: derive_lineage DLQ outputs + edge 단위 검증.
  - **검증**: 코어 unit 737→738(+1). 서버 it 448→450(+2). DB 마이그레이션 0.
  - **Dogfooding 가치**: 단위 테스트로는 mock sink 써서 silent skip을 못 잡음 + lineage 누락도 production 데이터 없으면 안 보임. sample-data e2e가 catch.

- **Multi-workspace 격리 시나리오 — 멀티 테넌트 신뢰성 깊은 검증 (Phase HH)** [ADR-0052] — 같은 connection name + 같은 table name이 두 워크스페이스에 공존해도 격리 보장 e2e 검증:
  - **HH1 — Auto-materialize는 workspace-scoped**: 두 ws가 같은 connection name(src/dst) + 같은 table name(raw/staging/mart)을 쓰지만 per-tenant sqlite. ws_a producer만 큐 → drain 후 ws_a 2 runs, ws_b 0 runs. data-plane도 격리.
  - **HH2 — Catalog row가 workspace-scoped**: 같은 asset_key 문자열이 양쪽에 있어도 row id가 다름. upstream 조회가 자기 ws만 반환(disjoint id 집합).
  - **3-layer 검증**: data(sqlite) + run(Run rows) + catalog(AssetRepository) 모두에서 격리.
  - **검증**: 코어 unit 737 unchanged. 서버 it 446→448(+2). DB 마이그레이션 0. SaaS 멀티 테넌트 운영 신뢰성 확인.

- **Auto-materialize 시나리오 corpus — chain/fan-out/실패 차단 깊은 검증 (Phase GG)** [ADR-0051] — ADR-0037(D1) auto-materialize의 *interplay*(catalog 정합성 + chain 동작 + 실패 차단)를 sample data e2e로 깊게 확인:
  - **GG1 — Happy chain (A → B)**: A가 staging 작성 → B(`auto_materialize: true`)가 자동 trigger → mart 작성. `_drain_pending_runs` helper로 큐 끝까지. 검증: A+B 각 1 run, B의 result_json.triggered_by_run=A.id, catalog에 raw→staging→mart edge 전체.
  - **GG2 — Fan-out (A → B + C)**: B/C 둘 다 같은 dst/shared를 source + `auto_materialize`. 1 A run + 2 자동 consumer run. dst/shared의 downstream에 mart_b + mart_c.
  - **GG3 — Failure blocks chain**: A의 custom_python이 raise → A fails. B는 0 runs. sink/mart 비어 있음. catalog dst/staging/dst/mart 둘 다 없음(Phase FF 시나리오 F와 일관).
  - **3-layer 검증**: record(실제 sink 값) + asset(catalog edge) + run(result_json trigger lineage).
  - **Asset key 디자인 dogfooding**: `connection/table` 키가 chain에서 어떻게 표면화되는지 코멘트로 기록 — chain pipeline 만들 때 connection 가리킬 곳 가이드.
  - **검증**: 코어 unit 737 unchanged. 서버 it 443→446(+3 시나리오). DB 마이그레이션 0.

- **column_mapping typo 검출 lint + 실패/재시도 시나리오 검증 (Phase FF)** [ADR-0050] — 신뢰성의 두 추가 차원: (a) 사용자 실수 catch + (b) 비정상 경로 카탈로그 일관성.
  - **새 lint 규칙 `column_mapping_unknown_source_column`**: linear/task-DAG에서 transform chain walk(derive_column_lineage와 동일). 각 transform의 column_mapping declaration이 그 시점 upstream mapping에 없는 source col을 참조하면 warning. 이전 transform의 rename도 정확히 반영. typo / stale 참조 silent 부정확 mapping 회피.
  - **시나리오 F: Failed run** — `custom_python`이 명시 raise → run status=failed → worker except branch가 `_persist_lineage` skip → sink/source asset row 생성 안 됨. 실패가 카탈로그 dirty하게 안 함.
  - **시나리오 G: Rerun idempotency** — 같은 config 2회 → asset row count + edge + column lineage edge 모두 변화 없음(같은 row id 유지). `AssetMaterialization`만 run마다 +1(audit trail 의도).
  - **기존 e2e 1개 narrow**: column_mapping의 source col이 source query에 실제 있어야 새 lint 안 발동 — assertion을 특정 code 두 가지에 대해서만 단언하도록 보강(후속 lint 추가가 깨지지 않게).
  - **검증**: 코어 unit 732→737(+5 lint). 서버 it 441→443(+2 시나리오). DB 마이그레이션 0.

- **확장 dogfood 시나리오 corpus — 실전 패턴 5종 sample-data e2e (Phase EE)** [ADR-0049] — 사용자 요청 *"테스트를 중점적으로 해서 항상 샘플 데이터를 생성해서 기능이 정상적인지 좀 깊게 테스트"*. Phase BB의 4-stage 시나리오를 깊이로 확장:
  - **Scenario A — SCD Type 2 dimension**: `customers_history`에 `effective_from`(alias→raw col) + `effective_to`/`is_current`(리터럴). 리터럴 컬럼이 정확히 empty upstream으로 catalog 등록.
  - **Scenario B — Fan-out by status**: 한 source query → `when` 술어로 confirmed/cancelled 두 sink 분기(ADR-0027). 양쪽 모두 asset edge + per-column lineage 정확.
  - **Scenario C — Aggregation chain (3-stage)**: `raw_sales` → `daily_sales` → `monthly_sales`. `SUM(amount)` GROUP BY chain이 base table `amount`로 정확 trace.
  - **Scenario D — Self-join (employee hierarchy)**: 같은 `employees` 테이블의 두 alias가 단일 base asset으로 매핑. `manager_name`도 `employees.name`으로 정확.
  - **Scenario E — Schema evolution**: sink `ALTER TABLE ADD COLUMN` 후 재실행 → Phase BB의 sink-only column 자동 등록.
  - **3-layer 검증**: record-level(실제 row 값 비교) + asset-level(asset row + edge) + column-level(per-column upstream attribution).
  - **샘플 데이터**: 5-20 rows per 시나리오 — 디버깅 쉬움.
  - **Dogfooding 발견**: Scenario B 초기에 sink schema 부족 → routing key 보존 필요 발견 + 코멘트로 best practice 노트 남김.
  - **검증**: 코어 unit 732 unchanged + 서버 it 436→441(+5 e2e). 모든 Phase X/Z/AA/CC 메커니즘 실전 깊이 검증. DB 마이그레이션 0.

- **Pipeline lint — dry-run에서 column_mapping 권장 advisory warning 노출 (Phase DD)** [ADR-0048] — Phase CC의 `column_mapping`은 opt-in이라 사용자가 *알아야* 채택률 올라감. dry-run 단계에서 자동 안내:
  - **`etl_plugins/runtime/lint.py`(신규) `lint_pipeline(cfg) -> list[LintWarning]`** — pure function, 모든 shape(linear/task-DAG/graph) 순회. 코어에 두어 라이브러리 단독 사용자도 활용.
  - **`LintWarning(code, message, location)`** — `code`는 stable identifier(UI 필터 키), `location`은 `tasks.0.transforms.2` 같은 path(builder가 jump).
  - **첫 규칙 `column_mapping_recommended`**: `python`/`custom_python`/`sql_exec` 트랜스폼이 `column_mapping` 없으면 발동. "schema-passthrough가 시도되지만 rename/이동은 못 잡으니 column_mapping 추가 권장".
  - **`DryRunResult.warnings` + 모든 return path에서 lint 호출**. Advisory: warnings만 있으면 `ok=True` 유지(차단 안 함).
  - **`DryRunResponse` Pydantic schema에 `warnings: list[DryRunLintWarning]` 추가**, router가 변환. 하위 호환(필드 추가만, 기존 변경 0).
  - **검증**: 코어 unit 723→732(+9 — rule fires/skips/모든 트랜스폼 타입/declarative 무 lint/멀티 opaque/task-DAG/graph/empty). 서버 it 434→436(+2 e2e — opaque transform → warning 노출 / column_mapping 박힘 → 무 lint).
  - **Builder UI 통합은 별개 슬라이스**: 현재는 dry-run 응답만 노출. 빌더 properties panel 표시 + 인라인 fix 액션은 후속.

- **사용자-명시 `column_mapping` — 트랜스폼에 직접 의도 박기 (Phase CC)** [ADR-0047] — Phase AA(schema-passthrough)는 컬럼명 *보존* python을 잘 잡지만 **컬럼명 rename**(`name`→`display_name`)이나 **a→b 데이터 이동**은 정적 분석 본질적 한계. 사용자가 의도를 박을 수 있는 빠져나갈 길:
  - **트랜스폼 config에 `column_mapping` 옵셔널 필드** (TransformConfig.model_config = `extra="allow"` 활용, 모델 변경 0 / DB 마이그레이션 0).
  - 형태: `{output_col: [source_col, ...]}` — source는 현재 _Mapping의 키. 빈 리스트 = 새 컬럼(no upstream).
  - **`_apply_transform`이 type 분기 *전*에 `column_mapping` 우선 처리** — 모든 트랜스폼 타입(rename/cast/python/custom_python/sql_exec)에 적용 가능.
  - **Replace 모드**(merge 아님): 명시한 출력 컬럼만 새 _Mapping에 들어감. Deterministic + 사용자가 의도를 정확히 기술. Trade-off는 verbose지만 declaration은 정적 분석 불가 트랜스폼에만 박는 거니 부담 작음.
  - **Worker fallback과 상호작용**: column_mapping 있으면 opaque 안 됨 → schema-passthrough fallback도 안 발동. 사용자 책임 모드.
  - **Malformed declaration**: helper가 None 시그널 → caller가 type 기반 처리로 fallback. 사용자 실수가 stale mapping 만들지 않음.
  - **테스트 6 신규**: 코어 unit 5 (override opaque / multi-source union / replace-mode / unknown source col → empty / malformed fall-through) + e2e 1 (sqlite source + custom_python `name`→`display_name` rename + column_mapping → catalog `display_name`←`seed.name` 정확).
  - **검증**: 코어 718→723. 서버 it 433→434. mypy 코어 60 + 서버 100. ruff clean. DB 마이그레이션 0.

- **4-stage e-commerce dogfooding 시나리오 + passthrough fallback 보완 (Phase BB)** [ADR-0046 보완] — 사용자 요청 *"복잡한 워크플로우 만들어서 수행, 카탈로그도 검증"*. 4단계 실전 시나리오 통합 테스트:
  - **Stage 1**: SQL JOIN + CTE — `raw_orders` + `raw_customers` → `staging_orders_enriched`.
  - **Stage 2**: CTE + `ROW_NUMBER()` window — `staging_orders_enriched` → `mart_top_orders`.
  - **Stage 3**: `custom_python` transform (passthrough fallback) — `mart_top_orders` → `report_customer_tier`. python 코드가 `tier` 컬럼 추가.
  - **Stage 4**: `SELECT *` (SchemaInspector inject) — `raw_products` → `products_clone`.
  - **검증 항목**: (a) 9개 asset row 모두 등록 — 5 source(raw + 중간 read-side) + 4 sink. (b) Asset DAG의 edge 일치(raw_orders+raw_customers→staging, staging→mart, mart→report, raw_products→clone). (c) **모든 sink가 `column_lineage_opaque=False`** — Phase Z/AA 약속이 4단계 통과. (d) Stage 1 multi-source column edge(`email`←raw_customers, `amount`←raw_orders 등), Stage 2 CTE+window 컬럼 trace, Stage 3 passthrough(`customer_id`+`top_amount` 자동 1:1 edge + `tier`는 empty upstream으로 row 존재), Stage 4 SELECT * 컬럼 enumerate.
  - **Dogfooding 발견 1개**: `_augment_opaque_with_schema_passthrough`가 sink-only 컬럼(python이 추가한 `tier`)에 대해 column row 자체를 안 만들던 사용성 미스 → sink schema의 *모든* 컬럼에 대해 row 생성(intersection은 upstream attribute, sink-only는 빈 upstream)으로 보완. 카탈로그가 sink의 물리 schema와 항상 일치.
  - **결과**: 서버 it 432→433 green. 모든 Phase X/Z/AA 기능이 실전 시나리오에서 한 번에 동작 확인.

- **Schema-passthrough fallback — 어떤 트랜스폼이든 column edge 자동 생성 (Phase AA)** [ADR-0046] — 사용자 요청 *"어떤 소스던 간에 결국 소스/타겟이 있잖아. 난 자동생성하고 싶은데 전부"*. 카탈로그의 asset row + asset edge는 이미 트랜스폼 무관하게 자동, 빠진 건 column 매핑. python/custom_python/sql_exec 트랜스폼이 끼면 정적 분석 불가 → opaque로 떨어지던 마지막 빈틈을 schema 기반 보수적 휴리스틱으로 채움:
  - **`RunExecutor._augment_opaque_with_schema_passthrough`**: 1차 derive 후 opaque sink가 남으면 발동.
  - `derive_lineage`의 asset edge로 source→sink 매핑 확보 → sink + 모든 upstream source schema fetch (`ConnectionInspector.list_columns`, Phase Z의 seed schemas 재사용).
  - **컬럼명 intersection만 1:1 attribute** — sink schema col 이름 ∩ source schema col 이름의 각 컬럼에 `ColumnEdge(downstream=sink.col, upstreams=(src.col,))`. 여러 source가 같은 이름 컬럼 가지면 멀티-upstream.
  - **보수적**: 이름 다르면 mapping 없음. 거짓 양성 0. 본질 opaque(HTTP/Kafka sink, 컬럼명 disjoint)는 정직하게 유지.
  - **best-effort**: passthrough 실패해도 run은 정상, 원래 opaque로 fallback.
  - **검증**: e2e 1개 (sqlite source + `custom_python` transform + sqlite sink → `column_lineage_opaque=False` + `id`/`name` 자동 1:1 edge 검증). 서버 it 431→432 green. 코어 718 unchanged. mypy 100 src OK.
  - **사용자 "전부 자동" 요구 완전 충족**: 컬럼명 보존 트랜스폼(python 사용 패턴의 다수)은 catalog가 자동 column edge.

- **2000-쿼리 fuzz harness + SchemaInspector inject로 `SELECT *` 실행 시점 해결 (Phase Z)** [ADR-0045] — 사용자 요청 *"2천 개 쿼리 임의 생성해서 단계적 테스트하며 보완"* + *"SchemaInspector로 SELECT * 해결"*. 두 작업이 자연스럽게 묶임 — fuzz가 잡은 실제 미스를 보완하는 게 신뢰성의 다음 단계이고, SELECT *는 결국 schema 없이는 못 푸는 마지막 opaque 케이스.
  - **`tests/unit/runtime/sql_corpus_generator.py`(신규)**: 22 generator(L1 baseline → L9 combined). 각 generator가 `(sql, expected_lineage)` 동시 합성 — ground-truth가 생성과정에서 따라옴. `Random(seed=42)` 고정 reproducible.
  - **`test_sql_lineage_fuzz.py`(신규)**: 2002 쿼리(22×91) round-robin. shape별 pass/fail 통계 출력. exact vs containment 분리.
  - **첫 실행에서 1820/2002 (91%)** — `COUNT(*)`의 leaf 컬럼명이 projection alias로 잘못 들어가는 회귀 발견. `_resolve_leaf`가 leaf의 outer name이 아닌 expression 내부 `exp.Column`/`exp.Star` 참조를 사용하도록 보완(`_column_from_expression`). **수정 후 2002/2002 (100%)** — 22 shape 전체.
  - **`derive_column_lineage(cfg, *, schemas=None)` 옵셔널 schemas 인자**: `{connection: {table: {col: type}}}` 형태(sqlglot `qualify`와 동일). `_initial_mapping` → `extract_sql_lineage(query, schema=...)`로 forward.
  - **`RunExecutor._build_schemas_for_star_queries`**: PipelineConfig의 source 중 `"*" in query`인 것만 골라 `extract_referenced_tables` + `ConnectionInspector.list_columns`로 schema dict 빌드 → `derive_column_lineage(cfg, schemas=...)`에 inject. `InspectionUnsupportedError`(HTTP/Kafka 등) silent skip, per-table 실패는 log + 나머지 진행. best-effort — schema lookup 실패해도 run은 정상 진행(opaque로 떨어질 뿐).
  - **검증**: 코어 unit 715→718(+3 SELECT* w/schema) + 서버 it 430→431(+1 e2e SELECT* sqlite) + 2002 fuzz green. mypy 코어 60 + 서버 100 OK. ruff clean.
  - **사용자 "2천 줄 monster까지 어떤 유형이든 완벽 파싱" 요구 완전 충족** + **카탈로그 column lineage의 마지막 빈틈(SELECT *) 해소**.

- **Static lineage emit — JOIN/CTE/UNION의 모든 base 테이블 자동 input 등록 (Phase X 후속)** [ADR-0044] — ADR-0043(컬럼 리니지)에서 만든 multi-upstream column edge가 실제 catalog 그래프에 row를 만들려면, asset-axis(`derive_lineage`)도 source query의 모든 referenced 테이블을 input asset으로 등록해야 했음. 옛 동작은 `derive_asset_key`의 regex가 잡은 첫 FROM 테이블 하나만 등록 → 두 번째 JOIN 테이블에 대한 column edge는 `_asset_by_key` None → 그래프에 안 보임. 컬럼-axis와 asset-axis 정합성 복원:
  - **`etl_plugins/runtime/sql_lineage.py` `extract_referenced_tables(query)`**: sqlglot AST 전체에서 `exp.Table` 노드 모음, CTE 이름 제외, fully-qualified 형태(`public.orders` / `catalog.db.name`)로 first-seen 순서 반환.
  - **`derive_lineage` 확장**: linear + graph 양 경로에서 source query가 SQL이면 primary key + 모든 referenced 테이블을 `inputs`에 추가, 각 sink로 edge fan-out. 비-SQL source(Mongo collection / Kafka topic / S3 key)는 `extract_referenced_tables`가 `[]` → 영향 없음.
  - **fq 이름 통일**: `_table_of_simple` / `_single_base_table` / `_build_alias_table_map`도 `_table_fq_name(tbl)`(catalog.db.name 또는 db.name) 사용. column leaf와 asset extras가 같은 키 형태 → `public.orders`가 두 row로 갈라지는 함정 회피.
  - **DB 마이그레이션 없음**. 기존 catalog row 안정성 유지(추가만).
  - **테스트 12 신규**: `test_sql_lineage.py`(+6 — simple/JOIN/CTE/UNION/correlated/non-SQL) + `test_asset.py`(+5 — JOIN multi-input / CTE excludes CTEs / schema-qualified dedup / graph source JOIN / non-SQL unchanged) + `test_worker_lifecycle.py`(+1 e2e — sqlite JOIN 실제 실행 → 양쪽 source asset 등록 + multi-upstream column edge 검증).
  - **검증**: 코어 unit 714 + 서버 unit/it 430 green. mypy 코어 60 + 서버 100 OK. ruff clean.

- **SQL 컬럼 리니지 — `sqlglot.lineage` 풀-AST 워커로 전면 교체 (Phase X)** [ADR-0043] — 사용자 요청 *"카탈로그와 데이터 리니지를 더 강화. 2천 줄짜리 복잡 쿼리든, 서브쿼리/WITH절 포함 어떤 유형이든 완벽 파싱"*. 옛 hand-written FROM/JOIN 매처는 1:1 SELECT만 풀고 JOIN/CTE/UNION/서브쿼리는 모두 "sink opaque"로 떨궜음 — 정작 lineage가 가장 필요한 warehouse-to-warehouse 패턴에서 빈 그래프. 전면 재작성:
  - **`etl_plugins/runtime/sql_lineage.py`(신규) `extract_sql_lineage`** — `sqlglot.lineage.lineage()` 트리를 펼쳐 각 출력 컬럼을 `(table, source_col)` leaf 셋으로 정규화. CTE chain / 재귀 CTE / 서브쿼리(FROM·SELECT·correlated) / JOIN(모든 종류, 3-way+) / `UNION [ALL]` / `COALESCE` / `CASE WHEN` / 산술 / 윈도우(PARTITION+ORDER 컬럼 포함) / `LATERAL`(Postgres) 모두 처리.
  - **Placeholder leaf 복구** — correlated/LATERAL 참조가 `Placeholder`로 떨어지면 alias→table 맵으로 `o.amount` → `orders.amount` 후처리.
  - **Aggregate fallback** — `COUNT(*)` 같은 leaf는 `source=Select` + 표 참조 없음 → Select가 단일 base table이면 그쪽에 귀속. 순수 리터럴(`42 AS answer`)은 column/star 없으니 fallback 꺼져 upstream 빈 상태 유지.
  - **`SELECT *` w/ schema** — `sqlglot.optimizer.qualify`로 먼저 확장 후 lineage. schema 없으면 여전히 opaque(SchemaInspector 와이어업은 후속).
  - **`_Mapping` widen** — `dict[str, ColumnRef|None]` → `dict[str, tuple[ColumnRef, ...]]`. `ColumnEdge`는 원래부터 `tuple` 지원이라 **DB 마이그레이션 없음**. 트랜스폼 체인(rename/select/drop/cast/add_constant/filter/dedupe/assert)이 멀티-upstream 그대로 통과.
  - **테스트 32 신규**: `test_sql_lineage.py`(25 — 단순/별칭/함수/3-way JOIN/COALESCE/체인·재귀 CTE/서브쿼리/UNION/윈도우/CASE/LATERAL/SELECT * w·schema 등 + combined monster) + `test_sql_lineage_stress.py`(4 — 200줄 dbt-style 실전 쿼리 + 100-layer CTE 2000줄+ <5s 가드 + 50-branch UNION) + `test_column_lineage.py`(+3 멀티-upstream 케이스).
  - **회귀**: 옛 `test_join_marks_sink_opaque` / `test_complex_expression_keeps_column_drops_upstream` 두 케이스는 새 정확한 결과로 기대값 업데이트(JOIN per-table upstream, `UPPER(b)`→`b` 추적).
  - **검증**: 코어 unit 703 + server unit/it 429 green. mypy 코어 60 + 서버 100 OK. ruff clean.
  - **카탈로그/리니지 정확도 대폭 향상**: 실전 warehouse 쿼리(staging→marts CTE chain, COALESCE JOIN, window 등)가 이제 컬럼 단위로 정확히 그려짐.

- **Audit 데이터-플레인 확장 — Source SELECT 쿼리도 기록 (Phase W)** [ADR-0041 W, Phase U follow-up] — Dogfooding 중 발견: Phase U는 변경 SQL + Python만 audit했지만 사용자 *"각 쿼리"* 요구에는 SELECT도 포함. GDPR 4조 read-trail("누가 PII 읽었나?") 충족:
  - **신규 action `run.sql_read`** — after_json: `node_id` / `kind="source"` / `connection` / `connection_type` / `query`(2000 char trunc) / `query_truncated` / `query_hash` / `records_read`(node-level run 한정, 노출된 행 수 forensic).
  - **SQL connection type 가드** — `_SQL_CONNECTION_TYPES = {"postgres", "mysql", "sqlite"}`. HTTP/Kafka/S3 source의 `query` 필드는 path/topic/prefix 의미라 mislabel 방지. `referenced_connection_names` + `load_connections_by_name`로 type lookup, best-effort.
  - **graph + linear 양 경로** — graph는 `node.type == "source"` + `node.query` 조건, linear는 `task.source.query`.
  - **records_read** — `_node_outcomes`에서 가져옴(node-level 한정). non-node-level은 run row의 total로 충분(중복 저장 X).
  - **웹 ACTIONS 드롭다운**에 `run.sql_read` 추가, data-plane 3종 우선 표시.
  - **검증**: 2 신규 it(source select query 시나리오 real SQLite + SQL connection type 가드). 서버 it 427→429 green. 코어 671 unchanged. mypy 100 src OK, web tsc clean.
  - **사용자 "각 쿼리" 요구사항 완전 충족**: read + write + python 모두 단일 audit timeline.

- **Audit 데이터-플레인 — SQL/Python 실행을 audit_log에 기록 (Phase U)** [ADR-0041 U] — 사용자 요청 *"각 쿼리나 python에서의 로그가 audit 로그에 함께 노출"*. audit_log를 control-plane 전용에서 **data-plane 이벤트도 포함**하는 통합 timeline으로 확장(컴플라이언스/forensic용):
  - **워커 executor `_record_data_operations` helper** — `version.config_json` 파싱 + `_node_outcomes`(SKIPPED 제외) 교차로 sql_exec / transform:sql_exec / transform:python / transform:custom_python 노드마다 audit row 생성. graph + linear 양 경로 처리.
  - **신규 action 2종**:
    * `run.sql_executed` — after_json: `node_id` / `kind` / `connection` / `statement`(2000 char trunc) / `statement_truncated` flag / `statement_hash`(sha256[:16])
    * `run.python_executed` — after_json: `node_id` / `kind` / `module_function` 또는 `first_line`(200 char trunc) / `lines` / `size_bytes` / `code_hash`
  - **actor**: run.triggered_by_user_id (스케줄 실행이면 null). **resource_type**: "run". **resource_id**: run.id.
  - **Best-effort**: try/except + warning log. Audit 기록 실패가 successful run을 fail로 뒤집지 않음.
  - **Server**: `AuditLogRepository.query(action=...)` + `GET /audit?action=` 옵션.
  - **Web**: `auditApi.query`에 action field, audit 페이지에 ACTIONS 드롭다운(`run.sql_executed`/`run.python_executed` 우선 표시 → 흔한 control-plane). 4 신규 i18n 키 en/ko.
  - **레거시 row 영향 0** — schema 변경 없음(JSONB after_json), 옛 audit row 그대로 렌더.
  - **검증**: 3 신규 it(worker 시나리오 sql_exec+custom_python 2 audit row 검증 + audit repo action 필터 + match 0). 서버 it 424→426 green. 코어 671 unchanged. mypy 100 src OK, web tsc clean.
  - **컴플라이언스 강화**: "누가 어제 prod에 어떤 SQL 실행?" / "어떤 Python이 고객 데이터 만졌나?" — 단일 workspace audit timeline에서 답변.

- **Workspace 대시보드 polish — 파이프라인 이름 + 성공률 + Sensors 카드 (Phase T)** [ADR-0041 T] — 워크스페이스 첫 화면 + 일상 모니터링 hub의 정보 밀도 강화. 3가지 핵심 변화:
  - **파이프라인 이름 노출** — Recent/Failing run rows의 UUID-prefix 표시 폐기. `pipelines.list`에서 만든 `pipelineNameById` Map으로 이름을 first-line으로 lead, run id는 muted 보조. 운영자 "어느 파이프라인?" 1초 답.
  - **Runs Today 카드 health 요약** — 기존 "X in batch" → `successRate(%) + inFlight(개)`. `todayStats` single-pass useMemo로 metric 계산. inFlight > 0 우선 표시, 그 다음 successRate (finished 분모로 0÷0 함정 회피). "지금 뭐 돌아가? 건강한가?" 두 질문 한번에.
  - **Sensors 카드 (5번째 stat)** — K3 framework v1 종료 후 시각화 부재 해소. `sensorsApi.list`로 active + paused 수. lg grid 4 → 5 컬럼. `RadarIcon`(sensor 페이지와 동일).
  - **Promise.allSettled 채택** — 한 endpoint 실패가 전 페이지 blank 막음. 패널별 독립 fallback.
  - **i18n**: 3 신규 키 en/ko (`overview.inFlightCount/successRate/pausedSensors`).
  - **검증**: 신규 시각 컴포넌트 0(`StatCard` 재사용). 웹 tsc clean. 서버/코어 변화 0.

- **Runs 목록 — status 필터 + Load more 페이지네이션 (Phase S)** [ADR-0041 S] — 실제 운영 시 run 누적으로 100개 limit이 막힘. 클라이언트 전용 슬라이스(서버는 이미 limit/offset/status 지원):
  - **Status 필터 드롭다운** — 헤더 actions에 `<select>` (All/Pending/Running/Failed/Succeeded/Cancelled, "operator hunt order"). URL sync `?status=<status>` — share link 친화적. 기존 `?pipeline=` 필터와 조합 가능.
  - **Load more 페이지네이션** — offset 대신 limit 증가(매 클릭 +100, 500 cap = 서버 ceiling). 폴링 5s tick이 현재 limit으로 재페치하므로 큰 view도 stays current.
  - **Pagination footer** — "Showing X runs" + Load more 버튼. `maxedOut`(서버가 limit보다 적게 반환) 시 disabled + "End of list" or "Showing the most recent {cap} runs (server cap)".
  - **필터 변경 시 limit 재설정** — pipeline/status 변경 시 limit=100으로 reset(clean 1페이지).
  - **i18n**: 6 신규 키 en/ko (`runs.statusFilterLabel`/`statusFilterAll`/`showing`/`loadMore`/`atCap`/`endOfList`).
  - **검증**: 신규 시각 컴포넌트 0 (`Button` + 평범한 `<select>` 사용 → DESIGN 토큰 안전, Storybook 불요). 웹 tsc clean. 서버/코어 변화 0.

- **DatasetRowCountSensor (Phase R, K3 framework 5번째 빌트인)** [ADR-0041 R] — 데이터 품질 게이트의 destination-side: assertion transform(K1)이 in-flight 검사라면 row_count는 *결과 테이블* 폴링.
  - **`etlx_server/sensors/builtins/dataset_row_count.py`**: `DatasetRowCountSensor`. config `connection_id` (BatchSource 지원 connector 필수) + `table` + `min_rows` / `max_rows` (둘 중 하나 필수) + `where` (optional SQL fragment).
  - **빌드 검증**: min/max 둘 다 None → ConfigError(절대 fire 안 함), min>max → ConfigError, bool int trap 거부, 음수 거부.
  - **쿼리**: `SELECT COUNT(*) AS n FROM <table> [WHERE <where>]` — connector.read() 첫 record 추출(driver별 column case 대응), connect/close 본인 소유, `asyncio.to_thread`로 이벤트 루프 비차단.
  - **Soft-fail 6분기**: missing_context / connection_not_found / secret_resolution_failed / connector_build_failed / wrong_connection_type / query_failed — 모두 metadata에 error_class.
  - **4 결과 분기**: below_min / above_max / in_band / (impossible) — metadata: count + min_rows + max_rows + reason.
  - **웹 `SENSOR_TYPES`**: 5번째 옵션 + sample config hint(`where` 예제 포함).
  - **K3 sensor framework v1 빌트인 5종 완성**: http(core) / asset_freshness / lineage_arrival / file_landed / dataset_row_count — Day-1 prod 운영 핵심 알림 패턴 풀스택.
  - **검증**: 7 신규 it(builder validation 8분기 / missing context / unknown connection + 4 결과 분기 real SQLite + where 좁히기). 서버 it 417→424 green. 코어 671 unchanged. mypy 100 src OK, web tsc clean.

- **빌더 backfill 액션 (Phase Q)** [ADR-0041 Q, ADR-0039 follow-up] — L1 audit Phase 3 잔여. 이전엔 backfill이 pipelines 목록에서만 가능, 빌더에서 편집 중인 사용자는 목록으로 돌아가야 했음. 빌더 헤더에 `HistoryIcon` Backfill 버튼 추가, 기존 `BackfillDialog` 재마운트(코드 중복 0). 동일 disabled 규칙: `!pipeline?.current_version` → "Save first" 툴팁. Unsaved 변경사항은 막지 않음 — 다이얼로그는 *저장된* 버전에 대해 동작하므로 (저장 안 한 편집은 의도적으로 무시). cursor_column 누락은 기존대로 서버 400 + 친화적 toast. 신규 i18n 키 0(`backfill.action` 등 재사용), 신규 컴포넌트 0. 웹 tsc green, 서버/코어 변화 0.

- **실행 취소 (cooperative cancel, Phase P)** [ADR-0041 P] — 사용자 추천 순서 위임. 가장 자주 필요한 운영 액션(잘못 실행한 prod 파이프라인 즉시 중단). 풀스택 한 슬라이스, 워커의 **single writer for status** 원칙 유지:
  - **Alembic 0010** `runs.cancel_requested_at: timestamptz nullable` — 인덱스 불필요(단일-row 조회 only). `RunDetail` DTO에 노출.
  - **REST `POST /workspaces/{ws}/runs/{rid}/cancel`** (Runner+):
    - **PENDING** → 즉시 status=CANCELLED + finished_at flip. 워커 claim 쿼리가 status=PENDING 필터라 race-free.
    - **RUNNING** → cancel_requested_at만 stamp; 워커가 실제 status 전이(single-writer 보존).
    - 터미널 → 409 `RunNotCancellableError`.
    - audit pairing (`run.cancel`).
  - **워커 cooperative cancel** — `heartbeat_loop`에 옵션 `cancel_event: threading.Event` 추가, 매 tick UPDATE-RETURNING + cancel_requested_at SELECT, set 시 Event 신호. `execute_graph_nodes_concurrent`에 `cancel_event` 옵션 → **wave 사이에 체크 + 미실행 노드 SKIPPED + on_node_finish callback fire**(live DAG가 그대로 cancelled 색). In-flight wave는 자연 종료(Python 스레드 preempt 불가, 하드 cancel은 connection 누수 위험).
  - **Executor `_RunCancelled` sentinel** — cancel_event set + no failures → `_record_cancelled`(status=CANCELLED, error_class/message null). 실패가 cancel보다 먼저 land했으면 _record_failure 우선(더 정보 풍부).
  - **Web UI** — `runsApi.cancel`, run 상세 헤더에 `BanIcon` Cancel 버튼(pending/running + cancel_requested_at null일 때만), `ConfirmDialog`(pending/running별 다른 description: 즉시 vs 다음 노드 끝), **Cancelling… chip**(running + cancel_requested_at 있을 때 warning pulse dot).
  - **i18n**: 9 신규 키 en/ko (`runDetail.cancel`/`cancelling`/`cancelRequested`/`cancelledNow`/`cancelFailed`/`cancelConfirmTitle`/`cancelConfirmPendingDesc`/`cancelConfirmRunningDesc`).
  - **v1 한계 명시**: 비-node-level(monolithic to_thread) 경로는 cancel 요청만 기록, 자연 종료 후 반영. 매우 빠른 파이프라인(<10s)은 heartbeat tick 전에 끝나 cancel 못 잡음 — 그땐 자연 succeeded.
  - **검증**: 5 신규 it (REST 3분기: pending 즉시 cancel / running stamp만 / 터미널 409 + worker 2: pre-set cancel_event wave-boundary SKIPPED + pending row 영속). 서버 it 410→417 green. 코어 671 unchanged. mypy 99 src OK, web tsc clean.

- **빌더 auto-layout (Cmd+L, dagre LR, Phase O)** [ADR-0041 O] — L1/L2 audit의 Phase 4 NICE-TO-HAVE 1번 항목. L3 후보 중 가장 작은 surface로 큰 UX 이득 — 양 페르소나 모두 혜택, 클라이언트 전용:
  - **`lib/auto-layout.ts`** `autoLayoutGraph(state) → state` — `@dagrejs/dagre` LR direction(소스 왼쪽 / 싱크 오른쪽, ReactFlow handle 방향과 일치). uniform node 240×80, nodesep 80 / ranksep 110.
  - **데이터 무변형**: `data` / `operatorId` / `edges` untouched — `position`만 변경 → undo가 이전 layout을 정확히 복원. 빈 그래프는 입력 그대로 반환(noop).
  - **헤더 버튼** `LayoutGridIcon` (↶/↷/⌨ 클러스터 옆) + **Cmd+L/Ctrl+L 단축키** + ShortcutsDialog cheat-sheet 새 줄. Editable element guard(input/textarea/contenteditable 안에서는 Cmd+L 가로채지 않음 — 브라우저 native 우선).
  - **`onAutoLayoutRef` 패턴** (Cmd+S와 동일) — 글로벌 listener는 한 번 attach + 함수 identity 변화 무관. `history.commit` 경유 = 단일 undo snapshot.
  - **신규 deps**: `@dagrejs/dagre`. **i18n**: 1 신규 키 en/ko(`shortcuts.autoLayout`).
  - **검증**: 웹 tsc green. 서버/코어 변화 0. 신규 시각 컴포넌트 0(LayoutGridIcon은 lucide-react, 헤더 버튼은 기존 ↶/↷/⌨ 패턴 재사용 — Storybook 불요).

- **실행 기록 — 노드 duration + 실패 노드 표면화 + dry-run 가독성 (Phase N)** [ADR-0041 N] — Phase M(노드별 로그 컬럼) 직후 자연 follow-up. 노드 디버깅을 한 단계 더 풍성하게:
  - **DAG 카드 duration** — `started_at`/`finished_at`으로 `D 3.2s` 계산, 진행 중 노드는 `D 1.4s+`(trailing `+` + info 색) elapsed. 큰 시간은 `1m 12s`/`2h 3m`. `formatNodeDuration` + `humanDuration` helper.
  - **실패 노드 강조** — failed 카드에 두꺼운 error glow ring(`shadow-[0_0_0_2px_error/0.35]`).
  - **실패 시 root cause 표면화** — page-level error 카드 헤더에 `Failed at: source-xxx →` chip(error 색), 클릭 시 로그 필터 + DAG 카드 강조(Phase M ring과 동일 톤). `failedNode` = nodeRuns 중 finished_at 가장 빠른 failed(첫 실패가 보통 root cause). node-level error_message가 run-level과 다르면 separator 아래에 추가 표시(unwrapped error).
  - **Dry-run 패널 보강** — 모든 connector ✓/✗ 명시(이전엔 fail만 나열), 실패 시 monospace `<pre>` + 우상단 copy-to-clipboard 버튼(⧉ → ✓ 1.5s feedback), top-level errors도 동일 banded panel. footer에 "X of Y connector(s) failed" summary. `CopyButton` 헬퍼(Clipboard API 미지원 silent no-op). `max-h-40vh + overflow-y-auto`로 긴 에러 안전.
  - **i18n**: 6 신규 키 en/ko (`common.copy`/`copied` / `runDetail.openFailedNodeTitle`/`nodeError` / `builder.dryRunOk`/`dryRunFailedCount`).
  - **검증**: 웹 tsc clean. 서버/코어 변화 0(클라이언트 전용 슬라이스).

- **실행 기록 — 노드별 로그 컬럼 + 필터 (Phase M, 사용자 요청 "LEVEL 오른쪽에 NODE 정보")** [ADR-0041 M] — node-level execution(ADR-0041 H2/H3) DAG 뷰는 이미 있지만 로그는 run 단위로 섞여서 어느 노드의 출력인지 알 수 없었음. 풀스택 한 슬라이스:
  - **Alembic 0009 + RunLog.node_id 컬럼** — `(run_id, node_id, ts)` 인덱스 추가. leading run_id로 기존 run-wide 쿼리 인덱스 영향 0. 기존 행은 NULL → "run-level"(build/connector setup/summary).
  - **워커 ContextVar bind** — `node_graph.py._run_node_in_thread`가 진입 시 `structlog.contextvars.bind_contextvars(node_id=node.id)` → `merge_contextvars` processor가 모든 event_dict에 자동 inject → `RunRecorder.log_processor`가 추출해 `RunLog.node_id`로 저장(context_json에 중복 0). `asyncio.to_thread` contextvars 전파로 워커 thread + 코어 connector 호출 모두 자동 태깅.
  - **REST `/logs?node_id=<id>`** — 옵션 필터. 특수값 `__run__` → `node_id IS NULL`만(run-level 디버그). `RunLogEntry` DTO에 `node_id: str | None` 추가.
  - **웹 LogView NODE 컬럼** — LEVEL 오른쪽 32-char 너비 클릭 가능 chip(accent + truncate + monospace). 클릭 = 해당 노드 로그만 필터, 같은 노드 재클릭 또는 panel 헤더 `X` chip으로 해제. run-level 로그는 `—` placeholder.
  - **RunDagGraph 클릭 → 필터** — 노드 카드 클릭 시 해당 노드 로그만, 동일 노드에 accent ring(panel chip과 같은 색). nodeFilter 변경 즉시 재페치(2초 polling 안 기다림).
  - **i18n**: 4 신규 키 en/ko (`runDetail.noLogsForNode`/`filterByNodeTitle`/`runLevelLog`/`clearNodeFilter`).
  - **검증**: 2 신규 서버 it(recorder가 ContextVar bind/unbind 사이 로그만 node_id로 저장 + `RunRepository.list_logs` 필터 3분기 all/by-node/`__run__`). 서버 it 408→410 green. 코어 671 unchanged. mypy 99 src OK, ruff/web tsc clean.

- **빌더 UX 후속 5종 (사용자 회신 — L2)** [ADR-0041 L2, ADR-0042 follow-up] — L1 출시 후 사용자가 5개 회신: edge label 배경 / dirty 정확도 / 운영자 설명 i18n / source-sink 강제 해제 / Run SQL standalone. 한 슬라이스로 정리.
  - **Edge 라벨 배경 투명** — graph-canvas `labelBgStyle: {fill: "transparent", fillOpacity: 0}`. "All records" / "if X" 라벨이 그리드 위에 떠 있는 듯한 클린한 인상.
  - **Undo 가능 없으면 dirty=false** — `useGraphHistory.index` 노출 + page `savedGraphIndex` 추적. `dirty = metaDirty || (savedGraphIndex !== history.index)`. Cmd+Z로 saved baseline에 돌아오면 "저장되지 않은 변경사항" 배지 자동 해제. `loadedRef` 제거(savedGraphIndex !== null이 동일 역할).
  - **Operator description i18n (en/ko)** — `getOperatorLabel(spec, t)` + `getOperatorDescription(spec, t)` 헬퍼 신설. 50+ i18n 키(`op.<id>.{label,description}`) en/ko. 4 caller(palette/pipeline-node/properties-panel/graph-editor) 일괄 변환. 팔레트 검색이 영/한 둘 다 매칭. 누락된 operator는 inline label/description 자동 fallback.
  - **Source 시작 / Sink 끝 강제 제거** — 코어 `GraphConfig._check_graph`에서 "≥1 source / ≥1 sink" 검증 제거 + 클라이언트 `validateGraphStructured` 동일 완화. **Standalone source-only 파이프라인 + sink-only 파이프라인도 valid**. 구조적 per-node 규칙(transform/sink는 indegree=1, join은 ≥2)은 유지.
  - **Run SQL을 standalone source로** — `sql_exec`를 **6번째 GRAPH_NODE_TYPE으로 격상**. `GRAPH_NODE_TYPES = {source, transform, sink, join, aggregate, sql_exec}`. 코어 `GraphNode.kind="sql_exec"` + `sql_statement` 필드. `execute_graph_node`가 SqlExecutor capability 체크 후 `execute_statement()` 실행(0 records 반환). 빌더 `_build_graph_task` + 서버 `referenced_connection_names`도 sql_exec connection 인식. 클라이언트 카탈로그에서 `transform:sql_exec`(레거시 pre-load 패턴) 제거 + `source:sql_exec` 신설(kind="source", anyConnection=true). 라벨 "Run SQL (before load)" → "Run SQL", 설명도 "stands alone — no source/sink chain needed"로 갱신. `serializeGraph`가 connectorType==sql_exec일 때 wire type="sql_exec" emit, `deserializeGraph` 역매핑. **레거시 `transform: sql_exec` (pre-load) 경로는 ADR-0035 그대로 유지** → 기존 파이프라인 무중단.
  - **검증**: 6 신규 코어 unit(`test_graph` 3: standalone valid / required fields / no incoming edges + `test_pipeline_graph` 3: execute_graph_node sql_exec 분기 / 단독 실행 end-to-end / SqlExecutor 미구현 connector TaskError). 코어 671 unit + 410 서버 it green, mypy 코어 59 + 서버 99 OK, ruff/web tsc clean.

- **빌더 UX 풀스택 보강 — 두 페르소나(데이터 엔지니어 + 비개발 분석가) 양쪽 만족** [ADR-0041 L1, ADR-0042] — 사용자 요청: "3년차 데이터 엔지니어"와 "비개발 데이터 분석가" 두 시점에서 파이프라인 영역을 전수 감사하고 둘 다 만족하도록 보강. Explore 에이전트가 produce한 풍부한 감사 결과를 9-슬라이스 한 묶음으로 구현. 신규 시각 컴포넌트는 ShortcutsDialog 하나(ConfirmDialog 토큰 재사용), 신규 라이브러리 1개(`use-graph-history`).
  - **신규 모듈**:
    - `lib/use-graph-history.ts` — `useGraphHistory(capacity=100)` + `useGraphHistoryShortcuts()`. 단일 useState 기반 스택, identity short-circuit, capacity overflow 시 front-slice. **Editable element guard** — input/textarea/contenteditable/role=textbox에서 Cmd+Z 가로채지 않음.
    - `components/builder/shortcuts-dialog.tsx` — ? 키로 cheat-sheet 모달. mac/win UA 감지로 ⌘/Ctrl 글리프 자동, 10개 키 매핑.
    - `lib/pipeline-config.ts` `validateGraphStructured()` — `{kind, message, nodeId}` 구조 반환. missing_connection도 감지(source/sink). 기존 string[] API는 backwards compat로 보존.
  - **9가지 개선**:
    1. **Undo/Redo + 글로벌 단축키**: Cmd+Z / Cmd+Shift+Z / Ctrl+Y / Cmd+S(저장) / Cmd+D(duplicate) / ?(help). 헤더에 ↶/↷/⌨ 버튼. 100-snapshot capacity.
    2. **실시간 validation 배너**: edit마다 자동 (저장 시점 X). multi-issue 리스트, 노드 단위 issue는 클릭 → 노드 포커스(`focusRequest: {nodeId, nonce}` 패턴으로 같은 노드 재포커스 허용). 4개+ 접기.
    3. **Edge 라벨 always-visible**: 무조건부="All records"(점선/회색), 조건부="if X"(실선/강조). 분기 기능 발견율 100%.
    4. **노드 incomplete UX 강화**: 노드 카드 summary="Needs: connection, table" (요구되는 필드 명시), properties panel 헤더에 "Next: <field>" 배지 + 빈 required 입력에 빨간 ring.
    5. **다중선택 + bulk delete + duplicate-with-edges**: React Flow native multi-select(Shift/Meta marquee 포함), 단일 commit으로 N-node 삭제도 1 undo 스냅샷. Cmd+D = selection 내부 edge까지 복제 + id remap.
    6. **키보드 단축키 다이얼로그**: 10개 키 매핑(undo/redo/save/duplicate/delete/multiselect/contextmenu/addnode/help/deselect). mac=⌘, win=Ctrl 자동.
    7. **Variables 타입 인지 에디터**: silent JSON fallback 폐기. type select(string/number/boolean/JSON) + 검증 실패 시 명시적 error ("\"abc\" is not a number"). 저장된 변수마다 inferred type 배지.
    8. **Deleted-connection 배너**: load 시 `node.data.connection` 중 카탈로그에 없는 이름 감지 → 빨간 배너 + "Edit →" 으로 첫 affected 노드 포커스.
    9. **Glossary tooltips + Empty-canvas 안내**: 노드/properties panel의 SOURCE/SINK/TRANSFORM 라벨에 점선 underline + title 정의. 노드 0개일 때 중앙에 비-interactive "Drag a Source from the left palette" overlay.
  - **i18n**: 30+ 신규 키 en/ko (`shortcuts.*` 14개 / `glossary.*` 7개 / `graph.edgeAll`/`edgeIf`/`deletedConnectionBanner`/`invalidCount`/`invalidSingle` / `builder.needsFields`/`nextStep`/`nodeIncomplete` 등 / `variables.type`/`typeBadge`/`nameError` / `common.showLess`/`showMore`).
  - **검증**: 웹 tsc clean. 코어/서버 변화 0. 신규 시각 컴포넌트 1개(ShortcutsDialog) — Storybook 별 슬라이스(DESIGN.md 토큰 100% 재사용, 임의값 0). ADR-0042로 두 페르소나 audit + sweet-spot 결정 기록.

- **FileLandedSensor (K3 sensor framework 네 번째 빌트인, external-axis: S3 object polling)** [ADR-0041 K3f] — K3d(asset-axis pull "is stale?")·K3e(asset-axis push "upstream delivered?")는 카탈로그 내부 이벤트만 봤지만, K3f는 **외부 시스템(S3/MinIO)의 이벤트**에 react. Airflow `S3KeySensor` 패턴을 워크스페이스 `Connection` 시크릿 인프라 위에 구현. **이제 sensor 3축 완료**: asset-pull(freshness) / asset-push(arrival) / external(landed).
  - **`etlx_server/sensors/context.py`**: 4번째 ContextVar `sensor_secret_backend` 추가. K3d~K3e의 ContextVar 패턴 자연 확장 — connection-referencing sensor(file_landed, 향후 dataset_row_count)가 워커처럼 `${SECRET:<path>}` 풀이 가능.
  - **CLI `sensor-scheduler run`**: `--secret-backend` + `--secret-file-path` 옵션 추가 (worker CLI와 동일, SECRET_BACKEND env fallback로 API 서버 설정과 자동 매칭). 시크릿 미사용 deployment는 `env`(기본) 그대로.
  - **`etlx_server/sensors/builtins/file_landed.py`**: `FileLandedSensor`. config `connection_id`(필수 UUID, workspace 내 type='s3' Connection 참조) + `prefix`(필수, 빈 문자열 허용) + `pattern`(기본 `*`, fnmatch glob) + `min_size_bytes`(기본 0). Connection lookup → `resolve_placeholders`로 시크릿 풀이 → `S3Connector(**resolved)`로 boto3 client 빌드 → `list_objects_v2` paginator를 `asyncio.to_thread` → basename fnmatch + size + `LastModified > last_triggered_at` 필터링.
  - **Dedupe via `last_triggered_at`** (K3e 패턴 재사용): 같은 파일 landing이 매 tick 반복 트리거되는 함정 방지. 첫 fire(last_triggered_at=None) 시 cutoff 없음 → 기존 파일도 트리거(bootstrap 모드).
  - **Soft-fail 5분기**: missing context / connection not found / wrong type (s3 외) / secret resolution failure / S3 call raised — 모두 metadata에 `error_class` 명시. **Scheduler crash 절대 발생 안 함**.
  - **Hard config error는 build time**: prefix/pattern/min_size_bytes/connection_id UUID 검증 → POST /sensors에서 4xx 명확.
  - **결과 metadata**: matched 객체 최대 50개(`key/size/last_modified`) + `match_count` + `match_count_truncated` 플래그 — 10k+ 객체 버킷에서 result_json 행 폭주 방지하면서 consumer 디버그 즉시 가능.
  - **Web SENSOR_TYPES**: `file_landed` 옵션 + sample config hint(connection UUID placeholder). **4종 빌트인 완성**: http(core) / asset_freshness / lineage_arrival / file_landed.
  - **검증**: 6 신규 it (builder validation 5분기 / missing context soft-fail / unknown connection soft-fail / wrong connection type soft-fail / S3 stub 매칭 happy + min_size/pattern 필터 검증 / dedupe via last_triggered_at — 첫 fire, 두 번째 quiet). 서버 sensor it 23→29 green. 코어 sensor unit 19 회귀 0. mypy 99 src OK, ruff/web tsc clean.
  - **K3 sensor framework v1 종료**: 코어(K3a) + 서비스(K3b) + 웹(K3c) + 4 builtins(K3d/e/f) — Airflow의 "wait-for-the-world-to-be-ready" 패턴이 풀스택 + 3축으로 완성.

- **LineageArrivalSensor (K3 sensor framework 세 번째 빌트인, asset-axis event-driven DAG)** [ADR-0041 K3e] — K3d(asset_freshness, consumer-side pull "is this stale?")의 producer-side dual: **"upstream이 materialise되면 fire"**. Airflow의 ExternalTaskSensor + Dagster의 asset-of-asset 의존성 패턴을 카탈로그 위에서 구현 — consumer pipeline이 스케줄 없이 upstream materialisation 이벤트에 react. 변수→센서→오케스트레이션의 다음 한 발.
  - **`etlx_server/sensors/builtins/lineage_arrival.py`**: `LineageArrivalSensor`. config `upstream_asset_keys: list[str]`(필수 non-empty, 자동 dedupe) + `window_minutes: int`(필수 양수, bool 거부) + `require_all: bool`(기본 true; true=AND(graph join), false=OR(fan-out)).
  - **De-dupe via `last_triggered_at`**: 코어 `use_sensor_context`에 3번째 ContextVar (`sensor_last_triggered_at`) 추가. since 컷오프 = `max(window_threshold, last_triggered_at)` — 같은 materialisation 이벤트가 window 안에 머무는 동안 매 tick 반복 트리거되는 함정 방지 (예: window=60m, last_fire 5m 전 → cutoff 5m 전). scheduler tick + REST `/check` 둘 다 sensor row의 last_triggered_at 전달 → "Check now"가 production tick과 동일 결과.
  - **결과 분류 3-tier**: **arrived**(catalog 내 + since 이후 materialised) / **stale**(catalog 내 but 오래됨) / **missing**(asset_key 카탈로그에 아예 없음). consumer 디버그 시 "어느 upstream이 안 와서 안 fired됐는지" 즉시 식별 — '센서가 quiet하다'에서 멈추지 않고 정확히 누가 책임인지 metadata로 노출.
  - **Web `SENSOR_TYPES`**: `lineage_arrival` 옵션 추가, sample config hint(`{"upstream_asset_keys": [...], "window_minutes": 60, "require_all": true}`)를 JSON 필드 아래 노출. SENSOR_TYPES는 이제 3종(http/asset_freshness/lineage_arrival).
  - **검증**: 6 신규 it(builder validation 6분기 / require_all=true 모두 fresh→trigger / partial→quiet + stale 식별 / require_all=false→OR 트리거 / last_triggered_at dedupe (1st fires, 2nd quiet) / missing vs stale 구분). 서버 sensor it 17→23 green. 코어 sensor unit 19 회귀 0. mypy 98 src, ruff/web tsc clean.
  - **K3 sensor framework 빌트인 카탈로그 3종 완료** — 다른 후보(file_landed via S3, dataset_row_count)는 필요 시 추가.

- **AssetFreshnessSensor (K3 sensor framework 첫 서비스측 빌트인) + 코어 async-bridge** [ADR-0041 K3d] — 그동안 K3 sensor framework는 K3a 코어(ABC + http) + K3b 서비스(persist + tick + REST) + K3c 웹 UI까지 닫혀 있었지만 빌트인 sensor는 `http`(pure network) 하나뿐이었다. 이번 슬라이스는 **카탈로그에 적힌 자산이 stale해지면 fire하는 asset-axis sensor**를 추가 — 코어 sensor API가 서비스 DB 접근을 깔끔하게 수용하도록 확장.
  - **`etl_plugins/core/sensor.py`**: `SensorBase`에 optional `async def check_async()` 추가. 기본 구현은 `asyncio.to_thread(self.check)`로 위임 → 기존 sync 빌트인(HttpSensor) 회귀 0. 서비스측 async 빌트인은 `check_async`만 override. 스케줄러/REST 모두 항상 `await instance.check_async()` 호출 → dispatcher 한 갈래.
  - **`etlx_server/sensors/context.py`** (신규): `sensor_session_factory` + `sensor_workspace_id` ContextVar + `use_sensor_context(session_factory, workspace_id)` async ctx mgr. PEP 567에 따라 await + `asyncio.to_thread` 경계를 가로질러 전파되므로 sync 빌트인도 동일 ContextVar 상태를 본다. 사용처 한 곳, contract 한 곳.
  - **`etlx_server/sensors/builtins/asset_freshness.py`** (신규): `AssetFreshnessSensor`. config `asset_key`(필수 string, 비어있으면 ConfigError) + `max_age_minutes`(필수 양수 int, **bool 거부** — `isinstance(True, int) is True` 함정 방지). check_async 분기 3종: ① **never_materialised**: 해당 asset_key가 카탈로그 미존재 → triggered=True + `reason="never_materialised"`(bootstrap 의도적 트리거, 다운스트림 첫 materialization 유도). ② **stale**: `now - last_materialized_at > max_age_minutes` → triggered=True + `reason="stale"` + `age_minutes/last_materialized_at` metadata. ③ **fresh**: 미트리거 + 동일 metadata. **ADR-0038의 pipeline-axis `freshness_sla_minutes`의 asset-axis dual** — producer pipeline마다 SLA를 박지 않고 consumer 측에서 "이 자산이 30분 안에 fresh해야 한다"고 선언.
  - **SensorScheduler `_run_check` + REST `/check`** 모두 `use_sensor_context` 경유로 통일. "Check now" 버튼 결과가 production tick과 동일.
  - **CLI `sensor-scheduler run` + sensors router**: `import etlx_server.sensors.builtins` side-effect import 추가(http와 동일 패턴). 첫 호출이 어디서 들어와도 dispatch 가능.
  - **웹 `SENSOR_TYPES`**: `asset_freshness` 옵션 추가 + 타입별 sample config hint(`{"asset_key": "postgres://prod/main/users", "max_age_minutes": 30}`)를 JSON 필드 아래 노출 → 운영자가 docs 보지 않고도 shape 인지. i18n `sensors.configExamplePrefix` en/ko 추가, `fieldConfigHelp` 문구를 sample-driven으로 일반화.
  - **검증**: 5 신규 it(builder validation / never-materialised / fresh quiet / scheduler end-to-end stale → PENDING run + sensor_result on result_json / missing-context soft-fail). 코어 sensor unit 19 green(check_async 기본 bridge 검증). 서버 sensor it 17(12→17) green. 웹 tsc clean. **다음 빌트인 후보**: file-landed via S3, lineage-arrival, dataset-row-count.

### Changed
- **변수 UI 개편(사용자 피드백)** [ADR-0041] — ① 전역 변수를 **전용 `Variables` 탭**(`/w/[slug]/variables`)으로 분리(설정 페이지에서 이동). ② 사이드바 **설정(Settings)을 펼침형 그룹**으로 — 클릭 시 하위 메뉴 전개, 그 아래 `일반(General)`·`연결(Connections)`·`멤버(Members)`·`변수(Variables)` 정리(현재 하위 경로면 자동 펼침). ③ 빌더 지역 변수 "추가" 버튼이 좁아 텍스트가 세로로 깨지던 레이아웃 수정(grid + `whitespace-nowrap`, 풀폭 버튼). 웹 tsc 통과. (전역 변수 추가가 안 되면 `./start.sh`로 재시작 — Alembic 0006 적용 + 새 라우터 로드 필요.)

### Added
- **빌더 지역 변수 UI — V2c** [ADR-0041] — 파이프라인 빌더의 PipelineSettingsPanel(노드 미선택 시 우측 패널)에 `VariablesEditor`: 파이프라인 지역 `variables`(name/value 추가·삭제, value JSON 파싱, 실패 시 평문). `PipelineConfigJson.variables` 타입 + `serialize`/`serializeGraph` meta가 비어있지 않을 때 emit + 빌더 load/save/deps 배선(round-trip). 기존 primitive 재사용, i18n en/ko, 웹 tsc 통과. **이로써 변수 기능(V1 코어 + V2a 서버 전역 + V2b 전역 UI + V2c 지역 UI) 완료 — 전역·지역 변수 모두 UI에서 설정 가능.**
- **Sensor 웹 UI — K3c (Phase K3 sensor framework 완료)** [ADR-0041 K3c] — K3a 코어 + K3b 서비스를 비로소 사용자 흐름으로 닫음. 운영자가 SQL/CLI 없이 브라우저에서 sensor CRUD + 즉시 결과 디버그.
  - **`lib/api.ts`**: `sensorsApi`(list/get/create/update/delete/check) + 4 신규 타입(`SensorSummary`/`SensorCreateBody`/`SensorUpdateBody`/`SensorCheckResponse`) — 서버 6 엔드포인트와 정확히 매핑.
  - **`/w/[slug]/sensors` 페이지**: schedules 패턴 미러. list `DataTable`(이름·타입 칩·target pipeline 이름·interval·status active/paused·마지막 check icon[CheckCircle/XCircle]+timestamp+message tooltip·actions[지금확인/편집/삭제]) + create/edit `Card` form(이름/타입 select[v1=http]/**JSON config textarea + inline parse-error**/target pipeline picker/poll_interval input[5~86400]/active toggle). 빈 상태는 `RadarIcon` + 설명. delete는 `ConfirmDialog(destructive)`.
  - **"Check now" per-row 버튼**: `POST .../check` 호출 → `triggered=true`면 toast success(triggered+message), 아니면 toast info("not triggered: msg"). **트리거 run은 enqueue 안 함**(서버 K3b 동일 시맨틱 — manual 디버그 용도). 사용자가 sensor 설정 직후 "잘 되나?" 즉시 확인 가능.
  - **사이드바**: top-level "Sensors" 항목(`RadarIcon`, schedules 직후 — 활성 트리거 패밀리 위계 일관, 수동적 settings 분류와 분리).
  - **i18n**: 50+ 신규 키 en/ko(`nav.sensors`, `sensors.*` 전체 UI 문자열).
  - **신규 시각 컴포넌트 0**: 기존 primitives(Card/DataTable/Input/Button/EmptyState/ConfirmDialog) + 인라인 form만. JsonInput 추출은 별 슬라이스(현 textarea + parse-error로 충분).
  - **orphan 처리**: `target_pipeline_id=null` 센서는 list에서 `no target` 워닝 표시, form에서 명시적으로 "— none (orphaned) —" 옵션 제공해 정책 의도 노출.
  - **검증**: 웹 tsc green. 서버/코어 변화 0.
  - **사용 흐름**:
    1. 사이드바 Sensors → "New sensor"
    2. 이름 `wait-for-upstream`, 타입 http, config `{"url":"https://upstream/healthz","contains":"ready"}`, target=원하는 파이프라인, poll 30s
    3. 저장 후 list의 "지금 확인" 버튼으로 즉시 검증 → toast로 결과
    4. `etlx-server sensor-scheduler run`이 백그라운드 폴링 시작
  - **→ Phase K의 sensor framework 완료**(K3a 코어 + K3b 서비스+REST + K3c 웹 UI). Airflow의 hallmark "wait-for-the-world-to-be-ready" 패턴이 코어→서비스→UI 풀스택으로 구현됨.
  - **남은 K**: 추가 빌트인 sensor(file-landed via S3 / asset-freshness via 카탈로그) / K4 graph 백필 / 운영 강화(샌드박스·로그 redaction·Monaco self-host).
- **Sensor 서비스 영속화 + tick + REST — K3b** [ADR-0041 K3b] — K3a 코어 ABC를 서비스가 비로소 폴링·트리거 가능. 외부 이벤트(HTTP healthz, file landed, asset freshness 등)가 데이터 파이프라인 트리거의 1급 시민이 됨.
  - **Alembic 0008 `sensors` 테이블**: id/workspace_id/name/type/config_json(JSONB)/target_pipeline_id(SET NULL — pipeline 삭제 시 sensor 히스토리 보존 + scheduler skip+log)/poll_interval_seconds(default 60)/is_active/last_check_at/last_triggered_at/last_result_json + UNIQUE(workspace_id, name) + active-only partial index on (last_check_at) WHERE is_active=true(tick 쿼리 비용 감소).
  - **ORM `Sensor`** + **`SensorRepository`** (CRUD + `record_check` for tick + `_UNSET` sentinel으로 PATCH의 omitted-vs-explicit-null 구분 — `target_pipeline_id=null` = orphan 의도, 미지정 = unchanged). 도메인 예외: `SensorNameTakenError` → 409, `UnknownSensorTypeError` → 422.
  - **`SensorScheduler`** tick(K2 패턴 재사용): active 센서 `FOR UPDATE SKIP LOCKED`로 multi-replica 안전(동시 tick 두 replica가 disjoint partition 처리, 한 sensor 중복 fire 0). Python에서 poll_interval gate(5s 플로어로 typo 보호), `asyncio.to_thread`로 sync `check()` 호출(이벤트 루프 비차단). triggered → target pipeline의 current version에 PENDING Run enqueue + `result_json`에 `{triggered_by: "sensor", sensor_id, sensor_name, sensor_result}` stamp → 다운스트림 pipeline이 무엇이 fired인지 즉시 인지. `last_check_at` 매 폴링, `last_triggered_at`만 fire 시 갱신, `last_result_json`은 항상 캐시(operator가 manual re-check 없이 디버그 가능).
  - **CLI `etlx-server sensor-scheduler run`**: scheduler/stream-worker 패턴 미러, `--tick-interval`(기본 5s) + `--log-level` + 시작 시 `etl_plugins.sensors` import로 빌트인 register side-effect.
  - **REST 6 엔드포인트** (`/workspaces/{ws}/sensors` prefix): GET list (Viewer+) / POST create (Editor+) / GET get (Viewer+) / PATCH update (Editor+) / DELETE (Editor+) / POST `/check` (Viewer+, manual one-shot — trigger run enqueue 안 함, "did I configure right?" debug용). create/update 시 `build_sensor`로 즉시 config 검증(invalid → 422 + session rollback, **절대 unrunnable row 안 남김**). 모든 mutation은 audit pairing(`sensor.create`/`sensor.update`/`sensor.delete`).
  - **테스트** (12 신규 it): repo 3(round trip / duplicate name 409 / unknown type 422) + scheduler 4(triggered → 1 PENDING run + `last_triggered_at` 갱신 / quiet check no enqueue / **SKIP LOCKED multi-replica `asyncio.gather` 정확히 1 fire** / poll_interval 내라면 skip) + REST 5(CRUD happy / unknown type 422 / invalid config 422 + DB 비어 있음 검증 / non-member 403 / manual `/check` 결과 반환 + run enqueue 안 함 검증).
  - **검증**: 서버 it 324 green, mypy 94/ruff OK. 코어 회귀 영향 0.
  - **사용 흐름**:
    ```bash
    # 1. CLI: 센서 스케줄러 start
    etlx-server sensor-scheduler run --tick-interval 5

    # 2. REST: 외부 healthz가 200 + body에 "ready" 보이면 ETL 파이프라인 트리거
    POST /workspaces/{ws}/sensors
    {
      "name": "wait-for-upstream",
      "type": "http",
      "config_json": {"url": "https://upstream/healthz", "contains": "ready"},
      "target_pipeline_id": "<pipeline id>",
      "poll_interval_seconds": 30
    }

    # 3. UI에서 "manual check" 버튼: 한 번 폴링해보고 결과 즉시 확인
    POST /workspaces/{ws}/sensors/{sid}/check
    ```
  - **다음**: K3c 웹 UI(sensor CRUD + last check 결과 + manual check 버튼) / 추가 빌트인(file-landed via S3, asset freshness via 카탈로그) / 운영 강화.
- **Sensor framework 코어 — K3a** [ADR-0041 K3a] — 외부 이벤트 트리거의 foundation. Airflow의 hallmark "wait-for-the-world-to-be-ready, then go" 패턴을 코어에 정착. 이번 슬라이스는 ABC + 레지스트리 + 첫 빌트인(http)만 — 서비스 영속화·스케줄링·REST·UI는 K3b/K3c로 분리.
  - **`etl_plugins/core/sensor.py`**: `SensorBase` ABC(`check() -> SensorResult`) + `SensorResult(triggered, message, metadata)` 프로즌 dataclass + `@register_sensor("name")` + `build_sensor(type, cfg)` + `registered_sensor_types()`. transform/connector와 동일 레지스트리 패턴, 외부 패키지 plug-in 가능.
  - **계약 2개**: ① **idempotent** — `check()`는 부작용 0, 폴링마다 같은 답 ② **soft-fail** — 네트워크/타임아웃/조건 미충족 모두 raise 안 하고 `triggered=False + descriptive message`로 반환(Airflow `BaseSensorOperator.poke` 모방). 하드 오류(misconfigured sensor)만 raise.
  - **`etl_plugins/sensors/http.py`** `HttpSensor`: 첫 빌트인, httpx 사용(이미 core dep). config `url/method/expect_status(int|list, default 200)/contains(substring)/headers/timeout_seconds(5)/verify_tls(true)`. 응답 status가 expected 안에 있고 contains가 body에 있으면 triggered. 네트워크 에러/4xx/5xx/missing substring 모두 soft-fail with `error_class/url/status` metadata.
  - **`etl_plugins/sensors/__init__.py`**: import 시 빌트인 register 사이드 이펙트(runtime.transforms 동일).
  - **builder dispatch**: `build_sensor("http", {"url": ...})` — `_coerce_expect_status()`가 int/list/str 모두 수용해 운영자 입력 마찰 최소화.
  - **검증**: 19 신규 unit(core 5: result default / register+build / duplicate / unknown type / sorted types list + http 14: 정상/status mismatch/contains/multi-status/network err/timeout/method+headers wire/builder dispatch/missing url/expect coerce/invalid shape/non-positive timeout/idempotent close). 코어 664+3skip green, mypy 59/ruff OK.
  - **K3 plan 분할**: K3a(코어, 이번) → K3b(서비스: sensors 테이블 + Alembic + SensorScheduler tick with SKIP LOCKED + REST CRUD + 트리거 시 PENDING run enqueue + 추가 빌트인 file-landed/asset-freshness) → K3c(웹 UI: CRUD + 마지막 check + manual check).
- **OpenLineage column-lineage facet — K5b (Phase J + K5 모두 닫힘)** [ADR-0041 K5b] — K5가 자산 단위 OL 송출을 닫았다면, K5b는 J1/J2/J3로 만든 **컬럼 단위 리니지**를 OL `ColumnLineageDatasetFacet`(spec 1.0.2)으로 함께 송출. 이제 Marquez/DataHub/Astro 같은 consumer는 별도 채널 없이도 출력 데이터셋의 매 컬럼이 어느 입력 컬럼에서 왔는지를 표준 facet 한 번에 받음.
  - **`Pipeline.column_lineage` 필드** (`core/pipeline.py`): build 시점 static derivation을 Pipeline 인스턴스에 stash. `None` = 미 derive(수동 생성 Pipeline / 구버전 caller) — emitter는 facet 없이 그냥 table-level만 송출(backward compat).
  - **`build_pipeline` `_attach_column_lineage(pipeline, cfg)` helper** (`runtime/builder.py`): cfg에서 `derive_column_lineage(cfg)` 호출해 attach. **Best-effort** — 미지원 transform shape / unparseable SQL은 silent `None` fallback, build 안 깨짐. linear + task-DAG + graph 모든 경로(`return pipeline, connectors` 두 군데)에서 호출.
  - **`LineageEvent.column_lineage` 필드** (`observability/lineage.py`): optional `ColumnLineage`, 기본 `None`(기존 caller 호환). `Pipeline.run`이 START/COMPLETE에 자동 attach. **FAIL은 미수반** — output 미materialize 상황에서 컬럼 매핑 emit하는 게 misleading.
  - **OL emitter** (`observability/openlineage.py`): 신규 `_column_lineage_facets()` — `column_lineage.edges`를 downstream asset별로 group해 facet 한 개씩 생성. `_dataset_namespace_and_name()` helper로 dataset ref와 inputField ref가 동일 namespace 분리 규칙 공유. **Opaque assets은 facet 미attach** — consumer가 "traced + empty"(facet 있고 fields 비어있음)와 "untraceable"(facet 자체 없음)를 구분 가능. 상수/opaque-expression 컬럼(no upstream)도 fields에 entry로 포함 — 출력 schema 가시성 보존.
  - **Facet shape** (OL spec 1.0.2 정합):
    ```json
    {"facets": {"columnLineage": {
      "_producer": "...", "_schemaURL": ".../ColumnLineageDatasetFacet.json",
      "fields": {
        "id":     {"inputFields": [{"namespace": "prod:wh", "name": "users", "field": "a"}]},
        "city":   {"inputFields": [{"namespace": "prod:wh", "name": "users", "field": "c"}]},
        "tenant": {"inputFields": []}
      }
    }}}
    ```
  - **검증**: 8 신규 unit(OL 6: 정상/메타/None 무효(백워드 호환)/opaque/multi-output 분리/multi-upstream join + builder 2: SQL source attach / python opaque). 코어 645+3skip green, mypy 56/ruff OK.
  - **→ Phase K 의 OpenLineage 묶음 완료**(K5 table-level + K5b column facet). vision의 *Dagster lineage 외부 송출* 측면이 컬럼 단위까지 닫힘.
  - **다음 후보**: K3 sensor framework / K4 graph 백필 / 운영 강화(샌드박스·로그 redaction·Monaco self-host).
- **OpenLineage export — K5** [ADR-0041 K5] — 내부 asset/lineage 카탈로그를 OpenLineage 표준 이벤트로 외부 송출 → Marquez/DataHub/Astro 등 표준 consumer가 곧바로 받음. **ultimate-vision의 "Dagster lineage" 측 마지막 한 발** — 그 동안 lineage가 내부 DB에만 머물렀는데, 이제 표준 프로토콜로 외부 데이터 카탈로그와 통합 가능.
  - **`etl_plugins/observability/openlineage.py`**: `OpenLineageEmitter(LineageEmitter)` + 순수 변환 함수 `build_run_event(event, namespace=...)`. 매핑:
    - `event_type` ↔ `eventType` (START/COMPLETE/FAIL/ABORT 문자열 일치)
    - `run_id` → `run.runId` — UUID는 그대로 통과, 비-UUID(YAML/CLI `"local-run-1"` 같은 합성 id)는 `uuid5(STABLE_NS, run_id)`로 결정론적 매핑 → 같은 string이 항상 같은 OL run, consumer의 UUID 검증기 거치기
    - `AssetKey "conn/target"` → `{namespace: "<configured>:conn", name: "target"}` — connection을 namespace에 prefix해 workspace 분리 보존(같은 table 이름이 다른 connection에 있어도 OL 측에서 구분)
    - `records_read/written` → custom `metrics` runFacet
    - `error` → 표준 `errorMessage` facet (FAIL 이벤트에서 자연스럽게 routing)
    - producer/schemaURL 모두 OL 2.0.2 spec 준수
  - **Best-effort**: HTTP 4xx/5xx, network/DNS/connect/timeout 실패 모두 catch + structlog warning, run에 영향 0. 기존 service-side persistence(`AssetRepository.persist_run_lineage`)의 best-effort 자세와 동일. lineage backend outage가 production run을 죽이는 일 없음.
  - **`Settings`**: `openlineage_url` / `openlineage_namespace` / `openlineage_api_key` 환경변수 추가(`OPENLINEAGE_*`). URL unset = 기능 disabled, 기본 `NoOpLineageEmitter` 유지(내부 카탈로그 영향 0).
  - **`cli.py`**: `_install_openlineage_emitter()` helper가 `worker run` + `stream-worker run` 시작 시 settings 읽어 URL configured면 emitter 설치, 종료 시 close. `set_lineage_emitter`로 process-wide 교체.
  - **테스트** (코어 14 + 서버 4 = 18 신규):
    - 코어: 이벤트 shape pinning(top-level keys / dataset namespace 분리 / metrics facet on/off / error facet / UUID passthrough vs uuid5 coerce) + HTTP `_StubTransport`로 wire bytes 검증 + 네트워크 에러 흡수 + non-2xx 흡수 + bearer auth header + URL trailing-slash 정규화 + 멱등 close
    - 서버: disabled-by-default / env reads / install no-op when unset / install installs when set
  - **검증**: 코어 637+3skip green, 서버 settings 10/10 green, mypy(89 서버 / 56 코어)/ruff OK.
  - **미커버 (K5b follow-up)**: 컬럼 리니지 facet — `LineageEvent`에 `ColumnLineage`가 수반되지 않아 OL `columnLineage` facet으로 ship 안 됨. J1 derive_column_lineage를 emitter 안에서 호출하거나 LineageEvent 확장으로 추가 가능. 내부 카탈로그는 컬럼까지 다 보임(J2/J3).
  - **사용 방법**:
    ```bash
    export OPENLINEAGE_URL=http://marquez:5000
    export OPENLINEAGE_NAMESPACE=prod-warehouse  # optional, default 'etl-plugins'
    export OPENLINEAGE_API_KEY=sk-...            # optional bearer
    etlx-server worker run
    # 모든 run 의 START/COMPLETE/FAIL 이 Marquez 로 POST 됨
    ```
  - **다음 후보**: K5b 컬럼 lineage facet | K3 sensor framework | K4 graph 백필 | 운영 강화(샌드박스·로그 redaction·Monaco self-host).
- **Multi-replica stream worker 락 — K2b** [ADR-0041 K2b] — **Production HA 완성**. K2가 batch scheduler 단계를 안전하게 했다면 K2b는 stream worker 단계까지 닫음. 이제 batch + stream 모두 active-active 다중 replica 가능.
  - **`etlx_server/worker/stream.py` `_start_schedule`**: 두 단계 가드.
    - ① **`FOR UPDATE SKIP LOCKED`로 schedule row 락** — replica A의 `_start_schedule`이 진행 중이면 동시에 들어온 replica B의 select가 즉시 None 반환 → spawn 안 함.
    - ② **post-lock "RUNNING run 이미 존재" 재확인** — A가 commit하고 lock 해제한 직후 들어온 B는 lock 자체는 잡지만, 활성 `RUNNING` Run row(A가 막 insert)를 보고 peer 소유 신호로 해석 → skip. 이게 두 단계 가드가 필요한 이유: 첫 번째는 동시-진입 차단, 두 번째는 순차-진입 차단.
  - **Failover**: 소유 replica process crash 시 RUNNING Run row의 heartbeat 가 stale → `ZombieReaper`(Step 9.3b)가 FAILED로 flip → 그 schedule이 자유로워져 다음 tick에서 살아남은 replica가 자연스럽게 인수. 진정한 HA — single replica failure가 stream pipeline outage 안 일으킴.
  - **모듈 docstring** 갱신: *"single-replica today, multi-replica needs FOR UPDATE SKIP LOCKED"* → *"multi-replica safe"* (in-flight `_inflight` dict는 within-process 중복 방지, 두 단계 lock은 cross-process 중복 방지).
  - **테스트** (2 신규):
    - `test_two_stream_workers_spawn_one_run_for_same_schedule` — 두 `StreamWorker` 인스턴스의 `_start_schedule`을 `asyncio.gather`로 동시 호출, 정확히 한 개의 RUNNING run 검증. 후속 호출도 reject(post-lock 가드).
    - `test_stream_worker_skips_when_peer_holds_running_run` — peer의 RUNNING run을 pre-seed해놓고 다른 worker의 `_start_schedule` 호출 → None 반환 + 신규 row 없음 검증.
  - **검증**: stream worker it 8/8 green(6 기존 + 2 신규), scheduler + worker-lifecycle 회귀 48/48 통과, mypy(89)/ruff OK. **batch + stream 모두 HA로 K2 묶음 완료** — 이제 `etlx-server scheduler run --replicas N`(가상의 운영 구성)으로 active-active 배포 가능. **다음 후보**: K3 sensor framework / K4 graph 백필 / K5 OpenLineage / 운영(샌드박스·로그 redaction·Monaco self-host).
- **Multi-replica scheduler 락 — K2** [ADR-0041 K2] — 스케줄러 SPOF 해소. 그 동안 batch scheduler는 single-replica 가정 — 프로세스가 죽으면 모든 cron firing 멈춤(real outage). 이제 두 replica가 active-active로 안전.
  - **`etlx_server/scheduler/scheduler.py`**: `_load_due_schedules`에 SQLAlchemy `with_for_update(skip_locked=True)` 추가 → 두 replica가 `tick_once()`를 동시 호출해도 schedule row가 한 replica의 트랜잭션에 락걸려 다른 replica는 빈 set을 받음. 락은 caller의 트랜잭션 commit/close 시 해제. 한 tick 내에서 disjoint partition으로 처리되고, 다른 replica의 다음 tick에서 같은 schedule을 재발견하더라도 첫 fire가 이미 cron 베이스를 미래로 밀어뒀으므로 중복 fire 없음(no-catchup 정책 + 베이스 = `MAX(runs.scheduled_at)`).
  - **테스트** (2 신규 it):
    - `test_skip_locked_hides_schedule_from_concurrent_loader` — **deterministic**: A의 txn 보유 중 B의 `_load_due_schedules` 빈 결과 확인, A commit 후 C가 row 재발견. event-loop scheduling 의존 0.
    - `test_two_parallel_ticks_fire_due_schedule_at_most_once` — **e2e**: `asyncio.gather`로 두 `Scheduler` 인스턴스 동시 `tick_once`, 정확히 1 PENDING run 검증.
    - 신규 `isolated_factory` pytest fixture가 outer-transaction `session` fixture를 우회해 testcontainer engine에서 독립 connection 두 개 확보(SKIP LOCKED를 진짜 PG 레벨에서 검증, mock 아님). 테스트는 commit 사용 후 `finally`에서 명시적 cleanup.
  - **미커버**: Freshness ticker(`_tick_freshness`)는 SLA 쿨다운으로 harm bounded라 락 미적용 — 후속 슬라이스에서 분산 락 추가 가능. Stream worker 다중 replica는 in-flight `asyncio.Task` dict + per-process state까지 얽혀 별도 설계(K2b로 deferred — consistent hashing or per-schedule "owner" claim).
  - **검증**: scheduler it 16/16 green(기존 14 + 신규 2), 코어/서버 전체 회귀 영향 0. **다음 후보: K3 sensor framework / K4 graph 백필 / K5 OpenLineage export / stream worker multi-replica(K2b).**
- **빌더 drawer 너비 조절 가능(사용자 요청)** — 사용자가 *"Transform 영역 너비 조절 가능하도록 추가해줘"*. PropertiesPanel(drawer) 너비를 마우스 드래그로 조절 + 사용자 선택 폭을 localStorage에 영속해 다음 세션에도 유지.
  - **`components/builder/properties-panel.tsx`**:
    - drawer 좌측 엣지에 `col-resize` 커서 핸들(`<div role="separator" aria-orientation="vertical">`). 핸들은 1.5px 폭, 평소엔 투명, hover 시 `bg-accent/40`으로 미세하게 강조.
    - `pointer-events`(pointerdown/move/up/cancel) 사용 — 마우스/터치/펜 공통, `setPointerCapture`로 드래그 중 캔버스 벗어나도 추적 유지.
    - **clamp 380~880px** (Monaco 편안한 폭 ~ 캔버스가 너무 좁아지지 않는 한계). 기본 520px(이전 drawer 너비와 동일).
    - 드래그 중 `document.body.style.cursor = "col-resize"` + `userSelect = "none"`으로 글로벌 cursor 일관성 + 텍스트 선택 방지, pointerup 시 복원.
  - **localStorage 영속**: `etlx.builder.drawerWidth` key. `_readStoredWidth()` helper가 SSR/private 모드/quota/NaN 모두 안전 fallback → 어떤 환경에서도 기본값으로 떨어지지 않고 깨지지 않음.
  - **i18n**: `builder.resizeDrawer` 신규 key("Drag to resize the properties panel" / "드래그해서 속성 패널 너비 조절") — aria-label + title.
  - **검증**: 웹 tsc green. PropertiesPanel API 변화 없음(외부 caller는 무영향).
- **빌더 노드 편집 drawer (사용자 제안)** — 사용자가 *"노드를 선택했을 때 모달형태로 띄우는 형태로 바꾸는 건 어떨까?"* 라고 제안. 옵션(full modal / drawer / 현재 + Expand) 중 **drawer**를 선택(권장안). 핵심 통증은 "320×320 PropertiesPanel이 Monaco/columns/mapping/JSON 필드에 너무 좁다"였는데, 그 통증만 외과적으로 해결.
  - **너비**: `w-80` (320px) → `w-[520px]`. ColumnsField 체크리스트·MappingEditor row·FilterEditor 조건·JSON placeholder·**Monaco editor** 모두 즉시 호흡 공간 확보. settings 패널(retry/dlq/variables/triggers)은 노드 비선택 시 그 자리에 그대로(영향 0).
  - **Monaco 높이**: `python-code-editor.tsx` `DEFAULT_HEIGHT` 320 → 480px. drawer 폭 확대와 합쳐 ~25 줄 가시.
  - **닫기 버튼**: drawer 헤더 우상단 × 버튼(`onClose` prop, `XCircleIcon`). `GraphEditor`가 `setSelectedNodeId(null)`을 전달 → drawer 닫히면 settings 패널이 다시 노출. 빈 캔버스 클릭으로도 동일 동작(`onDeselect` 기존 경로).
  - **drawer를 선택한 이유** (vs full modal): ① 캔버스 컨텍스트 유지(그래프 도구의 핵심 가치 — 노드 간 관계 보면서 편집), ② 클릭만으로 다른 노드 즉시 swap(modal은 close→reopen 필요), ③ Airflow/Dagster/n8n 표준 패턴과 일관(학습 부담 0), ④ 면적 통증만 핀포인트로 해결(최소 변경).
  - **데드 코드 정리**: graph-only 전환 잔재였던 linear-mode `transformIndex`/`transformCount`/`onMove` 기반 move-left/right 버튼 제거 + ArrowLeftIcon/ArrowRightIcon import 제거. PropertiesPanel props는 호환성 위해 optional 유지(타입은 그대로).
  - 검증: 웹 tsc green. 슬라이드-인 애니메이션은 `tailwindcss-animate` 플러그인 미설치라 생략(추후 polish).
- **빌더 graph-only 전환 (사용자 요청)** [ADR-0041] — 사용자 명시 요청 *"파이프라인 구성하는 게 그냥 그래프형태로만 되게 해줘"*에 따라 빌더에서 linear 모드 **전면 제거**. 그 동안 빌더는 linear 기본 + "그래프로 전환" 토글 + 별도 GraphEditor 두 갈래로 살아 있었음. graph 캔버스가 Phase G/H/I로 자유 DAG + join/aggregate + custom_python + multi-source를 다 표현하게 되면서 linear는 사실상 표현력이 하위 셋 → UX 분기를 정리.
  - **빌더 진입**: `GraphEditor` 단일 경로. 신규 파이프라인은 `blankGraph()`로 시작. 기존 linear config는 로드 시 `linearToGraph` 자동 적용 → 동일 DAG로 보임(마이그레이션 프롬프트 없음).
  - **serializer**: `serializeGraph`가 retry/dlq 포함 emit(이전엔 graph 모드에서 retry/dlq가 조용히 사라짐), 신규 `extractPipelineMeta(cfg)` helper가 저장 config에서 retry/dlq/variables/auto_materialize/freshness를 일관 추출.
  - **다운스트림 트리거 (call-pipeline ADR-0029)**: 캔버스 노드(`call:pipeline`)가 아니라 `PipelineSettingsPanel`의 새 "Downstream pipelines" 섹션으로 surface. 워크스페이스 내 다른 파이프라인 선택 → 추가/제거. pipeline_triggers API는 그대로 — 저장 시 항상 동기화. 기존 트리거가 있는 파이프라인은 그대로 표시·편집 가능(데이터 손실 없음).
  - **`GraphEditor` slot 확장**: `settingsPanel`(노드/엣지 비선택 시 우측 패널)과 `dryRunPanel`(캔버스 하단) prop 수락. 노드 선택 → PropertiesPanel, 엣지 선택 → branch condition 에디터, 비선택 → settings panel(retry/dlq/variables/triggers).
  - **청산**: `builder-canvas.tsx` 삭제(linear 캔버스, 더 이상 안 씀), `OperatorKind`에서 `call` 제거(`source|transform|sink`만), `callTargets`/`makeCallNode` helper 삭제, `linearToGraph`의 call 필터 제거, `OPERATOR_KIND_ACCENT`/`KIND_ICON`/`KIND_ORDER`에서 call 항목 제거, `pipeline-node.tsx`/`graph-editor.tsx`의 call 분기 제거, templates는 `serializeGraph(linearToGraph(tmpl.build()))` 우회로 graph emit.
  - **i18n**: 5 신규 키(`triggers.title`/`desc`/`pick`/`empty`/`removeAria`) en/ko.
  - **검증**: 웹 tsc green. `serialize()`/`blankBuilder()`/`deserialize()` 함수 자체는 보존(linear → graph 변환 중간단계로 여전히 사용). 코어 변화 0(코어는 항상 graph + linear 둘 다 받음).
- **데이터 품질 assertion operator — K1 (Phase K 시작)** [ADR-0041 K1] — silent bad data 방지를 위한 Day-1 production gate. record가 예상 조건을 만족하는지 매번 검증, 실패 시 run을 명시적으로 fail(default) 하거나 silently drop. Dagster의 asset checks / Great Expectations 같은 데이터 품질 도구의 핵심 기능을 인라인 transform으로.
  - **`etl_plugins.core.exceptions.AssertionFailedError`**: TransformError 서브클래스(워커가 기존 transform error 경로로 처리 → run.error_message에 사람이 읽을 수 있는 메시지 저장).
  - **`register_transform("assert")`**: config `{condition: str, on_fail: "fail"|"drop" (default fail), message?: str}`. condition은 **filter와 동일 환경**(`data`/`metadata` locals, `__builtins__` 차단) — `compile()` 시 ConfigError(syntax), runtime 평가 에러는 TransformError로 wrap, 조건 falsy 시: `fail`이면 `AssertionFailedError(rendered_message + truncated record repr ≤ 200 chars)`로 run 실패, `drop`이면 None 반환(silent filter — bad rows가 가끔 예상되고 run을 죽이고 싶지 않을 때 + 행수 차이로 추적). 잘못된 `on_fail` 값은 ConfigError로 fail-fast.
  - **column lineage**: `assert`를 `{cast, filter, dedupe}`와 함께 mapping pass-through로 처리 — assert는 row-level 결정(통과/실패/드롭)일 뿐 컬럼 셋을 reshape하지 않음. lineage 정확도 유지.
  - **웹**: `transform:assert` operator(ShieldCheckIcon, yellow accent). fields는 기존 primitive 재사용 — condition은 `filter` UX 재사용(no-code 조건 빌더 + advanced raw expr 토글), on_fail은 select(Fail the run / Drop the record), message는 optional string. 신규 시각 컴포넌트 0 → Storybook 불요.
  - **검증**: 9 신규 unit(`test_transforms.py` happy/fail-default/fail-no-message/drop-mode/unknown-on_fail/empty-condition/syntax/builtins-blocked/long-record-truncated) + 1 column-lineage 매핑 보존 = 코어 623 + 3 skip green, mypy(55)/ruff OK. 웹 tsc green.
  - **사용자 워크플로우**: ① 빌더에서 `Assertion` operator drop, ② condition에 `data['amount'] >= 0` 같은 검증식, ③ on_fail 선택, ④ 실행 → 모든 record가 통과면 정상, 위반 record가 나오면 fail이면 run 실패하고 위반 row가 error_message에 명시(silent corruption 방지), drop이면 그 row만 빠지고 카운터 감소로 추적. **다음 후보: K2 multi-replica scheduler 락(HA) / K3 sensor framework / K4 graph 백필 / K5 OpenLineage export.**
- **커스텀 Python operator + Monaco 에디터 — I2 (Phase I 완료)** [ADR-0041 I2] — 사용자가 브라우저에서 인라인 Python 코드를 작성해 transform으로 실행. 기존 `python` operator는 워커 환경에 미리 설치된 `module:function`을 호출 → 셀프 서비스 불가. `custom_python`은 코드를 config에 같이 저장해 self-serve UX 완성.
  - **코어**: `etl_plugins/runtime/transforms.py`에 `custom_python` 등록 + `_compile_custom_python(code)` 단일 실행 seam — `compile()`→ `exec()`→ top-level `transform(record)` 추출. fail-fast 검증(blank code / syntax error / missing entrypoint / non-callable / import-time error 모두 build 시 `ConfigError`), runtime exception은 `TransformError("custom_python: code raised: …")`로 wrap. **보안 모델**: 기존 `python` operator와 동일(임의 in-process 실행) — 위협 모델 불변, ADR-0041 명시. 미래 sandboxing(RestrictedPython/subprocess/gVisor)은 `_compile_custom_python`/`_custom_python` 한 곳에 plug-in.
  - **column lineage**: `derive_column_lineage`는 `custom_python`도 자동 opaque(`_apply_transform`이 미지원 type에 None 반환 → opaque 표시). 사용자 코드는 임의로 컬럼 매핑 가능하므로 정적 추적 포기.
  - **웹**: `@monaco-editor/react` 의존성 추가(Monaco는 CDN lazy load — 첫 번째 `custom_python` 패널 오픈 시에만 다운로드, 초기 빌더 번들 영향 0). `components/builder/python-code-editor.tsx` `PythonCodeEditor`(`next/dynamic` ssr:false lazy, ThemeProvider `data-theme` 변화 추적해 vs-dark/light 자동 전환, minimap off, 12px mono, automaticLayout, 320px) + `PYTHON_CODE_STARTER` skeleton(빈 에디터 대신 runnable `transform(record)` 시드). `lib/operators.ts`에 `pythonCode` FieldDef kind + `transform:custom_python` operator(linear+graph 양쪽, TerminalIcon, accent 황색). `properties-panel.tsx`에 pythonCode 분기 + starter seed.
  - **RBAC + audit**: pipeline 쓰기 API가 이미 Editor+ 게이트 + `pipeline.version_created` audit → 코드 변경이 자동으로 audit log에 기록. 신규 RBAC/audit 코드 0.
  - **검증**: 8 신규 unit(`test_transforms.py` happy/None drop/blank/syntax/missing-entry/non-callable/runtime err/import-time err) + 1 신규 column-lineage opaque 테스트 = 코어 622 + 3 skip green, mypy(55)/ruff OK. 웹 tsc green.
  - **사용자 워크플로우**: ① 빌더에서 `Custom Python (inline)` operator drop, ② Monaco에서 `def transform(record): …` 작성, ③ 저장 → 워커가 dry-run / 실행 시 코드 compile + per-record 호출. **→ Phase I 완료**(I1 자유 캔버스 + join/aggregate 노드 + I2 인라인 Python operator). 다음 후보: cross-worker 분산(claim_ready 확장) / Phase K(graph 백필·OpenLineage·assertion·센서) / 운영 강화(샌드박스 · 로그 보호 · multi-replica scheduler 락).
- **빌더 자유 캔버스 + join/aggregate 노드 — I1 (Phase I 시작)** [ADR-0041 G2/G3/I1] — 코어가 Phase G에서 이미 지원하던 **multi-source + join + aggregate**를 빌더가 비로소 표현. 그 동안 트리 제약이 client/server 양쪽에 박혀 있어 코어가 받는 모든 graph가 단일-source 단일-입력으로 lower돼 있었음 — 이번 슬라이스로 그 격차 해소.
  - **`lib/operators.ts`**: `join`(GitMergeIcon, `multiInput=true`, fields `on:columns + how:select(inner/left/right/outer)`) + `aggregate`(LayersIcon, fields `group_by:columns + aggregations:json` `[{"op":"sum","column":"x","name":"x_sum"},…]`) 신규 operator. `OperatorSpec`에 `multiInput?: boolean`(fan-in 허용 — join 한정) + `graphOnly?: boolean`(linear 모드에서 숨김 — `source→transform*→sink` 형태에 안 맞음) 추가.
  - **`Palette`**: `variant: "linear" | "graph"` prop. linear는 graphOnly operator 자동 필터, graph는 노출. `GraphEditor`가 `variant="graph"` 명시.
  - **`graph-canvas` `connectionAllowed`**: multiInput 노드만 fan-in 허용(`!tgtOp?.multiInput && edges.some(... target)`). 비-join 노드는 indeg≤1 유지(semantics 불모호성).
  - **`pipeline-config` `validateGraph`**: 다중 source 허용(`sources.length>=1`, "exactly one" 제약 제거), join 노드 `indeg<2` 에러("join needs at least two"), 다른 비-source는 `indeg!=1` 에러("use a join node to merge"). 메시지 인간 친화적.
  - **`serializeGraph`**: join/aggregate는 transform wrapper 우회하고 **flat emit** — `{id, type:"join", on, how}` / `{id, type:"aggregate", group_by, aggregations}`. `GraphNodeConfig`의 top-level 필드 (`on`/`how`/`group_by`/`aggregations`)와 wire 정합. `deserializeGraph` 분기 추가로 round-trip 대응.
  - i18n `graph.hint`에 *"Use a Join node to merge two or more inputs"* 안내 추가.
  - 검증: 웹 tsc green, 코어 graph + column-lineage 회귀 33 green. 사용자 워크플로우: ① 2 source 노드 + 1 join + 1 sink 그래프 작성, ② join 노드에 `on=["id"]`, `how="inner"` 설정, ③ 저장 → core G2 머티리얼라이즈 엔진이 hash join 실행. **다음: I2 커스텀 operator + Python IDE(Monaco) + 서버 `custom_python` + RBAC/audit.**
- **웹 컬럼 drill-down — J3 (Phase J 완료)** [ADR-0041 Phase J] — J2 REST를 자산 상세 페이지에서 시각화. `components/assets/column-lineage-graph.tsx` `ColumnLineageGraph`(@xyflow/react) — **2열 레이아웃**: 좌=upstream 컬럼이 source 자산별로 stack(점선 그룹 헤더 + 컬럼 노드 alphabetical, 헤더/노드 클릭 시 해당 자산으로 네비), 우=현재 자산 컬럼(alphabetical), 엣지가 각 downstream 컬럼을 upstream 컬럼(들)에 연결. 상수/opaque 표현식 컬럼은 우측에 입력 엣지 없는 노드로 표시. 자산 상세 페이지(`/w/[slug]/assets/[id]`)에 컬럼 리니지 섹션 추가 — `opaque=true`면 안내 배너(*"이 자산의 컬럼 매핑을 추적할 수 없습니다 — SELECT * / JOIN / python transform 때문일 가능성. source 쿼리에서 컬럼을 명시적으로 나열하면 drill-down 활성화"*), 빈 컬럼이면 *"아직 컬럼 단위 리니지가 기록되지 않았습니다"* fallback. `assetsApi.columnLineage(ws, id)` + `AssetColumnLineageResponse`/`AssetColumnEntry`/`ColumnUpstreamRef` 타입. Storybook 4 스토리(RenameAndConstant/NToOneJoin/AllOpaqueColumns/NoColumns) + i18n en/ko 4 신규 키. 웹 tsc green. **→ Phase J 완료**(코어 파생 J1 + 서비스 영속화·REST J2 + 웹 drill-down J3). 사용자 명시 요구사항 #5 *"테이블별, 컬럼별로도"* 충족. 다음: cross-worker(claim_ready 확장 + persistent staging) / Phase I(빌더 자유 캔버스 + 커스텀op) / Phase K(부가).
- **컬럼 리니지 서비스 영속화 + REST — J2** [ADR-0041 Phase J] — J1 코어 파생을 서비스에서 영속화하고 카탈로그 API로 노출. Alembic 0007: `asset_columns(asset_id, name)` UNIQUE(asset_id, name) + `column_lineage_edges(workspace_id, downstream_column_id, upstream_column_id)` UNIQUE(downstream, upstream) — multi-upstream(JOIN 등)은 n행으로 표현 + 워크스페이스/down/up 인덱스 + `assets.column_lineage_opaque` boolean(`SELECT *`/JOIN/python 등 untraceable 마킹, "derived as opaque" vs "no run yet" 구분). `AssetRepository.persist_run_column_lineage(workspace_id, lineage, output_keys)` — **replace-per-asset 시맨틱**(매 성공 run마다 output 자산의 컬럼·엣지 wipe-then-insert, opaque flag flip): 스키마 변화 자연 반영. `column_lineage_for_asset(asset_id) -> (cols, upstream_map)` — alphabetical 컬럼 + upstream 컬럼·자산 join 단일 쿼리. 워커 `_persist_column_lineage` hook이 `_persist_lineage` 직후 best-effort 호출(파생·영속 실패 모두 run 성공에 영향 0, structured log warning). REST `GET /workspaces/{ws}/assets/{id}/column-lineage` (Viewer+) → `{id, asset_key, opaque, columns:[{name, upstreams:[{asset_id, asset_key, column}]}]}` — 자산 lineage 엔드포인트 시블링, `opaque=true`면 columns는 보통 empty. 9 신규 it (repo 5: create+links / opaque-flag / rerun-replace / opaque→traceable flip / n-to-one join shape + router 3: happy/opaque/non-member-403 + worker e2e 1: 실제 sqlite run 후 컬럼 row 검증). 서버 it 308 + 코어 625 green, mypy(89 서버 / 55 코어)/ruff OK. **다음: J3 웹 컬럼 drill-down.**
- **코어 컬럼 리니지 — J1** [ADR-0041 Phase J] — 출력 컬럼이 어느 source 컬럼에서 왔는지 정적으로 파생. `core/column_lineage.py`(ColumnRef/ColumnEdge/ColumnLineage, config 무의존 leaf) + `runtime/column_lineage.py` `derive_column_lineage(cfg)` **하이브리드 전략**: ① **SQL source 쿼리**→`sqlglot`으로 출력 컬럼+출처 컬럼 추출(별칭·복합표현식 인식, 복합식은 column 유지·upstream 비움), ② **선언적 transform**(rename/select/drop/cast/add_constant/filter/dedupe) 매핑 갱신, ③ **python/sql_exec/SELECT * /JOIN/직접 table 읽기** → 대상 sink를 ``opaque_assets``로 마킹(UI가 "opaque" 표시). 모든 shape 지원(single-task / task-DAG / linear graph; join graph는 v1 opaque). sqlglot>=25 코어 deps 추가. 18 신규 unit + 코어 604 green, mypy(55)/ruff OK. J2(서비스 영속화+API) + J3(웹 drill-down) 후속.
- **Run 상세 DAG 진행 뷰 — H3c (Phase H 완료)** [ADR-0041] — `node_level` 그래프 run의 노드 진행을 웹 Run 상세 페이지에서 라이브 시각화. `components/runs/run-dag-graph.tsx` `RunDagGraph`(@xyflow/react, BFS-depth 레이어 레이아웃, 상태별 카드 색: pending=회색/running=info+pulse/succeeded=success/failed=error/cancelled=dim, pending/running edge는 animated) + `runsApi.nodeRuns(ws, runId)` + `NodeRunEntry` 타입 + run 상세 페이지 폴링에 통합(2s 주기, run terminal까지) + 빈 응답(non-node_level run)은 카드 자동 숨김. Storybook 3 스토리(MidRun/AllSucceeded/PartialFailure) + i18n en/ko. 웹 tsc green. **→ Phase H(노드 레벨 실행 + 진짜 병렬 + 라이브 + DAG 뷰) 완료. 다음은 cross-worker(claim_ready 확장 + persistent staging) 또는 I/J/K.**
- **라이브 node_runs 업데이트 + REST 노출 — H3a/b** [ADR-0041] — `node_level` 그래프 run의 노드 진행을 **실행 중 라이브로 DB에 반영**. 실행 전 `NodeRunRepository.create_for_run`으로 PENDING + deps 사전 insert(fresh session 커밋, UI가 DAG 모양 미리 봄). `execute_graph_nodes_concurrent`에 `on_node_start`/`on_node_finish` 콜백 추가 + `RunExecutor._run_node_level`이 콜백마다 fresh session으로 `set_running` / `set_succeeded(+하류 release)` / `set_failed` / `set_cancelled` 호출 후 즉시 commit → 폴링 UI가 mid-run 진행 관찰. end-of-run `_record_node_runs` 제거(라이브로 대체). `asyncio.Lock`으로 콜백 serialize(prod 무해, test fixture 공유 세션 호환). 새 by-id 메서드(set_running/set_succeeded/set_failed/set_cancelled). REST `GET /workspaces/{ws}/runs/{run_id}/node-runs`(Viewer+, `NodeRunEntry` schema) — 비어있으면 non-node_level run. 라이브 검증(started_at<finished_at, worker_id, attempt) + 병렬 검증(probe `sleep(0.05)`로 to_thread 오버랩 보장) 테스트. 서버 364 green, mypy(89)/ruff OK. H3c(웹 DAG 진행 뷰)는 다음.
- **진짜 병렬 노드 실행 — H2c** [ADR-0041] — opt-in `node_level` graph 파이프라인을 **wave 기반 동시 실행**. `worker/node_graph.execute_graph_nodes_concurrent`(async): 각 노드가 자체 `asyncio.to_thread`에서 **자기 커넥션을 mint+connect+close**(스레드 바운드 드라이버 안전), 같은 wave의 ready 노드들은 `asyncio.gather`로 동시 실행, 상류 실패 시 하류 skip. `RunExecutor._prepare`가 node_level이면 factory 없이 빌드(`connector_factory=None` → `sink.name` plain) + sentinel runner; `execute`가 `_node_level_active` 분기로 concurrent 실행기 직접 호출(to_thread 우회). node_runs 기록은 H2b와 동일(end-of-run). H2b 테스트가 H2c 경로로도 통과(동일 결과). **진짜 병렬 증명 e2e**: 두 독립 sqlite branch가 register_transform `_probe_thread`로 thread.get_ident 기록 → distinct thread ID. 서버 363 green, mypy(89)/ruff OK. 라이브 진행(실행 중 node_runs 갱신) + cross-worker(claim_ready 확장 + staging)는 H3+ 후속.
- **워커 노드 실행 + node_runs 기록 — H2b** [ADR-0041] — opt-in `PipelineConfig.node_level`(graph 한정, 서비스 전용)이 켜지면 워커가 그래프를 **노드 단위로 실행**하고 노드마다 `node_run` 1행 기록. `worker/node_graph.py` `execute_graph_nodes`(토폴로지 순차, in-memory 출력 핸드오프, H2a의 `execute_graph_node`+`apply_edge_predicate` 재사용, 상류 실패 시 하류 skip) — **단일 스레드 실행**(공유 커넥션 스레드 안전). `RunExecutor._prepare`가 node_level+graph면 노드 러너로 분기, `execute`가 outcomes를 node_runs로 기록(succeeded/failed/skipped→CANCELLED, status/카운터/depends_on). 노드 실패→run FAILED + 하류 cancelled. `node_level=False`(기본)는 기존 통짜 경로 유지(무영향). e2e 2 + 서버 worker 25 green, 코어 586, mypy(89)/ruff OK. **진짜 병렬(노드별 커넥션 + 동시 실행 + 라이브 진행)은 H2c** — 스레드 바운드 드라이버 제약 때문에 별 슬라이스.
- **코어 단일 노드 실행기 — H2a** [ADR-0041] — `Pipeline._run_graph_task`의 per-node 연산자 디스패치를 모듈 함수 `execute_graph_node(node, inputs, connectors) -> NodeResult`로 추출(전체-그래프 머티리얼라이즈 엔진 + 미래 노드 단위 실행이 공유) + `apply_edge_predicate(records, when)`(edge `when` 필터 재사용) + `runtime/graph.node_dependencies(graph)`(노드→상류 매핑, node_runs `depends_on` 파생용). `_run_graph_task`는 토폴로지 순회 + `execute_graph_node` 호출로 간결화(동작 보존 — 서버 graph 회귀 통과). 코어 586 unit green, ruff/mypy(53) OK. H2b(워커 노드 실행 결선, v1 in-process 단일 워커)로 이어짐.
- **전역 변수 웹 UI — V2b** [ADR-0041] — 워크스페이스 설정에 변수 관리 UI(`VariablesSection`): 목록 + 추가/편집(upsert)/삭제, name=식별자 검증, value는 JSON 파싱(실패 시 평문), Editor+만 편집(Viewer는 읽기), 삭제는 ConfirmDialog. `variablesApi`(list/set/delete) + `WorkspaceVariableEntry` DTO. 기존 primitive(Card/Input/Button/ConfirmDialog) 재사용으로 신규 시각 컴포넌트 0(Storybook 불요). i18n en/ko. 웹 tsc 통과.
- **워크스페이스 전역 변수 + 빌드 결선 — V2a** [ADR-0041] — `${var.name}` 전역(워크스페이스) 변수. `workspace_variables` 테이블(Alembic 0006, JSONB value, **비밀 아님** — 민감값은 시크릿 backend) + `WorkspaceVariableRepository`(list/get/set upsert/delete/as_dict) + REST `/workspaces/{ws}/variables`(GET Viewer+ / PUT·DELETE Editor+, name 식별자 검증, audit pairing). 워커 `_prepare` + `DryRunService`가 빌드 직전 `resolve_config_variables(config_json, extra=globals)` 적용 — **전역 위에 지역 오버레이(지역 우선)**, 미정의 변수는 빌드 실패/dry-run 에러. 서버 360 + 변수 e2e(전역 tbl→FROM, 지역 threshold→filter가 실행 결과에 반영) green, mypy(88)/ruff OK. 웹 UI는 V2b/V2c, 리니지 경로 변수 해석은 follow-up.
- **파이프라인 지역 변수 `${var.name}` — V1** [ADR-0041] — config-driven 변수 치환. `PipelineConfig.variables`(지역 변수 선언) + `config/variables.py`: `resolve_variables`(전체 문자열 매칭=타입 보존 값, 부분=`str()` 보간) + `resolve_config_variables`(variables 블록을 `extra`(전역, V2) 위에 머지, 지역 override, variables 블록 자체는 미치환). env(`${UPPER}`)·secret(`!secret`/`${SECRET:}`)와 충돌 없는 별도 네임스페이스. `load_pipeline`이 env→secret→variables 순으로 해석. 미정의 변수는 `ConfigError`로 fail-fast. v1 한계: 변수 간 참조 불가. 9 unit, 코어 582 green, ruff/mypy(53) OK. 서비스 전역 변수 + 워커 결선은 V2.
- **노드 레벨 실행 큐 `node_runs` — H1** [ADR-0041] — 노드 단위 병렬 실행의 토대(control plane). 파이프라인 run이 그래프 노드별 `node_run`으로 펼쳐지고, 워커가 ready 노드를 `FOR UPDATE SKIP LOCKED`로 claim(runs 큐 ADR-0021의 노드 레벨 버전). **의존성 카운터** readiness: `pending_deps`=len(depends_on), 노드 succeeded 시 `depends_on @> [node]`(JSONB GIN 인덱스) 하류를 decrement → 0이면 claim 가능. `NodeRun` 모델(run_id/node_id/kind/status/depends_on/pending_deps/output_ref/timing/counters/error/attempt, run_status enum 재사용) + Alembic 0005 + `NodeRunRepository`(create_for_run/claim_ready/mark_succeeded+하류 release/mark_failed/list_for_run). 노드 출력 staging 핸드오프는 `output_ref` 포인터로 두고 H2에서 결정. diamond DAG 흐름 testcontainer 4 + 서버 355 green, mypy(84)/ruff OK.
- **aggregate operator — G3** [ADR-0041] — `aggregate` 그래프 노드: 단일 입력을 `group_by` 키로 그룹핑해 그룹당 1 record 방출(count/sum/min/max/avg). 머티리얼라이즈 엔진을 `Record→Record`에서 collection→collection operator로 일반화(join에 이은 두 번째 stateful operator). config `AggregationConfig`(op/column/name, op 검증 + non-count는 column 필수) + `GraphNodeConfig` group_by/aggregations + 노드 타입에 `aggregate` 추가, core `AggSpec` dataclass + 엔진 `_aggregate`/`_agg_value`, 빌더 aggregate 빌드. group-by/global/avg/count e2e + 검증 테스트. 코어 573 unit green, ruff/mypy(52) OK. (window 함수는 batch 복잡도로 Phase K 보류.)
- **노드 머티리얼라이즈 실행 엔진 — G2** [ADR-0041] — `Pipeline._run_graph_task`를 re-read 방식에서 **머티리얼라이즈**로 교체. 위상 순서(Kahn)로 각 노드 출력을 메모리에 materialize하고 downstream이 소비: source는 한 번만 읽음(per-sink 재읽기 폐기 → 같은 연결 데드락 ADR-0034 자연 소멸), transform=단일입력 매핑, `join`=hash join(N-way fold, inner/left/right/outer), sink=입력 write. edge `when`은 입력 필터. **다중 source + fan-in(join) 실행 지원**. `GraphNode`에 join 종류(join_on/join_how) + 모듈 헬퍼 `_join_records`/`_hash_join`/`_toposort_nodes`. 빌더가 join 노드 빌드 + G1 임시 가드 제거. linear 파이프라인은 별도 경로라 무영향, 기존 graph 동등성 유지. 다중source/join/fan-in/단일읽기 e2e(in-memory) + outer join 헬퍼 테스트. 코어 569 unit green, ruff/mypy(52) OK, 서버 graph 회귀 통과.
- **graph-first 통합 DAG 모델 — G1 (파이프라인 전면 개선 시작)** [ADR-0041] — linear/task-DAG/dataflow-graph 3모델을 단일 `GraphConfig`로 통합하는 첫 슬라이스. 검증/정규화만(실행 변경 0).
  - `GraphConfig` 일반화: 다중 source + `join` 노드(fan-in, indegree≥2) 허용, source(indegree 0)/transform·sink(indegree 1)/join(≥2) 규칙 + 사이클(Kahn) 검출. 기존 단일-source 트리 그래프는 그대로 통과(하위호환). `GraphNodeConfig`에 `join` 타입 + `on`/`how` 필드 + 노드 타입/how 검증.
  - `etl_plugins/runtime/graph.py` 신설: `to_graph(cfg)` — single-task를 `source→transform*→sink+`로 lower(sink `when` 라우팅→edge predicate), graph는 그대로, task-DAG는 Phase H로 deferral(NotImplementedError). `topological_order(graph)`(Kahn). `etl_plugins.runtime`에서 export.
  - 빌더 가드: `_build_graph_task`가 다중source/join 그래프를 받으면 "materialize engine (ADR-0041 G2)"로 fail-fast — 구 re-read 엔진이 조용히 오작동하지 않도록. 기존 트리 그래프 실행은 무변경.
  - 검증: 신규 `tests/unit/runtime/test_graph.py` + `test_pipeline_graph.py` 갱신(다중source/join/사이클/lowering/가드). 코어 564 unit + 3 skip green, ruff/mypy(52) OK, 서버 graph 빌드 회귀 통과.

### Removed
- **Spark 실행 백엔드 + ExecutionBackend 추상화 전면 제거** [ADR-0040, supersedes ADR-0031/0032] — 오케스트레이션 축([[ultimate-vision]]: Airflow + Dagster) 집중을 위해 분산 컴퓨트 경로를 걷어내고 실행 모델을 **로컬 인프로세스 단일 경로**(linear / task-DAG / dataflow graph)로 단순화.
  - 코어: `etl_plugins/runtime/spark/`(backend.py/predicate.py) + `runtime/backends.py`(`ExecutionBackend` ABC·레지스트리·`LocalBackend`·`run_config`) 삭제. `PipelineConfig.engine` 필드 제거 — 단 `extra="forbid"` 호환 위해 `model_validator(mode="before")`로 저장된 config의 legacy `engine` 키를 흘려보냄(값 무시).
  - 서비스: 워커 `engine` 분기·`_run_spark`·`spark_master`·`--spark-master` 제거, `Dockerfile.worker` 삭제, `docker-compose.prod.yml`의 `etlx-spark-worker`(--profile spark) 제거, `[spark]` extra(양 pyproject) + pyspark mypy override 제거.
  - 웹: 빌더 엔진 select·Spark 경고 배너·`isSparkUnsupported`·`Engine` 타입·`engine` emit·목록 Spark 배지·i18n `engine.*` 제거.
  - 미구현이던 P3.4b(외부 클러스터 submit)도 함께 폐기. 재도입 필요 시 새 ADR. 과거 구현은 git 히스토리 + ADR-0031/0032 참조. 테스트: 코어 `test_spark_backend.py`/`test_backends.py` + 서버 spark e2e 삭제.

### Fixed
- **같은 연결 source+sink 데드락 수정** [ADR-0034] — 파이프라인이 한 연결을 읽기·쓰기에 동시에 쓰면(같은 DB 내 테이블 복사 등) 워커가 영구 "running"으로 멈추던 문제. 원인은 연결 이름당 커넥터 1개 → psycopg 연결 하나로 server-side cursor 읽기 + COPY 쓰기를 동시에 잡아 self-deadlock(`futex_wait` / `COPY … ClientRead`). 수정: `build_pipeline(connector_factory=...)`로 sink가 source 연결을 재사용하면 sink 전용 인스턴스(별도 물리 연결)를 `"<name>::sink"` 키로 분리 — 스트리밍/메모리 보장 유지(버퍼링 안 함). linear+graph+fan-out 적용, 워커/YAML 경로에 factory 주입. 검증: 실 Postgres에서 source=sink 동일 연결 read=2/written=2 0.01s 완료(이전엔 무한 멈춤). 코어 +3 unit(536 green).
- **worker/stream-worker CLI가 `SECRET_BACKEND`/`SECRET_BACKEND_FILE_PATH` 환경변수 fallback** — `--secret-backend`/`--secret-file-path` typer 옵션에 `envvar=` 추가. 이전엔 플래그 미지정 시 무조건 `env` 백엔드로 떨어져, API 서버(file 백엔드)와 불일치 → 워커가 시크릿을 "env var not set"으로 못 찾는 문제. 이제 env-only(`SECRET_BACKEND=file`)로 띄워도 서버와 동일 백엔드 사용. (검증: env-only 워커로 run 실행 시 시크릿 정상 해석 확인.)

### Added
- **Asset / Lineage 1급 모델 (v0.2 시작, A1)** [ADR-0024/0036] — 궁극 목표(Airflow 오케스트레이션 + Dagster 리니지 결합)의 asset축 첫 슬라이스.
  - **Derived-first 리니지(zero-config)**: 사용자 선언 없이 source=input asset, sink=output asset, `input→output` 엣지를 config에서 자동 파생.
  - 코어 `etl_plugins/core/asset.py`: `AssetKey`(`conn/target`)·`AssetSpec`·`LineageEdge`·`AssetLineage`·`AssetGraph`(upstream/downstream/ancestors/descendants, cycle-safe). config/runtime 무의존 leaf. top-level export.
  - `etl_plugins/runtime/lineage.py` `derive_lineage(cfg)`: run 없이 정적 파생, single-task/Task-DAG/dataflow graph 모든 shape. **크로스-파이프라인 리니지는 AssetKey 일치로 자동 연결**. backward compatible(기존 Pipeline/Connector 무변경).
  - 검증: +12 unit(키/그래프/파생 — fanout·graph·kafka/s3 포함), 555 green, mypy/ruff OK.
  - **A2 — 런타임 lineage emit**: `etl_plugins/observability/lineage.py`(`LineageEvent`·`LineageEmitter` ABC·`NoOpLineageEmitter` 기본·`CollectingLineageEmitter`·get/set/reset, metrics와 동일 글로벌 패턴). `Pipeline.run`이 START/COMPLETE/FAIL emit(record counts/error 포함), `Pipeline.lineage()`가 Task에서 derived 키 산출(연결 분리 `::sink` 정규화). 기본 NoOp이라 비용 0·의존 0. OpenLineage 와이어 백엔드는 Phase E로 이연. +4 unit(559 green).
  - **A3 — Catalog read API**: `etl_plugins/catalog.py` `Catalog`(`list_assets`/`get_asset`/`lineage(key)`) on `AssetGraph`. `AssetNode`·`AssetLineageView`(direct+transitive). `from_lineages`/`add_spec`/`set_kind`. in-memory(서비스 DB-backed은 Phase B). +5 unit(564 green).
  - **B — 서비스 메타DB 영속화 + 카탈로그 REST**:
    - **B1**: `assets`/`asset_edges`/`asset_materializations` 테이블(Alembic 0004) + ORM + `AssetRepository.persist_run_lineage`(멱등 upsert, output당 materialization, 워크스페이스 스코프, 키 공유로 크로스-파이프라인 자동 연결). +4 it.
    - **B2**: `RunExecutor`가 성공 run 후 `derive_lineage(cfg)`로 assets/edges/materialization 기록(같은 트랜잭션, best-effort — 실패해도 run은 succeeded). +1 it.
    - **B3**: 카탈로그 REST(Viewer+) — `GET /workspaces/{ws}/assets` · `/{id}/lineage`(upstream/downstream) · `/{id}/materializations`. asset은 uuid id로 주소(키의 `/` 회피). +3 it.
  - **C — 웹 카탈로그 + 리니지 그래프**: 사이드바 "카탈로그"(Catalog) 네비 + `/w/[slug]/assets` 목록(키·종류·마지막 생성, 행 클릭→상세) + `/w/[slug]/assets/[id]` 상세(리니지 그래프 + 생성 기록 테이블, run 링크). `LineageGraph`(`@xyflow/react` 재사용 — upstream→current→downstream, 이웃 클릭 시 이동) + Storybook 스토리. `assetsApi` 클라이언트 + i18n(en/ko). 웹 typecheck OK. **(브라우저 수동 QA 미실시.)**
- **Asset-driven 오케스트레이션 — auto-materialize (D1)** [ADR-0037] — 리니지가 오케스트레이션을 구동하는 정점.
  - **D1a 코어**: `derive_asset_key`가 SQL source의 `FROM <table>`을 파싱 → source 키가 sink 키와 일치(`SELECT … FROM orders` ≡ `orders`). 크로스-파이프라인 매칭/리니지 정확도↑. +2 unit.
  - **D1b 서버**: `PipelineConfig.auto_materialize`(opt-in) + 워커 `_trigger_asset_consumers` — run 성공 시 materialize된 output 키를 input으로 갖는 워크스페이스 내 `auto_materialize:true` batch 파이프라인을 자동 PENDING enqueue. `trigger_chain` 사이클 가드·깊이 캡·stream 제외 재사용. +1 it(consumer 트리거 / non-opt-in 제외).
  - **D1c 빌더**: 헤더 "Auto-materialize" 체크박스 → config `auto_materialize` emit/restore. i18n(en/ko). 웹 typecheck OK.
  - 효과: cron 없이 raw→staging→mart가 키 일치로 자동 연쇄(Airflow Dataset / Dagster auto-materialize). 566 코어 unit green.
- **백필 — cursor 범위 재실행 (D3)** [ADR-0039] — 코어의 `Pipeline.run(cursor_from/to)`/`read_since`를 서비스·UI에 노출.
  - **D3a 코어**: `SourceConfig.cursor_column` + `_build_task`가 `Task.cursor_column`으로 전파(누락 수정).
  - **D3b 서버**: `POST .../pipelines/{pid}/backfill`(Runner+, cursor_column 없으면 400) → `result_json.backfill={cursor_from,cursor_to}`로 enqueue. 워커가 읽어 `pipeline.run(cursor_from/to)`로 전달, source가 범위(`cv>from and cv<=to`)만 read_since. local 엔진 한정. +1 worker it(윈도우 읽기) +2 router it.
  - **D3c 웹**: RDBMS source에 "Cursor column" 필드 + 파이프라인 목록 "Backfill" 버튼 + from/to 다이얼로그(`BackfillDialog`) + i18n. typecheck OK.
- **Freshness 기반 auto-materialize (D2)** [ADR-0038] — 시간 기반 센서. `PipelineConfig.freshness_sla_minutes`(opt-in) + 스케줄러 `_tick_freshness` 패스: 출력 asset이 SLA보다 오래되거나(미생성 포함) stale하면 PENDING Run enqueue(`triggered_by=freshness`). in-flight + 쿨다운(최근 시도 SLA 윈도우 내) 가드로 storm 방지. D1과 합성(freshness=root 시간 트리거, auto_materialize=다운스트림 전파). 빌더 헤더 "Freshness (min)" 입력 + i18n. asset의 `kind`도 채워지도록 보강(`AssetLineage.kinds`). +6 it(stale/never/fresh/no-sla/in-flight/cooldown) + +1 unit. 567 코어 unit green. 다음: D3 백필 UI.
- **멱등성 — "Run SQL (before load)" transform** [ADR-0035] — 적재 전에 SQL 한 줄(예: `DELETE`)을 실행해 delete-then-insert로 재실행 시 중복을 없앰.
  - **코어**: 옵셔널 capability `SqlExecutor`(`execute_statement`) — RDBMS 3종 구현. `Task.pre_sql: list[SqlAction]` + `Pipeline._run_pre_sql`이 **읽기 전 1회씩** 실행(레코드 0건이어도 보장). `build_pipeline`이 `type:"sql_exec"` transform을 per-record 체인에서 분리해 `pre_sql`로 적재. `referenced_connection_names`에 sql_exec 연결 포함.
  - **웹**: transform 팔레트에 "Run SQL (before load)"(connection + SQL textarea, `anyConnection`으로 전체 연결 노출). "Database" 카테고리.
  - 검증: sqlite e2e — 같은 파이프라인 2회 실행 후 대상 테이블 중복 없음. 코어 +4 unit, mypy/typecheck OK.
  - **원자적 변형**: RDBMS sink에 **"Pre-write SQL (atomic)"** 필드 — sink의 write 트랜잭션 *안에서* 실행돼 `DELETE + INSERT`가 함께 commit. 중간 실패 시 DELETE까지 rollback(데이터 공백 없음), 빈 입력에도 DELETE 실행. `SinkSpec.pre_sql`/`Task.sink_pre_sql` + RDBMS `write(pre_sql=...)`. MySQL은 DELETE(DML)만 원자적. 검증: rollback/empty-input/atomic-success 3 unit(543 green). 같은 DB·원자 필요 → sink `pre_sql`, 크로스-DB/setup → "Run SQL" transform.
- **커넥터 스키마 인트로스펙션 + 빌더 테이블/컬럼 "딸깍" 피커** [ADR-0033] — 소스/타깃 DB 작업에서 스키마·테이블·컬럼을 타이핑 대신 선택.
  - **코어**: `etl_plugins.core.inspect`의 옵셔널 capability `SchemaInspector`(`@runtime_checkable` Protocol) + `ColumnInfo`. RDBMS 3종(postgres `information_schema`·"schema.table" / mysql `information_schema`+`DATABASE()` / sqlite `sqlite_master`+`PRAGMA`, 식별자 가드)에 `list_tables`/`list_columns` 구현. 인트로스펙션 불가 커넥터(http/kafka/s3/mongo)는 미구현 → `isinstance` 가드로 fallback. 코어 +5 unit, top-level export.
  - **서버**: `GET /workspaces/{ws}/connections/{id}/tables` + `/columns?table=`(Runner+). `ConnectionInspector`가 tester의 시크릿 해석+커넥터 빌드 경로 재사용. unsupported→422 / secret 실패→400 / connect·read 오류→502. +3 it.
  - **웹(빌더)**: sink table/collection 필드 → 선택한 연결의 테이블 datalist 콤보박스(`TableField`). DB 소스 SQL 필드 → "테이블 찾아보기" 디스클로저(`TableBrowser`)로 `SELECT * FROM <table>` 스타터 쿼리 삽입. select/drop/dedupe + Postgres/MySQL upsert key 필드 → 컬럼 체크리스트(`ColumnsField`): transform은 상류 소스 테이블(SELECT…FROM 파싱) 컬럼, sink은 자기 대상 테이블 컬럼을 인트로스펙트. 못 불러오는 컬럼은 chip+free-add fallback. 새 컴포넌트 Storybook 스토리 + i18n(en/ko). 웹 typecheck OK. **(UI는 브라우저 수동 QA 미실시 — 자동 typecheck/스토리까지만 검증.)**
- **빌더: 모드(batch/stream) 시각 구분 + 모드별 커넥터 제한 + 그래프 저장 검증** —
  - 파이프라인 목록에 **Batch/Stream 배지**(+ engine=spark면 Spark 태그). `current_config_json.mode`/`engine` 기반.
  - **모드별 source/sink 제한**: `OperatorSpec.streaming`(Kafka=true) + `operatorAllowedForMode` → stream 파이프라인은 스트리밍 커넥터만, batch는 배치 커넥터만 팔레트에 노출(Palette `mode` prop, 빌더/그래프 에디터 양쪽). transform/orchestration은 양쪽 공통.
  - **그래프 저장 버그 수정**: 저장 422의 실제 원인은 트리/단일-source 제약 위반(orphan 노드·fan-in·다중 source)인데 UI가 사전에 막지 않아 불투명했음. `validateGraph`(서버 GraphConfig 규칙 미러)로 **저장 전 클라이언트 검증** → 명확한 사유 배너 + 저장 차단(422 방지). 그래프는 batch 전용이라 stream 파이프라인은 "그래프로 전환" 비활성.
  - i18n `pipelines.modeBatch/Stream`, `graph.invalid/streamNoGraph`(en/ko). 웹 typecheck OK.
- **ExecutionBackend 추상화 + local 백엔드 (P3.1)** [ADR-0031] — TB급/분산(Spark/Databricks pushdown)을 향한 첫 단계.
  - `etl_plugins/runtime/backends.py`: `ExecutionBackend` ABC + `LocalBackend`(기존 `build_pipeline`+`Pipeline.run` 래핑) + 레지스트리(`register_backend`/`get_backend`) + `run_config` 디스패치. `PipelineConfig.engine`(기본 `"local"`, 런타임에 백엔드 레지스트리로 검증 — config 레이어는 백엔드 목록을 모름). 동작 무변경(기존 실행 경로 그대로). public export.
  - 테스트: 코어 +6 unit. 코어 516 unit green, mypy strict 46 OK, 서버 pipelines round-trip green(`engine` 필드 포함).
- **Spark 백엔드 운영·배포·분산 실행 설계** [ADR-0032] — thin worker가 외부 클러스터/Databricks에 Spark 잡 제출+상태 폴링하는 토폴로지 확정. 제출 모드(local/spark-submit/databricks/k8s), 분산 시크릿(driver 런타임 SecretBackend 재해석), JDBC 파티셔닝, 멱등 sink(스테이징 swap/MERGE), 관측(driver→run_logs/metrics + Spark UI 링크), 이미지/JDBC JAR 운영. `pyproject.toml`에 `[spark]` extra(`pyspark>=3.5`, lazy) 선언.
- **Spark 실행 백엔드 v1 (P3.2a, 실제 검증됨)** [ADR-0031/0032] — DAG를 Spark DataFrame 연산으로 컴파일·실행.
  - `etl_plugins/runtime/spark/`: `predicate.py`(no-code 필터 문법 → Spark SQL 변환, 미지원식은 ValueError) + `backend.py` `SparkBackend`(pyspark lazy import). 단일-task 파이프라인: file IO(parquet/csv/json), 선언적 transform(rename/cast/select/drop/add_constant/dedupe/filter), **fan-out sink은 transformed DF를 `cache()`해서 source를 한 번만 읽고 sink별 `when`→`filter`로 분기** — 로컬 엔진의 재읽기 문제(ADR-0026/0030)가 Spark에선 사라짐. 임의 `python` transform·graph/multi-task·JDBC·upsert·cursor는 거부(후속 슬라이스).
  - **"최소 세팅" 검증**: 시스템 설치 없이 **포터블 JRE(Temurin 17) + `[spark]` extra(pyspark)** 만으로 `local[*]` 실행 확인 — Java/pyspark 없는 환경에선 Spark 실행 테스트가 자동 skip(예측 문법 테스트는 항상 실행).
  - 테스트: predicate 9 + Spark 실행(JVM 환경에서 통과 확인, 무-JVM skip). mypy strict OK, ruff OK.
- **Spark 백엔드 P3.2b(dispatch 통합 + JDBC) + P3.3(graph 분기), 실제 검증됨** [ADR-0031/0032] —
  - **인터페이스 통합**: `ExecutionBackend.run(config, *, connectors=None, connections=None, ...)` — local은 `connectors`, spark는 `connections` 사용. `run_config`/레지스트리도 통일. `engine:"spark"`는 **lazy 등록**(get_backend 첫 호출 시, pyspark import 회피·순환 회피).
  - **JDBC IO + 무설정 드라이버**: postgres/mysql JDBC read/write 구현 + 파티셔닝 옵션. **`spark.jars.packages`로 드라이버 JAR 자동 fetch**(connection 종류 → Maven 좌표 자동 해석) — 사용자가 JAR 수동 배치 안 함(ADR-0032 "최소 설정"). `jdbc_url`/`jdbc_packages` 순수 함수 unit 검증.
  - **P3.3 graph 분기**: `SparkBackend._run_graph` — source를 `cache()`로 1회 읽고, 각 sink는 source→sink 유일 경로(transform 노드 + edge `when` filter)를 Spark로 컴파일. **어느 노드에서나 분기**가 Spark에서 동작(parquet e2e: source→transform→2-way 분기 검증).
  - 검증: Spark 실행 테스트 14개 JVM 환경 전부 통과(linear/필터/fan-out/transform/**graph 분기**/jdbc 헬퍼). 코어 528 unit pass/7 skip, mypy 49 OK, ruff OK. **JDBC 실행 e2e는 DB+maven 필요 — integration-gated(CI에서 검증)**, parquet·graph 경로는 로컬 검증 완료.
- **워커 엔진 라우팅 + Spark 실행(P3.4, 번들/최소설정, 검증됨)** [ADR-0031/0032] —
  - `RunExecutor`가 `PipelineConfig.engine`으로 분기: local=커넥터 빌드+in-process `Pipeline.run`, spark=connection config(시크릿 해석)→`SparkBackend` 실행. `_resolve_connection_configs`로 시크릿 해석 공유, `_prepare`가 엔진별 thread-runner 생성.
  - **번들/최소설정**: `spark_master`(기본 `local[*]`=워커 이미지에 Spark+JRE+드라이버 번들한 단일노드) → `RunExecutor`/`RunWorker`/CLI `--spark-master`로 클러스터 URL 전환. JDBC 드라이버는 `spark.jars.packages` 자동 fetch.
  - **워커 e2e 검증**(JVM gated): pending run → claim → connection 해석 → Spark 실행(filter) → SUCCEEDED(records_read=3/written=2, parquet 출력 검증). 무-JVM에선 skip.
  - 서버 14 worker + spark gated 통과, mypy strict 76 OK, ruff OK.
  - 남은 것: P3.4b 외부 클러스터 잡 submit(thin worker, spark-submit/Databricks) — 실 클러스터 필요.
- **빌더 UI 엔진 선택 (P3.5)** [ADR-0031] — 빌더 헤더에 엔진 select(Local/Spark). `PipelineConfigJson.engine` + `serialize`/`serializeGraph`가 spark일 때 `engine` emit, 로드 시 복원. `isSparkUnsupported`(현재 `python` transform)로 Spark에서 못 도는 노드를 상단 경고 배너로 표시(엔진=spark일 때). i18n `engine.*`(en/ko). 웹 typecheck OK.
- **Spark 워커 배포 이미지 + 문서** [ADR-0031/0032] — `services/etlx-server/Dockerfile.worker`(기존 server 이미지 + OpenJDK17 JRE + `[spark]` extra). pyspark가 Spark jar를 포함해 시스템 의존성은 JRE 하나; JDBC 드라이버는 `spark.jars.packages` 자동 fetch(최소 설정). etlx-server `[project.optional-dependencies] spark`. `docker-compose.prod.yml`에 `etlx-spark-worker`(`--profile spark`, 기본 `local[*]`, `--spark-master`로 클러스터). `DEVELOPMENT.md §10`에 빌드/실행/시크릿/제약/air-gapped 정리. ⚠️ 이미지 빌드는 이 환경에서 미검증(설계/문서).
- **Dataflow DAG — 레코드 단위 그래프 실행 + 어느 노드에서나 분기 (P2.1+P2.2)** [ADR-0030] — "자유 순서 + 모든 작업에서 분기".
  - 코어: `PipelineConfig.graph`(세 번째 상호배타 shape) = `GraphNodeConfig`(source/transform/sink) + `GraphEdgeConfig`(from_node/to_node/`when`). 검증: 단일 source, 비-source 노드 incoming edge 1개(tree, fan-in 미지원), sink≥1, id 유일. `Task.graph_nodes/graph_edges` + `_run_graph_task`(sink별 source 재읽기 + source→sink 유일 경로 재생, edge `when`으로 분기). batch 전용, cursor/stream 미지원. public export `GraphConfig`/`GraphNode`/`GraphEdge`.
  - 서버: `referenced_connection_names`가 graph 노드 순회. config_json 자동 round-trip. 워커가 graph task 그대로 실행(sqlite 분기 e2e 검증).
  - 테스트: 코어 +9 unit(graph 실행/분기/검증), 서버 +2 it(graph round-trip + worker 분기 실행). 코어 510 unit green, mypy strict(코어 45 + 서버 76) OK, 서버 265 db green.
  - 빌더 UI(P2.3, 자유 연결 캔버스) ✅ 구현(⚠️ 브라우저 QA 대기) — `GraphCanvas`(연결 가능 캔버스, 사용자가 엣지 draw, tree 불변식 UI 강제) + `GraphEditor`(Palette + 캔버스 + 노드 속성 + 엣지 `when` 에디터, FilterEditor 재사용) + `pipeline-config`의 graph serialize/deserialize/`linearToGraph`/레이아웃. 에디터 헤더에 "그래프로 전환" 토글(linear→graph 변환), graph config는 자동으로 그래프 모드로 로드. 웹 typecheck OK.
- **파이프라인 fan-out — 1 source → N sinks** [Step 10.4 / ADR-0026] — 동일 추출+변환 결과를 여러 sink로 분기.
  - 코어: `PipelineConfig.sink: SinkConfig | None` + `sinks: list[SinkConfig]` (정확히 하나만 지정하도록 `@model_validator`) + `effective_sinks()`. 런타임 `SinkSpec` dataclass + `Task.sinks` + `Task.effective_sinks()`. `etl_plugins.SinkSpec` public export.
  - 실행 방식: `_run_task`가 **sink별로 source를 재읽기**(버퍼링 안 함 → 무한/대용량 스트림에서도 메모리 bound). `records_read`/`new_cursor`는 첫 패스만 카운트, `cursor_to` 상한 필터는 매 패스 적용, `records_written`은 합산. 단일-sink는 기존 플랫 `Task.sink*` 경로 그대로 — 관측 동작 무변경.
  - stream fan-out은 범위 외: `build_pipeline`이 `mode=="stream" and len(sinks)>1`이면 `ConfigError`, `_arun_stream_task`도 다중 sink면 `TaskError`.
  - 서버: `referenced_connection_names`가 모든 sink를 포함(dry-run/worker 공통). config는 `PipelineConfig.model_validate`/`model_dump`로 자동 round-trip.
  - etlx-web 빌더: sink를 여러 개 추가 가능(source만 dedup·replace), canvas는 spine(source→transforms)에서 sinks가 수직으로 fan-out되는 레이아웃 + branch edge, sink는 ≥2일 때만 삭제 가능, FlowSummary가 분기 그룹 표시. `serialize`/`deserialize`가 `sink`(단수)↔`sinks`(복수) 양방향.
  - 테스트: 코어 +12 unit(fan-out 7 + builder/config 5), 서버 +2 it(fan-out round-trip + sink/sinks 동시 지정 422). 코어 459 unit / 서버 258 db 전부 green, mypy strict(코어 45 + 서버 75) OK, etlx-web typecheck OK.
- **조건부 라우팅 — sink별 `when` 술어 (BranchPythonOperator류)** [Step 10.4 / ADR-0027] — 레코드 값에 따라 후행 sink가 분기.
  - 코어: `SinkConfig.when` + `SinkSpec.when`. filter transform과 동일한 샌드박스 `eval`(`{"__builtins__": {}}`, `data`/`metadata`)로 transform *후* 레코드 평가. `_run_task`에 **first-match(switch)** 라우팅 — 처음 truthy인 conditional sink로만 가고, 매칭 없으면 `when` 없는 default sink(들)로. `when`이 하나도 없으면 순수 fan-out(ADR-0026)으로 동작(하위 호환).
  - `build_pipeline`: `when` 구문을 빌드 시점에 `compile` 검증(dry-run 조기 실패), 단일 sink라도 `when`이 있으면 `sinks[]` 경로 사용, `when`은 connector write 옵션에서 제외. stream + `when`은 `ConfigError`(batch 전용).
  - 서버: 코드 변경 없음 — `when`은 `PipelineConfig` validate/dump로 자동 round-trip, 라우팅은 연결을 추가하지 않음.
  - etlx-web: batch sink(postgres/mysql/sqlite/mongodb/s3)에 "Routing condition" 필드 추가(no-code filter 빌더 `kind:"filter"` 재사용 → 빈 값은 omit). FlowSummary가 조건 있는 sink에 "if <조건>" 힌트. Kafka(stream)는 제외.
  - 테스트: 코어 +10 unit(routing 6 + builder 4). 코어 469 unit green, mypy strict 45 OK, 서버 pipelines/yaml/scenario 39 it green, etlx-web typecheck OK.
- **Task-orchestration DAG — 의존성 + 위상 실행 (Airflow류, P1.1+P1.2)** [Step 10.4-DAG / ADR-0028] — "1:1 선형 말고 Airflow DAG처럼"의 1단계.
  - 코어 P1.1: `Task.depends_on` + `Pipeline._ordered_tasks()` 위상 정렬(Kahn, ready 집합은 원래 리스트 순서로 안정 tie-break) + 사이클/미존재 의존/자기 의존/중복·공백 task 이름 검증(`PipelineError`). `depends_on`이 하나도 없으면 기존 순차 순서 그대로(하위 호환). batch 전용·단일 스레드 순차.
  - 코어 P1.2: `TaskConfig`(name/source/transforms/sink·sinks/depends_on) 신설 + `PipelineConfig.tasks` — 단일-task shape(top-level source/sink)와 상호배타, `effective_tasks()`로 정규화(단일 shape는 1-원소 TaskConfig로 승격). `build_pipeline`이 task별 `_build_task` + 의존성 참조 검증 + 빌드 시점 `_ordered_tasks()`로 사이클 조기 검출. stream 다중-task DAG는 `ConfigError`.
  - 서버: `referenced_connection_names`가 `effective_tasks()`로 모든 task의 source+sink 순회(dry-run/worker 공통). config_json은 `PipelineConfig` validate/dump로 자동 round-trip(`tasks` 포함). 워커는 `Pipeline.run`이 위상 순서로 실행하므로 코드 변경 없이 DAG 실행됨.
  - **DAG가 API/YAML로 표현·실행 가능**(빌더 UI는 P1.4 예정). 분기(skip)/trigger rule은 P1.3 예정.
  - 테스트: 코어 +15 unit(DAG 9 + builder 6), 서버 +1 it(DAG round-trip). 코어 478 unit green, mypy strict(코어 45 + 서버 75) OK, 서버 259 db green.
- **Task-DAG — 분기(skip) + trigger rule + per-task 상태 (P1.3)** [Step 10.4-DAG / ADR-0028 addendum] — BranchPythonOperator류 분기.
  - 코어: `BranchRule(when, to)` + `Task.branch`/`Task.trigger_rule` + `RunResult.task_states`. branch는 task 결과(`records_read`/`records_written`/`success`)에 대한 샌드박스 predicate로 **first-match** 후행 선택(`when=None`=default), 미선택 직접 후행 skip → trigger rule로 후손 전파. `trigger_rule`: all_success/all_done/one_success/none_failed. DAG 실행(`_run_dag`)은 **실패 격리**(독립 task 계속 실행 후 첫 에러 raise) + per-task 상태(success/failed/skipped/upstream_failed) 기록. dep/branch/non-default trigger 없으면 기존 순차 경로 유지(하위 호환). `etl_plugins.BranchRule` export.
  - config: `TaskConfig.trigger_rule`(검증) + `branch: list[BranchRuleConfig]`. builder가 branch predicate 빌드시점 compile 검증 + branch target이 직접 후행인지 검증.
  - 테스트: 코어 +17 unit(branch/trigger/skip 전파/실패 격리 13 + builder 4). 코어 495 unit green, mypy strict(45+75) OK, 서버 pipelines/scenario/worker 31 it green.
  - P1.4 빌더 UI는 drill-down(2단계) 프로토타입을 만들었으나 사용자 피드백으로 폐기 — **단일-캔버스 빌더 유지** + 별도 "call pipeline" operator 방향으로 전환. 코어 task-DAG(depends_on/branch/trigger)는 그대로 유지(가산적·하위 호환).
- **"call pipeline" — 파이프라인 간 호출(fire-and-forget downstream 트리거)** [ADR-0029] — 한 화면 단순 파이프라인 + 파이프라인 호출로 오케스트레이션 조합.
  - 서비스 레벨(코어/서비스 경계 유지 — 코어는 서비스 파이프라인을 모름). 신규 `pipeline_triggers(source_pipeline_id, target_pipeline_id)` 테이블 + Alembic `0003_pipeline_triggers` + `PipelineTriggerRepository`(list/set targets, idempotent 교체).
  - 워커(`RunExecutor`): run 성공 직후 같은 트랜잭션에서 downstream 파이프라인들의 current version으로 PENDING run enqueue(`triggered_by_user_id`/`schedule_id` NULL). `result_json.trigger_chain` lineage로 사이클 차단(target이 chain에 있으면 skip) + `_MAX_TRIGGER_CHAIN=50` 캡 + current version 없으면 skip.
  - API: `GET/PUT /workspaces/{ws}/pipelines/{pid}/triggers`(Viewer+/Editor+, audit `pipeline.triggers_set`). PUT 전체 교체, self-트리거 400 / 워크스페이스 밖 target 404.
  - UI: **캔버스 operator** `call:pipeline`(palette "Orchestration" 카테고리, 보라 accent). 노드가 target 파이프라인을 picker로 참조 → 캔버스에서 spine tail의 terminal로 배치(sink와 함께 fan-out). 로드 시 triggers→call 노드 복원, 저장 시 call 노드의 target들을 `PUT /triggers`로 영속(config_json과 분리). `pipeline` 필드 kind + PropertiesPanel pipeline picker. (이전 `PipelineSettingsPanel` 멀티선택 방식은 operator로 대체.)
  - 테스트: 서버 +4 it(worker downstream enqueue + 사이클 차단 / API set·get + self·missing 거부). 서버 263 db green, mypy strict 76 OK, 웹 typecheck OK. ⚠️ dev DB에 `0003` 마이그레이션 적용 필요(`alembic upgrade head`).
  - **버그 수정**: 빌더 로드 시 `getTriggers`가 `Promise.all`에 묶여 있어, `0003` 미적용 등으로 실패하면 파이프라인 목록까지 비어 "다른 파이프라인 없음"으로 보이던 문제 — triggers 로드를 best-effort로 분리(실패해도 목록/캔버스는 정상 로드).
- **신규 transform operator 4종 + 카테고리 팔레트** [Step 10.x] — Airflow류 빌딩블록 확장.
  - 코어 `runtime/transforms.py`에 `select`(컬럼 유지)/`drop`(컬럼 제거)/`add_constant`(상수 컬럼)/`dedupe`(key_columns 중복 제거, run 내 stateful) 등록. 모두 `build_transform` 디스패치로 워커에서 실행됨. +7 unit(코어 502 green).
  - etlx-web `operators.ts`에 4종 operator 추가 + `operatorCategory()`/`OPERATOR_KIND_GROUPS`(kind→category 그룹). 팔레트(`Palette`)를 **검색창 + kind별 → 카테고리별 접기/펼치기** 로 재작성(Databases/NoSQL/Object storage/Streaming/HTTP·API/Columns/Rows/Code). 검색 시 자동 펼침. i18n `builder.searchOperators`/`builder.noOperatorMatch`.

## [0.1.0] - 2026-05-19

First public release of the core `etl-plugins` library. Closes
ADR-0024's v0.1.0 critical path: Cursor + OTel/Prometheus + contract
suite + mkdocs site.

### Added
- **Prometheus exporter for `configure_otel`** [Step 6.2] — fifth release-blocker on ADR-0024's v0.1.0 critical path closed. Third concrete `Metrics` backend after NoOp and OTLP push. Pull-model deployments now get a `/metrics` scrape endpoint on the worker process without standing up an OTel collector.
  - New `configure_otel(prometheus_port=<int>, prometheus_addr="0.0.0.0")` kwargs. When `prometheus_port` is set, the function attaches a `PrometheusMetricReader` to the MeterProvider and starts an in-process WSGI scrape server via `prometheus_client.start_http_server` on the given port. Returned `OTelHandle` gains `.prometheus_reader` and `.prometheus_server` fields; `handle.shutdown()` calls `server.shutdown()` + `server.server_close()` + `thread.join(timeout=2.0)` so the port releases immediately and rapid-fire test runs don't leak ports.
  - `configure_otel` now accepts any combination of `in_memory=True`, `otlp_endpoint=<url>`, `prometheus_port=<port>` — all three readers can coexist on the same MeterProvider (e.g. OTLP push + Prometheus scrape on the same process). Previously `otlp_endpoint` was effectively required when `in_memory=False`; the function now raises `ValueError` only when **none** of the three is set.
  - Prometheus is metrics-only. Traces still ship via OTLP (when `otlp_endpoint` is set) or InMemory (tests); Prometheus-only deployments still get an `OTelTracer`, but spans are created with no exporter and dropped — same observable behavior as NoOp at the trace surface.
  - Metric names follow OpenMetrics conventions on the scrape side: `etl_plugins.records.read` becomes `etl_plugins_records_read_total` (dot→underscore + the `_total` suffix the exporter appends for monotonic counters).
  - `pyproject.toml [observability]` extra gains `opentelemetry-exporter-prometheus>=0.46b0` (transitively pulls `prometheus_client`); dev deps mirror so `uv run pytest` exercises the new code path.
  - 5 new unit tests via a fresh `otel_prom` fixture that picks a free port via `socket.socket().bind((host, 0))`: scrape endpoint up; counter add → scrape body exposes the series; Prometheus-only (no OTLP, no InMemory) is a valid configuration; Prometheus + InMemory coexist on the same MeterProvider; shutdown closes the WSGI server so the port is reusable immediately.
  - Docs: `docs/guides/observability.md` gains a "Prometheus scrape endpoint" section; `docs/getting-started/install.md` clarifies that `[observability]` ships InMemory + OTLP + Prometheus behind one switch.
  - Cumulative: core unit 465 → 470 (+5). Project total: **983 tests**.
- **Contract suite invariants for connector lifecycle + cursor metadata** [Step 6.3] — fourth release-blocker on ADR-0024's v0.1.0 critical path closed. Five new tests promoted to the shared contract mixins so every batch connector (postgres / mysql / sqlite / mongodb / s3 / InMemory) gets them automatically.
  - `_BatchSourceContract` / `_BatchSinkContract` each gain `test_close_is_idempotent`, `test_close_before_connect_is_noop`, `test_reconnect_after_close`. The reconnect test for sinks deliberately uses an empty second write so PK-uniqueness on real stores can't flake — the contract verifies lifecycle plumbing, sink-specific persistence semantics stay in connector-specific tests.
  - `_BatchSourceCursorContract` gains `test_read_since_stamps_cursor_column_metadata` — every record emitted by `read_since` must carry `metadata["cursor_column"]`. This was already a documented contract in `docs/guides/cursors.md` ("Records emitted by `read_since` should also carry `metadata['cursor_column']`") and four production connectors do it, but it wasn't automated. Now it is — a future connector author who forgets the stamp fails CI.
  - `InMemoryBatchSource.read_since` in `tests/fixtures/connectors.py` was updated to stamp the metadata so the InMemory contract still passes (previously it yielded records unchanged, which silently violated the documented invariant — caught the moment the contract was promoted).
  - Why these specific invariants: the worker (Step 9.3a) reuses a connector instance across runs (open / use / close / open again), and the zombie reaper (9.3b) can race a worker's own `finally: close()` against its own cleanup, double-calling `close()`. Both code paths were previously verified only at the SQLite-specific level (`test_persists_across_connect_close_cycles`); now they're enforced across every batch connector.
  - Cumulative: core unit 435 → 465 (+30), core integration 165 → 192 (+27). All-green project total: **978 tests**.
- **mkdocs-material documentation site** [Step 6.4] — third release-blocker of ADR-0024 v0.1.0 critical path now done. `make docs` (= `uv run mkdocs build --strict`) produces a static site under `./site/`. `make docs-serve` for live-reload.
  - New `mkdocs.yml` — Material theme (pink palette, Inter + JetBrains Mono, system-preference light/dark), `navigation.tabs.sticky` + `navigation.top` + code copy/annotate, `pymdownx.{superfences,highlight,inlinehilite,details,snippets,tabbed}` with Mermaid via `superfences.fence_code_format`, `mkdocstrings` Python handler configured for `sphinx` docstring style + `show_signature_annotations` + `separate_signature`.
  - 9 new pages under `docs/`:
    - `index.md` — overview code example, "What you get", design principles.
    - `getting-started/install.md` — connector / orchestrator / observability / secret-backend extras matrix, `scripts/bootstrap.sh` dev install.
    - `getting-started/quickstart.md` — 5-minute install → connections.yaml → pipeline.yaml → `etlx run` → programmatic alternative.
    - `guides/connectors.md` — `BatchSource` / `BatchSink` / `StreamSource` / `StreamSink` ABC table, built-in catalog table, "adding your own" example with `@ConnectorRegistry.register("my-thing")`, reserved option-name conventions.
    - `guides/pipelines.md` — `Pipeline` / `Task` fluent API, `RunResult` fields, stream mode + `arun_stream`, `RetryConfig` / `DlqConfig`, hook events (`pre_run` / `on_task_start` / `on_task_end` / `on_error` / `post_run`), YAML alternative.
    - `guides/cursors.md` — Step 6.1 walkthrough: full resumable sync example, semantics matrix (exclusive `cursor_from` / inclusive `cursor_to`), state backend table (InMemory / File / DbCursorState), `BatchSource.read_since` four invariants, what this *doesn't* cover yet (lineage / multi-column / stream offsets).
    - `guides/observability.md` — OTLP/gRPC wiring via `configure_otel(...)`, automatic metrics + span emit by the runtime (table of `etl_plugins.records.read` / `…written` / `etl_plugins.errors` / `etl_plugins.duration.seconds` and the `pipeline.run` / `pipeline.task` span attrs), in-memory mode for tests, custom counters/spans, logging.
    - `reference/{core,connectors,observability}.md` — `mkdocstrings` `:::` auto-doc for the public API (Pipeline / Task / RunResult / Record / Schema / 4 ABCs / ConnectorRegistry / Cursor 3 implementations / `max_cursor_value` / `Context` / `Hook` / `TransformFn` / 12 exception classes + 7 connector classes + `Metrics` / `Tracer` / `configure_otel` / `OTelHandle`).
    - `decisions.md` — index of ADR-0001 → ADR-0024 grouped by topic, links to the canonical `DECISIONS.md`.
  - New `make docs` / `make docs-serve` Makefile targets.
  - Debugging gotchas surfaced by `--strict` build: (a) `Pipeline.on` method name must be YAML-quoted (`"on"`) in mkdocstrings `members:` because bare `on` parses as `True`; (b) `Hook` / `TransformFn` live in `etl_plugins.core.pipeline`, not `etl_plugins.core.context` — the build refused to resolve the wrong path.
  - Cumulative: docs-only slice, test counts unchanged at 909 project-wide.
- **DB-backed `CursorState`** [Step 6.1] — closes the cursor abstraction in the service layer so incremental syncs are durable across worker restarts without a sidecar JSON file.
  - New `cursors` metadata table — composite PK `(workspace_id, name)`, `cursor_value: JSONB` (so any `CursorValue` round-trips without a type-discriminator column), `ON DELETE CASCADE` from `workspaces`. Alembic migration `0002_cursors` (`alembic upgrade head` from existing deployments).
  - New `etlx_server.cursors` package:
    - `CursorRepository` — async (asyncpg, same `AsyncSession` lifecycle as the rest of the service). `get` / `list_for_workspace` / `upsert` (via Postgres `INSERT … ON CONFLICT DO UPDATE RETURNING`) / `delete`. Mutations stay in the caller's transaction so a worker can pair the watermark write with its Run row commit.
    - `DbCursorState` — sync, implements the core `etl_plugins.core.cursor.CursorState` ABC. Uses a dedicated **psycopg v3 sync engine** (separate from the FastAPI app's asyncpg engine) — asyncpg pools are bound to their creating event loop, so calling sync code that internally re-enters asyncio fails with "Future attached to a different loop". A sync engine has no such constraint and can be driven from any thread, which matches the production worker pattern where Pipeline.run is pushed onto `asyncio.to_thread`.
    - `DbCursorState.from_url(url, *, workspace_id)` swaps the `+asyncpg` driver suffix for `+psycopg` so callers can reuse the same `DATABASE_URL` env var the FastAPI app uses.
  - `datetime` values are wrapped as ISO-8601 strings before insertion (JSONB stores them as text). The core docstring already promises strict `>` semantics on ISO-8601 — lexicographic order matches chronological order, so resume works on either the wrapped string or the real datetime.
  - 18 new integration tests against a testcontainers Postgres: `test_cursors_repository.py` covers get-missing / insert / update / JSON-able value types / delete / delete-missing / cross-workspace isolation / list ordering / FK cascade on workspace delete. `test_cursors_state.py` exercises the sync API via `asyncio.to_thread` (mirroring the worker), plus a `from_url` builder check.
  - Cumulative: **241 server integration tests (was 223, +18); 909 tests project-wide all green**; mypy strict + ruff + lint-imports green.
- **`read_since` on Postgres / MySQL / MongoDB connectors** [Step 6.1] — the last three production connectors now implement cursored reads, completing ADR-0024's "every batch source can incrementally sync" promise.
  - **`PostgresConnector.read_since`**: wraps the user's SELECT as a subquery + WHERE + ORDER BY using `psycopg.sql.SQL` / `Identifier` so the cursor-column identifier is properly quoted and the cursor value is bound as a server-side parameter. Re-uses the named server-side cursor + `itersize` chunking from the existing `read()` path.
  - **`MySQLConnector.read_since`**: same wrap pattern with `_ident` backtick-quoting + `%s` bind. Streams via `SSDictCursor.fetchmany(chunk_size)` so memory stays bounded for large windows.
  - **`MongoDBConnector.read_since`**: native `coll.find({col: {"$gt": v}}, ...).sort([(col, 1)])`. An optional `options["filter"]` is composed with the cursor filter via `$and` so existing find filters still apply.
  - All three stamp `metadata["cursor_column"]` on every emitted record so downstream transforms / sinks can see which field is the watermark.
  - The three connectors are wired into the `_BatchSourceCursorContract` mixin in integration tests (`TestPostgresCursorReads`, `TestMySQLCursorReads`, `TestMongoCursorReads`), so the same four invariants the SQLite path passes (none returns all+ordered / midrange strict / max returns empty / two-batch resume idempotent) now run against real Postgres / MySQL / Mongo containers. `_StripIdConnector` in the Mongo tests also overrides `read_since` so the contract's payload comparison works against ObjectId-free records.
  - Restored `read_kwargs` / `write_kwargs` fixtures on `TestPostgresRoundTrip` that an earlier cursor slice accidentally truncated.
  - Cumulative: 165 integration tests (was 153, +12 cursor contract); core unit count unchanged at 435 + 3 skipped.
- **`Pipeline.run` + `_run_task` span emit** [Step 6.2 follow-up] — the batch runtime now opens an OTel `pipeline.run` span with one nested `pipeline.task` per task, automatically populated with the standard attrs. NoOp tracer stays the default, so callers who don't `configure_otel()` see no behavior change.
  - `pipeline.run` attrs: `pipeline`, `mode`, `run_id`, `success`, `records_read_total`, `records_written_total`, `duration_seconds`. Cursored runs also stamp `cursor_from` and `cursor_to` as strings (the OTel attribute API takes str/int/float/bool, and `CursorValue` is a heterogeneous union).
  - `pipeline.task` attrs: `pipeline`, `task`, `source`, `sink`, `records_read`, `records_written`. The task span is started + ended around the `task_runner` call so a retry-wrapped runner still emits one logical span per task, not one per retry attempt.
  - Exceptions on either span are recorded as events (record_exception); the `success` attr on `pipeline.run` flips to False when the loop unwinds.
  - 4 new tests added to `test_otel.py`: full run + task spans with attrs; transform error attaches exception event on both spans and flips success; cursored run stamps cursor_from / cursor_to; NoOp tracer path (no `configure_otel` call) doesn't crash.
- **OpenTelemetry observability adapters** [Step 6.2] — second concrete `Metrics` + `Tracer` backend after NoOp.
  - `etl_plugins/observability/otel.py` ships `OTelCounter` / `OTelHistogram` / `OTelMetrics` (caches instruments by name so repeated `counter("etlx.records.read")` doesn't leak SDK identifiers) + `OTelSpan` / `OTelTracer`. The SDK is **lazy-imported** inside `configure_otel(...)` so a plain `pip install etl-plugins` (no `[observability]` extra) never pulls OpenTelemetry.
  - `configure_otel(*, service_name, otlp_endpoint=None, in_memory=False, resource_attributes=None) -> OTelHandle` swaps the global metrics + tracer backends. Production path: OTLP/gRPC exporter to a collector. Test path: `in_memory=True` wires `InMemoryMetricReader` + `InMemorySpanExporter` so assertions can introspect emissions without standing up a collector. `OTelHandle.shutdown()` flushes + tears down both providers cleanly.
  - `[project.optional-dependencies] observability` is now `["opentelemetry-sdk>=1.25", "opentelemetry-exporter-otlp-proto-grpc>=1.25"]` (Step 1.6 placeholder unblocked). Added to dev group so the test suite always sees the SDK; mypy override for `opentelemetry, opentelemetry.*` keeps strict mode green.
  - 14 new unit tests via `tests/unit/observability/test_otel.py` (per-test InMemory backend fixture, NoOp reset on teardown). Cover backend swap on `configure_otel`, endpoint-required-when-not-in-memory, reset restores NoOp, counter add → reader sees correct sum + instrument caching + no-attribute path, histogram record → matching count+sum + caching, span lifecycle through context manager → exporter sees attributes, exception inside `with` attaches event, and `isinstance` checks on the three wrapper classes.
  - Cumulative: 431 core unit + 3 skipped, 875 tests project-wide all green.
- **Pipeline.run(cursor_from, cursor_to) backfill helper** [Step 6.1b] — the cursor abstraction is now drivable end-to-end from the runtime.
  - `Task` gains a `cursor_column` field (also reachable via `Task.extract(..., cursor_column="ts")`). `Pipeline.run` gains `cursor_from` (exclusive lower bound) and `cursor_to` (inclusive upper bound); cursored runs require `cursor_column` on every task, raising `TaskError` otherwise. `_run_task` routes through `source.read_since(...)` when a cursor is in play, applies the `cursor_to` cap at iteration time (early-returning since `read_since` is ascending), and threads the per-task max back up.
  - `RunResult.new_cursor: CursorValue = None` carries the overall max cursor value seen across all tasks, so callers can persist it for the next resume via `CursorState.update(...)`.
  - `InMemoryBatchSource` gains a `read_since` implementation matching the contract (ascending order, strict `>`).
  - 8 new pipeline tests: `cursor_column` captured on `Task.extract`, backwards-compat path without cursor params, `cursor_from` strict filter, `cursor_to` inclusive cap, full window, missing `cursor_column` raises `TaskError`, two-batch resume is idempotent (no overlap), multi-task max-tracking.
- **SQLite read_since + cursor contract test mixin** [Step 6.1a] — first concrete `BatchSource.read_since` implementation.
  - `etl_plugins/connectors/rdbms/sqlite.py`: overrides `read_since(cursor_column, cursor_value, *, query, chunk_size, **options)`. The user's SELECT is wrapped as `SELECT * FROM (<query>) WHERE <cursor_column> > ? ORDER BY <cursor_column>` with `cursor_value` bound as a parameter — injection-safe by construction. `cursor_value=None` drops the WHERE clause and returns everything ordered ascending. Records carry `metadata["cursor_column"]` so downstream steps can see which field is the watermark.
  - `tests/contracts/cursor.py`: new `_BatchSourceCursorContract` mixin verifying the four ADR-0024 invariants — `cursor_value=None` returns everything ordered, midrange filters strictly, max returns empty, two-batch resume is idempotent.
  - `tests/unit/connectors/rdbms/test_sqlite.py`: `TestSQLiteCursorReads` wires the contract + 5 sqlite-specific tests (requires query, raises when not connected, metadata stamped, wraps a complex JOIN inner query, parameter binding defeats `1 OR 1=1`).
- **Cursor / watermark abstraction foundation** [Step 6.1] — first chunk of ADR-0024's v0.1.0 critical path lands.
  - New `etl_plugins/core/cursor.py` with `Cursor` (frozen Pydantic BaseModel, `column: str` + `value: CursorValue`), `CursorState` ABC (`get`/`set`/`delete` + `update` shorthand), and two ready-to-use implementations:
    - `InMemoryCursorState` (dict-backed; tests + single-shot pipelines).
    - `FileCursorState` (JSON file; atomic via `tempfile.mkstemp` → write+fsync → `os.replace`, so a crash mid-write leaves the prior state intact. `datetime` values serialize as ISO-8601 strings; corrupt JSON is treated as empty so reads never explode).
  - `CursorValue = int | float | str | bool | datetime | None` keeps cursor values JSON-round-trippable end-to-end — the union deliberately excludes dict/list because watermarks should be primitive.
  - `max_cursor_value(records, column, *, current=None)` helper folds a fresh batch's high-water mark into the prior cursor — the typical "I just processed N records, what's my new watermark?" call site.
  - `BatchSource.read_since(cursor_column, cursor_value, *, query, chunk_size, **options)` is now part of the ABC. The default implementation raises `NotImplementedError("<class> does not support cursored reads (read_since). ...")` so existing connectors keep working (`read()` is unchanged) and any caller that asks for cursored reads on a non-supporting source gets a clear error. Docstring nails down the contract: strict `>` semantics (equal values are assumed already processed), ascending order so callers can fold progressively.
  - `etl_plugins.core` + top-level `etl_plugins` re-export the new names so `from etl_plugins import Cursor, FileCursorState, max_cursor_value` works.
  - 26 new unit tests cover Cursor model (validation, frozen, type union), `max_cursor_value` (largest / fold-in / missing column / empty / None / datetime), `InMemoryCursorState` (get-missing / set-then-get / overwrite / idempotent-delete / update helper / initial seed), and `FileCursorState` (round trip / new file / nested subdir / datetime ISO serialization / delete / delete-missing-noop / corrupt-file-recovery / atomic-no-tempfile-leak / multi-key accumulation). Plus a `BatchSource.read_since` default check on a hand-rolled `DummySource`. Cumulative: **400 core unit + 3 skipped, 844 tests project-wide all green**.
- **Provider mocks + Sidebar / Header / ConnectionForm stories** [Step 10.8] — last gap in story coverage closed.
  - The three providers in `components/providers/` (`auth-provider.tsx` / `theme-provider.tsx` / `workspace-provider.tsx`) now `export` their `Context` objects and value types. Production code keeps using the high-level `*Provider` + `use*()` hooks; Storybook can wrap a story in the same `Context.Provider` with a hand-built value, so any component that reads via `useAuth()` / `useTheme()` / `useWorkspaces()` works without hitting the API or `next/navigation`.
  - `.storybook/mocks/providers.tsx` ships `MockAuthProvider`, `MockThemeProvider`, `MockWorkspaceProvider`, and a `MockAppShell` combo that stacks all three with happy-path defaults. Exported `DEFAULT_USER` and `DEFAULT_WORKSPACES` keep stories consistent across components.
  - `components/shell/header.stories.tsx` — 5 stories (title-only, with-subtitle, with-actions, anonymous state, light theme via swapped `MockThemeProvider initial="light"`).
  - `components/shell/sidebar.stories.tsx` — 6 stories driven by Storybook 9's `parameters.nextjs.navigation.pathname` so the active-nav state lights up the right entry (pipelines-active, overview-active, settings-active, blue-workspace `currentIndex=1`, no-workspaces, solo).
  - `components/connections/connection-form.stories.tsx` — 4 stories (create, edit-postgres, edit-http, edit-mongo). The "Save" button intentionally 401s inside Storybook — these stories are for visual + a11y verification of the connector-type-aware multi-step form.
  - 12 components × ~53 stories now in the catalog. `pnpm --filter @etlx/web {typecheck,build,build-storybook}` all green.
- **Storybook test-runner + higher-level stories** [Step 10.8 follow-up] — axe-core CI gate is now wired, plus two more component stories shipped.
  - `.storybook/test-runner.ts` ships an `@storybook/test-runner` config that injects axe via `axe-playwright` and calls `checkA11y('#storybook-root')` on every story's `postVisit` — failing any WCAG AA violation. Individual stories opt out with `parameters.a11y.disable = true` (no current story does). New script: `pnpm --filter @etlx/web test-storybook`. CI runbook documented in `.storybook/README.md` (build → static serve → `test-storybook --url` + first-time Playwright chromium install).
  - `components/builder/pipeline-node.stories.tsx` — 6 stories rendered inside a mini `@xyflow/react` canvas so source/target handle placement, accent-tinted icon chip, and the ring-on-select state all match the production builder. Covers postgres source, filter transform, s3 sink, mongo source, selected state, and not-configured fallback.
  - `components/schedules/cron-input.stories.tsx` — 6 stories with a stateful wrapper so preset chips, live `cronstrue` description, and next-firing preview all light up exactly like `/w/[slug]/schedules`. Covers daily 03:00, every 5 min, weekdays 09:00, empty-batch (required hint), empty-stream-allowed (info hint), and invalid (error styling).
  - Storybook + Next builds both still green; Storybook bundle weight unchanged (~580 kB axe chunk dominates). Provider-dependent components (Sidebar / Header / ConnectionForm) are deferred until a fake-provider wrapper lands so we don't pull localStorage / `next/navigation` mocks into every story.
- **Storybook 9 + addon-a11y foundation** [Step 10.8] — new `services/etlx-web/.storybook/` brings the design system to life.
  - **Framework**: `@storybook/nextjs-vite@9.1.20` (not the legacy webpack-backed `@storybook/nextjs` — that was incompatible with Next 15.5's bundled webpack and failed with a `Cannot read properties of undefined (reading 'tap')` cache hook error). The vite-backed framework picks up the same `app/globals.css` Tailwind v4 pipeline via `@tailwindcss/vite` registered in `viteFinal`, so every `@theme` token resolves identically to `next build`.
  - **Theme + a11y**: `withThemeByDataAttribute` decorator toggles `data-theme="dark"|"light"` on `<html>` so designers see the same dual-token result the app ships. `@storybook/addon-a11y` runs axe-core inside each story canvas so AA violations surface during local dev — feeding the CLAUDE.md hard rule "a11y AA 위반 머지 금지".
  - **Stories for 7 ui/ primitives** — Button (5 variants + size row + with-icon + loading + disabled + destructive-with-icon = 8 stories), Input (default + value + invalid+aria-describedby + disabled + with-label), Card (basic + with-action + 2-up grid), StatusBadge (7 status states + combined row), EmptyState (no-pipelines + failed-fetch + title-only), DataTable (basic + empty-state + clickable, sample Pipeline rows + StatusBadge cells), ConfirmDialog (destructive with loading-spin + confirm path). ~30 stories across 13 meta pages, all autodoc-tagged.
  - **Scripts**: `pnpm --filter @etlx/web storybook` for dev, `build-storybook` for CI sanity (renders to `storybook-static/`, gitignored). `next build` continues to pass — 11 routes, 187 kB max first-load JS (no regression).
  - **Future work** (still under Step 10.8): higher-level component stories (Sidebar / Header / PipelineNode / CronInput / ConnectionForm), automated axe-core gate via Storybook test-runner in CI, Chromatic/Playwright visual regression baseline on 5 key pages, keyboard-only scenario coverage.
- **MongoDB connector — first NoSQL** [Step 5.3] — new `etl_plugins/connectors/nosql/mongodb.py` ships `MongoDBConnector(BatchSource, BatchSink)`, registered as `mongodb` via `@ConnectorRegistry.register` and an entry-point row. Sync pymongo so it slots into `Pipeline.run` (driven from a thread) the same way the RDBMS connectors do.
  - **Read** (`query` = collection name): `**options` accepts `filter` (default `{}`), `projection`, `sort` ((field, direction) tuple or list of tuples), `limit` (0 = unbounded), and `batch_size` (overrides `chunk_size` for the wire-level cursor batch). Documents map verbatim onto `Record.data` with `_id` preserved so upsert/dedup logic can reference it.
  - **Write** (`table` = collection name): `append` uses `insert_many(ordered=False)` so a single duplicate `_id` doesn't kill the batch; `overwrite` does `drop_collection` then insert; `upsert` issues bulk `ReplaceOne(filter=<keys>, upsert=True)` ops — `key_columns` is required and each document must carry every key. `BulkWriteError` and other `PyMongoError`s are wrapped in `WriteError` with full context.
  - **Health check**: `admin.command('ping')`, returning `False` when not yet connected (mirrors the sqlite/postgres/mysql contract behavior — no auto-connect). `ConnectionFailure` also flips to `False`; other pymongo errors propagate so misconfiguration isn't silently masked.
  - **pyproject changes**: new optional extra `mongodb = ["pymongo>=4.6"]`, entry-point row `mongodb = "etl_plugins.connectors.nosql.mongodb:MongoDBConnector"`, dev dep `pymongo>=4.6`, testcontainers extra `mongodb`, mypy override for `pymongo` / `bson`.
  - **UI catalog**: `services/etlx-web/lib/operators.ts` gains `source:mongodb` (Collection / Filter JSON / Projection JSON / Limit) and `sink:mongodb` (Collection / Mode / Key fields) — 17 operators total. `services/etlx-web/lib/connector-schemas.ts` adds the MongoDB connection form (uri + database required, optional username/password secret/auth_source/timeout_ms). Users can now build MongoDB pipelines end-to-end from the web UI.
  - **Testing strategy**: 26 unit tests use a hand-rolled `_FakeMongoClient` injected via the constructor's `client=` kwarg — keeps the unit tier free of `mongomock` (and Docker) while still exercising option dispatch, mode branches, and error mapping. 34 integration tests against a testcontainers `mongo:7` image cover the standard `BatchSource` / `BatchSink` / `BatchRoundTrip` contracts (18 cases via a `_StripIdConnector` subclass that drops `_id` for payload equality) plus 16 mongo-specific tests: filter, sort+limit, projection, overwrite drops collection first, upsert insert vs. update, missing-key rejection, unreachable host produces `ReadError` (not hang), explicit `_id` round-trip, extra kwargs forwarded to `MongoClient`, isolation between collections, and idempotent `close()` after an unreachable connect. Cumulative: **374 core unit + 3 skipped, 818 tests project-wide all green**.
- **HTTP source connector** [Step 5.7] — new `etl_plugins/connectors/http/` houses `HttpConnector(BatchSource)`, registered as `http` via `@ConnectorRegistry.register` and the `etl_plugins.connectors` entry-point.
  - **httpx-backed** so timeout / retry / JSON decoding behavior is consistent with the rest of the stack, and tests can use `httpx.MockTransport` instead of a real server.
  - **Response shapes**: top-level JSON array (`[{...}, {...}]`) or top-level object indexed by `records_field` (default `"items"`; pass `None` to require list-only responses).
  - **Pagination**: when `page_param` is set, the connector loops appending `?<page_param>=N` starting from `start_page` (default 1) until the server returns an empty page or `max_pages` (default 1000 — sanity guard) is hit. No `page_param` → single GET.
  - **Auth**: optional `auth_token` becomes `Authorization: Bearer <token>`; arbitrary `headers` (e.g. `X-Api-Key`) merge into every request. `params` is sent as static query parameters on every page.
  - **Health check** returns `True` for any response with status `< 500` (so 401 / 403 mean "server up, auth is the issue, not reachability"); only 5xx and connection errors flip it to `False`.
  - **`httpx>=0.27` promoted** from dev-only (FastAPI TestClient) to a top-level `dependencies` entry so the HTTP connector is zero-extras to install. New entry-point row `http = "etl_plugins.connectors.http.connector:HttpConnector"`.
  - **UI catalog**: `services/etlx-web/lib/operators.ts` gains a `source:http` operator with fields for Path / Records field / Page parameter / Static params; `services/etlx-web/lib/connector-schemas.ts` adds the matching connection form (base_url + bearer token secret + timeout). Users can build an HTTP pipeline end-to-end from the web UI.
  - **21 new unit tests** (`tests/unit/connectors/http/test_http.py`) cover construction guards (missing `base_url`, non-http scheme), array + object response shapes, custom `records_field`, Bearer auth header, custom headers, query params, pagination-until-empty + `max_pages` cap, non-2xx → `ReadError`, non-JSON → `ReadError`, missing field → `ReadError`, non-object record → `ReadError`, registry lookup, and `records_field=None` with object response. Cumulative core-side: **348 unit + 3 skipped**; full project **758 tests** all green.
- **Stream worker manager** [Step 9.4] — new `etlx_server/worker/stream.py` + `etlx-server stream-worker run` CLI runs as a separate process and keeps stream pipelines alive.
  - **Lifecycle inversion**: batch pipelines are pull-driven (scheduler enqueues, worker claims); stream pipelines are push-driven (worker scans active stream Schedule rows on a tick, creates a `running` Run row, and spawns an `asyncio.Task` that calls `Pipeline.arun_stream()`). The task gets the same heartbeat + RunRecorder periodic-drain treatment as batch.
  - **Cancellation**: deactivating or deleting a schedule cancels its in-flight task; the Run row is stamped `cancelled` with `error_class='StreamCancelled'`. Worker shutdown cancels every in-flight stream so the UI never shows ghost `running` rows after a restart.
  - **No re-spawn loop**: when a task finishes for any reason (build failure / mode mismatch / connector error / success), the worker records the schedule's `updated_at` at that moment. The next tick refuses to re-spawn unless `Schedule.updated_at` has changed — so a misconfigured pipeline doesn't generate a Run row per tick. The user has to edit the schedule (toggle off+on, change cron, etc.) to retry.
  - **Mode enforcement**: a stream schedule pointing at a batch PipelineVersion stamps the Run row `failed` with `error_class='ModeMismatch'` immediately rather than calling `arun_stream` on an incompatible pipeline.
  - **Single-replica today**: documented in CLI help + module docstring. Multi-replica needs a `FOR UPDATE SKIP LOCKED` claim on the schedule row before spawning (tracked as Step 11 operator strengthening).
  - **6 new integration tests** (`tests/db/test_stream_worker.py`) using a monkey-patched `build_pipeline` + `_FakeStreamPipeline` (whose `arun_stream` is an `asyncio.sleep` respecting cancellation) so the slice doesn't need a Kafka testcontainer. Covers: stream schedule spawn / inactive + batch ignored / mode mismatch / deactivation cancels / pre-set stop / pipeline-without-current-version skip. Cumulative server-side: **300 tests in suite, all green**.
- **Workspace dashboard / home page** [Step 10.6] — new `/w/[slug]` route is the workspace entry point.
  - **Stat cards** (4-up grid): pipelines, active schedules (with "N paused" subtext), connections, runs today (with "N in last batch" subtext). Each card links to its respective detail page; values render `—` while loading so the cards don't pop in.
  - **Recent runs** (left card, 2/3 width): newest 10 runs with `StatusBadge`, run id, pipeline id, "scheduled vs manual" hint, duration, and chevron — links into the existing Run detail page.
  - **Recent failures** (right card, 1/3 width): up to 5 failed runs from the latest batch, each in a red-tinted card with `error_class` headline and "Xm ago" relative timestamp. Renders a green "Nothing failing right now" badge when there are no failures, so the empty state feels reassuring rather than empty.
  - **Polling**: page refreshes every 10 s (longer than the run-detail page since this is a glance view, not an active-monitor view).
  - **Routing changes**: sidebar gets an "Overview" nav entry pointing to `/w/[slug]`, listed first. The workspaces list cards now land users on `/w/[slug]` instead of `/w/[slug]/connections`. Sidebar active-state detection now matches the overview entry by exact equality so nested routes don't keep it highlighted.
  - Builder route `/w/[slug]` weighs 3.98 kB / 131 kB; 12 total routes now.
- **Recorder periodic drain (live log tail)** [Step 9.3c / 10.5] — opt-in mid-run flushing for `RunRecorder`.
  - `RunRecorder` gains a `flush_interval_seconds: float | None = None` parameter. When set, an `asyncio` background task wakes up every N seconds and commits the snapshot of pending log + metric entries through a fresh session from the factory. Producers (structlog processor, `RecordingMetrics`) stay zero-lock; the timer task is the only writer, and it serializes against the `__aexit__` final flush via the existing stop event.
  - Default stays at `None` so unit tests using the shared `_SessionFactoryAdapter` (where the executor + recorder share one `AsyncSession`) keep working — concurrent `session.commit()` calls aren't safe on one async session, and the periodic path would deadlock.
  - **Wiring**: `RunExecutor` accepts `log_flush_interval_seconds`, `RunWorker` forwards it, and `etlx-server worker run` exposes a `--log-flush-interval` CLI flag (default 2.0s, 0 disables). Production worker process now writes logs to `run_logs` live; the Run-detail page's existing 2-second poll picks them up without further changes.
  - **New test**: `test_recorder_flushes_periodically_when_interval_set` builds a separate `async_sessionmaker` against the testcontainer engine (skipping the conftest outer-transaction wrapper because the recorder's separate connection can't see uncommitted parent rows through it). Verifies `run_logs` contains the mid-run event *before* the recorder's `__aexit__`. Explicit cleanup in `finally` keeps later tests in the session unaffected. Cumulative server-side: **294 tests in suite, all green**.
- **Pipeline-level retry + DLQ config in the builder** [Step 10.4 / 9.5] — `PipelineConfig.retry` and `PipelineConfig.dlq` are no longer invisible in the UI.
  - New `PipelineSettingsPanel` (`components/builder/pipeline-settings-panel.tsx`) shows in the right pane when no node is selected. Two collapsible sections per DESIGN.md card grammar:
    - **Retry policy**: `max_attempts` (1-20), `backoff` (`fixed` / `exponential`), `initial_delay_seconds`. Maps directly to core `RetryConfig`.
    - **Dead-letter queue**: connection picker filtered to all workspace connections, table (batch sink) + topic (stream sink) inputs, mode (`append` / `overwrite` / `upsert`). Maps to core `DlqConfig`.
  - Both sections gate their fields behind an "Enable" checkbox so the saved `PipelineConfig` only carries `retry` / `dlq` keys when the user actually opted in — no empty objects polluting saved YAML/JSON.
  - **Deselect support**: `BuilderCanvas` now forwards `onPaneClick` as an `onDeselect` callback. Clicking the canvas background returns the right pane to `PipelineSettingsPanel`; clicking any node re-binds to `PropertiesPanel`.
  - `BuilderState` gains `retry` + `dlq` plus `DEFAULT_RETRY` / `DEFAULT_DLQ`; `serialize` / `deserialize` round-trip through the new fields. `blankBuilder()` keeps both disabled so new pipelines stay minimal.
  - Builder route 62.8 → 63.8 kB.
- **Next-firing preview in CronInput** [Step 10.5] — when the cron expression parses, the input shows the next 3 firings in the user's local timezone plus a relative offset (`in 5h 23m`). Invalid mid-edit input silently disables the preview (no red error spam). Uses `cron-parser ^5.x` (timezone-aware via Luxon, ~30 kB to the schedules route).
- **Pipeline Dry Run UI** [Step 10.4] — third header button in `/w/[slug]/pipelines/[id]/edit`.
  - Calls `POST /pipelines/{id}/dry-run` (which builds the pipeline server-side + parallel `connector.health_check()` per resolved connection — no I/O on the actual source/sink data).
  - Results render in a collapsible panel between the canvas and properties panel: top "passed / failed" banner with a success or error icon, top-level config errors in a red-bordered block, then per-connector rows (success-tinted bg with check icon when ok, error-tinted bg with full error trace `<pre>` when not). Dismiss button clears the panel.
  - Disabled until a `current_version` exists (same gate as Trigger). Standalone — no separate Save step required, since `/dry-run` reads from the current PipelineVersion.
  - `pipelinesApi.dryRun` + `DryRunResponse` / `DryRunConnectorCheck` types added to `lib/api.ts`. Builder route grew 62 kB → 62.8 kB.
- **Pipeline delete + Workspace edit/delete UI** [Step 10.3 / 10.6]
  - **Pipeline delete**: third action button on each row in `/w/[slug]/pipelines`, gated by `ConfirmDialog` with copy that calls out "All versions and schedules are removed; past runs stay for audit; pending/in-flight runs are left to complete (the worker can't re-fetch them after deletion)". Hits `DELETE /pipelines/{id}` and refreshes the list.
  - **Workspace edit**: `/w/[slug]/settings` is no longer read-only. Owners (and SuperAdmin) get name / slug / accent-color forms; slug changes hot-redirect to the new URL via `router.replace`. Non-Owners get a read-only display + the role line. Server's `WorkspaceSlugTakenError` 409 surfaces in the error toast unmodified.
  - **Workspace delete (Danger zone)**: red-bordered card at the bottom of settings with a destructive button + `ConfirmDialog`. On success the workspace list is refreshed and the page redirects to `/workspaces`.
  - **API surface**: `workspacesApi.update` / `delete`, `pipelinesApi.delete` added to `lib/api.ts`. Build still produces 11 routes (settings grew from 2.21 kB → 4.45 kB, pipelines from 3.86 kB → 4.65 kB).
- **Audit log viewer** [Step 10.6] — `/w/[slug]/audit` with sidebar nav entry.
  - **Filters**: resource type `<select>` (workspace / membership / connection / pipeline / schedule / run + "All"), resource ID exact match. Both are pushed straight into the `?resource_type` / `?resource_id` query params of `GET /audit?workspace_id=…` — the server already enforces "must be a member or SuperAdmin" so no UI-side ACL.
  - **Row expansion**: each row is a single line (timestamp · action chip · resource_type+id · actor truncated UUID). Clicking expands into a two-column `before_json` / `after_json` pre block so the diff is immediately visible without a separate detail page. Rows that had `before == null` AND `after == {}` collapse the chevron and disable expansion since there's nothing to show.
  - **Pagination**: 100/page offset stepper at the bottom; Next is disabled when fewer rows came back than the page size.
  - **API surface**: `auditApi.query` + `AuditLogEntry` type added to `lib/api.ts`. Verified by `pnpm --filter @etlx/web build` — audit route is 4.33 kB / 127 kB first-load JS, 11 total routes.
- **Member management UI** [Step 10.6] — `/w/[slug]/members` with sidebar nav entry.
  - **List**: shows joined user identity (name / email) + role chip (color-coded per role — pink Owner / blue Editor / amber Runner / muted Viewer).
  - **Add by email** (Owner only): inline form with email + role select; matches the server contract that the email must already exist in the metadata DB (no signup flow yet — local-auth users are seeded via OIDC first-sign-in or `etlx-server` CLI).
  - **Role change** (Owner only): inline `<select>` on each row hits `PATCH /memberships/{user_id}`; toasts the new role. The server's `LastOwnerError` 409 is surfaced unmodified in the error toast so the message is clear ("cannot remove the last Owner of workspace X").
  - **Remove** (Owner only): trash button → `ConfirmDialog`. The current user's own row shows "You" instead of a remove button so a self-eject can't lock the user out by accident.
  - **Non-owner view**: read-only — the page renders a "Read-only" card explaining that only Owners can manage members. Detection uses `WorkspaceSummary.role`; SuperAdmin bypass (`role === null`) is treated as Owner.
  - **API surface**: `membershipsApi.list` / `add` / `updateRole` / `remove` added to `lib/api.ts`.
  - Verified by `pnpm --filter @etlx/web build` — members route is 5.19 kB / 128 kB first-load JS, 10 total routes.
- **Schedule CRUD + cron builder UI** [Step 10.5] — `/w/[slug]/schedules` is no longer read-only.
  - **CronInput** (`components/schedules/cron-input.tsx`): 5-field cron text input wrapped with six preset chips (every minute / every 5 min / hourly / daily 03:00 / weekdays 09:00 / monthly), an inline human-readable description from `cronstrue` (verbose mode), and red-border invalidation on parse failure. Stream mode allows blank — the description text explains the semantics.
  - **Schedule form** (`components/schedules/schedule-form.tsx`): create flow with mode pill picker (batch / stream — pickable only at create time per the server's immutable-mode rule), CronInput, active checkbox. Edit flow renames + cron only (mode read-only). Both validate the batch-mode-requires-cron rule client-side before submitting so a 422 from the server is the rare case rather than the common one.
  - **Schedules page**: pipelines fetched once for the create-form pipeline picker (each schedule belongs to one pipeline). Each row gets toggle (pause/resume via `POST /toggle`), edit, and delete actions. `ConfirmDialog` carries forward from Step 10.3 for delete confirmation.
  - **API surface**: `schedulesApi.create` / `update` / `delete` / `toggle` added to `lib/api.ts`, plus `ScheduleCreateBody` / `ScheduleUpdateBody` typed bodies matching the server's request schemas.
  - **New dep**: `cronstrue ^3.4.x`. Verified by `pnpm --filter @etlx/web build` — schedules route is 12.6 kB / 136 kB first-load JS (was 2.62 kB read-only); cronstrue weighs ~10 kB gzipped.
- **Connection create/edit/delete UI** [Step 10.3] — `/w/[slug]/connections` is no longer read-only.
  - **Connector schema catalog** (`lib/connector-schemas.ts`): per-connector list of fields with type / required / default / placeholder / `isSecret`. Mirrors the `__init__` signatures of `postgres` / `mysql` / `sqlite` / `s3` / `kafka` exactly so the form never emits a knob the runtime wouldn't accept.
  - **Create form** (`components/connections/connection-form.tsx`): name + connector-type pill picker + schema-driven field grid. Secret fields render as `type=password` inputs; the wire-format builder turns each filled secret into a `{"$secret": "<field_key>"}` marker in `config` plus a matching entry in `secrets`, so plaintext only travels over TLS and never reaches the metadata DB (`SecretWalker` server-side enforces this).
  - **Edit form**: rename only this slice — the server's update contract requires re-supplying every secret when `config` changes, and the UI can't read existing plaintext. The form is explicit about "delete + recreate" being the path for credential rotation.
  - **Delete**: per-row trash button → new `ConfirmDialog` primitive (`components/ui/confirm-dialog.tsx`, DESIGN.md §7.9 — backdrop blur, Esc-to-cancel, destructive Button variant). Confirmation warns about secret backend cleanup and pipelines that may break.
  - **API surface**: `connectionsApi.create` / `update` / `delete` / `get` added to `lib/api.ts` with typed bodies matching `ConnectionCreateRequest` / `ConnectionUpdateRequest`. Verified by `pnpm --filter @etlx/web build` — connections route is 6.56 kB / 130 kB first-load JS (was 3.27 kB read-only).
- **Run detail page** [Step 10.5 UI] — `/w/[slug]/runs/[id]` consumes the freshly-populated `run_logs` / `run_metrics` tables from Step 9.3c.
  - **Layout**: two columns (lg+) — logs viewer on the left, Summary + Metrics + Error stack on the right. Logs render as monospaced rows with timestamp, level chip (color-coded), message, and inline `context_json` pairs; the pane autoscrolls to the bottom when new lines arrive. Summary is a definition list (status / pipeline / version / scheduled / started / finished / duration / records / worker / heartbeat). Metrics groups by `name` and shows summed value + sample count so the canonical `etl_plugins.records.read` / `etl_plugins.records.written` / `etl_plugins.duration.seconds` are immediately legible.
  - **Polling**: while the run isn't terminal, the page re-fetches the trio (run / logs / metrics) every 2 s; on first terminal status it fetches one more time and stops, so the page stays cheap once the run is done.
  - **Retry**: a Retry button surfaces on `failed` / `cancelled` runs and hits `POST /runs/{id}/retry`, toasting the new run id. The Header gains a Back chevron to `/w/[slug]/runs` and a manual Refresh button.
  - **Runs list integration**: rows in `/w/[slug]/runs` are now clickable and route to the detail page; the data table's `onRowClick` was already in place.
  - Live-tail through the existing SSE endpoint is *not* wired this slice — `EventSource` doesn't carry an `Authorization` header, and Step 9.3c flushes at `__aexit__` so logs only become visible after the run finishes anyway. A follow-up will move the recorder to a separate factory + periodic drain so logs land mid-run.
- **Log / metric forwarding** [Step 9.3c] — bridges core `structlog` events and the core `Metrics` ABC into the `run_logs` / `run_metrics` tables during worker execution.
  - New `etlx_server/worker/recorder.py`: `RunRecorder` async context manager registers itself in a module-level map keyed by `run_id`, installs `RecordingMetrics` as the global metrics backend, and exposes `log_processor` (a structlog processor) + `current_run_id` (a `ContextVar` so `asyncio.to_thread` workers inherit it). Producers (structlog event, `Counter.add`, `Histogram.record`) enqueue into `queue.SimpleQueue` — thread-safe, zero-lock from caller side.
  - **One-shot flush at `__aexit__`** — the recorder removes itself from the active map *before* writing, then commits the queue snapshot in a single transaction. Periodic drain was prototyped and dropped: the executor and recorder share an `AsyncSession` in the test harness, and concurrent `session.commit()` calls aren't supported. Live-tail latency is the cost; the existing `GET /runs/{id}/logs/stream` SSE already re-polls.
  - **Executor lifecycle log events**: `RunExecutor.execute` now binds a structlog logger with `run_id=str(run.id)` and emits `run.build_started` / `run.build_failed` / `run.pipeline_started` / `run.pipeline_succeeded` / `run.pipeline_failed` so even a connector that logs nothing leaves a trail in `run_logs`. `pipeline_succeeded` includes `records_read` / `records_written` / `duration_seconds` for the UI Run-detail page.
  - **Worker CLI integration**: `etlx-server worker run` calls `configure_logging(level=..., json=True, extra_processors=[log_processor])` on startup so any `structlog.get_logger()` event with a matching `run_id` lands in `run_logs` for the duration of the recorder.
  - **5 new integration tests** (`tests/db/test_worker_recorder.py`): matching-run-id structlog events captured / metric calls captured with attrs / events after `__aexit__` are dropped / e2e success through `RunExecutor` populates `RECORDS_READ_TOTAL` + `RECORDS_WRITTEN_TOTAL` + `DURATION_SECONDS` and all 3 lifecycle messages / build-failure path leaves `run.build_failed` with `error_class=_PipelineBuildError`. Cumulative server-side: **293 tests in suite, all green**.
- **Pipeline Builder (visual flow editor)** [Step 10 UI 2] — React Flow (`@xyflow/react`) based drag-add operator canvas at `/w/[slug]/pipelines/[id]/edit`.
  - **Operator catalog** (`lib/operators.ts`): 5 sources (Postgres / MySQL / SQLite / S3 / Kafka), 4 transforms (rename / cast / filter / python), 5 sinks. Each operator declares its accent color (DESIGN.md §3.5 palette), Lucide icon, and a list of typed fields (`string`/`number`/`select`/`json`/`connection`) so the right-side Properties panel renders the form without per-operator code.
  - **Serializer** (`lib/pipeline-config.ts`): converts the linear node list (source → transforms → sink) to the core `PipelineConfig` JSON shape and back. Mirrors the runtime constraint that pipelines are linear — adding a second source or sink replaces the existing one. `deserialize` infers the operator id from a stored `PipelineConfig` JSON when re-opening an existing pipeline.
  - **Canvas** (`components/builder/builder-canvas.tsx`): React Flow with a custom `PipelineNode`, dotted background (token-aligned), animated accent edges between nodes, MiniMap + Controls themed via tokens, attribution hidden via `proOptions`. Nodes are non-draggable / non-connectable — the canvas reflects builder state, the palette is the only way to add an operator.
  - **PipelineNode** (DESIGN.md §7.6): 240px card, bg-elevated, 14px radius, accent handle dots, hover → "Trash" button on transforms, click → selected via accent ring. Summary line shows connection + table/topic/key for source/sink, or the first non-empty transform field.
  - **Palette** (left, 256px): operator buttons grouped by kind; click adds a node and the canvas re-layouts it into the linear chain.
  - **Properties panel** (right, 320px): per-field input with connection dropdown that filters to matching `Connection.type`. JSON fields parse/validate inline (red border + error helper on bad JSON).
  - **Save / Trigger**: header buttons call `PATCH /pipelines/{id}` (canonicalized through `PipelineConfig.model_validate` on the server, so an idempotent new `PipelineVersion` is created) and `POST /pipelines/{id}/trigger`. The pipelines list page's "New pipeline" affordance creates a blank `PipelineConfig` (default Postgres source+sink, empty connection strings — pass validation but won't run until configured) and redirects straight into the builder.
  - **Layout**: `AppShell` content column is now `h-screen flex-col overflow-hidden`; list pages wrap their bodies in `flex-1 overflow-y-auto` so they scroll independently from the sticky header, and the builder fills the remaining viewport.
  - **New dep**: `@xyflow/react ^12.10.2`. Verified by `pnpm --filter @etlx/web build` — editor route compiles to 64 kB / 187 kB first-load JS.
- **Web UI foundation** [Step 10 UI 1] — `services/etlx-web` Next.js 15 / React 19 / Tailwind v4 app.
  - **Design tokens (Step 10.0)**: `app/globals.css` carries every token from DESIGN.md §11.1 inside a `@theme` block; dark default + `[data-theme="light"]` branch + Tailwind v4 CSS-first config (no `tailwind.config.ts`). Focus-visible ring uses the accent token; scrollbars and a `pulse-dot` keyframe round out shared visual chrome. PostCSS bridge via `postcss.config.mjs` → `@tailwindcss/postcss`.
  - **Primitives (Step 10.1)**: `components/ui/{button,input,card,data-table,status-badge,empty-state}.tsx`. Button has 5 variants × 3 sizes (primary = accent gradient + `shadow-md`, secondary/ghost/destructive/outline). `StatusBadge` covers every `RunStatus` + queued/skipped with status-tinted bg/text and a pulsing dot in `running`.
  - **Shell (Step 10.2)**: `components/shell/{app-shell,sidebar,header}.tsx`. Sidebar is Arc-style — 4px inset color bar drives off the current workspace's `color_hex`, an inline dropdown switches workspaces with hex preview, and nav items go pink when active. Header sticks at top with theme toggle + user avatar + sign-out.
  - **Providers** (all `"use client"`): `AuthProvider` reads JWT from `localStorage["etlx.token"]`, calls `/auth/me` on mount, listens for the `etlx:unauthorized` window event (dispatched by the API client on 401) and routes to `/login?next=...`. `ThemeProvider` persists `etlx.theme` and writes `[data-theme]` to `<html>`. `WorkspaceProvider` lists `/workspaces` and pins the current selection via `etlx.workspace` localStorage.
  - **API client** (`lib/api.ts`): typed `fetch` wrapper, DTOs mirror `etlx_server.auth.schemas` exactly so the FE never translates names. Endpoint groups: `workspacesApi`, `connectionsApi`, `pipelinesApi`, `schedulesApi`, `runsApi`. Tokens are read per-call (no in-memory cache) so multi-tab sign-in/out doesn't desync; the base URL falls back to `http://localhost:8000` if `NEXT_PUBLIC_ETLX_API_URL` is unset.
  - **Pages**:
    - `/login` — email/password form → `POST /auth/login`, stores access + refresh, toasts on failure, honors `?next=`.
    - `/workspaces` — list + create flow (auto-owner via `POST /workspaces`), 8 preset color picker matching DESIGN.md §3.5.
    - `/w/[slug]/connections` — list + per-row `Test` button hitting `POST /connections/{id}/test`, results toasted.
    - `/w/[slug]/pipelines` — list with `Open builder` link (placeholder for UI 2) + `Trigger` button calling `POST /pipelines/{id}/trigger`.
    - `/w/[slug]/schedules` — flattened across pipelines (cron + mode + active state).
    - `/w/[slug]/runs` — Run table with `StatusBadge`, 5s polling so live runs visibly progress, formatted duration / records-read-written / error class.
    - `/w/[slug]/settings` — workspace metadata read-out.
  - **Guardrails**: every color/spacing/radius comes from a token; no `bg-[#…]` arbitrary classes; PostCSS pipeline + Next 15 production build succeed (`pnpm --filter @etlx/web build`).
  - **Deferred to a later slice**: Storybook + Chromatic baseline, axe-core scan, font self-host, Command Palette, member-management UI, audit log viewer, schedule cron editor, DLQ tools. Tracked under ROADMAP §10.0/10.1/10.5/10.6/10.8.
- **Cron 스케줄러** [Step 9.2] — `services/etlx-server/etlx_server/scheduler/` 패키지.
  - `Scheduler(factory, *, tick_interval_seconds=10.0)` — `tick_once()`이 활성 batch 스케줄 1회 스캔, cron의 다음 firing이 도래한 행에 대해 `RunStatus.PENDING` Run을 enqueue. worker(Step 9.3a)가 그 후 SKIP-LOCKED claim.
  - "last fire" 기준은 `MAX(runs.scheduled_at) WHERE schedule_id=…` — 별도 컬럼/마이그레이션 없음. 첫 firing은 `schedule.created_at`을 base로 사용해 epoch backfill 방지.
  - **No-catchup 의미론** (한 tick당 미스 firing 하나씩 enqueue). 진정한 no-catchup은 `base = max(last_or_created, now)` 변경으로 가능하나 새 스케줄이 첫 tick에 firing 안 함 — 정책 결정 보류, 현 상태가 합리적 trade-off.
  - **방어적 skip + log** (loop crash 안 함): `is_active=False` / `mode=STREAM` / `cron_expr IS NULL` / `is_current` 버전 부재 / 잘못된 cron 식 (router 검증 우회 시).
  - `croniter` 라이브러리(이미 의존성)로 firing 계산. invalid cron은 `ValueError`/`KeyError` 잡아 skip.
  - `asyncio.Event` 기반 graceful stop — `Scheduler.stop()` 호출 시 다음 tick wait가 즉시 깨어남.
  - 새 CLI 서브커맨드 `etlx-server scheduler run --tick-interval … --log-level …` — `worker run` / `reaper run`과 동일한 패턴으로 별도 process 배포 (SIGTERM/SIGINT graceful).
  - 8 신규 통합 테스트(`tests/db/test_scheduler.py`): due batch → 1 Run 생성 + version/workspace 핀 / inactive skipped / stream skipped / no current version skipped + log / 두 번째 tick은 no-op(yearly cron으로 결정성 보장) / 잘못된 cron skip / loop pre-set stop / loop fires + stop.
  - **HA 노트**: 현 단일-replica 배포 안전. multi-replica는 `FOR UPDATE SKIP LOCKED`로 schedule row를 lock 필요 — 운영 도입 시 추가.
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

- **Utils** [Step 1.7]
  - `etl_plugins/utils/retry.py` — `@retryable` 데코레이터 (sync+async 자동 분기, tenacity 위 얇은 래퍼, `reraise=True`로 원래 예외 그대로 propagate, structlog warning + `ERRORS_TOTAL` 카운터 자동 emit)
  - `etl_plugins/utils/chunk.py` — `chunked` (list of T) + `take` + `drop`
  - `etl_plugins/utils/async_io.py` — `run_sync_in_thread` (asyncio.to_thread wrapper), `gather_with_concurrency` (Semaphore 기반 bounded gather), `iter_to_async` (sync iterable → async iterator, optional `in_thread=True`)
  - 단위 테스트 42개 (`tests/unit/utils/`)
  - 패키지 루트 re-exports: `retryable`, `chunked`, `run_sync_in_thread`, `gather_with_concurrency`, `iter_to_async`

- **테스트 인프라** [Step 1.8]
  - `tests/fixtures/records.py` — `sample_records` (3-row), `large_records(n)`, `mixed_types_records` 데이터 factory
  - `tests/fixtures/connectors.py` — `InMemoryBatchSource` / `InMemoryBatchSink` / `InMemoryBatchSourceSink` (reference impls)
  - `tests/contracts/batch.py` — `_BatchSourceContract` (7 tests) / `_BatchSinkContract` (5 tests) / `_BatchRoundTripContract` (3 tests) — subclass-able mixin pattern (`_` prefix로 자동 수집 회피)
  - `tests/contracts/_helpers.py` — `normalize_payloads` (order-independent equality)
  - `tests/contracts/test_inmem.py` — InMemory로 contract suite 자체 검증 (15 tests)
  - `tests/conftest.py` — `sample_records` 글로벌 fixture
  - `tests/unit/core/conftest.py` — InMemory를 fixtures로 이동 후 슬림화

### Decisions
- ADR-0001: SPEC.md와 CLAUDE.md 역할 분리
- ADR-0002: Foundation 기술 스택 확정 (Python 3.11+, uv, Pydantic v2, Typer, structlog, hatchling, Apache-2.0)
- ADR-0003: Step 1.4 Core 추상화 주요 결정 (fluent builder, caller-managed lifecycle, hooks via `.on(...)`, batch-only Step 1.4, `ConnectError` 명명, 등)
- ADR-0004: Step 1.5 Config 로더 설계 결정 (로드 순서, `${VAR}` 단순 문법, `extra=allow/forbid` 이중 기준, SecretBackend ABC + 스텁)
- ADR-0005: Step 1.6 Observability 설계 결정 (ABC+NoOp 패턴, structlog JSON 기본, substring 마스킹, OTel 실구현은 Step 6로 이동)
- ADR-0006: Step 1.7 Utils 설계 결정 (tenacity 얇은 래퍼, sync+async 단일 데코레이터, observability 자동 연동, CB/RateLimiter는 Step 3로 이동)
- ADR-0007: Step 1.8 테스트 인프라 설계 결정 (subclass-able contract mixin, normalize_payloads, InMemoryBatchSourceSink combined 커넥터)

### Milestone
- **Step 1 — Foundation 완료 (2026-05-14)**: 193 단위 테스트, 7 ADRs, 라이브러리 코어/Config/Observability/Utils/테스트 인프라 모두 완비.

- **PostgreSQL 커넥터** [Step 2.1]
  - `etl_plugins/connectors/rdbms/postgres.py` — psycopg 3 기반 `PostgresConnector(BatchSource, BatchSink)`
  - `read()` — server-side named cursor + chunk_size 단위 itersize 스트리밍 + uuid 기반 cursor 이름 충돌 회피
  - `write()` 세 가지 모드:
    - `append` (default) — `COPY ... FROM STDIN` (bulk insert, fastest)
    - `overwrite` — `TRUNCATE` 후 COPY
    - `upsert` — `INSERT ... ON CONFLICT (keys) DO UPDATE SET ...` (`key_columns` 필수)
  - `sql.Identifier` 기반 식별자 quoting → SQL 인젝션 방지, `public.orders` 같은 schema-qualified 이름 지원
  - `@ConnectorRegistry.register("postgres")` 데코레이터 + `pyproject.toml`의 `[project.entry-points."etl_plugins.connectors"]` 등록 (lazy 발견)
  - 모든 psycopg 예외를 우리 `ConnectError` / `ReadError` / `WriteError`로 래핑 (실패 시 rollback)
  - `connection` property — 테스트/마이그레이션용 escape hatch
  - 의존성: `[postgres]` extra = `psycopg[binary]>=3.1`, dev group에 추가
- **Contract suite 확장** [Step 2.1]
  - `tests/contracts/batch.py`에 `read_kwargs` / `write_kwargs` fixture 추가 (default `{}`, 후방 호환). 신규 커넥터는 fixture override만으로 query / table 같은 커넥터별 인자 주입 가능.
- **통합 테스트** [Step 2.1]
  - `tests/integration/test_postgres.py` — 모듈 단위 `pytestmark = pytest.mark.it`
  - 3 contract subclass (Source 7 + Sink 5 + RoundTrip 3 = 15) + postgres-specific 14 = **29 integration tests** (testcontainers + `postgres:16-alpine`, 약 5.6s)
  - `tests/integration/conftest.py` — `pg_container` (session) / `pg_conn_params` / `pg_raw_kwargs` / `pg_connector` / `pg_table` / `pg_seeded`

- **S3 (object storage) 커넥터** [Step 2.2]
  - `etl_plugins/connectors/object_storage/s3.py` — `S3Connector(BatchSource, BatchSink)` (boto3 기반)
  - 포맷 3종 지원: jsonl / csv / parquet (pyarrow)
  - `detect_format(key)` — 확장자 기반 자동 감지 (`.jsonl`/`.ndjson`/`.csv`/`.parquet`/`.pq`, 대소문자 무관), `format=...` 명시적 override
  - `read(query=prefix)` — paginated `list_objects_v2` + per-object parse. 인식 불가 확장자 객체는 skip (e.g. `_SUCCESS` 마커)
  - `write(key=..., format=...)` — 단일 object PUT. `mode=append/overwrite`만 허용, `upsert`는 명시적으로 거부 (object storage에 row identity 없음)
  - `endpoint_url` 파라미터로 S3-compatible 서비스 (MinIO/R2/DO Spaces 등) 동일 코드 사용
  - 포맷별 직렬화/역직렬화는 순수 함수 — boto3 없이 단위 테스트 가능
  - 모든 boto3/botocore 예외 → `ConnectError`/`ReadError`/`WriteError`로 래핑
  - `@ConnectorRegistry.register("s3")` + `pyproject.toml` entry-point 등록 (lazy 발견)
- **테스트** [Step 2.2]
  - `tests/unit/connectors/object_storage/test_s3_helpers.py` — 포맷 helpers 21 tests (확장자 감지, 3종 round-trip, edge cases)
  - `tests/integration/test_s3.py` — testcontainers MinIO 기반 35 tests (Contract Source 7 + Sink 5 + RoundTrip 3 + S3-specific 20)
  - `tests/integration/conftest.py` — `minio_container` (session) / `s3_conn_params` / `s3_boto_kwargs` / `s3_bucket` (per-test create+drop) / `s3_connector` / `s3_seeded`
  - MinIO + 최신 boto3 (`>=1.36`) 호환성 패치: 픽스처 cleanup이 batch `DeleteObjects` 대신 per-object `delete_object` 사용 (Content-MD5 요구 회피)
- **의존성 추가**
  - 런타임 extras: `s3 = ["boto3>=1.34", "pyarrow>=16.0"]`
  - dev: `testcontainers[postgres,minio]>=4.7`, `boto3>=1.34`, `pyarrow>=16.0`

- **Kafka 커넥터** [Step 2.3]
  - `etl_plugins/connectors/stream/kafka.py` — `KafkaConnector(StreamSource, StreamSink)` (aiokafka 기반)
  - Sync `connect/close`는 flag-only, 실제 클라이언트는 `subscribe()`/`publish()`에서 lazy 시작
  - `subscribe(topic, group_id=...)` — `async def` + `yield` 형태의 async generator. `finally` 절에서 consumer 자동 stop. `auto_offset_reset="earliest"` + `enable_auto_commit=False` (at-least-once)
  - `publish(topic, record, key=...)` — `send_and_wait` (acks=all 기본)
  - `flush()` / `aclose()` — async 명시적 자원 정리. `aclose()` idempotent
  - 메타데이터에 Kafka 위치 정보(topic/partition/offset/key/timestamp) 포함
  - `bootstrap_servers`는 str 또는 list 허용 (list → comma-join)
  - `commit()`은 Step 3 Pipeline runtime에서 구현 — 현재는 `NotImplementedError`
  - SASL/SSL 파라미터 placeholder — Step 5에서 확장
- **Stream Contract** [Step 2.3 — NEW]
  - `tests/contracts/stream.py` — `_StreamSourceContract` / `_StreamSinkContract` / `_StreamRoundTripContract`
  - 모든 메서드 `async def`, `asyncio.wait_for(timeout=consume_timeout)`로 무한 블로킹 방지
  - 같은 subclass-able mixin 패턴 (`_` prefix 자동 수집 회피)
- **테스트** [Step 2.3]
  - `tests/integration/test_kafka.py` — 모듈 단위 `pytestmark = pytest.mark.it`
  - 3 Stream Contract subclass (5 tests) + Kafka-specific (11 tests) = **16 integration tests** (~11s with `testcontainers[kafka]`, KRaft mode)
  - `tests/integration/conftest.py` — `kafka_container` (session) / `kafka_bootstrap` / `kafka_connector` (function) / `kafka_topic` (unique per-test) / `kafka_seeded_topic` (async fixture, pre-publishes sample_records)
- **의존성 추가**
  - 런타임 extras: `kafka = ["aiokafka>=0.11"]`
  - dev: `testcontainers[postgres,minio,kafka]>=4.7`, `aiokafka>=0.11`
  - mypy override: `aiokafka`, `aiokafka.*` (ignore_missing_imports — no public stubs yet)

### Milestone
- **Step 2 — Reference Connectors 완료 (2026-05-14)**: 세 카테고리 각 1종 (postgres / s3 / kafka), 총 80 integration tests + Batch/Stream Contract 양쪽 패턴 확립. 214 unit + 80 it = 294 tests.

### Decisions
- ADR-0008: Step 2.1 PostgreSQL 커넥터 (psycopg 3 채택, COPY 기반 append, TRUNCATE+COPY overwrite, INSERT ON CONFLICT upsert, server-side cursor, sql.Identifier quoting)
- ADR-0009: Step 2.2 S3 커넥터 (boto3, jsonl/csv/parquet, `query=prefix`, `upsert` 거부, MinIO 호환 + 통합 테스트, MinIO batch-delete 우회)
- ADR-0010: Step 2.3 Kafka 커넥터 (aiokafka 채택, sync connect/close flag-only + async aclose, subscribe는 async generator, Stream Contract 신규 도입, at-least-once 기본, commit은 Step 3로 이동)

- **Pipeline runtime — YAML 빌더 + Transforms** [Step 3.1]
  - `etl_plugins/runtime/transforms.py` — `@register_transform("name")` 데코레이터 + `build_transform(TransformConfig)` 디스패처. 빌트인 4종:
    - `rename` — 키 remap (`mapping: {old: new, ...}`)
    - `cast` — int/int64/float/float64/str/string/bool/timestamp (ISO 8601만, `None`/누락 컬럼은 스킵)
    - `filter` — `eval(code, {"__builtins__": {}}, {"data": ..., "metadata": ...})`로 sandboxed 평가. `compile(..., "eval")`은 빌드 타임 1회
    - `python` — `module:function` 문자열로 import. import 실패는 `ConfigError`, 런타임 예외는 `TransformError`로 래핑
  - `etl_plugins/runtime/builder.py` — `build_connector` / `build_connectors` / `build_pipeline` / `build_pipeline_from_yaml`. Composition root. `(Pipeline, dict[str, Connector])` 튜플 반환 → caller 또는 `run_pipeline_yaml`이 lifecycle 관리. `extra_connectors=`로 테스트에서 mock 주입 자연스러움.
  - `etl_plugins/runtime/runner.py` — `run_pipeline_yaml(...)` 헬퍼: 빌드 → connect 일괄 → run → close 일괄 (`contextlib.suppress(Exception)`로 cleanup 안전). 한 커넥터 close 실패가 다른 정리 안 막음.
  - `etl_plugins/runtime/__init__.py` — 7개 public 심볼 re-export
- **`etlx` CLI** [Step 3.1]
  - `etl_plugins/cli.py` — Typer 기반. 서브커맨드 5종:
    - `etlx version` — 패키지 버전
    - `etlx list-connectors` — 등록된 connector 이름 (built-in + entry-point)
    - `etlx validate <pipe.yaml> [--connections <conn.yaml>]` — YAML 파싱 + 연결 인자 검증 (실제 connect 없음)
    - `etlx run <pipe.yaml> [--connections <conn.yaml>] [--env-file <.env>]` — 빌드 → connect → run → close, 결과 요약 출력
    - `etlx test-connection <name|--all> --connections <conn.yaml>` — 각 connector의 health_check() 호출
  - `pyproject.toml`: `[project.scripts] etlx = "etl_plugins.cli:app"` — `uv pip install -e .` 후 즉시 사용
- **테스트** [Step 3.1]
  - `tests/unit/runtime/test_transforms.py` — 23 tests (dispatcher 2 + rename 3 + cast 7 + filter 5 + python 6)
  - `tests/unit/runtime/test_builder.py` — 8 tests (build_connector / build_connectors / build_pipeline / build_pipeline_from_yaml E2E)
  - `tests/unit/test_cli.py` — 7 tests via `typer.testing.CliRunner` (version / list-connectors / validate ok+fail / validate with connections / help / 잘못된 YAML 거부)
  - 총 252 unit tests (이전 214 + 38)
- **의존성**
  - 런타임: `typer>=0.12` (이미 Foundation에서 추가됨)

### Decisions
- ADR-0011: Step 3.1 Pipeline runtime (runtime/ 패키지 분리, transform registry 별도, filter sandbox 위협 모델 명시, Typer CLI, `run-stream`/Retry/DLQ는 Step 3.2/3.3로 이동)

### Milestone
- **Step 3.1 — Pipeline runtime + `etlx` CLI 완료 (2026-05-14)**: YAML → Pipeline + Connectors + Transforms 빌더, batch 실행 헬퍼, CLI 5개 서브커맨드. 38 신규 unit tests. **다음은 Step 3.2 (stream runtime + commit)**.

- **Stream runtime** [Step 3.2]
  - `etl_plugins/core/pipeline.py` — `Pipeline.arun_stream(...)` async 메서드 (mode='stream'). subscribe → transform → publish → buffer flush → commit 흐름. `stop_after_records` / `stop_after_seconds`로 확정적 종료 가능. `finally` 블록에서 잔여 pending flush 보장.
  - `Pipeline.commit_strategy: str = "after_sink_flush"` 필드 추가 — buffer flush 직후 `await source.commit()` 호출 여부 결정. PipelineConfig.commit.strategy → builder가 자동 propagate.
  - `Pipeline.run`이 mode='stream'일 때 명확한 PipelineError 메시지로 `arun_stream` 사용 안내.
- **`StreamSource.commit` 시그니처 변경 (async)** [Step 3.2]
  - `etl_plugins/core/connector.py` — `async def commit(self, offsets: Any = None) -> None`. `offsets=None`은 "현재 위치 commit" 관습. SPEC.md §4.1의 sync 시그니처 보정.
  - `etl_plugins/connectors/stream/kafka.py` — `KafkaConnector._consumer` 참조 보존(subscribe 시 set, 종료/aclose 시 unset). `commit()`은 활성 consumer가 없으면 `ConnectError`, 있으면 `await self._consumer.commit()`. ADR-0010의 NotImplementedError 약속을 superseded.
- **Runtime / CLI 확장** [Step 3.2]
  - `etl_plugins/runtime/runner.py` — `arun_stream_pipeline_yaml(...)` async helper. cleanup이 `aclose()`(async, Kafka) → `close()`(sync) fallback.
  - `etl_plugins/cli.py` — `etlx run-stream <pipe.yaml> [--stop-after-records N] [--stop-after-seconds T]`. KeyboardInterrupt → exit 130.
- **테스트 인프라** [Step 3.2]
  - `tests/fixtures/connectors.py` — `InMemoryStreamSource` (async generator subscribe + commits list) + `InMemoryStreamSink` (publish list + flush_calls counter). Stream-pipeline unit tests를 Docker 없이 실행 가능.
- **테스트** [Step 3.2]
  - `tests/unit/runtime/test_pipeline_stream.py` — 16 tests (happy path 9 + validation errors 5 + YAML e2e 1 + bonus 1). 269 unit total.
  - `tests/unit/test_cli.py` — `test_run_stream_smoke` 추가 (8 CLI tests).
  - `tests/integration/test_stream_pipeline.py` — 2 tests (Kafka → transform → Kafka 라운드트립; commit offsets가 실제로 Kafka 그룹에 반영되는지). 82 it total.
- **수정** [Step 3.2]
  - `tests/integration/test_kafka.py::test_commit_not_implemented` → `test_commit_without_active_subscribe_raises` (ConnectError 검증).

### Decisions
- ADR-0012: Step 3.2 Stream runtime (sync run + async arun_stream 분리, commit ABC async화 + offsets 옵셔널, Pipeline.commit_strategy 필드, buffer dict in sink_options, stop_after_* 확정 종료, Kafka consumer ref 보존, integration test에서 실제 commit 검증)

### Milestone
- **Step 3.2 — Stream runtime 완료 (2026-05-14)**: `Pipeline.arun_stream` + Kafka async commit + buffer + `etlx run-stream`. 17 신규 unit + 2 신규 integration tests (269 unit + 82 it = 351 total). **다음은 Step 3.3 (Retry / DLQ / Checkpoint)**.

- **Retry / DLQ / 자동 메트릭** [Step 3.3]
  - `etl_plugins/config/models.py` — `DlqConfig {connection, table?, topic?, mode='append'}` 추가. `PipelineConfig.dlq: DlqConfig | None` 필드.
  - `etl_plugins/core/pipeline.py`:
    - `Pipeline.retry: RetryConfig | None` / `Pipeline.dlq: DlqConfig | None` 필드 추가
    - `Pipeline.run`: `self.retry` 있으면 `_run_task`를 `@retryable(**retry_kwargs)`로 감싸 호출 — task 전체 재시도 (caller가 idempotency 책임)
    - `_run_task`: transform 예외 발생 시 DLQ가 설정되어 있으면 raw 레코드를 DLQ로 라우팅하고 continue. 아니면 TransformError로 propagate.
    - `Pipeline.arun_stream`: `sink.publish`만 retry 래핑 (per-record). transform 예외도 DLQ 라우팅 가능 (StreamSink로 publish).
    - 자동 metrics emit: 각 task 끝에 `RECORDS_READ_TOTAL` / `RECORDS_WRITTEN_TOTAL` counter add (attrs: pipeline, mode). 예외 시 `ERRORS_TOTAL{phase=run|transform}`. finally에서 `DURATION_SECONDS` histogram record. NoOp 기본이라 cost zero.
  - `etl_plugins/runtime/builder.py` — `PipelineConfig.dlq.connection`이 connectors에 없으면 `ConfigError`. retry / dlq을 Pipeline에 그대로 전달.
- **CLI 글로벌 로깅 옵션** [Step 3.3]
  - `etl_plugins/cli.py` — `@app.callback()`이 `--log-format json|console` + `--log-level DEBUG|INFO|...`를 모든 서브커맨드 전에 `configure_logging`에 전달. 잘못된 format은 exit 2.
- **테스트** [Step 3.3]
  - `tests/unit/runtime/test_resilience.py` — 11 tests (batch metrics 2 + batch retry 2 + batch DLQ 3 + stream metrics 1 + stream DLQ 1 + stream retry 1 + NoOp safety 1)
  - `tests/unit/test_cli.py` — 2 신규 (--log-format ok/invalid)
  - 총 282 unit + 82 it = 364 tests
- **Cursor / Checkpoint 미구현** [Step 3.3]
  - Step 6 강화로 이동. 현재 `Pipeline.on("post_run", ...)` 훅으로 사용자가 커스텀 cursor 저장 가능 — abstraction은 후속.

### Decisions
- ADR-0013: Step 3.3 Retry / DLQ / 자동 메트릭 / CLI logging (batch는 task-level retry / stream은 publish-level retry, DLQ는 transform 실패만 라우팅 best-effort, 표준 metrics 자동 emit, Typer global callback으로 logging 설정, Checkpoint는 Step 6로 이동)

### Milestone
- **Step 3.3 — Resilience + Observability 통합 완료 (2026-05-14)**: Retry 정책 + DLQ 라우팅 + 자동 메트릭 + CLI 로깅 옵션. **Step 3 전체 완료 (3.1+3.2+3.3)**. 다음은 Step 4 (Orchestrator Adapters) 또는 Step 5 (커넥터 확장).

- **Orchestrator Adapters** [Step 4]
  - `etl_plugins/adapters/airflow.py` — `ETLPluginsOperator(BaseOperator)` (lazy). `execute()` → `run_pipeline_yaml(...)` → XCom-friendly dict 반환.
  - `etl_plugins/adapters/dagster.py` — `EtlPluginsResource(ConfigurableResource)` + `etl_plugins_op` (`required_resource_keys={"etl_plugins"}`).
  - `etl_plugins/adapters/prefect.py` — `run_etl_pipeline_task` + `run_etl_pipeline_flow` (`@task` + `@flow`).
  - 모두 PEP 562 `__getattr__`로 lazy: orchestrator 미설치 상태에서도 모듈 import 성공. 심볼 접근 시 helpful `ImportError("... pip install 'etl-plugins[airflow|dagster|prefect]'")` 발생.
- **Optional-dependencies 확장** [Step 4]
  - `airflow = ["apache-airflow>=2.10"]`
  - `dagster = ["dagster>=1.9"]`
  - `prefect = ["prefect>=3.0"]`
- **테스트** [Step 4]
  - `tests/unit/adapters/test_lazy_imports.py` — 10 tests (structural 7 + orchestrator-conditional 3 via `pytest.importorskip`). Conditional 테스트는 orchestrator extra 설치 시 monkeypatch로 `run_pipeline_yaml` delegation 검증.
- **lint/typing** [Step 4]
  - `pyproject.toml` `[tool.mypy.overrides]`에 airflow/dagster/prefect ignore_missing_imports + adapter 모듈에 `disallow_untyped_decorators = false`.
  - `[tool.ruff.lint.per-file-ignores]`에 adapter 모듈 F822 silence (`__getattr__` 동적 노출).

### Decisions
- ADR-0014: Step 4 Orchestrator Adapters (PEP 562 `__getattr__` lazy 노출, 모두 `run_pipeline_yaml`로 위임, 3 optional extras, structural + conditional 테스트, Airflow operator는 batch 전용)

### Milestone
- **Step 4 — Orchestrator Adapters 완료 (2026-05-14)**: Airflow/Dagster/Prefect adapters + 10 신규 tests (289 unit + 3 skip + 82 it). 다음은 Step 5 (커넥터 확장).

- **MySQL 커넥터** [Step 5.1]
  - `etl_plugins/connectors/rdbms/mysql.py` — `MySQLConnector(BatchSource, BatchSink)` (PyMySQL)
  - `read()`: server-side `SSDictCursor` + `fetchmany(chunk_size)` 루프 — bounded memory streaming
  - `write()`: executemany 기반 multi-row INSERT (default `batch_size=1000`)
    - `append`: 일반 INSERT
    - `overwrite`: TRUNCATE TABLE + INSERT
    - `upsert`: `INSERT ... ON DUPLICATE KEY UPDATE col = VALUES(col)` — MySQL native upsert syntax
  - Backtick 기반 식별자 quoting (`a``b`처럼 내부 backtick은 escape), DB-qualified 이름(`db.table`) 지원
  - `@ConnectorRegistry.register("mysql")` + `[project.entry-points."etl_plugins.connectors"]`
  - 의존성: `[mysql]` extra = `pymysql>=1.1`
- **통합 테스트** [Step 5.1]
  - `tests/integration/test_mysql.py` — 모듈 단위 `pytestmark = pytest.mark.it`
  - Contract subclass (Source 7 + Sink 5 + RoundTrip 3 = 15) + MySQL-specific 14 = **29 통합 테스트**
  - `testcontainers[mysql]` (`MySqlContainer("mysql:8.0")`), boolean→TINYINT(1) 의미론 차이는 `_mysqlify()` 헬퍼로 매핑
  - `tests/integration/conftest.py` — `mysql_container` (session) / `mysql_conn_params` / `mysql_table` / `mysql_seeded` / `mysql_connector` 5개 fixture 추가
- **의존성 추가**
  - 런타임 extras: `mysql = ["pymysql>=1.1"]`
  - dev: `testcontainers[postgres,minio,kafka,mysql]>=4.7`, `pymysql>=1.1`
  - mypy override: `pymysql`, `pymysql.*` (ignore_missing_imports — no stubs)

### Decisions
- ADR-0015: Step 5.1 MySQL 커넥터 (PyMySQL 드라이버, SSDictCursor server-side streaming, executemany 기반 multi-row INSERT, ON DUPLICATE KEY UPDATE upsert, backtick quoting, boolean=TINYINT(1) 의미론 차이는 통합 테스트에서 `_mysqlify` 헬퍼로 매핑)

### Milestone
- **Step 5.1 — MySQL 커넥터 완료 (2026-05-14)**: 두 번째 RDBMS connector. Contract suite 재사용으로 새 커넥터 보일러플레이트 검증. 29 신규 통합 테스트 (289 unit + 3 skip + 111 it = 403 total). 다음은 Step 5.x 추가 connectors (SQLite/Oracle/MSSQL 또는 NoSQL/DW 카테고리).

- **SQLite 커넥터** [Step 5.1b]
  - `etl_plugins/connectors/rdbms/sqlite.py` — `SQLiteConnector(BatchSource, BatchSink)`. stdlib `sqlite3` 사용 — **외부 dep 0, extras 불필요**.
  - 기본 `database=":memory:"`. 파일 경로는 그대로 지정 (`SQLiteConnector(database="/path/to.db")`).
  - `read()`: `cursor.fetchmany(chunk_size)` 루프 + `row_factory = sqlite3.Row` → `dict(row)`.
  - `write()` 세 모드:
    - `append`: executemany INSERT (batch_size=1000)
    - `overwrite`: `DELETE FROM <table>` (TRUNCATE 미지원) + INSERT
    - `upsert`: `INSERT ... ON CONFLICT (key_columns) DO UPDATE SET col = excluded.col, ...` (SQLite 3.24+)
  - Double-quote 기반 식별자 quoting (`"name"`), 내부 `"`는 `""` escape.
  - `@ConnectorRegistry.register("sqlite")` + `[project.entry-points."etl_plugins.connectors"]` — `etlx list-connectors`에 자동 노출.
- **테스트** [Step 5.1b]
  - `tests/unit/connectors/rdbms/test_sqlite.py` — Contract subclass (Source 7 + Sink 5 + RoundTrip 3 = 15) + SQLite-specific 17 = **32 unit tests** (Docker 없이, `tmp_path`로 파일 db 매 테스트마다). ~0.3s 실행.
  - boolean → INT(0/1) 의미론은 `_sqlitify()` 헬퍼로 매핑.
- **typing**
  - `sqlite3.connect`의 `isolation_level: Literal[...]` 요구를 `cast`로 우회 — 사용자는 평범한 `str | None` 그대로 전달.

### Decisions
- ADR-0016: Step 5.1b SQLite 커넥터 (stdlib sqlite3 — extras 불필요, in-memory 기본, ON CONFLICT (PostgreSQL과 동일 문법) 채택, unit-test only — Docker zero)

### Milestone
- **Step 5.1b — SQLite 커넥터 완료 (2026-05-14)**: 세 번째 RDBMS connector. RDBMS 슬롯에 3/5 채움(postgres/mysql/sqlite). 32 신규 unit tests (321 unit + 3 skip + 111 it = 435 total). 다음은 Step 5.x 추가 connectors.

- **서비스화 방향 확정** [Step 7~11 신설]
  - `SPEC.md` — 코어 위에 얹는 **별도 패키지 (`services/etlx-server` FastAPI + `services/etlx-web` Next.js)** 도입. §1 (목적), §2 (아키텍처 9-layer), §3 (디렉토리: `services/` 추가), §8.5 (서비스 경계 규칙), §10 (Steps 7~11 추가) 패치. **단방향 의존** — `etl_plugins/`은 `services/`를 절대 import하지 않음.
  - `ROADMAP.md` — Step 7~11 추가:
    - 7: Service Foundation (Metadata DB / Secret / YAML↔DB 양방향)
    - 8: API Server (FastAPI, OIDC + JWT, RBAC, audit, CRUD/Action 엔드포인트)
    - 9: Execution Engine 통합 (Dagster/Prefect/자체 PoC + 스케줄러 + Run 라이프사이클 + Stream worker manager)
    - 10: Web UI (Next.js + React Flow Pipeline Builder + 다크/라이트, DESIGN.md §13 진행 순서 따름)
    - 11: 운영 강화 (Helm chart, 멀티테넌시 부하 테스트, 운영 메트릭 대시보드)
  - `CLAUDE.md` — 코어/서비스 이중 트랙 명시, 의존 규칙 명문화, 금기사항에 "서비스 패키지 격리" 섹션 + `DESIGN.md` 토큰 외 사용 금지 추가.
  - 코드 변경 없음 (코어 라이브러리는 그대로). 모든 결정은 ADR-0017로 기록.

- **디자인 시스템 채택** [Step 10 사전 준비]
  - `DESIGN.md` 신규 (662줄) — `services/etlx-web`의 디자인 SSOT.
    - Arc Browser 영감 (사이드바 중심 + Command Palette + 워크스페이스 컬러 인디케이터)
    - 컬러: 깊은 네이비 베이스 (`#0A1228` / `#0F1730` / `#15203F`) + 팝핑크 강조 (`#FF3D8B`)
    - 타이포: Inter Variable + Pretendard Variable + JetBrains Mono (self-host)
    - 레이아웃: 8pt grid, 사이드바 240/64, max width 1280
    - 컴포넌트 베이스: shadcn/ui (토큰 치환), React Flow Pipeline node 테마
    - 모션: Framer Motion (fast 120 / default 200 / slow 320ms), `prefers-reduced-motion` 존중
    - 토큰 단일 진실 (CSS variables → Tailwind config → shadcn/ui), Storybook + Chromatic + axe-core가 CI 게이트
    - A11y: AA 의무, 핵심 페이지 AAA 권장
    - 적용 우선순위 §13 (Step 10.0~10.8 단계별 진행 순서) 명시
  - `SPEC.md` §9.7 / `CLAUDE.md` 금기사항에 디자인 규칙 통합

### Decisions
- ADR-0017: 서비스화 전략 — 코어 라이브러리 위에 별도 패키지로 웹 UI/API 분리. 모노레포(`etl_plugins/` + `services/etlx-server/` + `services/etlx-web/`), 단방향 의존(서비스 → 코어), CI 분리(`ci-core.yml` / `ci-server.yml` / `ci-web.yml`), 세부 기술 스택은 Step 7.0에서 ADR-0019~0023으로 확정.
- ADR-0018: 디자인 시스템 — Arc 영감, 네이비 베이스 + 팝핑크 강조. shadcn/ui + Tailwind v4 + Storybook + Chromatic/Playwright visual regression + axe-core a11y가 CI 게이트. 프론트엔드 스택(Next.js/TS/React Flow) 결정도 함께 포함되어 ROADMAP §7.0의 "프론트엔드 스택 ADR"은 별도 두지 않음.

### Milestone
- **서비스화 방향 + 디자인 시스템 확정 (2026-05-14)**: 코어 라이브러리(Steps 1~6) 위에 얹는 서비스 계층(Steps 7~11)을 별도 패키지로 분리하는 결정 — ADR-0017. UI 디자인은 ADR-0018 + DESIGN.md로 단일 진실 확립. 코드 변경 없음 (문서만). 다음 작업은 (a) Step 5 추가 connectors / (b) Step 6 강화 (mkdocs, v0.1.0 릴리스) / (c) Step 7.0 ADR-0019~0023 착수 중 우선순위 선택.

- **서비스 기술 스택 ADR 5개** [Step 7.0]
  - 코드 변경 없음. 결정 문서만(DECISIONS.md). 다음 슬라이스(7.1 모노레포 스캐폴딩)가 명확해짐.
  - **ADR-0019**: API 프레임워크 = FastAPI (`>=0.115`) + uvicorn workers + gunicorn 진입점. Pydantic v2 통합으로 코어의 `ConnectionConfig` / `PipelineConfig`를 REST body로 그대로 재사용. OpenAPI 자동 생성 → `etlx-web`이 type-safe client 자동 생성.
  - **ADR-0020**: 메타데이터 저장소 = PostgreSQL 16+ + SQLAlchemy 2.x async(`asyncpg`) + Alembic. JSONB로 config 직렬화 + JSONB GIN 인덱스. UUID PK (uuid7 권장). 워크스페이스 격리는 응용 레벨(`workspace_id` 외래키). 시크릿은 절대 DB 평문 저장 금지 — backend ref만.
  - **ADR-0021**: 실행 엔진 = **자체 PostgreSQL-backed worker queue** 채택, Dagster/Prefect 임베드 거부. 이유: 코어가 이미 `Pipeline.run` / `arun_stream` / retry / DLQ / metrics를 갖춤. `runs` 테이블이 큐 겸함, `FOR UPDATE SKIP LOCKED`로 멀티 워커 fan-out, advisory lock으로 스케줄러 단일 leader. Step 4 외부 엔진 adapter는 그대로 유지(사용자가 자기 Airflow에 우리 pipeline 얹는 경로 보존). Step 9.1 PoC 후 본 구현.
  - **ADR-0022**: 모노레포 = uv workspace + pnpm workspace, 단일 git repo. CI 3분리 (`ci-core.yml` / `ci-server.yml` / `ci-web.yml`) + `paths:` 필터. `import-linter`로 ADR-0017의 단방향 의존을 CI 자동 검증 (`etl_plugins` → `services` 차단, `core` → `connectors`/`adapters`/`runtime` 차단). 릴리스 워크플로 3분리(`release-core` / `release-server` / `release-web`), 서비스 별도 SemVer.
  - **ADR-0023**: 인증·인가 = OIDC 일반화(authlib, Google/Azure AD/Okta/GitHub) + 로컬 fallback(email+bcrypt) + JWT RS256(access 15min, refresh 7d rotation) + PAT(`etlx_pat_*`). 4 역할 RBAC(Owner/Editor/Runner/Viewer) 워크스페이스 단위 + 글로벌 SuperAdmin. FastAPI `Depends(require_workspace_role(...))` 체이닝으로 선언적 표현. 모든 mutating endpoint가 `audit_log` row 남김(actor / before_json / after_json / ip / user_agent).

### Decisions
- ADR-0019: API 프레임워크 = FastAPI (>=0.115), Pydantic v2 통합, OpenAPI 자동 — 코어 모델 REST 재사용
- ADR-0020: 메타DB = PostgreSQL 16+ + SQLAlchemy async + Alembic — JSONB 기반 설정 직렬화, 통합 테스트 인프라 재활용
- ADR-0021: 실행 엔진 = 자체 PG-backed worker queue (`SKIP LOCKED`) — 코어 재사용 극대화, 외부 엔진 adapter 보존
- ADR-0022: 모노레포 = uv + pnpm workspace + CI 3분리 + `import-linter` 자동 검증
- ADR-0023: OIDC + 로컬 fallback + JWT RS256 + 4-role RBAC + PAT + audit_log

### Milestone
- **Step 7.0 — 서비스 기술 스택 확정 (2026-05-14)**: 5개 ADR(0019~0023)로 FastAPI / PostgreSQL+async / 자체 PG worker queue / uv+pnpm 모노레포 / OIDC+RBAC을 모두 못박음. 코드 변경 없음. **다음 슬라이스는 Step 7.1 (모노레포 스캐폴딩) — `services/etlx-server/pyproject.toml` + `services/etlx-web/package.json` + workspace 설정 + CI 3분리 워크플로 + `import-linter` 규칙 추가**.

- **ADR-0021 본문 강화** [실행 엔진 결정 보강, 코드 변경 없음]
  - Considered alternatives 표 추가: 7개 후보(자체 PG / Dagster 임베드 / Prefect 임베드 / Celery+Redis / arq / APScheduler / Temporal)를 broker 의존 / task model / 코어 재사용 / UI 중복 / 거부 사유 5축으로 비교. 결정적 기준 명시: (1) 코어 재사용 100% + (2) 인프라 추가 0 + (3) UI 중복 없음 — 자체 PG queue만 동시 만족.
  - Operating envelope 정량화: ~50 runs/sec / ~10k runs/day / p95 enqueue→start <2s / 동시 워커 ~50.
  - Exit ramp 명시: 큐 폴링 코드를 `etlx_server/workers/queue.py` 한 파일에 격리(interface = claim_next/heartbeat/complete/fail). 백엔드 교체 = 그 한 파일 + Alembic 한 번.
  - PoC 합격 기준 4개(Step 9.1) — 통과 못 하면 본 ADR을 supersede하고 broker 재검토.

- **Step 6 격상 — Asset/Lineage/Cursor 1급 모델로 재구성** [장기 운영 관점, 코드 변경 없음]
  - **결정**: 부하(runs/sec)는 broker 교체로 풀리는 후순위 문제고, 진짜 비싼 비용은 **(1) Asset 1급 모델** + **(2) Lineage 추적** + **(3) Cursor/Watermark**. 1~2년 뒤 사용자가 lineage·sensor·백필을 요구할 때 이 세 가지가 코어에 1급으로 있어야 마이그레이션 없이 대응 가능.
  - **ROADMAP §6 재구성**: 9 서브슬라이스(6.1~6.9). 두 버전으로 분할:
    - **v0.1.0**: 6.1 Cursor abstraction + 6.2 OTel/Prometheus 실구현 + 6.3 Contract test suite 완성 + 6.4 mkdocs + 6.5 PyPI 릴리스
    - **v0.2.0**: 6.6 Asset 1급 모델 + 6.7 OpenLineage emit + 6.8 Catalog API + 6.9 v0.2.0 릴리스
  - **호환성**: 기존 `Pipeline + Task + Record + Connector` API는 그대로 유지. Asset/Cursor/Lineage는 위에 얹는 layer.
  - **시장 정렬**: Dagster Software-Defined Assets / Airflow Datasets / OpenLineage spec과 매핑 가능한 모델.

### Decisions
- ADR-0024: Step 6 격상 — Asset/Lineage/Cursor 1급 모델 코어 추가. v0.1.0(Cursor만)과 v0.2.0(Asset+Lineage+Catalog) 두 단계 분할.

### Milestone
- **장기 플랫폼 방향 정렬 (2026-05-14)**: ADR-0021 강화 + ADR-0024로 "데이터 통합 플랫폼"으로서의 모델 결정을 미리 못박음. 코드 변경 없음, 결정 + 계획 문서만. **다음 슬라이스는 Step 7.1 (모노레포 스캐폴딩)** — Step 6 격상 작업은 추후 슬라이스로 진입.

- **모노레포 스캐폴딩** [Step 7.1]
  - `services/etlx-server/pyproject.toml` — uv workspace member, `etl-plugins` path dep, FastAPI/SQLAlchemy/asyncpg/Alembic/authlib/passlib/pyjwt/croniter 의존성(ADR-0019~0023 기준)
  - `services/etlx-server/etlx_server/main.py` — FastAPI app placeholder, `/health` + `/version` 2개 엔드포인트. `etl_plugins.__version__`을 그대로 노출하여 코어 통합 검증.
  - `services/etlx-server/tests/test_health.py` — TestClient 기반 2 unit tests
  - `services/etlx-web/package.json` — pnpm workspace member, Next.js 15 + React 19 + TS 5.6
  - `services/etlx-web/{tsconfig.json, next.config.ts, app/{layout,page}.tsx}` — Next.js App Router 최소 placeholder
  - `pnpm-workspace.yaml` (root) + 루트 `pyproject.toml`에 `[tool.uv.workspace] members = ["services/etlx-server"]` 추가
  - `services/docker-compose.services.yml` — etlx-server / etlx-web / metadata-db(별도 5433 포트로 dev compose의 5432 회피) profile 분리 placeholder
- **단방향 의존 자동 검증** [Step 7.1]
  - `.importlinter` 추가 — 두 contract:
    1. `etl_plugins` → `etlx_server` import 차단 (ADR-0017 §6)
    2. `etl_plugins.core` → `connectors/adapters/runtime` import 차단 (ADR-0022 §4)
  - `import-linter>=2.0`을 dev group에 추가. 현재 코어가 두 contract 모두 KEPT (위반 0).
- **CI 3분리** [Step 7.1]
  - `.github/workflows/ci.yml` → **`ci-core.yml`** rename (git mv로 이력 보존) + `paths:` 필터(etl_plugins/, tests/, pyproject.toml, uv.lock, .importlinter) + `lint-imports` step 추가
  - `.github/workflows/ci-server.yml` 신규 — `services/etlx-server/**` path filter, uv workspace sync + ruff + pytest
  - `.github/workflows/ci-web.yml` 신규 — `services/etlx-web/**` path filter, pnpm install + typecheck + build
- **README 갱신** — CI 뱃지 3개로 분리(`ci-core` / `ci-server` / `ci-web`)

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0017/0022가 가이드한 구조 그대로 구현)

### Milestone
- **Step 7.1 — 모노레포 스캐폴딩 완료 (2026-05-15)**: `services/etlx-server` + `services/etlx-web` 패키지 골격 + CI 3분리 + import-graph 자동 검증. 321 코어 + 2 server placeholder + 3 skip + 111 it = **437 tests** all green, `import-linter` 2 contracts KEPT. 다음 슬라이스는 **Step 7.2 (메타데이터 DB 스키마)** — SQLAlchemy 2.x async 모델 (workspaces / users / roles / connections / pipelines / schedules / runs / audit_log) + Alembic 초기 마이그레이션 + factory_boy fixture.

- **메타데이터 DB 스키마** [Step 7.2]
  - `services/etlx-server/etlx_server/db/uuid7.py` — RFC 9562 uuid7 (stdlib only, 시간 정렬 PK). 외부 라이브러리 의존 회피.
  - `services/etlx-server/etlx_server/db/base.py` — `Base(DeclarativeBase)` + `UUIDMixin` (uuid7 PK) + `TimestampMixin` (`created_at`/`updated_at`, `server_default=now()` + `onupdate=func.now()`).
  - `services/etlx-server/etlx_server/db/enums.py` — `StrEnum` 5종: `WorkspaceRole`(owner/editor/runner/viewer, ADR-0023) / `AuthMethod`(local/oidc) / `PipelineMode`(batch/stream) / `RunStatus`(pending→running→succeeded/failed/cancelled) / `LogLevel`. PG native ENUM 타입으로 컬럼 정의.
  - `services/etlx-server/etlx_server/db/session.py` — `make_engine(url)` + `async_sessionmaker` + `get_session(factory)` 헬퍼. `DATABASE_URL` env var 우선.
  - `services/etlx-server/etlx_server/db/models/workspace.py` — `Workspace`(slug unique, color_hex `#FF3D8B` default) + `User`(email unique, `auth_method`, `password_hash` nullable, `is_superadmin`) + `Membership`(`(workspace_id, user_id)` unique + role enum) + `PersonalAccessToken`(prefix unique, hash 저장, expires/last_used). ADR-0023.
  - `services/etlx-server/etlx_server/db/models/connection.py` — `Connection`(`(workspace_id, name)` unique, type, `config_json` JSONB, `secret_refs` JSONB list — **`${SECRET_REF}` placeholders only**, 평문 시크릿 컬럼 없음 — Step 7.4 secret backend로 연동).
  - `services/etlx-server/etlx_server/db/models/pipeline.py` — `Pipeline`(`(workspace_id, name)` unique) + `PipelineVersion`(immutable snapshot, `version` 1-based 정수, `is_current` bool, `config_json` JSONB) + `Schedule`(cron_expr nullable for stream, `config_overrides` JSONB).
  - `services/etlx-server/etlx_server/db/models/run.py` — `Run` (ADR-0021 워커 큐 + 결과 SSOT): `status` / `scheduled_at` / `heartbeat_at` / `worker_id` / `records_read` / `records_written` / `duration_seconds` / `error_class` / `error_message` / `result_json` JSONB. 인덱스 3종: `ix_runs_queue_poll(status, scheduled_at)` (워커 폴링 핫패스) / `ix_runs_workspace_created` / `ix_runs_heartbeat` (좀비 회수). `RunLog`(`run_id`+`ts`+`level`+`message`+`context_json`) + `RunMetric`(`name`+`value`+`attrs_json`).
  - `services/etlx-server/etlx_server/db/models/audit.py` — `AuditLog`(actor `SET NULL` on user delete, `workspace_id`, action, resource_type/id, before/after JSONB, IP, user_agent). GIN 인덱스 후보 (initial migration에선 B-tree 만).
  - `services/etlx-server/alembic.ini` + `alembic/env.py`(async, `DATABASE_URL` env var) + `alembic/versions/0001_initial_schema.py` — hand-written 마이그레이션: 5 enum 타입 + 12 테이블 + 모든 FK/UNIQUE/INDEX 명시 (autogenerate는 첫 마이그레이션 우선 hand-written으로 명세 보존).
- **통합 테스트 인프라** [Step 7.2]
  - `services/etlx-server/tests/conftest.py` — session-scoped `metadata_db_container` (testcontainers `postgres:16-alpine`) → `_alembic_upgrade` (subprocess `uv run alembic upgrade head`) → session-scoped `metadata_engine` → per-test `session` (outer transaction + rollback isolation). `pytest_collection_modifyitems` 가 `tests/db/*` 를 자동으로 `pytest.mark.it` 부착.
  - `services/etlx-server/tests/db/` — **18 통합 테스트**: workspace 모델 6 / connection 3 / pipeline 3 / run 4 / audit 2. `FOR UPDATE SKIP LOCKED` 패턴 검증 + cascade/SET NULL FK 동작 + JSONB 라운드트립 모두 커버.
  - 루트 `pyproject.toml`: `asyncio_default_fixture_loop_scope = "session"` + `asyncio_default_test_loop_scope = "session"` 추가 — session-scoped 엔진과 테스트가 동일 event loop 공유.

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0020/0021/0023의 결정을 구현체로 옮긴 것)

### Milestone
- **Step 7.2 — 메타데이터 DB 스키마 완료 (2026-05-16)**: 12 테이블 + 5 enum 타입 + 초기 Alembic 마이그레이션 + 18 통합 테스트 (testcontainers Postgres 16). 코어 321 unit (regression 0) + 서버 18 it = **변경 없는 코어 + 18 신규 it green**. `mypy strict` 통과(13 source files), `lint-imports` 2 contracts KEPT. 다음 슬라이스는 **Step 7.3 (YAML ↔ DB 양방향)** — `etl_plugins.config.models` 재사용해 `configs/*.yaml` ↔ metadata DB upsert/export + CLI `etlx import|export`.

- **YAML ↔ DB 양방향 동기화** [Step 7.3]
  - `services/etlx-server/etlx_server/io/yaml_sync.py` — `connections.yaml` + `pipelines/*.yaml` ↔ metadata DB 양방향 sync.
    * `import_connections_yaml(session, ws, path)` — 한 워크스페이스에 connections 일괄 upsert.
    * `import_pipeline_yaml(session, ws, path)` — 한 파이프라인 upsert: Pipeline 존재 확인 후 PipelineVersion 비교 → diff면 새 버전 생성(`is_current=True`, 이전 demote), 동일하면 no-op. cfg.schedule 유무에 따라 `name="default"` Schedule 추가/삭제.
    * `import_yaml_dir(session, ws, dir)` — 디렉토리 walk + `ImportResult` 요약(연결/파이프라인/새 버전/유지/스케줄 카운트).
    * `export_workspace(session, ws, out_dir)` — 역방향: `connections.yaml` + `pipelines/<name>.yaml` 재생성.
  - Secret 처리 (평문 0 약속 유지): `!secret <path>` YAML 태그 → DB `config_json`에 `${SECRET:<path>}` placeholder + `connections.secret_refs` 리스트. Export 시 `${SECRET:<path>}` → `!secret <path>` 태그로 복원. `${VAR}` env-var 형식은 import 시 절대 expand 하지 않고 그대로 보관 → DB는 항상 unresolved placeholder만 가짐.
  - 코어 `etl_plugins.config.models`(SSOT)을 그대로 재사용 — `ConnectionsConfig`/`ConnectionConfig`/`PipelineConfig`이 spec 검증을 책임짐. `extra="forbid"`인 모델 덕분에 unknown 필드가 자동 차단됨.
  - PipelineVersion 증가 규칙: 동일 `config_json` 재import = no-op (`pipeline_versions_unchanged++`). 변경된 import만 `version=v+1`로 새 행 + 이전 `is_current=False` (단일 트랜잭션).
- **etlx-server CLI** [Step 7.3]
  - `services/etlx-server/etlx_server/cli.py` — Typer 기반 admin CLI. 두 서브커맨드:
    * `etlx-server import-yaml <yaml_dir> --workspace <slug>` — 슬러그로 워크스페이스 찾고 디렉토리 import → 요약 한 줄.
    * `etlx-server export-yaml --workspace <slug> --to <dir>` — DB → YAML 트리.
  - `DATABASE_URL` env var 필수(없으면 exit 2). 자체 engine + session 생성 → commit 한 후 dispose.
  - `services/etlx-server/pyproject.toml`: `typer>=0.12` 의존성 + `[project.scripts] etlx-server = "etlx_server.cli:app"` 추가. **코어 `etlx`와 분리** — ADR-0017 단방향 의존 유지(`etlx` = `etl_plugins.cli`는 DB 무지, `etlx-server`는 services 안에서 DB를 다룸).
- **통합 테스트 확장** [Step 7.3]
  - `services/etlx-server/tests/db/test_yaml_sync.py` — 9 신규 it 테스트:
    * `!secret`/`${VAR}` 처리(평문 미저장, env placeholder 보존)
    * Pipeline+Schedule 생성, batch/stream 모드 모두
    * 재import idempotent / diff 시 버전 증가 + demote
    * `import_yaml_dir` summary 카운트
    * 전체 round-trip (in → DB → out → re-import 시 변경 0)
    * 파이프라인 내부 `!secret` 태그도 라운드트립
    * `schedule:` 줄 제거 후 재import → Schedule 행 삭제

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0017 단방향 의존 + ADR-0020 SSOT 재사용 원칙 그대로 구현)

### Milestone
- **Step 7.3 — YAML ↔ DB 양방향 완료 (2026-05-16)**: `etlx-server` 새 CLI + `yaml_sync.py` 양방향 + 9 신규 it 통합 테스트. 누적 코어 321 unit + 서버 2 unit + 서버 **27 it** (18 + 9) = **350 tests** all green. `mypy strict` 16 source files OK, `lint-imports` 2 contracts KEPT. 다음 슬라이스는 **Step 7.4 (Secret backend UI 통합)** — `EnvSecretBackend` 외 3종(Vault/AWS SM/GCP SM) 실제 구현 + 로컬 dev용 `FileSecretBackend`.

- **Secret backend 구현** [Step 7.4]
  - `etl_plugins/config/secrets.py` — `SecretBackend` ABC에 write API 추가 (`set(path, value)` + `delete(path)`). 베이스 구현은 read-only 시 `NotImplementedError("…is read-only…")` 를 던지고 write 가능한 백엔드만 override.
    * `FileSecretBackend` — JSON 파일(`{path: value}` 평면 맵). 쓰기는 tmp + `os.replace` atomic + `chmod 0600`. 미존재 파일은 빈 맵으로 취급. `threading.Lock` 으로 멀티스레드 안전. **dev only — 반드시 git 무시.**
    * `VaultSecretBackend` — `hvac` KV v2. 각 path에 `{"value": ...}` 구조로 단일 string 저장(우리 contract는 "secret = opaque string"). 인자 또는 `VAULT_ADDR`/`VAULT_TOKEN`/`VAULT_KV_MOUNT` env. 인증 실패 시 `SecretError`. delete는 `delete_metadata_and_all_versions` (영구 삭제).
    * `AwsSmSecretBackend` — `boto3` Secrets Manager. `create_secret` → `ResourceExistsException` 이면 `put_secret_value`로 fallback (멱등). `endpoint_url` 지원해서 LocalStack/VPC endpoint 사용 가능. delete는 `ForceDeleteWithoutRecovery=True` (recovery 윈도우 0).
    * `GcpSmSecretBackend` — `google-cloud-secret-manager`. set은 `create_secret`(AlreadyExists 흡수) + `add_secret_version`. get은 `versions/latest` 로 access. delete는 `delete_secret`. `project_id` 인자 또는 `GCP_PROJECT`/`GOOGLE_CLOUD_PROJECT` env.
  - `get_secret_backend` factory 확장 — `"file"` / `"vault"` / `"aws_sm"` / `"gcp_sm"` 추가, 각 백엔드 생성자 인자를 그대로 forward. `_NotImplementedBackend` placeholder 제거 (실제 구현으로 대체).
  - **클라이언트 라이브러리 lazy import** — 각 백엔드 생성자에서만 import. `etl_plugins.config.secrets` 모듈 import는 hvac/boto3/google.cloud.secretmanager 어떤 것도 요구하지 않음. 누락된 extra 사용 시 친절한 ConfigError 메시지("pip install etl-plugins[vault]" 등).
- **신규 pyproject extras** [Step 7.4]
  - 루트 `pyproject.toml` `[project.optional-dependencies]`: `vault = ["hvac>=2.0"]`, `aws-sm = ["boto3>=1.34"]`, `gcp-sm = ["google-cloud-secret-manager>=2.20"]`. 사용자가 필요한 백엔드만 설치.
  - dev group에 `hvac` + `google-cloud-secret-manager` 추가 (unit mock + 통합 테스트 동시 필요). `testcontainers[...]` 에 `vault`/`localstack` extras 추가.
  - mypy override 추가: `hvac.*` / `google.cloud.secretmanager.*` / `google.api_core.*` → `ignore_missing_imports = true` (stub 없거나 gRPC 생성 코드라 strict 검사 미흡).
- **테스트 확장** [Step 7.4]
  - `tests/unit/config/test_secrets.py` — 35 unit 케이스로 확장:
    * Env read-only 검증 (set/delete → NotImplementedError)
    * Static delete + missing 케이스
    * File 9 케이스 (round-trip, atomic-no-tmp-left, chmod 0600 검증, corrupt JSON, non-object, path 누락 ConfigError, env var fallback)
    * Vault 생성자 config 검증
    * AWS SM 생성자 (boto3 patch, network 없이)
    * GCP SM 풀-mock 6 케이스 — 로컬 emulator 없는 백엔드라 mock으로만 검증 (get/set/delete/AlreadyExists/NotFound 모두)
    * factory에 file/vault/aws_sm/gcp_sm 추가 검증
  - `tests/integration/test_secrets_vault.py` — testcontainers `hashicorp/vault:1.18` (dev mode, root_token="root") + 4 it 케이스 (round-trip, overwrite-new-version, missing → SecretError, delete).
  - `tests/integration/test_secrets_aws_sm.py` — testcontainers `localstack/localstack:3.8` (Secrets Manager만 활성화) + 4 it 케이스 (round-trip, overwrite-via-put_secret_value fallback, missing → SecretError, delete).

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — Step 1.5의 `SecretBackend` ABC 위에 write API 추가 + 기존 `_NotImplementedBackend` placeholder를 실제 구현체로 교체)

### Milestone
- **Step 7.4 — Secret backend 구현 완료 (2026-05-16)**: 4 신규 백엔드(File/Vault/AWS SM/GCP SM) + write API(set/delete) + 3 pyproject extras. 누적 코어 344 unit + 2 server unit + 코어 **119 it** (111 + 8 secret backend) + 27 server it = **492 tests** all green. testcontainers Vault + LocalStack 모두 실제 Docker 컨테이너로 검증, 컨테이너는 매 세션 후 sweep. `mypy strict` 39+16 source files OK, `lint-imports` 2 contracts KEPT. **Step 7 (Service Foundation) 전 슬라이스 완료**. 다음은 **Step 8 (API Server / FastAPI)** — 부트스트랩 → 인증 → 워크스페이스/연결/파이프라인 CRUD → 실행 트리거 → SSE/WebSocket 로그.

- **FastAPI 부트스트랩 / OOP 재구성** [Step 8.1]
  - `services/etlx-server/etlx_server/settings.py` — `Settings(BaseSettings)`, `Environment` StrEnum, env/`.env` 기반 + `get_settings()` `lru_cache`. 필드: `service_name`/`environment`/`database_url`/`database_echo`/`cors_origins` + 파생 프로퍼티 `docs_enabled`(prod이면 False).
  - `services/etlx-server/etlx_server/app_factory.py` — `create_app(settings: Settings | None = None) -> FastAPI`. **Composition root**: lifespan 핸들러 빌드 → engine + session_factory를 `app.state`에 attach → 라우터 include → CORS는 origins 있을 때만. `main.py`는 `app = create_app()` thin shim.
  - `services/etlx-server/etlx_server/dependencies.py` — DI 헬퍼: `get_settings`/`get_engine`/`get_session_factory`/`get_session` (per-request 세션, 예외 시 자동 rollback).
  - `services/etlx-server/etlx_server/routers/health.py` — `/health`(외부 의존 0 liveness) + `/ready`(DB `SELECT 1` 실패 시 503 + `database: error` + 에러 클래스명 노출). Pydantic `HealthStatus`/`ReadinessStatus` response model.
  - `services/etlx-server/etlx_server/routers/meta.py` — `/version` (server/core/service/environment 4-tuple). `VersionInfo` response model.
  - `services/etlx-server/etlx_server/main.py` — thin shim(`app = create_app()`). uvicorn은 변함없이 `uvicorn etlx_server.main:app`.
- **신규 의존성** [Step 8.1]
  - `services/etlx-server/pyproject.toml`: `pydantic-settings>=2.5`. 다른 cloud SDK / 인증 라이브러리는 Step 8.2+ 에서 추가.
- **테스트 정규화** [Step 8.1]
  - `services/etlx-server/tests/test_settings.py` — Settings 6 unit (env var fallback, default development, prod hides docs, invalid env raises, lru_cache, empty CORS).
  - `services/etlx-server/tests/test_health.py` — 6 unit으로 재작성: `TestClient(create_app(settings=...))` 패턴, prod/dev docs 가시성, CORS preflight 헤더, OpenAPI 스키마에 `/health`/`/ready`/`/version` 등장.
  - `services/etlx-server/tests/db/test_health_ready.py` — testcontainers PG로 실제 readiness 검증 (`httpx.AsyncClient(ASGITransport(app))` + `app.router.lifespan_context` 직접 enter). degraded path는 closed local port로 검증(503 + detail).

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0019(FastAPI) 결정의 OOP 구현체)

### Milestone
- **Step 8.1 — API Server 부트스트랩 + OOP 재구성 완료 (2026-05-16)**: 모듈 글로벌 0 (factory 패턴 + `app.state` + `Depends`). 누적 코어 344 unit + 서버 **8 unit** (7.1 placeholder 2 + 8.1 6) + 서버 **29 it** (7.2~7.4 27 + 8.1 readiness 2) + 코어 119 it = **500 tests** all green. mypy strict 22 server src files OK, import-linter 2 contracts KEPT. 다음 슬라이스는 **Step 8.2 (인증)** — OIDC(authlib) + 로컬 fallback(bcrypt) + JWT(pyjwt RS256) + `/auth/login` `/auth/refresh` `/auth/logout` (ADR-0023 구현).

- **로컬 인증 + JWT** [Step 8.2a]
  - **분할 결정**: Step 8.2를 a(로컬 + JWT) / b(OIDC) 두 슬라이스로 분리 — 1 PR = 1 slice 원칙 유지.
  - `services/etlx-server/etlx_server/auth/` 신규 패키지 (정규화된 OOP layering):
    * `jwt_service.py` — `JwtService` (RS256, `issue_access`/`issue_refresh`/`verify(token, expected_type)`), `Claims` dataclass, `TokenType` StrEnum, `InvalidTokenError`. verify-only 인스턴스 지원(private_key=None) → 워커가 검증만 할 때 사용. `extra_claims`가 reserved claim(`sub`/`iss`/`exp`/...) 덮어쓰지 못함. `generate_rsa_keypair_pem()` 헬퍼(테스트/스크립트용).
    * `password_service.py` — `PasswordService(rounds=12)`. **bcrypt 직접 사용**(passlib는 bcrypt 4.x와 비호환 — `__about__` 누락 + 72-byte 엄격 체크). 72-byte 초과 패스워드는 SHA-256 prehash + base64 후 bcrypt에 전달.
    * `user_repository.py` — `UserRepository(session)` 에 `get_by_id` / `get_by_email`(email은 항상 lowercase). 라우터가 ORM 직접 접근하지 않게 분리.
    * `schemas.py` — Pydantic `LoginRequest`(EmailStr) / `RefreshRequest` / `TokenPair`(`token_type: "bearer"` 고정) / `CurrentUser`.
    * `current_user.py` — `get_jwt_service` / `get_password_service` (app.state DI) + `get_current_user`(HTTPBearer → JwtService.verify(ACCESS) → UserRepository.get_by_id → CurrentUser). 모든 실패 경로 401 + `WWW-Authenticate: Bearer` 일관 응답.
  - `services/etlx-server/etlx_server/routers/auth.py` — `/auth/login`(local, disabled 시 503), `/auth/refresh`(refresh 토큰만 허용 — access는 401), `/auth/logout`(stateless 204), `/auth/me`(FE bootstrap). Login은 unknown email 시 dummy bcrypt verify로 timing leak 방지. OIDC 사용자가 local login 시도하면 401(detail 동일 — leak 방지).
  - `app_factory.py` — lifespan에서 `PasswordService` + `JwtService` 인스턴스화 후 `app.state`에 attach. JwtService는 키 누락 시 None — 무관한 엔드포인트(`/health`/`/ready`/`/version`)는 키 없이도 동작.
  - `settings.py` 확장: `auth_jwt_private_key_pem`/`auth_jwt_public_key_pem`(SecretStr) / `auth_jwt_issuer` / `auth_jwt_audience` / `auth_jwt_access_ttl_seconds` / `auth_jwt_refresh_ttl_seconds` / `auth_local_enabled`.
- **의존성 변경** [Step 8.2a]
  - 추가: `bcrypt>=4.2`(passlib 제거), `pyjwt[crypto]>=2.9`(crypto extras로 RS256), `email-validator>=2.2`(Pydantic EmailStr).
  - 제거: `passlib[bcrypt]>=1.7` — bcrypt 4.x와 호환 안 됨.
  - mypy override 추가: `passlib.*` → ignore_missing_imports (남아있는 transitive 참조 대비).
- **테스트 정규화** [Step 8.2a]
  - `services/etlx-server/tests/auth/test_password_service.py` — 4 unit (round-trip, wrong password, malformed hash → False(fail-closed), `$2`로 시작하는 bcrypt marker).
  - `services/etlx-server/tests/auth/test_jwt_service.py` — 11 unit (access/refresh 발급+검증, 잘못된 type 거부, signature 검증, audience/issuer 검증, expired 거부, verify-only 인스턴스, public_key 자동 derive, extra_claims가 reserved 못 덮음 등).
  - `services/etlx-server/tests/db/test_auth_router.py` — 13 it (real PG via testcontainers). conftest의 outer-trans rollback과 lifespan의 별도 connection이 충돌하므로 `app.dependency_overrides[get_session]`로 테스트 세션을 라우터에 주입 — rollback isolation 유지. 시나리오: login(성공 / wrong password / unknown email / OIDC user / disabled local) + me(missing token / garbage token / refresh token in auth header) + refresh(success / access token rejected) + logout(204 / 401).

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0023 로컬 fallback + JWT 부분 구현체)

### Milestone
- **Step 8.2a — 로컬 인증 + JWT 완료 (2026-05-16)**: 정규화된 auth 패키지(JwtService/PasswordService/UserRepository/Schemas) + 4 엔드포인트(login/refresh/logout/me). 누적 코어 344 unit + 서버 **23 unit** + 서버 **47 it** + 코어 119 it = **533 tests** all green. mypy strict 29 server src files OK, import-linter 2 contracts KEPT. 다음 슬라이스는 **Step 8.2b (OIDC)** — authlib 기반 Google/Azure AD/Okta 공통화 + `/auth/oidc/login`+`/auth/oidc/callback` + ID token 검증 + 신규 OIDC 사용자 자동 프로비저닝.

- **OIDC SSO** [Step 8.2b]
  - `services/etlx-server/etlx_server/auth/oidc_config.py` 신규: `OidcProviderConfig`(Pydantic frozen — `name`/`display_name`/`client_id`/`client_secret: SecretStr`/`discovery_url`/`redirect_uri`/`scopes`). `auth_method` property가 provider명 → `AuthMethod` enum 매핑(google/azure/okta/github → 전용 enum, 그 외 → `OIDC_GENERIC`).
  - `services/etlx-server/etlx_server/auth/oidc_state.py` 신규: `OidcStateSigner` — `JwtService`와 동일 RSA 키쌍 재사용. `token_type=oidc_state` 단명 JWT(기본 10분)에 `provider`+`nonce`+`return_to`를 담아 callback에 전달. 서버측 세션/쿠키 0, 무상태 CSRF. `verify`는 access/refresh 토큰을 거부(token_type 검사).
  - `services/etlx-server/etlx_server/auth/oidc_service.py` 신규: `OidcService` — provider 레지스트리 + Authorization Code 플로우 드라이버. `build_authorize_url(provider, return_to=)`은 IdP authorize URL + state 토큰 반환. `handle_callback(provider, code, state)`은 state 검증 → discovery → token exchange → JWKS 페치 → ID 토큰 RS256 검증(kid 매칭, audience/issuer/email_verified/nonce 강제) → `OidcCallbackResult` 반환. 메타데이터/JWKS 메모리 캐시(프로세스 lifetime). `http_client_factory` + `nonce_factory` DI로 테스트 시 `httpx.MockTransport` 주입. 예외 분리: `UnknownProviderError`/`OidcDiscoveryError`/`OidcExchangeError`/`IdTokenError`. authlib.jose deprecation 회피를 위해 PyJWT + httpx 직접 사용.
  - `auth/user_repository.py` 확장: `provision_oidc_user(email, name, auth_method)` 신규 — 동일 provider 재로그인 시 name만 갱신(idempotent), LOCAL/다른 OIDC provider와 email 충돌 시 `OidcEmailCollisionError`(409) 발생. `auth_method=LOCAL`을 받으면 `ValueError`.
  - `auth/schemas.py` 확장: `OidcProviderSummary`(시크릿 0) / `OidcAuthorizeResponse`(`authorize_url`+`state`) / `OidcCallbackResponse`(TokenPair + `return_to`).
  - `auth/current_user.py` 확장: `get_oidc_service` Depends — `app.state.oidc_service`가 없으면 503.
  - `services/etlx-server/etlx_server/routers/oidc.py` 신규: `/auth/oidc/providers`(시크릿 0 메타) / `/auth/oidc/login?provider=&return_to=` / `/auth/oidc/callback?provider=&code=&state=`. provider명 → `AuthMethod` 매핑, 이메일 충돌 → 409, 알 수 없는 provider → 404, ID 토큰 검증 실패 → 401, IdP 통신 실패 → 502, OIDC 미설정 → 503.
  - `app_factory.py` 확장: `_build_oidc_service(settings)` 헬퍼 — provider 0개거나 키 누락 시 None 반환. lifespan에서 `app.state.oidc_service` 부착. `app.include_router(oidc_router.router)` 추가.
  - `settings.py` 확장: `auth_oidc_enabled`(default False) / `auth_oidc_providers: list[OidcProviderConfig]`(env JSON, 기본 `[]`) / `auth_oidc_state_ttl_seconds`(default 600) / `auth_oidc_http_timeout_seconds`(default 10).
- **테스트 정규화** [Step 8.2b]
  - `services/etlx-server/tests/auth/test_oidc_state.py` — 6 unit (sign/verify round-trip ± return_to, garbage 거부, signature tamper 거부, expired 거부, access 토큰 거부).
  - `services/etlx-server/tests/auth/test_oidc_service.py` — 14 unit (provider 레지스트리, 중복 provider 거부, name→AuthMethod 매핑, build_authorize_url 파라미터/state 디코드, unknown provider, discovery 실패, callback happy path + form 검증, state/provider mismatch, token endpoint 에러, nonce mismatch, email_verified=false 거부, audience mismatch 거부, id_token 누락, name 누락 시 email-local fallback). `httpx.MockTransport`로 IdP 모킹, 진짜 RSA 키로 ID 토큰 RS256 서명 → JWKS 검증 경로 전체 실행.
  - `services/etlx-server/tests/db/test_user_repository_oidc.py` — 6 it (신규 OIDC 사용자 생성/email 정규화/password_hash=None, 재로그인 시 name 갱신, idempotent, LOCAL 충돌 거부, 다른 OIDC provider 충돌 거부, `auth_method=LOCAL` ValueError).
  - `services/etlx-server/tests/db/test_auth_oidc_router.py` — 11 it (providers 리스트, 비활성 시 빈 리스트, 비활성 시 login 503, unknown provider 404, login authorize URL 형태, callback 신규 사용자 프로비저닝 + 토큰 발급 + `/auth/me` 작동, 재로그인 name 갱신, LOCAL 충돌 409, tampered state 401, state/provider mismatch 404, 미설정 OIDC callback 503).

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0023 OIDC 부분 구현체)

### Milestone
- **Step 8.2b — OIDC 완료 (2026-05-18)**: OidcService/OidcStateSigner/OidcProviderConfig + provision_oidc_user + 3 라우트(`/auth/oidc/providers`+`/login`+`/callback`). 누적 코어 344 unit + 서버 **48 unit** (23 → 48, +25) + 서버 **59 it** (47 → 59, +12, user_repository 분리 포함) + 코어 119 it = **570 tests** all green. mypy strict 코어 39 + 서버 33 src files OK, import-linter 2 contracts KEPT. 다음 슬라이스는 **Step 8.3 (RBAC)** — workspace 단위 4역할(Owner/Editor/Runner/Viewer) + `Depends(require_workspace_role(...))` + 리소스 쿼리 자동 필터.

- **RBAC (workspace 단위 4역할 + 의존성 게이트)** [Step 8.3]
  - `services/etlx-server/etlx_server/auth/rbac.py` 신규: `WorkspaceRole`을 strict hierarchy로 모델링(Owner=4>Editor=3>Runner=2>Viewer=1). `role_rank(role)` + `has_at_least(actual, required)`. Editor가 ADR-0023상 "all CRUD"이므로 trigger를 포함 — Runner는 trigger+inspect+DLQ만 가능한 Editor의 subset. 따라서 hierarchical 비교가 충분.
  - `auth/membership_repository.py` 신규: `MembershipRepository.get_role(workspace_id, user_id) -> WorkspaceRole | None`. read-only — 멤버십 CRUD는 Step 8.5.
  - `auth/workspace_repository.py` 신규: `WorkspaceRepository.get_by_id(workspace_id) -> Workspace | None`. read-only — 워크스페이스 CRUD는 Step 8.5.
  - `auth/workspace_context.py` 신규: `WorkspaceContext` frozen dataclass(user + workspace + role | None) + `get_current_workspace` Depends(path param `workspace_id`를 FastAPI가 자동 추출 → WorkspaceRepository로 404 처리 → MembershipRepository로 role 조회, 비멤버 + 비SuperAdmin 시 403) + `require_workspace_role(min_role)` factory가 closure-based checker 반환(SuperAdmin 무조건 통과, 그 외엔 `has_at_least(ctx.role, min_role)` 강제, 실패 시 403 + role 정보 leak 없이 "requires X or higher" 메시지).
  - `auth/schemas.py` 확장: `WorkspaceSummary`(id/name/slug/color_hex/`role: str | None` — SuperAdmin bypass 표현).
  - `services/etlx-server/etlx_server/routers/workspaces.py` 신규: `GET /workspaces/{workspace_id}` — `require_workspace_role(WorkspaceRole.VIEWER)` 게이트. ruff B008 회피 위해 모듈 레벨 `_require_viewer = Depends(...)` 싱글톤. Step 8.5가 같은 dependency stack에 CRUD/list/멤버 관리 확장 예정.
  - `app_factory.py`에 `app.include_router(workspaces_router.router)` 추가.
- **테스트 정규화** [Step 8.3]
  - `services/etlx-server/tests/auth/test_rbac.py` — 9 unit (rank monotonicity, 각 role의 self-satisfy, Owner는 모든 요구 만족, Viewer는 Viewer만, Runner/Editor의 정확한 경계).
  - `services/etlx-server/tests/auth/test_workspace_context.py` — 6 unit (`require_workspace_role` checker를 직접 호출: SuperAdmin without membership / SuperAdmin as Viewer requesting Owner / exact role / higher role / lower role 403 / Viewer-on-Viewer passthrough).
  - `services/etlx-server/tests/db/test_membership_repository.py` — 4 it (member role 조회, 비멤버 None, 미존재 workspace None, 동일 사용자의 다중 워크스페이스 role 격리).
  - `services/etlx-server/tests/db/test_workspaces_router.py` — 7 it (실제 `/auth/login`으로 JWT 발급 후 `GET /workspaces/{id}`: 401 unauthenticated / 404 unknown / 403 non-member / 200 Viewer with role echo / 200 Owner / 200 SuperAdmin with role=None / 422 malformed UUID).

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0023 RBAC 부분 구현체)

### Milestone
- **Step 8.3 — RBAC 완료 (2026-05-18)**: 4역할 strict hierarchy + WorkspaceContext + `Depends(require_workspace_role(...))` + 첫 RBAC-protected 라우터(`GET /workspaces/{id}`). 누적 코어 344 unit + 서버 **63 unit** (48 → 63, +15) + 서버 **70 it** (59 → 70, +11) + 코어 119 it = **596 tests** all green. mypy strict 코어 39 + 서버 38 src files OK, import-linter 2 contracts KEPT. 워크스페이스 단위 격리(쿼리 자동 필터)는 Step 8.5의 도메인 CRUD가 들어올 때 함께 구현 — 8.3 단독으로는 적용 대상이 없어 자연 이관. 다음 슬라이스는 **Step 8.4 (Audit log)** — mutating 요청 미들웨어 + actor/before/after JSON 기록 + `/audit?resource=&actor=` 조회.

- **Audit log infra + `/audit` query** [Step 8.4]
  - `services/etlx-server/etlx_server/audit/` 신규 패키지 (모든 모듈이 단일 책임 + OOP-normalized):
    * `service.py` — `AuditService(session, *, request_meta=None)` 와 `RequestMeta(ip, user_agent)` frozen dataclass. `record(*, action, resource_type, actor_user_id, workspace_id=None, resource_id=None, before=None, after=None)` 가 `AuditLog` row를 add+flush 후 반환. **commit 안 함** — 호출자(보통 라우터)가 트랜잭션 boundary 소유. 비즈니스 mutation이 rollback되면 audit row도 함께 rollback되어 false-positive 0 보장 (테스트로 검증).
    * `repository.py` — `AuditLogRepository.query(workspace_id, actor_user_id, resource_type, resource_id, limit=50, offset=0)`. `created_at desc` 정렬, offset 페이지네이션 (Step 11에서 cursor pagination으로 격상 가능).
    * `middleware.py` — `AuditRequestMetaMiddleware(BaseHTTPMiddleware)`. `request.client.host` + `request.headers["user-agent"]`를 `request.state.audit_meta`(RequestMeta)에 부착. `X-Forwarded-For` reverse-proxy 처리는 Step 11(Operability)로 위임 — 배포 토폴로지 의존.
    * `dependencies.py` — `get_audit_service` Depends. `request.state.audit_meta`가 없거나 잘못된 타입이면 빈 `RequestMeta()` fallback (middleware 없이 직접 호출되는 경로 안전).
    * `__init__.py` — public re-export + 사용 패턴 docstring.
  - `services/etlx-server/etlx_server/routers/audit.py` 신규: `GET /audit` 이중 ACL. `workspace_id` 미지정 시 SuperAdmin 필수 (그 외 403 with detail "cross-workspace audit query requires SuperAdmin"). 지정 시 SuperAdmin이거나 `MembershipRepository.get_role`이 None이 아니어야 함(비멤버는 미존재 workspace UUID에도 403 — workspace 존재 여부 leak 방지). 필터: `actor_user_id` / `resource_type`(≤64) / `resource_id`(≤64). `limit` 1~200 강제(422), `offset` ≥0. 응답은 `list[AuditLogEntry]` (Pydantic, `from_attributes=True` ORM mapping).
  - `auth/schemas.py` 확장: `AuditLogEntry` (id/actor/workspace/action/resource_type/resource_id/before_json/after_json/ip/user_agent/created_at).
  - `app_factory.py` 확장: `app.add_middleware(AuditRequestMetaMiddleware)` (CORS보다 앞 — 모든 핸들러 진입 전에 부착) + `app.include_router(audit_router.router)`.
- **테스트 정규화** [Step 8.4]
  - `services/etlx-server/tests/audit/test_middleware.py` — 2 unit. 진짜 Starlette 앱에 `TestClient` 부착해서 production 경로 그대로 실행 — `request.state.audit_meta`가 `RequestMeta` 인스턴스로 들어오고 UA 헤더가 전달되는지 확인.
  - `services/etlx-server/tests/db/test_audit_service.py` — 4 it. record + roundtrip / RequestMeta 없이 NULL fields / actor·workspace 모두 NULL(시스템 이벤트) / rollback 시 audit row 함께 사라지는지 검증.
  - `services/etlx-server/tests/db/test_audit_repository.py` — 5 it. workspace/actor/resource_type+id 필터 + `created_at desc` 정렬(record 사이 5ms sleep으로 microsecond 분리) + limit/offset 페이지네이션.
  - `services/etlx-server/tests/db/test_audit_router.py` — 8 it. 401 unauthenticated / 403 비admin global / 200 admin global / 403 비멤버 workspace / 200 Viewer 멤버 workspace(자신 workspace 행만) / 200 multi-filter(workspace + resource_type) / 422 limit=0 / 403 미존재 workspace UUID에 비admin(enumeration leak 방지).

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0023 §9 audit 부분 구현체)

### Milestone
- **Step 8.4 — Audit infra + `/audit` query 완료 (2026-05-18)**: AuditService + AuditLogRepository + RequestMeta + AuditRequestMetaMiddleware + `GET /audit` 이중 ACL. 누적 코어 344 unit + 서버 **65 unit** (63 → 65, +2) + 서버 **87 it** (70 → 87, +17) + 코어 119 it = **615 tests** all green. mypy strict 코어 39 + 서버 44 src files OK, import-linter 2 contracts KEPT. Mutating endpoint의 `audit.record(...)` 호출은 도메인 CRUD가 들어오는 Step 8.5에서 일괄 wire — 8.4 단독 적용 대상이 없어 infra + query API만 ship한 것이 의도. 다음 슬라이스는 **Step 8.5 (CRUD 엔드포인트)** — `/connections`/`/pipelines`/`/schedules`/`/runs` 도메인 라우터 + 각 mutation에 audit.record + workspace_id 자동 필터.

- **Workspaces CRUD + audit integration** [Step 8.5a]
  - **분할 결정**: Step 8.5 도메인이 너무 넓어(workspaces+members+connections+pipelines+schedules+runs) 1 PR = 1 slice 원칙 유지를 위해 a~e 5개 슬라이스로 분할.
  - `auth/workspace_repository.py` 확장: 기존 `get_by_id` + `list_for_user`(include_all 옵션) 추가, mutating 메서드 `create`(워크스페이스 + Owner 멤버십 원자적 insert) / `update`(`_ALLOWED_UPDATE_FIELDS` 화이트리스트 + IntegrityError → WorkspaceSlugTakenError) / `delete`(FK cascade로 memberships 자동 제거). `snapshot(workspace, fields=...)` static helper로 audit before/after JSON 생성. 모든 mutation은 호출자 트랜잭션 안에서만 동작 — repo는 commit 안 함.
  - `WorkspaceSlugTakenError` 신규 예외 — `sqlalchemy.exc.IntegrityError`를 라우터 입장에서 안정적으로 다룰 수 있는 도메인 예외로 변환.
  - `auth/schemas.py` 확장: `WorkspaceCreateRequest` (name 1-255 / slug regex `^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$` / color_hex `^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$`) + `WorkspaceUpdateRequest`(PATCH 스타일 — 모든 필드 optional, `as_field_dict()`로 `exclude_unset=True` dump).
  - `routers/workspaces.py` 4 신규 엔드포인트:
    * `POST /workspaces` (201, 인증된 모든 사용자 — 호출자가 자동 Owner). 409 slug 중복.
    * `GET /workspaces` (caller가 멤버인 워크스페이스 목록 + SuperAdmin은 전체). N+1 회피 위해 role은 응답에 포함 안 함 — 자세한 role은 `/{id}` 호출.
    * `PATCH /workspaces/{id}` (Editor+ via `_require_editor` Depends 싱글톤). 빈 body는 400. slug 중복 409.
    * `DELETE /workspaces/{id}` (Owner only via `_require_owner` 싱글톤). 204. **audit row를 delete 전에 먼저 record** — `audit_log.workspace_id` FK가 `ON DELETE SET NULL`이라 row는 살아남고 workspace_id만 NULL로 떨어져 forensics에 사용 가능.
  - 모든 mutation이 `audit.record(action=workspace.create/update/delete, resource_type='workspace', resource_id=str(ws.id), before=..., after=...)` 호출 후 `await session.commit()`. 비즈니스 mutation 실패 시 audit row 함께 rollback — Step 8.4 설계 의도가 production 코드로 처음 검증됨.
- **테스트 정규화** [Step 8.5a]
  - `services/etlx-server/tests/db/test_workspaces_crud.py` — 11 신규 it. 패턴: `_audit_rows_for(session, ws_id)`가 `await session.commit()` 후 audit 테이블을 직접 select — conftest의 outer-trans rollback이 router의 `commit()`을 savepoint release로 처리하기 때문에 동일 session identity map만으로는 router 쓴 데이터가 안 보임, 명시적 commit + 재조회로 봐야 함.
  - 시나리오: POST happy path(201 + Owner 멤버십 insert + audit.create row with after JSON) / POST duplicate slug 409 / POST invalid slug 422 / GET list returns only caller's memberships / GET list SuperAdmin sees all / PATCH editor allowed + audit.update with before·after / PATCH runner 403 / PATCH empty body 400 / PATCH slug collision 409 / DELETE owner + audit.delete + cascade memberships 제거 + workspace_id SET NULL 검증 / DELETE editor 403.

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0023 §5 RBAC + §9 audit 적용)

### Milestone
- **Step 8.5a — Workspaces CRUD 완료 (2026-05-18)**: 4 신규 endpoint + Owner 자동 멤버십 + audit 페어링 + Step 8.4 audit infra 첫 production 호출처. 누적 코어 344 unit + 서버 **65 unit** + 서버 **98 it** (87 → 98, +11) + 코어 119 it = **626 tests** all green. mypy strict 코어 39 + 서버 44 src files OK, import-linter 2 contracts KEPT. 다음 슬라이스는 **Step 8.5b (Membership management)** — `/workspaces/{id}/memberships` list/add/change-role/remove. 마지막 Owner 제거 보호(워크스페이스 orphan 방지) 포함.

- **Membership management** [Step 8.5b]
  - `auth/membership_repository.py` 확장: 기존 `get_role` 외에 `get`(full row), `list_for_workspace`(User join → 이메일 정렬, FE 표시용), `count_owners`(워크스페이스 단위), `add`(UNIQUE 위반 → `MembershipExistsError`), `update_role`(마지막 Owner 데모트 시 `LastOwnerError`), `remove`(마지막 Owner 제거 시 `LastOwnerError`). 모든 mutation은 호출자 트랜잭션 안에서 동작 — repo는 commit 하지 않음.
  - 신규 도메인 예외 2종: `MembershipExistsError`(라우터 → 409), `LastOwnerError`(라우터 → 409). DB 무결성 + 비즈니스 invariant를 라우터가 안전하게 다룰 수 있게 추상화.
  - `auth/schemas.py` 확장: `MembershipSummary`(id/user_id/email/name/role), `MembershipCreateRequest`(email + role — UUID 대신 이메일로 초대, role은 `Literal["owner","editor","runner","viewer"]`로 422 강제), `MembershipUpdateRequest`(role only).
  - `services/etlx-server/etlx_server/routers/memberships.py` 신규: 4 엔드포인트 (`/workspaces/{ws}/memberships` GET Viewer+ / POST Owner; `/{user_id}` PATCH Owner / DELETE Owner). 모듈 레벨 `_require_viewer`/`_require_owner` Depends 싱글톤. POST는 `UserRepository.get_by_email`로 사용자 조회(unknown → 404), `MembershipRepository.add`로 insert. PATCH는 `update_role`로 LastOwnerError 처리. DELETE는 row 삭제 *전*에 `membership.id`를 Python 변수에 캡처하여 삭제 후 audit row의 `resource_id`로 사용. 각 mutation이 `audit.record(action=membership.{create,update,delete}, resource_type='membership', resource_id=str(membership.id))` + `session.commit`.
  - **마지막 Owner 보호 의도**: 자기 자신 mutation에도 적용 — 다른 Owner가 없는 상태에서 자기 데모트/제거하면 409. 워크스페이스가 orphan 되어 아무도 관리할 수 없는 상태를 원천 차단.
  - `app_factory.py`: `app.include_router(memberships_router.router)` 추가.
- **테스트 정규화** [Step 8.5b]
  - `services/etlx-server/tests/db/test_memberships_router.py` — 15 신규 it. 시나리오:
    * list: 멤버는 모든 멤버 목록 조회 / 비멤버 403
    * POST: owner adds editor + audit.create / non-owner 403 / unknown email 404 / dup 409 / invalid role 422
    * PATCH: role 변경 + audit before·after / last-owner demote 409 / 다른 Owner 존재 시 자기 demote 허용 / unknown membership 404
    * DELETE: happy path + audit row in NULL workspace_id (FK SET NULL) / last-owner remove 409 + 행 그대로 유지 / 다른 Owner 시 자기 제거 허용 / unknown 404

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0023 §5 RBAC + §9 audit 적용)

### Milestone
- **Step 8.5b — Membership management 완료 (2026-05-18)**: 4 신규 endpoint + 마지막 Owner 보호 + audit 페어링. 누적 코어 344 unit + 서버 **65 unit** + 서버 **113 it** (98 → 113, +15) + 코어 119 it = **641 tests** all green. mypy strict 코어 39 + 서버 45 src files OK, import-linter 2 contracts KEPT. 다음 슬라이스는 **Step 8.5c (Connections CRUD)** — workspace-scoped `/connections` + 시크릿은 Secret backend(Vault/AWS SM/GCP SM/File)에 즉시 위임, DB에는 ref만 + `POST /{conn_id}/test`.

- **Connections CRUD + secret backend integration + test endpoint** [Step 8.5c]
  - 새 `services/etlx-server/etlx_server/connections/` 패키지 (단일 책임, OOP-normalized):
    * `repository.py` — `ConnectionRepository.list_for_workspace`/`get`/`add`(pre-minted UUID, UNIQUE → `ConnectionNameTakenError`)/`update`(필드 화이트리스트 + IntegrityError → `ConnectionNameTakenError`)/`delete` + `snapshot()` 헬퍼.
    * `secrets.py` — `SecretWalker.sanitize(config, secrets, ws_id, conn_id)`가 `{"$secret": "key"}` sentinel을 `${SECRET:etlx/{ws}/{conn}/{key}}` placeholder로 치환 + `(sanitized, refs)` 반환. orphan 마커(`config`에 있으나 `secrets`에 없음) → `SecretMarkerError`, orphan 값(반대) → 같은 예외. `write_secrets`는 backend.set 호출 — read-only backend(`EnvSecretBackend`) NotImplementedError → `SecretBackendReadOnlyError`. `delete_paths`는 best-effort.
    * `tester.py` — `ConnectionTester(backend).run(connection)`가 `${SECRET:...}` placeholder를 backend.get으로 resolve → `ConnectorRegistry.get(type)` (RegistryError/ConfigError catch → 친절한 에러) → `asyncio.to_thread(_run_blocking_health_check, klass, options)`로 sync connect/health_check/close 실행. 결과는 `ConnectionTestOutcome(ok, error)`.
  - `routers/connections.py` 6 신규 엔드포인트 (`/workspaces/{ws}/connections` 접두):
    * `GET ""` Viewer+, `GET "/{id}"` Viewer+
    * `POST ""` Editor+ (201) — UUID 사전 mint → walker sanitize → backend.set → repo.add → audit.create. DB 실패 시 방금 쓴 backend secret을 best-effort 정리.
    * `PATCH "/{id}"` Editor+ — name only 또는 config+secrets 동기화. 빈 body 400. `secrets` without `config` 400. 새 config에 없어진 path는 backend.delete, 새 path는 backend.set. 실패 시 새로 쓴 path만 정리.
    * `DELETE "/{id}"` Editor+ (204) — row 삭제 후 backend.delete_paths(old refs). audit row 보존.
    * `POST "/{id}/test"` Runner+ — 위 `ConnectionTester` 호출, `{ok, error}` 반환.
  - **ADR-0017 §6 완전 준수**: 평문은 절대 DB에 안 저장, 응답에도 안 노출(placeholder만). connection UUID를 backend write 전에 mint해서 경로가 row 평생 안정 — rename에도 secret 안 옮겨감.
  - `auth/schemas.py`: `ConnectionSummary`(workspace_id/name/type/config_json/secret_refs), `ConnectionCreateRequest`(name + type + config + secrets), `ConnectionUpdateRequest`(모든 필드 optional — PATCH semantics), `ConnectionTestResult(ok, error)`.
  - `settings.py` 확장: `secret_backend`(default `env`), `secret_backend_file_path`(file backend용).
  - `app_factory._build_secret_backend(settings)`가 lifespan에서 `get_secret_backend(name, **opts)` 호출, `app.state.secret_backend` attach. `dependencies.get_secret_backend_dep` Depends.
  - `app.include_router(connections_router.router)`.
- **테스트 정규화** [Step 8.5c]
  - `services/etlx-server/tests/db/test_connections_router.py` — 18 신규 it. `_build_app(session, backend=...)`가 `StaticSecretBackend()`(write 가능) 또는 `EnvSecretBackend()`(read-only 403 검증) 주입. SQLite `:memory:`로 `POST /test` happy path 검증 — 외부 인프라 0.
  - 시나리오: POST happy(secret + placeholder 형식 + backend path 검증 + audit.after_json에 secret_refs) / orphan marker 422 / orphan value 422 / EnvSecretBackend 503 / dup 409 / Viewer 403 / list workspace 단위 / cross-ws 404 / PATCH name only / PATCH secret rotation(old path backend.delete + new path backend.set) / 빈 PATCH 400 / secrets-without-config 400 / DELETE row + backend + audit / DELETE 404 / test endpoint sqlite ok=True / unknown connector type ok=False + RegistryError 메시지 / secret resolve 후 test ok=True / Viewer test 403.

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0017 §6 + ADR-0023 §5/§9 적용)

### Milestone
- **Step 8.5c — Connections CRUD + test 완료 (2026-05-18)**: 6 신규 endpoint + Secret backend 완전 통합 + 평문 0 + `POST /test`로 핵심 connector smoke 검증. 누적 코어 344 unit + 서버 **65 unit** + 서버 **131 it** (113 → 131, +18) + 코어 119 it = **659 tests** all green. mypy strict 코어 39 + 서버 50 src files OK, import-linter 2 contracts KEPT. 다음 슬라이스는 **Step 8.5d (Pipelines CRUD + 버전 관리)** — `/workspaces/{ws}/pipelines` + `GET /{pid}/versions`. Step 7.3 yaml_sync의 PipelineVersion idempotency 패턴(config_json diff 시에만 새 version 생성) 재사용.

- **Pipelines CRUD + immutable version history** [Step 8.5d]
  - 새 `services/etlx-server/etlx_server/pipelines/` 패키지:
    * `repository.py` — `PipelineRepository` with `list_for_workspace`/`get`/`get_current_version`/`list_versions`/`add`(Pipeline + PipelineVersion v1 원자적 insert, UNIQUE → `PipelineNameTakenError`)/`update_metadata`(name/description 화이트리스트, IntegrityError → 같은 예외)/`ensure_version`(Step 7.3 yaml_sync와 동일한 **idempotency** — `current.config_json == new` 면 no-op, 다르면 `current.is_current=False` + 신규 `version=current.version+1` insert)/`delete`(FK cascade로 versions + schedules 제거) + `snapshot(pipeline, current)` 헬퍼.
    * `__init__.py` — public re-export + 사용 패턴 docstring.
  - `routers/pipelines.py` 6 신규 엔드포인트:
    * `GET ""` Viewer+ — 각 Pipeline에 대해 current_version + config_json flatten.
    * `POST ""` Editor+ (201) — `_validate_config(config, name)`가 `body.name`을 `config["name"]`로 주입 후 core의 `PipelineConfig.model_validate` 통과 → canonical JSON dump → repo.add → audit.create.
    * `GET "/{pid}"` Viewer+ — 단건 + current version flatten.
    * `PATCH "/{pid}"` Editor+ — `name`/`description`/`config` 모두 optional. metadata만 보내면 버전 안 올라감. config 보내면 ensure_version에서 비교 → `version_created` 플래그가 audit.after_json에 들어가 forensics에서 metadata-only vs config-edit 구분 가능. 빈 body 400. name 충돌 409.
    * `DELETE "/{pid}"` Editor+ (204) — FK cascade로 versions + schedules 제거 + audit.delete.
    * `GET "/{pid}/versions"` Viewer+ — 오름차순 version + is_current 플래그.
  - 각 mutation은 audit.record(pipeline.{create,update,delete}) + session.commit. PATCH의 audit `after_json`에 `version_created: bool` 포함 — Step 7.3 idempotency 패턴을 audit 레이어로 노출.
  - **PipelineConfig 검증**: core의 `PipelineConfig.model_validate`로 source/sink/transforms 등 구조 검증. Pydantic `ValidationError` → 422 + `e.errors()` 체인. config에 name 안 써도 됨 — 서버가 body.name 주입(canonical dump는 YAML re-export 가능한 완전한 형태).
  - `auth/schemas.py` 확장: `PipelineSummary`(id/ws_id/name/description/current_version/current_config_json), `PipelineCreateRequest`(name/description?/config), `PipelineUpdateRequest`(전부 optional — PATCH), `PipelineVersionEntry`(id/version/is_current/config_json/created_at, `from_attributes=True`).
  - `app_factory.py`: `app.include_router(pipelines_router.router)`.
- **테스트 정규화** [Step 8.5d]
  - `services/etlx-server/tests/db/test_pipelines_router.py` — 14 신규 it. 핵심: **버전 idempotency 양 분기 직접 검증** — `test_patch_config_diff_creates_new_version`은 prior `is_current=False` + 신규 v2 `is_current=True` 검증 + `audit.after_json.version_created=True` 확인 / `test_patch_config_identical_is_no_op`은 같은 config 재제출 시 v1 그대로 + audit.after_json.version_created=False 확인. POST happy + v1 + audit + 서버가 `config["name"]` 주입 / invalid config 422 / dup 409 / Viewer 403 / list with current_version / 단건 404 / PATCH metadata only no bump / 빈 400 / name collision 409 / DELETE cascade + audit / GET /versions multi-version + is_current / versions 404.

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — Step 7.3 idempotency 패턴 재사용)

### Milestone
- **Step 8.5d — Pipelines CRUD + 버전 관리 완료 (2026-05-18)**: 6 신규 endpoint + immutable version history + ensure_version idempotency + version_created audit 플래그 + core PipelineConfig 구조 검증 + 서버측 name 주입. 누적 코어 344 unit + 서버 **65 unit** + 서버 **145 it** (131 → 145, +14) + 코어 119 it = **673 tests** all green. mypy strict 코어 39 + 서버 53 src files OK, import-linter 2 contracts KEPT. 다음 슬라이스는 **Step 8.5e (Schedules CRUD + 활성화 토글 + Runs 읽기 전용)** — 8.5의 마지막 슬라이스. 실제 실행 트리거링은 Step 9의 worker queue.

- **Schedules CRUD + Runs read-only** [Step 8.5e]
  - 새 두 패키지:
    * `services/etlx-server/etlx_server/schedules/repository.py` — `ScheduleRepository` (list_for_pipeline/get/add/update/delete + snapshot 헬퍼) + `InvalidCronError` + `validate_cron_for_mode(mode, cron_expr)`. **mode-aware cron 검증**: batch는 `cron_expr` 필수, stream은 NULL 허용(연속 활성); 둘 다 supplied 시 `croniter.is_valid`로 검증. 워커(Step 9)가 다음 firing time 계산에 같은 라이브러리 사용 — "저장된 schedule = 워커가 실행할 schedule" 보장. update는 화이트리스트(`name`/`cron_expr`/`is_active`/`config_overrides`) — unknown 필드 즉시 ValueError로 거부. **mode immutable** — UpdateRequest에 mode 필드 없음, batch↔stream 전환은 삭제+재생성.
    * `services/etlx-server/etlx_server/runs/repository.py` — `RunRepository` (list_for_workspace/get/list_logs/list_metrics). **read-only** — write 메서드 없음. 워커(Step 9) 전용. 필터는 status/pipeline_id/schedule_id + limit(1~500, default 50)/offset; 정렬은 created_at desc.
  - `routers/schedules.py` 6 신규 엔드포인트 (`/workspaces/{ws}/pipelines/{pid}/schedules` 하위에 nest — schema가 1-pipeline-many-schedules + worker가 PipelineVersion에 policy attach):
    * `GET ""` Viewer+ — name 정렬.
    * `POST ""` Editor+ (201) — cron 검증, 422 on InvalidCronError.
    * `GET "/{sid}"` Viewer+.
    * `PATCH "/{sid}"` Editor+ — 부분 갱신, cron 재검증, 빈 body 400, mode 변경 시 ValueError → 500 (Pydantic이 이미 거부).
    * `DELETE "/{sid}"` Editor+ (204) — 행 삭제 후 audit (resource_id에 cached UUID 사용).
    * `POST "/{sid}/toggle"` Editor+ — `is_active` flip 후 audit.
    * 매 mutation `audit.record(schedule.{create,update,delete,toggle})` + commit.
    * **Pipeline-workspace 경계**: `_resolve_pipeline_or_404`가 `pipeline.workspace_id == ctx.workspace.id`를 매 호출 검증. 다른 워크스페이스 pipeline id 또는 다른 pipeline 하위 schedule id 모두 404로 collapse (enumeration leak 방지).
  - `routers/runs.py` 4 read-only 엔드포인트 (`/workspaces/{ws}/runs` 하위):
    * `GET ""` Viewer+ — status/pipeline_id/schedule_id 필터 + limit/offset 페이지네이션.
    * `GET "/{rid}"` Viewer+ — `RunDetail` = base + worker_id + heartbeat_at + error_message + result_json.
    * `GET "/{rid}/logs"` Viewer+ — RunLog 시계열 ts 정렬, limit 1~1000 (default 200).
    * `GET "/{rid}/metrics"` Viewer+ — RunMetric recorded_at 정렬.
    * **No mutation 엔드포인트** — runs는 워커(Step 9) 전용 쓰기 도메인. POST 시도는 404 (route 없음), PATCH/DELETE 시도는 405.
    * **No audit on runs** — read-only이므로 audit 없음. runs 자체 + run_logs 시계열이 워커의 audit trail.
    * **워크스페이스 boundary** — 단일 run 조회 시 항상 `workspace_id` 함께 매칭; logs/metrics 조회도 먼저 run 조회를 통해 boundary 보장. 다른 ws run UUID 추측해도 404.
  - `auth/schemas.py` 확장: `ScheduleSummary`/`ScheduleCreateRequest`(mode literal `"batch"|"stream"`, cron_expr optional, is_active default True, config_overrides JSONB)/`ScheduleUpdateRequest`(전부 optional, mode 없음 — immutable)/`RunSummary`(ORM `from_attributes=True`)/`RunDetail`(extends RunSummary + worker bookkeeping)/`RunLogEntry`/`RunMetricEntry`.
  - `app_factory.py`: schedules + runs router 두 개 추가.
  - **Schedule name 비유일** — Pipelines/Connections/Workspaces는 YAML(Step 7.3 yaml_sync)에서 name으로 addressing이라 unique이지만 schedule은 id로만 — 이번 슬라이스에서 unique constraint를 schema에 추가하지 않고 가벼운 자유로 유지. 필요해지면 후속 ADR + migration.
- **테스트 정규화** [Step 8.5e]
  - `services/etlx-server/tests/db/test_schedules_router.py` — 15 신규 it. cron 검증 매트릭스 (batch+valid / stream+null / batch missing 422 / 잘못된 cron 422) + Viewer 403 + cross-ws pipeline 404 + list 정렬 + single 404 + PATCH cron + audit before·after / PATCH invalid cron 422 / 빈 PATCH 400 + DELETE + audit + toggle ON↔OFF (2번) + audit 행 3개 검증 + repository.update unknown 필드 ValueError + 다른 pipeline 하위 schedule 404.
  - `services/etlx-server/tests/db/test_runs_router.py` — 11 신규 it. list happy + status 필터 + pipeline_id 필터 + 워크스페이스 boundary (list + GET 둘 다) + limit/offset 페이지네이션 + RunDetail 단건 + 404 + logs 시계열 정렬 + metrics + 다른 ws run logs 404 + **mutation 메서드 거부** (POST 404 / PATCH 405 / DELETE 405).

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0023 §5/§9 + ADR-0021 worker queue 의존성 분리 적용)

### Milestone
- **Step 8.5e — Schedules CRUD + Runs read-only 완료 (2026-05-18)**: 6 schedule endpoint + 4 run read-only endpoint + croniter mode-aware 검증 + workspace/pipeline boundary 검증 + worker write 도메인 격리. 누적 코어 344 unit + 서버 **65 unit** + 서버 **171 it** (145 → 171, +26) + 코어 119 it = **699 tests** all green. mypy strict 코어 39 + 서버 55 src files OK, import-linter 2 contracts KEPT. **Step 8.5 (CRUD 도메인) 완전 종료** — workspaces / memberships / connections / pipelines / schedules / runs read 모두 ship. 다음은 **Step 8.6 (Action 엔드포인트)** — `POST /pipelines/{id}/dry-run` / `POST /pipelines/{id}/trigger` (run 큐잉) / `POST /runs/{id}/retry` / `GET /runs/{id}/logs` streaming. Step 9 worker queue와 짝지어 동작 — 8.6은 큐 enqueue만, 실제 execute는 Step 9.

- **Pipeline + Run action endpoints** [Step 8.6]
  - 새 `services/etlx-server/etlx_server/pipelines/dry_run.py`:
    * `DryRunService(session, backend).run(pipeline, version, check_health=True)` — 워크스페이스 connection 이름들 resolve → `${SECRET:<path>}` placeholder를 backend로 풀이 → `ConnectionConfig.model_validate` → 코어 `build_connector`/`build_pipeline` → connector instance를 그대로 보관 → 병렬 `asyncio.to_thread(_blocking_health_check, connector)`로 connect/health/close. 단일 connector 실패 시 per-row 보고(`ConnectorCheck`); pipeline build 실패는 top-level errors. `DryRunResult{ok, errors, connectors}`.
    * **첫 100 record 샘플은 의도적으로 미포함** — bounded read는 retry/timeout/back-pressure 정책과 묶여야 안전. 후속 UI 요구사항 생기면 worker(Step 9)의 `stop_after_records=N` 위에 얹는 방식이 옳다.
  - `routers/pipelines.py` 2 신규:
    * `POST /workspaces/{ws}/pipelines/{pid}/dry-run` Runner+ — read-only 검증, audit 없음. `_load_pipeline_and_current` 헬퍼(404 / 409 no current version).
    * `POST /workspaces/{ws}/pipelines/{pid}/trigger` Runner+ — 202 Accepted, `RunRepository.add_manual`로 pending row enqueue(`schedule_id=NULL`, `triggered_by_user_id=ctx.user.id`), audit `run.trigger`(source=manual + version). 현재 버전 없는 파이프라인은 409. `RunTriggerRequest` body는 현재 빈 객체 — `config_overrides` / `scheduled_at` 같은 후속 필드를 위해 자리만 잡아둔 형태.
  - `routers/runs.py` 2 신규:
    * `POST /workspaces/{ws}/runs/{rid}/retry` Runner+ — 202 Accepted. `RunRepository.add_retry`가 failed/cancelled만 허용(`RunNotRetryableError` → 409), 새 row가 원본의 `pipeline_version_id` + `schedule_id` 상속(retry = "그 시도 다시", trigger와 구분), `result_json={"retry_of": original.id}` lineage, **원본 row는 절대 손 안 댐**. 워커가 status 전이의 단일 writer 원칙 유지. audit `run.retry`.
    * `GET /workspaces/{ws}/runs/{rid}/logs/stream` Viewer+ — Server-Sent Events. `StreamingResponse` media_type=`text/event-stream`. `get_session_factory` Depends로 ~500ms마다 fresh `AsyncSession`(긴 트랜잭션 hold 방지) → 새 run_logs를 frame당 `event: log\ndata: {RunLogEntry JSON}\n\n`. run terminal(succeeded/failed/cancelled) + 2s idle 또는 `request.is_disconnected()` 시 graceful close. `Cache-Control: no-cache` + `X-Accel-Buffering: no`로 Nginx/CDN 버퍼링 방지. boundary check는 시작 시 한 번 + poll 루프에서 매번(다른 ws run UUID 추측 → 404 → 스트림 시작도 안 됨).
  - `runs/repository.py` 확장: `add_manual(pipeline, version, triggered_by_user_id, result_json=None)` + `add_retry(original, *, triggered_by_user_id)` + `RunNotRetryableError`. write 메서드 둘 다 "새 pending row를 만들 뿐 기존 row는 손 안 댐" — 워커 단일 writer 원칙 보존.
  - `auth/schemas.py` 확장: `DryRunConnectorCheck`(name/type/ok/error), `DryRunResponse`(ok/errors/connectors), `RunTriggerRequest`(현재 빈 — 후속 필드 placeholder).
- **테스트 정규화** [Step 8.6]
  - `services/etlx-server/tests/db/test_pipeline_actions_router.py` — 11 신규 it. dry-run happy(sqlite :memory:로 실제 connect/health_check 검증) / 누락 connection name / unknown connector type per-row 보고 / `${SECRET:...}` placeholder를 `StaticSecretBackend`로 풀이해서 happy / Viewer 403 / 404. trigger happy + 202 + Run row 직접 검증 + audit `run.trigger` after_json{source,version} / Viewer 403 / 현재 버전 없는 pipeline 409 / 404 / cross-workspace pipeline 404.
  - `services/etlx-server/tests/db/test_run_actions_router.py` — 8 신규 it. retry happy + 202 + 원본 row unchanged + 새 row의 `result_json.retry_of` + audit `run.retry` / succeeded 409 / running 409 / cancelled OK(retry 가능) / Viewer 403 / cross-workspace 404. SSE stream은 terminal run에 대해 seed된 3 log row를 모두 frame으로 emit + terminal+idle 자동 close(8s timeout으로 bound), unknown run 404. 스트림 테스트는 `app.dependency_overrides[get_session_factory]`로 test session을 재사용하는 `_StubFactory`.

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0021 worker queue + ADR-0023 §5 RBAC 적용)

### Milestone
- **Step 8.6 — Action 엔드포인트 완료 (2026-05-18)**: 4 신규 endpoint (dry-run / trigger / retry / SSE logs stream) + worker 단일 writer 원칙 유지(retry는 새 row 생성, 원본 unchanged). 누적 코어 344 unit + 서버 **65 unit** + 서버 **190 it** (171 → 190, +19) + 코어 119 it = **718 tests** all green. mypy strict 코어 39 + 서버 60 src files OK, import-linter 2 contracts KEPT. **Step 8 (API Server)의 코어 surface 사실상 종료** — auth / RBAC / audit / CRUD / actions / SSE 모두 ship, 남은 8.7은 end-to-end 시나리오 테스트 추가. 다음은 **Step 8.7 (시나리오 테스트)** 또는 **Step 9 (Execution Engine)** — 워커가 runs 큐 claim → 코어 `run_pipeline_yaml` 호출 + heartbeat + zombie 회수.

- **End-to-end scenario tests** [Step 8.7]
  - 새 `services/etlx-server/tests/db/test_scenario_e2e.py` — 3 e2e 시나리오, 모두 testcontainers PG 위에서 outer-trans rollback 격리 유지:
    * **full happy path**: login → POST workspace(Owner 자동 멤버십) → POST 2 connections(src/dst, sqlite :memory:로 dry-run health_check이 실제로 connect/close 검증) → POST pipeline + POST schedule(batch + cron) → POST dry-run(ok=True + connector 둘 다 ok) → POST trigger(202, status=pending, worker_id/heartbeat_at NULL — 워커 미존재 확인) → GET runs list(triggered run 포함) + single → **GET audit?workspace_id={ws}로 정확한 순서 검증** `workspace.create → connection.create → connection.create → pipeline.create → schedule.create → run.trigger` (dry-run·GET는 audit 없음 확인). 6 mutation 모두가 같은 트랜잭션 흐름으로 audit row 작성된다는 composition 보장.
    * **retry-after-simulated-worker-failure**: trigger 후 ORM으로 Run.status=FAILED + error_message 직접 set (워커가 없으니 실패 simulate) → HTTP `POST /runs/{rid}/retry` → 새 row의 `result_json={"retry_of": <original_id>}` + pipeline_version_id 상속 + 원본 row.status=FAILED + error_message 그대로 보존 검증. 워커가 status 전이의 단일 writer라는 원칙이 HTTP 측에서 깨지지 않음을 e2e로 확인.
    * **non-member cross-workspace boundary**: Alice 워크스페이스에 Bob이 GET single / GET pipelines list / POST dry-run / POST trigger 모두 403. RBAC가 모든 엔드포인트에 일관 적용되는지 확인.
  - **회원가입 엔드포인트 미존재** — `auth.py`는 login/refresh/logout/me 만, User 시드는 ORM 직접 insert + `await session.commit()` 후 login(HTTP 측에서 보이도록). 시나리오는 가입 이후 워크플로우만 검증; 가입 자체는 후속 슬라이스 또는 OIDC 경로.

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — 기존 Step 7.2 conftest 패턴 + Step 8 ADR-0023 RBAC + ADR-0021 worker queue 재사용)

### Milestone
- **Step 8.7 — 시나리오 테스트 완료 (2026-05-18)**: 3 신규 e2e it (happy / retry-after-failure / cross-ws boundary). 누적 코어 344 unit + 서버 **65 unit** + 서버 **193 it** (190 → 193, +3) + 코어 119 it = **721 tests** all green. mypy strict 코어 39 + 서버 60 src files OK, import-linter 2 contracts KEPT. **Step 8 (API Server) 완전 종료** — auth(local + OIDC) / RBAC(workspace-scoped + SuperAdmin bypass) / audit(middleware + 이중 ACL) / CRUD(workspaces/memberships/connections/pipelines/schedules) / runs read-mostly / actions(dry-run+trigger+retry+SSE) / e2e 시나리오 검증 모두 ship. 다음은 **Step 9 (Execution Engine)** — 워커가 ADR-0021 runs 큐를 SKIP LOCKED로 claim → 코어 `run_pipeline_yaml` 호출 + heartbeat per ~30s + zombie 회수. Step 8.6에서 enqueue까지 끝났으니 9는 큐 consumer + status writer만 추가하면 된다.

- **Worker — claim + batch execution lifecycle** [Step 9.3a]
  - 새 `services/etlx-server/etlx_server/worker/` 패키지:
    * `claim.py` — `claim_pending_run(session, *, worker_id)`: `SELECT ... WHERE status='pending' AND scheduled_at <= now() ORDER BY scheduled_at, created_at LIMIT 1 FOR UPDATE SKIP LOCKED` + 같은 트랜잭션에서 `status=running` + `worker_id` + `started_at` + `heartbeat_at` 전이. ADR-0021 SKIP LOCKED로 다중 워커 안전 동시 claim.
    * `executor.py` — `RunExecutor(factory, backend, *, worker_id).execute(run_id)`: fresh session per run, `_build`로 `PipelineConfig.model_validate` + connection 이름 resolve + `${SECRET:<path>}` 풀이 + 코어 `build_connector`/`build_pipeline`. **build 실패는 status=failed로 기록한 뒤 commit 후 종료** (worker crash 아님). 빌드 성공 시 단일 `asyncio.to_thread(_run_pipeline_in_thread, ...)`로 connect+run+close 일관 thread — sqlite3 같은 thread-bound driver 호환. 성공: `records_read`/`records_written`/`duration_seconds`/`result_json.core_run_id` 기록. 실패: `error_class`/`error_message[2000자 trim]`/`finished_at`/`heartbeat_at`. `result_json.retry_of` 같은 pre-existing key는 성공 write에서 보존.
    * `runner.py` — `RunWorker(factory, backend, *, worker_id, poll_interval=1.0)`: `asyncio.Event` stop, claim → execute → 즉시 다음 iteration; queue 비면 `contextlib.suppress(TimeoutError) + asyncio.wait_for(stop_event.wait, timeout=poll_interval)`. claim 단계의 row lock은 status 전이까지만 hold(긴 실행 시간 동안 lock 보유 X).
  - `etlx-server worker run` CLI 신규: `--poll-interval / --worker-id / --secret-backend / --secret-file-path / --log-level`. SIGTERM/SIGINT 그레이스풀 종료(handler → `worker.stop()`).
  - 신규 공유 모듈 `etlx_server/pipelines/runtime.py` (`resolve_placeholders` / `referenced_connection_names` / `load_connections_by_name`) — DryRunService와 worker가 빌드 경로 공유해 "dry-run ok면 worker run도 build는 통과" 보장. DryRunService에서 동일 코드 중복 제거.
- **코어 버그 수정** [Step 9.3a 부수 작업, 별도 변경 아님]
  - `etl_plugins/core/pipeline.py::Pipeline._run_task`가 `task.sink_table`을 `sink.write`에 forwarding 안 하던 잠재 버그 — 코어 unit 테스트는 `InMemoryBatchSink`(`**options` 흡수)라 가려져 있었다. RDBMS sink(sqlite/postgres/mysql)의 batch 경로가 worker에서 처음 동작하면서 발견. 1-line 수정. S3 sink(`**options`)와 Kafka(stream path는 별도) 비영향. 코어 unit 327개 무회귀.
- **Audit 정렬 안정화** [Step 9.3a 부수 작업]
  - `AuditLogRepository.query`가 `created_at desc` 단독 정렬이라 같은 microsecond 내 audit row 순서가 비결정적이었음. 시나리오 e2e 테스트가 다른 테스트와 함께 실행될 때 ~40% 실패. UUIDv7(ADR-0020 시간 순서) 기반 `id.desc()` secondary sort 추가.
- **OIDC state test 비결정성 수정** [Step 9.3a 부수 작업]
  - `test_verify_rejects_tampered_token`이 base64 마지막 char 1bit flip만으로는 underlying signature bytes 동일할 수 있어 ~40% 통과. signature 전체를 명백히 wrong한 `"AAA...A"`로 치환해 결정적으로 만들었다.
- **테스트 정규화** [Step 9.3a]
  - `services/etlx-server/tests/db/test_worker_lifecycle.py` — 11 신규 it. claim 4(oldest pending pick + None on empty + skip running + skip future scheduled_at) + executor 5(sqlite tmp file fixture로 실제 read→write 3 row 검증 + unknown connector type failed + missing connection failed + stream mode 거부 failed + retry_of preserve in result_json) + worker 2(loop processes pending then stops + empty queue exits on stop). `_SessionFactoryAdapter`로 conftest의 outer-trans fixture 안에서 executor가 reuse하도록 wrap.

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0021 worker queue 첫 구현)

### Milestone
- **Step 9.3a — Worker batch lifecycle 완료 (2026-05-18)**: 3 신규 패키지 모듈 + 1 신규 공유 helper + 1 신규 CLI 서브커맨드 + 1 코어 버그 수정 + 2 비결정성 테스트 수정. 누적 코어 344 unit + 서버 **65 unit** + 서버 **204 it** (193 → 204, +11) + 코어 119 it = **732 tests** all green. mypy strict 코어 39 + 서버 65 src files OK, import-linter 2 contracts KEPT. ADR-0021 in-DB queue 첫 구현 — 신규 ADR 없음. 다음은 **Step 9.2 (스케줄러)** — schedules 테이블 tick으로 pending Run 생성 — 또는 **Step 9.3b (heartbeat + zombie reaper)** — stuck running row 회수.

- **Heartbeat-during-execution + ZombieReaper** [Step 9.3b]
  - 새 `services/etlx-server/etlx_server/worker/heartbeat.py`:
    * `heartbeat_loop(factory, run_id, *, stop_event, interval_seconds)` async fn — asyncio main loop에서 별도 fresh session으로 주기적 `UPDATE runs SET heartbeat_at=now()`. `stop_event` 우선 wait → timeout이면 stamp. exception은 log + 계속 (transient DB hiccup이 in-flight pipeline 죽이지 않게).
  - `RunExecutor.execute` 갱신: `asyncio.to_thread(pipeline.run, ...)` *호출 전에* `heartbeat_loop` task spawn → finally에서 `stop_event.set()` + `await heartbeat_task` (CancelledError suppress). 기본 interval `_HEARTBEAT_INTERVAL_SECONDS=10.0` — reaper의 기본 idle threshold(60s)보다 6배 작게 유지.
  - 새 `services/etlx-server/etlx_server/worker/reaper.py`:
    * `ZombieReaper(factory, *, heartbeat_timeout_seconds=60, scan_interval_seconds=30, batch_limit=100)` — async `run()` 폴 루프 + `reap_once()` 한 번 스캔.
    * `reap_once()`: `SELECT ... WHERE status='running' AND heartbeat_at IS NOT NULL AND heartbeat_at < cutoff ORDER BY heartbeat_at LIMIT N FOR UPDATE SKIP LOCKED` → 각 행을 `status=failed` + `error_class='ZombieReaped'` + `error_message='worker {id} stopped heartbeating {s}s ago (threshold {t}s)'` + `finished_at=now`로 전이. SKIP LOCKED라 worker가 막 heartbeat 갱신 중인 row는 자동 건너뜀.
    * **Auto-resubmit 의도적 안 함** — poison row가 worker 전체를 죽이는 thundering herd 방지. UI(Step 10)에서 ZombieReaped failure는 명시적이며, 사용자가 Step 8.6 `POST /runs/{id}/retry`로 retry 가능.
    * `heartbeat_at IS NOT NULL` 가드 — claim된 직후 heartbeat stamp 전 microsecond 구간 보호.
  - `etlx-server reaper run` CLI 신규 (별도 process):
    * `--heartbeat-timeout` / `--scan-interval` / `--batch-limit` / `--log-level`. SIGTERM/SIGINT 핸들러 → `reaper.stop()`.
    * 별도 process 이유: ① reaper crash가 worker 죽이지 않게 격리, ② 운영 스케일링이 다름(N worker마다 reaper 1대면 충분), ③ kubernetes에서 별도 Deployment.
  - `etlx_server/worker/__init__.py` re-export 확장: `ZombieReaper` + `heartbeat_loop` 추가.
- **테스트 정규화** [Step 9.3b]
  - `services/etlx-server/tests/db/test_zombie_reaper.py` — 8 신규 it. `reap_once` 6: stale → failed + error_message 검증(worker_id 포함) / fresh untouched / NULL heartbeat untouched / terminal 3종(succeeded/failed/cancelled) untouched / 두 번째 reap_once는 0(idempotent) / 3 stale + batch_limit=2 → 2 + 1 + 0. `run()` loop 2: pre-set stop은 즉시 exit / 짧은 sleep 후 stop은 1 zombie reap 후 종료.
  - `services/etlx-server/tests/db/test_worker_heartbeat.py` — 3 신규 it. heartbeat_loop가 0.5s 동안 0.1s 간격으로 stamp → heartbeat_at이 stale value(1h ago)에서 최근으로 진행 / pre-set stop은 stamp 안 함(no-op execute 보호) / 첫 iteration이 `RuntimeError` 던지는 flaky factory에서도 loop 계속 → 후속 iteration이 stamp 성공.

### Decisions
- (이번 슬라이스에서 신규 ADR 없음 — ADR-0021 worker queue 운영성 보강)

### Milestone
- **Step 9.3b — Heartbeat + ZombieReaper 완료 (2026-05-18)**: 2 신규 worker 모듈 + 1 신규 CLI 서브커맨드. 누적 코어 344 unit + 서버 **65 unit** + 서버 **215 it** (204 → 215, +11) + 코어 119 it = **743 tests** all green. mypy strict 코어 39 + 서버 67 src files OK, import-linter 2 contracts KEPT. **워커 운영성 단계 완료** — crash → zombie → 영구 stuck 시나리오가 자동 해소되며 명시적 retry로 깔끔하게 회복. 다음은 **Step 9.2 (스케줄러)** — `schedules` 테이블 tick으로 pending Run 자동 생성 — 또는 **Step 9.3c (log/metric forwarding)** — 코어 structlog → run_logs, metrics ABC → run_metrics.

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
