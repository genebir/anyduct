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

> **최신 마일스톤 (2026-06-12 오전): 카탈로그 리니지 UI 보편화 — 사용자 피드백 "컬럼 리니지 인터페이스가 너무 구린데" → "보편 구현에 가깝게".** React Flow 둥둥 박스(컬럼별 개별 노드, 줌/팬 과잉, 추적 불가)를 DataHub/OpenMetadata 관례로 전면 교체:
> - **컬럼 리니지 멀티-홉 DAG**: 서버 `GET /assets/{id}/column-lineage-graph?depth=1~5`(column_lineage_edges 업스트림 BFS, 40자산 캡, truncated 프로브) + 웹 홉당 레인(루트 우측 끝)·barycenter 정렬·**전이적 hover/pin 추적**(전 홉 양방향)·비참여 컬럼 "+N개" 접기(루트는 전부)·홉 컨트롤·"더 위가 있음" 칩.
> - **자산(테이블) 리니지도 동일 관례**: `GET /assets/{id}/lineage-graph`(asset_edges **양방향** BFS — 업스트림 음수 depth 좌/다운스트림 양수 우, 60자산 캡) + 동일 시각 언어 레인 DAG(hover 추적, 클릭=이동). 기존 1홉 endpoint 2종은 하위호환 유지.
> - 시각 언어 = ERD 디자이너 재사용(엔티티 카드+행 포트 베지어), 결정적 레이아웃(측정 불필요), React Flow 의존은 assets 영역에서 제거. live 검증: weekly_summary←daily_totals←raw_events 3-레인 + daily_totals 중간 노드 양방향. 서버 e2e 4, 스토리 6종 재작성, Storybook 빌드 green.
>
> **이전 마일스톤 (2026-06-12 새벽 자율런): 데이터 플레인 마감 폴리시 + 컬럼 리니지 일반화 — 페르소나 dogfood 주도 (12 슬라이스).** am 6:00까지 자율 윈도우, live dev 스택(server+worker+web) 위에서 운영자/분석가/데이터엔지니어/데이터모델러 페르소나로 매 기능 실주행 검증:
> - **[운영자] 분할 backfill 운영 완결**: run 상세 + runs 목록 partition i/N 칩(`RunSummary.partition` — Run ORM @property lift, 목록 N+1 없이) · **분할 경계 제안**(`GET /pipelines/{pid}/cursor-stats` MIN/MAX/COUNT read-only + 다이얼로그 "제안" 버튼 — 채워주기만, 결정은 운영자. MIN-미만 nudge/MAX-원본 보존/좁은범위 degrade footgun 가드) · **마이그레이션 상세 Backfill 버튼**(append 전략=커서 파이프라인인데 진입점이 없던 dogfood 갭). live: 4윈도우 분할→무중복 적재→칩 노출 전 과정 실주행.
> - **[데이터엔지니어] P2f mysql Arrow fast-path**: read_arrow(SSCursor→컬럼 직조립, DECIMAL은 선언 precision 핀 — 첫-청크 추론이 자릿수 증가에 깨지는 것을 통합테스트가 발견) + write_arrow(executemany 슬라이스). mysql→pg cross-vendor 인터체인지 포함 통합 10. **보너스 버그픽스**: pyarrow 미설치 시 Arrow fast-path가 폴백 대신 ImportError로 죽던 갭(`_pyarrow_available` 가드).
> - **[분석가] 컬럼 리니지 대폭 정확화**: ① **sql 변환 자동 추론**(본문이 SQL → Phase X sqlglot 워커를 in-flight view 스키마로 재사용; 집계/rename/`SELECT *`/체인 정확, 실패는 opaque 폴백; `column_mapping_recommended`는 분석 불가 쿼리에만으로 노이즈 제거; Phase CC 명시 선언 우선 유지) ② **graph join/aggregate 리니지**(v1 선형-한정 → 토폴로지 워크: join=컬럼 합집합+공유키 양쪽 union, aggregate=group키+집계출력 재성형 — **v1이 aggregate 노드를 조용히 건너뛰던 부정확 수정**). live: 멀티소스(2 커넥션) join→sql 집계 카탈로그가 `total ← orders2.amount` 정확, **ELT pushdown(무이동) 실행에서도 리니지 정확**. 서버 e2e 잠금(`test_sql_transform_lineage_scenario`).
> - **[데이터모델러] ERD 무결성 점검**: CRUD/rename/delete REST + 목록/에디터 라우트 전부 정상(발견 0 — 성숙 영역).
> - **[운영] Step 11 대거 마감**: ① **Helm 차트(ADR-0096)** — `services/charts/etlx`, **kind 실배포로 install→7 pod Ready→login→run SUCCEEDED(Arrow) 전 사이클 검증**. 검증이 발견한 실결함: **prod 이미지 커넥터 extras 0**(dev-deps가 가려온 갭 — postgres 파이프라인도 RegistryError) → `etlx-server[connectors]` 경량 12종 + Dockerfile 수정(compose prod 수혜). 운영 교훈: 이미지 교체는 `helm upgrade`(rollout restart는 migrate hook 미실행 — UndefinedColumn 실측). ② **Grafana 대시보드**(`services/grafana/`, 메트릭 이름 exporter 실측 + live Grafana 13 import 검증). ③ **멀티 테넌시 100-ws 부담 테스트**(드레인 4.2s, 격리 무결). ④ stale compose placeholder 정리/백업 가이드 확인. 잔여: v1.0 릴리스(user-gated).
> - **[웹] Step 10.7 완료**: 전역 error boundary(`error.tsx`/`global-error.tsx`/`not-found.tsx`) + **Skeleton shimmer**(primitive+story, 9개 목록 페이지 "Loading…" 교체).
> - examples/elt_pushdown.yaml(+자격 lint 가드). 검증: 코어 unit 1270 / 서버 it 520+ / 통합 226 / helm lint / Storybook 빌드 전부 green(매 슬라이스), web tsc clean, live 스택+kind 실주행. **다음 후보**: 10.8 a11y 게이트(axe test-runner — 의존 추가라 사용자 정렬) / DW Arrow fast-path(live 게이트) / v1.0 릴리스(user-gated). (분포-기반 분할 제안은 6-12 NTILE 분위수로 완결.)
>
> **이전 마일스톤 (2026-06-10~11): 데이터 플레인 개편 — SQL-first(DuckDB) + Arrow 벡터 + 푸시다운 ELT (ADR-0093/0094).** 행 단위 Record 플레인의 성능(3~6× 느림)·자유도·러닝커브 한계를 사용자 평가로 합의, 운영 플레인은 유지하고 데이터 플레인만 단계 개편:
> - **Phase 1 완료 — `sql` 데이터셋 변환**: `{type: sql, query}` — in-flight 레코드를 DuckDB 릴레이션 `input`으로, 임의 SQL(JOIN/GROUP BY/윈도우/QUALIFY) 1급(P1a). 빌더 transform:sql operator(SQL IDE, batchOnly, P1b). 멀티-소스 JOIN e2e: pg×mysql → graph fan-in join → sql GROUP BY → pg 적재 testcontainers 검증(P1c).
> - **Phase 2 — Arrow/푸시다운**: `core/arrow.py`(Partition+Protocol+어댑터) + sql 변환 파일-백 DuckDB spill(메모리 초과 데이터셋 OK, P2a). postgres `read_arrow`/`write_arrow` COPY fast-path — 변환 0 단일-sink append/overwrite면 Record 플레인 통째 우회, PG→PG 500k행 **3.4×**(P2b). same-connection이면 `INSERT INTO…SELECT` 한 문장 무이동(P2c). **run별 데이터 경로 가시화**: `RunResult.data_paths`(pushdown/arrow/records/graph) → `result_json` → run 상세 칩(P2d).
> - **P2e graph trivial-체인 fast-path (2026-06-12)**: 빌더(graph shape) 파이프라인이 P2b/P2c를 못 타던 침묵 갭 해소 — `source→sink` 2노드 무필터 체인을 shadow Task로 linear 헬퍼에 라우팅(same-connection=자동 푸시다운 / cross-connection Arrow=bulk COPY). PG 통합 graph→COPY 실검증.
> - **P2.5 pushdown ELT 1급화 (ADR-0094)**: `{type: sql, pushdown: true}`(유일 변환+P2c 자격)이면 `INSERT INTO <t> WITH <view> AS (<source>) <query>` — **DW 안에서 변환 실행, 데이터 무이동**(Tier 2, dbt 방식). opt-in(로컬=DuckDB vs 푸시다운=타깃 dialect). 부적격은 로컬 폴백 + lint `sql_pushdown_ineligible`(차단 조건 명시) + data_paths 이중 가시화. **graph trivial 체인도 동작**(빌더는 graph로 저장 — `_try_graph_pushdown`, `SinkSpec.connection_name` 이름 비교로 mint된 sink 인스턴스 대응) + 빌더 pushdown 토글.
> - **Phase 3 — 클러스터링**: 멀티-replica 경합 실증(3-replica claim 무중복 + RunWorker 2대 공동 드레인) + asset upsert 레이스 `ON CONFLICT` 수정(P3a). **파티션 분할 실행(P3b, ADR-0095)**: `POST /pipelines/{pid}/partitioned-backfill {boundaries}` — half-open 커서 윈도우 sub-run N개 enqueue(무중복+완전커버), 멀티-replica가 병렬 claim → 단일 historical load가 워커 수에 비례 스케일아웃. 워커/코어 변경 0, Backfill 다이얼로그 분할 지점 입력까지 완결. **P3c**: deployment.md 멀티-replica 매트릭스+분할 backfill 운영 섹션, stale single-replica 서술 제거. → **ADR-0093/0094/0095 트랙 전 항목 완료** — 다음 후보: Arrow fast-path 커넥터 확대(mysql 등) / 분포 기반 자동 분할 / Helm 차트(Step 11).
> - 검증: 코어 unit 1249 green, PG/멀티소스 통합 11, 서버 워커 47 회귀 0, mypy/ruff/web tsc clean. 3-Tier 빅데이터 전략은 ADR-0093 보강분 참조(Tier 3 분산 엔진은 트리거 조건 명시 후 보류).
>
> **이전 마일스톤 (2026-06-10): ERD 완성 마감 — .damx 전수 정확 복원 + 주제영역 탭 + 산출물/검증/실행 연결 (Phase AJO→AKP).** 사용자 피드백 주도 인터랙티브 세션, 웹 전용(코어/서버 0). ERD는 "가져오기→그리기→검증→산출물→실행" 사이클이 닫힌 성숙 단계로 마감:
> - **.damx 정확 복원(전수 검증)**: 관계 = bracket 시그니처(`[relGUID][부모][자식][relGUID]`, MODEL 섹션 한정, 자기참조는 bracket x2 검증, 물리→한글 canon dedup) — 사용자 기준값 4파일 전수 일치(TMS 265/테이블표준화 16/CTC 147/안전지원 144, AKD/AKE). NOT NULL 플래그(TYPE 뒤 int32, 길이有+8/無+12, AJQ), PK 관계추론+PK→NN(AJO), 컬럼 논리명 전역 보강(AJS), 테이블 물리명(`[물리][논리]` 라벨, AJU), 영문 테이블명(PIT/CROSSING) 파싱(AKD).
> - **주제영역 탭(AKF→AKJ)**: DA# pane 구조 리버스(K_PANE_PRINT_DEV 경계+헤더명+논리/물리 pane쌍) → 한 다이어그램 안의 탭(ErdDesign.areas, 멤버십+탭별 좌표). "전체" 탭 없음(첫 탭 기본), 탭별 DA# 좌표 pane-구간-내 rect 탐색으로 전 탭 복원(다중탭 테이블 탭별 상이 좌표, FK-국소성 검증). 탭 인식 드래그/추가/삭제(고아 방지 시맨틱)/autoLayout/겹침정리.
> - **산출물/검증**: Excel 정의서(.xlsx, ExcelJS lazy — 개요/테이블/컬럼/관계·제약/주제영역 5시트, Apple풍 서식: #1D1D1F ink+헤어라인+#F5F5F7 헤더 freeze/autofilter+절제된 #0A84FF, AKN; 매핑정의서 S2T는 빈 템플릿이라 제거 AKO). DB 일치 검증(VerifyDbDialog — 실스키마 대조, 테이블/컬럼/타입 드리프트, AKK). 검색 자동완성(%부분일치+논리명+탭 점프, AKK). PNG(타이트 핏+선명도+캔버스 클램프 AJY, 다크 배경 AJZ, 로딩 오버레이 AKM).
> - **실행 연결(AKP)**: ERD→마이그레이션 생성 — 테이블당 파이프라인(mirror=ERD PK→key_columns, append=MDFCN_DT/REG_DT 커서 폴백), 기존 migration-config 재사용으로 detail/list 그대로 인식. parseSelectStarTable 한글 식별자 허용(기존 갭).
> - **앱 전반**: 모든 삭제 confirm(ERD목록/캔버스 onBeforeDelete/스케줄 clear — 나머지는 기존 보유, AKG), 다크모드 React Flow 줌버튼(unlayered+!important — @layer는 unlayered lib CSS에 짐, AKB), Vertica dialect(AKL).
> - 검증 방법: 순수 로직은 esbuild 번들+node 드라이버를 실제 .damx 4종에 실행(관계 수/좌표 분리/Excel zip 구조 등 구조 검증), web tsc clean + dev 라우트 200 매 슬라이스. **남은 후보(필요시)**: ERD diff→ALTER DDL / DB검증→ERD 반영 / 카탈로그 연동 / undo / composite FK.
>
> **이전 마일스톤 (2026-06-07): .damx import + ERD 폴리시 + 페르소나 dogfood 자율런 (Phase AHE→AIJ, ADR-0091).** 사용자의 DA#(.damx) ERD를 가져오고, ERD/앱을 페르소나(데이터모델러/운영자/분석가/데이터엔지니어)로 dogfood. 웹 전용(코어/서버는 ERD 서버백킹 AHD 외 무변경). 핵심:
> - **.damx 파서 (ADR-0091)**: DA#의 독자 .NET 바이너리(`FF FE FF <len> UTF-16LE`)를 리버스 — 컬럼/타입/PK 추출 + **실제 관계 레코드 파싱**(엔티티쌍 `[부모][자식]`+부모 키속성, 이름추측 아님) + 유령/중복 테이블 dedup. 실제 도면(i.png) 대조로 16관계 정확 복원. 디자이너 "Import .damx" 버튼.
> - **ERD 폴리시**: 까치발 floating 엣지(겹침방지), 클러스터-패킹 자동정렬(+LR/TB 토글), 미니맵+테이블검색+PNG export+SQL위상정렬(FK 전방참조 방지), 그래프용지 격자, Delete키 삭제, 빈상태/저장상태/테이블수 표시, 목록 검색/inline rename, 노드 "SQL 복사". 리니지/컬럼리니지 그래프에도 미니맵.
> - **앱 dogfood/수정**: cron 미리보기 UTC 불일치 버그 수정(서버=UTC), audit·runs CSV 내보내기, assets 필터 카운트, 대시보드 ERD 카드, 연결폼 비밀번호 표시토글, ERD목록 에러토스트(silent-swallow 수정), 하드코딩 aria-label i18n화, 모달 배경 일관화.
> - **검증**: 매 슬라이스 web tsc clean + dev 라우트 200 + i18n en/ko 1071 파리티. 코어 unit 1212 / 라우터 it 155 / ERD it 2 / 마이그레이션 it 3 green. 빌더(사이클·fan-in)·migration-config·format-time·usage워커 전부 점검 sound. **AHP→AIJ 21슬라이스(실수정 5건), 이후 안정 모니터링.**
>
> **이전 마일스톤 (2026-06-05): ERD 완성도 대폭 향상 — "$5천 고객" 불만 해소 (Phase AGZ→AHD, ADR-0090).** "5천 주고 샀다면 뭐가 불만일지" 평가 → 최대 불만 = **ERD만 localStorage**(나머지는 서버). 그 외 까치발 미렌더·우클릭 없음·커넥션 가져오기 없음·목록 없음·관계 편집 없음. 순차 해결:
> - **AGZ**: 까치발이 SVG marker로 안 뜨던 것을 **커스텀 엣지(CrowsFootEdge)**로 직접 그려 확실히 렌더(many `<`/one `│`).
> - **AHA**: 우클릭 컨텍스트 메뉴(pane: 테이블 추가 / node: 편집·복제·삭제) + **커넥션에서 테이블 가져오기**(하나/다중 선택, FK 추론 병합).
> - **AHB→AHC**: ERD를 **목록-우선**(파이프라인/마이그레이션처럼) — `/w/[slug]/erd` 목록 + `/erd/[id]` 에디터. + **관계선 클릭 카디널리티 편집**(1:1/1:N/N:1/N:M, 양 끝 기호 반영).
> - **AHD (서버 백킹, ADR-0090)**: ERD를 **파이프라인급 서버 리소스로 승격** — `erd_diagrams` 테이블(Alembic 0011) + REST CRUD(`/workspaces/{ws}/erd-diagrams`, audit, 워크스페이스 격리) + 통합 it 2. web은 localStorage→REST(erdApi) 전환(목록/디자이너 async load + 디바운스 autosave). 이제 ERD가 영속·공유·감사됨. erd-store.ts(localStorage) 제거.
> - 검증: 서버 통합 it 2 green, web tsc clean, dev /erd·/erd/[id] 200, 코어 무영향, mypy/ruff clean. 후속: 코어 type_mapping 연동 DDL / ERD→파이프라인 생성 / PNG export / composite FK.
>
> **이전 마일스톤 (2026-06-05): 인터랙티브 ERD 디자이너 (Phase AGX, ADR-0089).** 사용자가 *"직접 그릴 수 있는 기능"* 명확화 — 뷰어(AGW)가 아니라 손으로 그리는 디자인 도구. 파이프라인 빌더의 @xyflow/react 편집 패턴 재사용, **web/클라이언트 전용**(코어/서버/메타DB 변경 0). 신규: `lib/erd-design.ts`(순수 모델 + `toSql` DDL 내보내기 + 드래그-FK 생성) + `components/erd/erd-designer.tsx`(+story, 캔버스+우측 컬럼/PK 편집 패널+툴바) + `/w/[slug]/erd` 페이지 + 사이드바 nav "ERD designer" + i18n(erdDesign.*). 테이블 A→B 드래그 시 `<b>_id` FK 컬럼 자동 생성, CREATE TABLE+PK+FK를 5 dialect로 내보내기(quoting 차등), localStorage 자동저장(워크스페이스별). web tsc clean, dev /erd 200, 코어 무영향. AGW 뷰어(연결→ERD)와 상보적. **AGY: crow's foot(까치발) 표기** — 사용자 요청대로 클래식 ERD 카디널리티 마커(one=bar, many=crow's foot, SVG `orient=auto-start-reverse`)를 디자이너+뷰어 양쪽 엣지에 적용(smoothstep 직교선, 애니메이션 제거). FK A.<x>_id→B를 "B(one) │——< A(many)"로 표현. 후속: 서버 저장 다이어그램 / 코어 type_mapping 연동 DDL / composite FK.
>
> **이전 마일스톤 (2026-06-05): 연결 스키마 ERD 뷰어 (Phase AGW, ADR-0088).** 사용자 *"ERD 그리는 것도 넣고 싶어"*. 소스/싱크 DB 스키마를 파이프라인 작성 전 시각 탐색. **web-only**(코어/서버 변경 0) — 기존 SchemaInspector REST(tables/columns, ADR-0033) + @xyflow/react 재사용. 신규: `lib/erd.ts`(순수 모델 빌더) + `components/connections/schema-erd-graph.tsx`(테이블=엔티티 박스+컬럼, story 포함) + `/w/[slug]/connections/[id]/erd` 페이지 + connections 행 ERD 링크 + i18n(erd.*). **FK는 명명 규칙 추론**(`<x>_id`→테이블 x/xs/xes, UI에 "추론됨" 명시 — 커넥터가 FK 미노출, 진짜 introspection은 후속). N+1 컬럼 병렬 fetch + soft-fail + 60 테이블 캡(truncation 안내). web tsc clean, dev /erd 200, 코어 1212 무영향.
>
> **이전 마일스톤 (2026-06-05): NATS JetStream 커넥터 추가 — Streaming 6종 완비 (Phase AGU, ADR-0087).** RabbitMQ(AGT) 후 NATS 추가. core NATS는 fire-and-forget(ack 없음)이라 commit 컨트랙트 위해 **JetStream**(영속+ack) 사용. nats-py 네이티브 async(connect flag-only, js lazy). subscribe=durable **pull** consumer(pull_subscribe+fetch, 타임아웃=폴링 계속), publish=js.publish, **commit=msg.ack()**. 단위 9(fake JetStream). 배선+web+docs. 코어 unit **1212 passed**(+9 nats), mypy/ruff clean, web tsc + dev 200. **Streaming 6종(Kafka/Kinesis/SQS/Redis/RabbitMQ/NATS)** — 세션 누적 커넥터 12종. ⚠️ extra-only 커넥터(NATS/RabbitMQ/Cassandra/Redis 등)의 fake-client 단위는 드라이버 API에 대한 *우리 모델*을 검증 — 실서버 검증 권장(boto3 계열만 localstack 실검증).
>
> **이전 마일스톤 (2026-06-05): RabbitMQ 커넥터 추가 — AMQP Streaming (Phase AGT, ADR-0086).** 사용자가 자율 윈도우를 15:00로 연장. "무거운 의존"이라 미뤘던 스트리밍 브로커도 extra-only+lazy+fake-client 패턴으로 깔끔히 추가 가능함을 인지하고 RabbitMQ 추가. 드라이버는 pika(thread-unsafe) 대신 **aio-pika(네이티브 async)** — Kafka 패턴 동형(connect flag-only, 채널 lazy, aclose). subscribe=durable queue iterator 소비, publish=PERSISTENT default-exchange, **commit=message.ack()**(at-least-once). 단위 9(fake aio-pika async iterator). 배선(extra/entry-point/registry/mypy override) + web(stream source/sink/연결폼) + docs. 코어 unit **1203 passed**(+9 rabbitmq), mypy/ruff clean, web tsc + dev 200. **Streaming 5종(Kafka/Kinesis/SQS/Redis/RabbitMQ)**, 세션 누적 커넥터 11종(AGE→AGN + AGT).
>
> **이전 마일스톤 (2026-06-05): 커넥터 spree 마감 — lock-in 테스트 + examples + 문서 (Phase AGO→AGS).** 세션 산출물을 잠그고 사용자 진입점 보강:
> - **AGO**: cross-dialect 마이그레이션 매트릭스 lock-in(`test_migration_matrix.py`) — 7 대표 타입 × 10 SQL dialect = 70 셀 하드코딩 + 커버리지/totality 가드(72 unit). dialect 변경 교차 영향을 명시 diff로 포착 + living doc.
> - **AGP/AGQ**: examples 2종 — `cross_cloud_dw_migration.yaml`(Snowflake→BigQuery 타입변환+upsert), `stream_queue_to_stream.yaml`(SQS→Redis Stream, after_sink_flush=at-least-once). 둘 다 etlx validate ✓ + 로드 회귀 가드(example 테스트 3→5). connections.example에 클라우드 DW/스트림 추가.
> - **AGR/AGS**: `docs/guides/connectors.md` 카탈로그 7→전체 세트(RDBMS/DW·NoSQL·스트리밍 3표 + 마이그레이션 대상/능력 매트릭스) + 옵션 conventions에 auto_create/topic/group_id/iterator_type/wait_seconds.
> - check-yaml exclude에 connections.example(!secret) 추가. 코어 production 변화 0, 전 슬라이스 green.
>
> **이전 마일스톤 (2026-06-05): Redis Streams 커넥터 추가 (Phase AGN, ADR-0085).** AWS-localstack 계열(DynamoDB/Kinesis/SQS) 소진 후 Redis 추가. **Redis Streams**(XADD/XREADGROUP/XACK)가 key-value보다 StreamSource/Sink에 자연스럽고 SQS처럼 **commit=XACK** ack. subscribe=consumer group(XGROUP CREATE+MKSTREAM, BUSYGROUP 관용) XREADGROUP 폴링, publish=XADD, redis-py 동기를 asyncio.to_thread 래핑. localstack 비대상이라 DW 패턴(redis-py [redis] extra만, fake-client 단위 8, live 게이트). 4 슬라이스: 커넥터 `redis.py` → 단위 8 → 배선(extra/entry-point/registry/mypy override) → web(stream source/sink + group_id/연결폼). 코어 unit **1120 passed**(+8 redis 단위), mypy/ruff clean, web tsc + dev 200. **Streaming 4종(Kafka/Kinesis/SQS/Redis)**. 세션 누적 커넥터 10종 추가(AGE→AGN, ADR-0077~0085).
>
> **이전 마일스톤 (2026-06-05): SQS 커넥터 추가 — 큐 기반 Streaming, 의미 있는 commit (Phase AGM, ADR-0084).** Kinesis(AGL) 후 Streaming 추가. SQS도 boto3+localstack(dev-deps)라 실검증 가능 + 신규 의존 0. **Kinesis commit이 no-op이었던 것과 달리 SQS commit=메시지 delete(ack)** — StreamSource.commit 컨트랙트를 의미 있게 시연(삭제 안 하면 visibility timeout 후 재전달=at-least-once). subscribe=receive_message 롱폴, publish=send_message, topic=큐 이름(get_queue_url 해석+캐시) 또는 URL. 4 슬라이스: 커넥터 `sqs.py` → LocalStack 통합 it 2(publish→subscribe→commit 후 재수신 empty로 ack 검증) + 단위 9 → 배선(extra/entry-point/registry) → web(stream source/sink/연결폼). 코어 unit **1112 passed**(+9 sqs 단위), mypy/ruff clean, web tsc + dev 200. **Streaming 3종(Kafka/Kinesis/SQS)**, boto3-계열 실검증 3종(DynamoDB/Kinesis/SQS). 세션 누적 커넥터 9종 추가(AGE→AGM).
>
> **이전 마일스톤 (2026-06-05): Kinesis 커넥터 추가 — Streaming 확장 (Phase AGL, ADR-0083).** Streaming은 Kafka뿐이었음. Kinesis는 boto3+localstack가 이미 dev-deps라 **DynamoDB처럼 실제 testcontainers 검증** 가능 + 신규 의존 0. Kafka(aiokafka 네이티브 async)와 달리 boto3 동기 → async 컨트랙트를 `asyncio.to_thread`로 래핑. subscribe=샤드 iterator get_records round-robin 폴링(unbounded), publish=put_record, **commit/flush no-op**(raw Kinesis는 checkpoint 없음 — KCL/DynamoDB 후속). 4 슬라이스: 커넥터 `kinesis.py` → LocalStack 통합 it 2(publish→subscribe 왕복) + 단위 8 → 배선(extra/entry-point/registry, 스트림이라 SQL/migration 미추가) → web(stream source/sink/연결폼). 코어 unit **1103 passed**(+8 kinesis 단위), mypy/ruff clean, web tsc + dev 200. Streaming 2종(Kafka+Kinesis). DynamoDB와 함께 boto3-계열은 실검증 라인.
>
> **이전 마일스톤 (2026-06-05): Cassandra 커넥터 추가 — CQL 와이드칼럼, 마이그레이션 대상 (Phase AGK, ADR-0082).** DynamoDB(AGJ) 후 NoSQL 추가. Cassandra는 **CQL이 tabular**라 DynamoDB와 달리 SchemaInspector/Writer 구현 → **마이그레이션 매트릭스 합류(10×10=100 페어)**. 적응: 모든 INSERT가 upsert(append/upsert 동일), **decimal arbitrary-precision((p,s) 미허용 — `_NO_PRECISION_DECIMAL_DIALECTS` 신설)**, CREATE TABLE에 PRIMARY KEY 필수(미지정 시 첫 컬럼), Session API(cursor 아님), lowercase CQL 타입. 5 슬라이스: type_mapping cassandra dialect(+22 unit) → 커넥터 `cassandra.py`(fake-session 단위 11) → 배선(extra/entry-point/registry + CQL SELECT/LIMIT라 서버 audit/DLQ readable 추가) → web(source/full sink/연결폼/10×10) → docs. 코어 unit **1095 passed**(+33 cassandra), mypy/ruff clean, web tsc + dev 200. NoSQL 3종(MongoDB+DynamoDB+Cassandra). testcontainers 미포함(cassandra-driver C-ext + 컨테이너 느림 — DW 패턴, DynamoDB의 실제검증과 대비).
>
> **이전 마일스톤 (2026-06-05): DynamoDB 커넥터 추가 — NoSQL 확장 + 세션 첫 실제 testcontainers 검증 (Phase AGJ, ADR-0081).** DW 로드맵(AGE~AGH) 완주 후 다음 축. object-storage DLQ를 먼저 검토했으나 per-record 라우팅+fan-out 재읽기와 얽혀(S3 PUT 덮어쓰기 silent loss) 코어 핫패스 버퍼링이 필요 — 회귀 위험 커 보류. 대신 DynamoDB 선택: **localstack+boto3가 이미 dev-deps**라 DW 4종(fake-cursor only)과 달리 **실제 testcontainers 왕복 검증** 가능 + NoSQL 확장(MongoDB뿐이었음) + 신규 무거운 의존 0. `dynamodb.py`: boto3 resource, read=Table.scan(페이지네이션)/write=batch_writer, schemaless라 SchemaInspector/Writer 없음(마이그레이션 대상 아님), **float↔Decimal 변환**(boto3 DynamoDB가 float 거부), put=PK 교체라 append/upsert만(overwrite 거부). 통합 it 4(LocalStack write→scan 왕복+Decimal 라운드트립+upsert 교체) + 단위 9. 배선(extra/entry-point/registry, SQL 아니라 audit/DLQ 미추가) + web(source/sink/연결폼). 코어 unit **1062 passed**(+9 dynamodb 단위), mypy/ruff clean, web tsc + dev 200. NoSQL 2종(MongoDB+DynamoDB).
>
> **이전 마일스톤 (2026-06-05): ClickHouse 커넥터 추가 — DW 로드맵 완주 (Phase AGH, ADR-0080).** Snowflake(AGE)/BigQuery(AGF)/Redshift(AGG)에 이어 마지막 로드맵 DW. ClickHouse는 가장 이질적: CREATE TABLE에 **ENGINE = MergeTree + ORDER BY 필수**(PK→정렬키, 없으면 tuple()), **행단위 UPSERT 없음**(upsert는 명확한 WriteError + ReplacingMergeTree 안내), **Nullable()/LowCardinality() 래퍼 언랩**, 폭-명명 정수(Int64≠INT64 케이스 보존), String이 text/binary/JSON 통합, 트랜잭션 없음. 5 슬라이스: type_mapping clickhouse dialect(+25 unit) → 커넥터 `clickhouse.py`(fake-cursor 단위 15, _unwrap_ch_type 포함) → 배선 → web(source + **append/overwrite-only sink**(upsert 미노출)/연결폼/**9×9=81 마이그레이션 페어**) → docs. 코어 unit **1053 passed**(+40 clickhouse), mypy/ruff clean, web tsc + dev 200. **로드맵 DW 4종 완주**(Snowflake/BigQuery/Redshift/ClickHouse). 이번 인터랙티브 세션 누적 AGE→AGH = **4 클라우드 DW, 20 커밋, ADR-0077~0080**. testcontainers 미포함(live 서버 게이트 — 타 DW 선례). ClickHouse 부분 parity(append/overwrite만) 명문화.
>
> **이전 마일스톤 (2026-06-05): Redshift 커넥터 추가 — 세 번째 클라우드 DW (Phase AGG, ADR-0079).** Snowflake(AGE)/BigQuery(AGF)에 이어. Redshift는 postgres-derived(double-quote, 대부분 타입 동일)라 snowflake가 템플릿. 차이: SUPER(JSON)/VARBYTE(BLOB)/TEXT 없음(VARCHAR 65535)/TIMESTAMPTZ, official `redshift_connector` DBAPI(autocommit 아님 → commit/rollback 명시), upsert MERGE는 2023+ 엔진. 5 슬라이스: type_mapping redshift dialect(+21 unit) → 커넥터 `redshift.py`(fake-cursor 단위 10) → 배선 → web(source/sink/연결폼/**8×8=64 마이그레이션 페어**) → docs. 코어 unit **1013 passed**(+31 redshift), mypy/ruff clean, web tsc + dev 200. **클라우드 DW 3종 완비**(Snowflake/BigQuery/Redshift), 남은 DW: ClickHouse. testcontainers 미포함(AWS 매니지드 — live 클러스터 게이트). 대용량 COPY(S3)는 후속.
>
> **이전 마일스톤 (2026-06-05): BigQuery 커넥터 추가 — 두 번째 클라우드 DW (Phase AGF, ADR-0078).** Snowflake(AGE) 직후 같은 축. BigQuery는 RDBMS와 달라 적응: DBAPI(`google.cloud.bigquery.dbapi`) cursor 패턴, 백틱 quoting, dataset-scoped INFORMATION_SCHEMA, 서비스계정/ADC 인증, PK NOT ENFORCED, append은 multi-row INSERT 한 배치=한 job(DML 쿼터 고려 — load job은 후속). 5 슬라이스: type_mapping bigquery dialect(INT64/FLOAT64/NUMERIC/STRING/BOOL/TIMESTAMP/JSON/BYTES, +26 unit) → 커넥터 `bigquery.py`(fake-cursor 단위 11) → 배선(extra/entry-point/registry/audit/dlq) → web(source/sink/연결폼/**7×7=49 마이그레이션 페어**) → docs. 코어 unit **982 passed**(+37 bigquery: type 26 + smoke 11), mypy/ruff clean, web tsc + dev 200. **남은 DW**: Redshift/ClickHouse. testcontainers 미포함(BigQuery 에뮬레이터 제한 — live 프로젝트 게이트).
>
> **이전 마일스톤 (2026-06-05): Snowflake 커넥터 추가 — 첫 클라우드 DW (Phase AGE, ADR-0077).** 야간 DLQ/dogfood 백로그 소진 후 사용자가 "신규 DW 커넥터" 축 선택. AAQ(vertica/mssql) 패턴으로 5 슬라이스:
> - **type_mapping**: snowflake dialect(NUMBER/FLOAT/VARCHAR/TIMESTAMP_TZ/VARIANT/BINARY) + vendor 파싱(number/string/variant/object/array/timestamp_ntz|tz|ltz) + render_canonical DECIMAL precision에 NUMBER 추가. 코어 unit +25.
> - **커넥터** `snowflake.py`: BatchSource/Sink + SchemaInspector(information_schema) + ensure_table(auto_create+PK) + execute_statement + read/write/MERGE upsert. driver lazy import. fake-cursor 단위 9건(생성 SQL 검증 — live 계정 불요, testcontainers 미포함은 SaaS 게이트).
> - **배선**: pyproject [snowflake] extra+entry-point, registry _BUILTIN_MODULES, 서버 _SQL_CONNECTION_TYPES + DLQ _SQL_READABLE_TYPES(LIMIT 지원).
> - **web**: operators.ts source/sink, connector-schemas.ts 연결 폼(account/warehouse/role 등), migration-config MIGRATION_SUPPORTED_TYPES → **6×6 = 36 마이그레이션 페어**.
> - 검증: 코어 945 passed(+34 snowflake type+smoke), mypy/ruff clean, web tsc clean + dev 200. 코어 production 무영향(신규 모듈+목록 추가). **남은 DW 후보**: BigQuery/Redshift/ClickHouse.
>
> **이전 마일스톤 (2026-06-04): DLQ record 뷰어 — 실패 record 내용 조회 (Phase DLQ-1/DLQ-2, ADR-0075).** 운영-가시성 23슬라이스(AET→AFP) 포화 후 더 큰 축으로 전환. 그동안 DLQ는 빌더에서 *설정*하고 run 상세에서 라우팅 *수*(AFB)만 봤지 실패 record의 **내용**은 못 봤다.
> - **DLQ-1 (서버, ADR-0075)**: `GET /workspaces/{ws}/pipelines/{pid}/dlq/records?limit=N`(Viewer+, read-only, limit [1,200]). `DlqPreviewService`가 dry-run의 connection 해석을 재사용해 현재 버전 dlq config를 풀고, DLQ sink가 `BatchSource`면(모든 RDBMS) `asyncio.to_thread`에서 connect→read(dialect-aware `LIMIT`/mssql `TOP`)→close로 최대 N행 반환. 테이블명 화이트리스트 정규식(SQL splice 방어). 읽기 불가 시 `available=False`+안정적 reason(no_dlq/stream_dlq/connection_missing/sink_not_readable/read_failed 등). **코어 변화 0**(기존 `BatchSource.read`). 신규 서버 it 4(testcontainers — 실제 DLQ 라우팅 run 위 round-trip).
> - **DLQ-2 (web)**: run 상세 우측에 collapsible `DlqRecordsCard`(dlqRouted>0일 때, lazy fetch). available이면 컬럼 union 테이블, 아니면 reason별 안내(Kafka/HTTP는 미리보기 불가 등). `pipelinesApi.dlqRecords` + `DlqPreviewResponse`.
> - **DLQ-3 (web)**: 뷰어 카드에 Copy(records→JSON 클립보드, 티켓용) + Refresh(DLQ 테이블은 후속 run마다 증가, lazy fetch 캐시 갱신).
> - **DLQ-4 (web)**: 행 수 selector(50/100/200, 서버 캡 200) + "처음 N개만 표시" truncation 안내(no-silent-cap, 로그 ADB 원칙). → DLQ 뷰어 view/copy/refresh/limit 운영 완결.
> - **DLQ-5 (서버)**: preview를 SQL-dialect allow-list(`_SQL_READABLE_TYPES` = sqlite/postgres/mysql/vertica/mssql)로 제한 — 비-RDBMS sink(S3/Kafka/HTTP)는 SELECT를 못 받으므로 connector build *전에* `sink_not_readable`로 short-circuit(무거운 import 회피, 혼란스러운 read_failed 방지). 서버 it 5.
> - **DLQ-6 (web)**: 성공 run인데 DLQ로 라우팅된 경우(dlqRouted>0) status 배지 옆 "부분 성공" warning 칩 — 초록 배지에 묻히던 silent data drop 가시화.
> - **DLQ-7 (서버 e2e)**: 운영자 DLQ 조사 여정 HTTP 테스트 — login→ws→connections→DLQ 파이프라인→trigger→drain→GET run→GET dlq/records(실패 2행) + no-dlq 파이프라인 reason. auth+router+response_model HTTP 계약 검증(서비스 단위 DLQP1-5와 별개).
> - **DLQ-8 (코어 lint, ADR-0076)**: `dlq_recommended` advisory — `python`/`custom_python` transform + `dlq` 없음이면 "한 행 raise가 run 전체를 죽인다, dlq를 설정하라" 경고(파이프라인당 1회, sql_exec 제외, location=None). dry-run warnings가 이미 빌더/마이그레이션 UI에 표시되므로 **web 변화 0**. 코어 lint unit 19→25, 서버 21 it 회귀 0. → **DLQ 라이프사이클 완성**: 권고(DLQ-8)→설정(빌더)→실행→수(AFB)→부분 플래그(DLQ-6)→record 조회(DLQ-1~5)→e2e(DLQ-7).
> - **남은 후속(후보)**: per-run 필터(코어 변경 동반 — DLQ는 사용자 sink라 core가 run_id를 모름) / **S3·object-storage DLQ 읽기**(connector-specific) / 정렬·페이지네이션 / 빌더·마이그레이션 detail에서도 뷰어 노출.
> - **AFQ (post-DLQ dogfood)**: 빌더 DLQ 설정이 table(BatchSink)+topic(StreamSink)을 항상 둘 다 노출하던 것을, 선택된 connection 타입(kafka=stream)에 맞는 하나만 표시(미선택 시 table 기본). FieldDef.showWhen(AAF) 평행.
> - **AFR**: schedules 행에 "지금 실행"(Run now, ZapIcon) — 다음 cron tick 안 기다리고 pipelinesApi.trigger 후 새 run으로 이동(migrations Run now/ABK 평행).
> - **AFS**: 대시보드에 Assets 카드(자산 수 + /assets 링크 + opaque(컬럼 리니지 없음) sub-line) — 분석가 카탈로그 entry point. dashboard fan-out에 assetsApi.list 추가.
> - **AFT**: assets list "불투명만" 필터 + URL preset `?lineage=opaque` + 대시보드 Assets 카드 opaque>0 시 subset deep-link(ADI/ADK 패턴). AFS(신호)→AFT(필터+deeplink) 루프.
> - **AFU**: 워크스페이스 삭제 type-to-confirm 가드(관리자) — cascade(connections/pipelines/schedules/runs/audit) 비가역 삭제에 "이름 입력" + confirmDisabled(정확 일치 시만 활성, GitHub식). ConfirmDialog body(AAW) 활용.
> - **AFV**: 대시보드 Recent runs duration도 running이면 live 경과 → running 경과 표기 4 surface(runs list/run detail/migration detail/dashboard) 정합.
> - **DLQ-9 (서버 e2e)**: `dlq_recommended`(DLQ-8)가 dry-run REST 응답 warnings에 실리는지(+dlq 설정 시 사라지는 mirror) HTTP 계약 검증. 신규 서버 it 2. dogfood로 cancel-confirm/members-confirm/runs-retry-menu 등 다수 surface 이미 안전 확인(변경 0).
> - **AFW (서버 e2e)**: 운영자 backfill 여정 HTTP→worker→data 전체 — POST /backfill{from,to}→drain→sink에 (from,to] 윈도우 행만 안착(나머지 제외) 실증. 기존엔 enqueue shape(run_actions)+윈도우 로직(JJ service-level)만 따로 덮음. 신규 서버 it 1. 커버리지 감사로 sensor /check(+RR tick→drain), stream worker(fakes) 이미 충분 확인.
> - **AFX (서버 e2e)**: retry-after-failure 운영자 여정 HTTP — sink 테이블 없음→FAILED(error_class)→테이블 생성→POST /retry→새 run SUCCEEDED+3행+result_json.retry_of 링크+원본 FAILED 유지. 시뮬 status flip 아닌 실제 실패→복구. retry 응답이 RunSummary(no result_json)임을 명문화. 신규 서버 it 1.
> - **AFY (서버 e2e)**: fan-out 카탈로그 분석가 여정 HTTP — one-source→two-sink(when 술어) run 후 GET /assets에 양 sink+source, 각 sink lineage upstream에 공유 source. EE Scenario B(service-level)의 REST 변형, KK(chain)에 fan-out 추가. 신규 서버 it 1.
> - **green-check sweep**: scenario/journey/dlq/backfill/retry/lint e2e 79 passed(오늘 밤 추가분 상호 정합 확인).
> - **AFZ (서버 e2e)**: 데이터플레인 audit REST — custom_python+sql_exec run 후 GET /audit?action=run.sql_read/python_executed/sql_executed 세 이벤트가 run_id로 조회+payload 정확. QQ(service-level)/onboarding(sql_read만 REST)의 컴플라이언스 REST 변형. 신규 서버 it 1.
> - **AGA (서버 e2e)**: auto-materialize 체인 REST — A를 REST trigger→drain→B(auto_materialize) 자동 실행, runs A+B 각 1 succeeded, catalog raw→staging→mart, mart 데이터 정확. GG(service-level)의 REST 변형(asset-driven orchestration 비전). 신규 서버 it 1. 코어 단위 재확인 **911 passed**(905 base + DLQ-8 6).
> - **AGB (서버 e2e)**: multi-workspace 격리 REST — 동일 connection명/테이블/asset_key 두 ws가 runs·catalog 격리(ws_a run 후 ws_b 비어있음, 동일 키가 ws별 다른 row id, 교차 자산 조회 404). HH(service-level)의 REST 변형.
> - **AGC (서버 e2e)**: workspace-variable 해석 lineage REST — 변수 PUT 후 `${var.target_table}` sink 파이프라인 run → catalog key가 해석된 "dst/marts_prod"(literal `${var}` 누출 0). MM/ADR-0057의 REST 변형.
> - **AGD (서버 e2e)**: pipeline-evolution 카탈로그 REST — PATCH로 sink v1→v2 변경 후에도 GET /assets에 v1·v2 자산 둘 다(append-only 이력), 버전 2개. PP(service-level)의 REST 변형. **오늘 밤 서버 HTTP e2e 10건**(DLQ-7/9, AFW/AFX/AFY/AFZ/AGA/AGB/AGC/AGD) — service-level 시나리오의 REST 변형 well 사실상 소진. 이후 lean(green-check 위주).
>
> **이전 마일스톤 (2026-06-04): 운영-가시성 23슬라이스 (Phase AET → AFP).** 단일 슬라이스-단위 세션(web 전용, 코어/서버 변화 0, 매 슬라이스 tsc clean + dev 200). run 디버그(로그/오류 copy·카운트·records delta·DLQ count·heartbeat liveness·running 경과 3 surface) · 실패 triage error_class 5 surface · Last run 컬럼 5 surface · 헬스 signal→action 양축(schedules/sensors: 컬럼→대시보드 신호→필터+deeplink) · 분석가 asset 행수 delta · audit my-actions · builder dry-run 경고 수.
>
> **이전 마일스톤 (2026-06-04): Persona dogfood UX 폴리시 5th wave (Phase ACJ → ACS).** 4th wave 후 추가 10 슬라이스(+follow-up) — 분석가/데이터 엔지니어 양 페르소나 관점으로 매 슬라이스 검증·dogfooding. 핵심 축: trigger-source 가시화 확장 / connection·variable usage 인덱스(삭제 안전) / relative-time 지역화 통일 / 빈 목록 버그 / pipelines health 컬럼.
> - **ACJ**: 마이그레이션 폼 ACG/ACH 스마트 디폴트가 컬럼 로드 effect 안에서만 동작 → strategy 기본값 snapshot이라 "테이블 선택 후 mirror/append 선택" 흐름에서 침묵하던 버그 수정. `suggestKeyColumn`/`suggestCursorColumn` 추출 + strategy-aware effect로 양 순서 커버.
> - **ACK**: 마이그레이션 list Last run 컬럼에 trigger source 아이콘(ABG/ABW/ABY 패턴 확장 — 4 surface 정합).
> - **ACL**: Connections list "Used by" 컬럼 — `lib/connection-usage.ts`(linear/fan-out/graph walk) 참조 파이프라인 수. usage=null은 중립 "—"(false "unused" 방지).
> - **ACM**: connection 삭제 confirm에 사용 중 경고(ACL 인덱스 재사용, 깨질 파이프라인 목록). 운영 우선.
> - **ACL/ACM follow-up**: dogfood로 walker가 graph `sql_exec` 노드 + `dlq.connection` 누락하던 silent miss 2건 수정.
> - **ACN**: assets 카탈로그 relative time + absolute tooltip + Refresh. `lib/format-time.ts`로 relativeTime/absoluteTime 추출(NaN 가드) + 마이그레이션 2 페이지 dedup.
> - **ACO**: runs list scheduled_at을 relative + tooltip으로(공유 헬퍼).
> - **ACP**: relative time **지역화** 통일 — ACN/ACO 하드코딩 "5m ago"가 한국어 미지역화였던 점 교정. format-time이 `t`로 time.* 키 사용, migrations/runs/assets/dashboard 5 surface 단일 구현 + 대시보드 fmtTime dedup.
> - **ACQ**: schedules 빈 목록(필터 없음)이 EmptyState 도달 전 `common.loading` 영구 표시하던 dogfood 버그 수정(assets/connections 패턴 정합).
> - **ACR**: 변수 "Used by" + 삭제 경고 + 로딩 상태 — `lib/variable-usage.ts`(`${var.name}` 토큰 스캔)로 ACL/ACM 패턴을 워크스페이스 변수에 평행 적용.
> - **ACS**: pipelines list에 Last run 상태 컬럼(migrations AAP/ACK 패턴 — workspace runs 단일 fetch + 폴링 + StatusBadge + trigger 아이콘 + 지역화 relative time).
> - **ACT**: Connections "Test all" 헤더 버튼(순차 테스트 + ABS chip 갱신 + 요약 toast). onTest→runTest 추출.
> - **ACU**: 마이그레이션 schema 모드 key/cursor 일괄적용 경고(없는 테이블 런타임 실패 footgun 완화).
> - **ACV**: sensors Last check를 공유 relative time + tooltip(absolute+메시지)으로 → 시간 표기 전 surface 통일 완성.
> - **ACW**: audit resource_id/actor chip에 full UUID tooltip(8자 truncation 보완).
> - **ACX**: asset 상세 materializations relative time + row 수 천단위 포맷.
> - **ACY**: 대시보드 Connections 카드 "N unused" sub-line(ACL 인덱스 재사용, cleanup 신호, 추가 fetch 0).
> - **ACZ**: runs 빈 상태에 "파이프라인으로 이동" CTA(온보딩 일관성).
> - **ADA**: pipelines list "Last run" 축 필터(never/failed/ok — migrations ABI 평행, ACS 데이터 재사용).
> - **ADB**: run detail 로그 1000줄 cap 안내(no-silent-truncation).
> - **ADC**: pipelines list에서 누락 connection 참조 경고(삭제/리네임된 connection을 참조하는 파이프라인을 실행 전 포착).
> - **ADD**: migrations list에서 누락 connection 참조 경고(ADC 평행, From→To 빨강+⚠).
> - **ADE**: connection rename 시 사용 중 경고(EditForm usageCount) — connection 안전 라이프사이클 완성(생성→test→usage→rename→delete→orphan).
> - **ADF**: schedules list cron에 cronstrue 사람-읽기 tooltip.
> - **ADG**: `cronHuman`을 `lib/cron.ts`로 추출 + migrations schedule chip에도 적용.
> - **ADH**: 대시보드 Migrations 카드 never-run 신호 → `?lastRun=never` deeplink.
> - **ADI**: connections "unused" 필터 + 대시보드 Connections 카드 deeplink(`?usage=unused`).
> - **ADJ**: 대시보드 Sensors 카드 orphaned(타겟 삭제) 신호.
> - **ADK**: sensors orphaned 필터 + 대시보드 Sensors 카드 deeplink(`?filter=orphaned`).
> - **ADL**: migration detail에 누락 connection 배너(ADD의 detail 버전).
> - **ADM**: 변수 폼 create/update 명확성(이름 키 upsert footgun 완화).
> - **ADN**: asset 상세 헤더에 freshness("마지막 생성 X 전") — 분석가 진입 즉시 신선도.
> - **ADO**: members 목록에 현재 사용자 "(You)" — friendly-self 패턴(ABT/ABU) 3 surface 정합.
> - **ADP**: workspaces 페이지 로딩 중 EmptyState 깜빡임 수정(provider loading 활용).
> - **ADQ**: pipelines 컨텍스트 메뉴에 Dry run(검증→실행, 마이그레이션 ABP/ABQ 평행).
> - **ADR**: audit row 시간 relative + tooltip — 시간 표기 전 surface 완전 통일.
> - **ADS**: 대시보드 Pipelines 카드 broken(누락 connection) 신호(ADC→dashboard).
> - **ADT**: pipelines "broken" 필터 + 대시보드 ADS deeplink → 대시보드 4종 주의 신호(never-run migrations/unused connections/orphaned sensors/broken pipelines) 모두 deep-link 정합.
>
> - **ADU**: 빌더 connection 필드에 누락 참조 경고(작성 surface).
> - **ADV**: 마이그레이션 detail 누락 connection 시 Run now 비활성화(doomed run 능동 차단, Dry run은 유지).
> - **ADW**: pipelines list Trigger를 누락 connection 시 비활성화(ADV의 pipelines 버전 — 양 실행 surface 능동 차단).
> - **ADX (ADR-0074)**: 사용자 *"SQL 쿼리도 Python 처럼 IDE 활용해줘"* — `CodeEditor`(Monaco, language prop)로 일반화, sourceQuery SQL 모드 + sql_exec statement(신규 `kind:"sql"`)를 SQL IDE로. Python 에디터는 위임.
> - **ADY**: Monaco가 placeholder를 안 보여줘 사라진 SQL 예시 힌트를 "예: {example}" muted 라인으로 복원(source SQL + sql_exec).
> - **ADZ**: 코드/SQL 에디터 **전체화면 토글**(520px 드로어 좁음 해소) — remount 없이 컨테이너 CSS만 토글해 버퍼·커서 보존, Esc 종료. Python/SQL 전체 적용.
> - **AEA**: JSON 필드도 Monaco JSON 에디터 — 내장 언어 서비스로 **실시간 빨강 squiggle** 검증(textarea 사후 메시지 개선).
> - **AEB**: 5개 RDBMS sink의 pre_sql도 `kind:"sql"` → 빌더 모든 SQL 입력(source/Run SQL/pre_sql)이 동일 SQL IDE.
>
> - **AEC/AEE/AEF (서버 e2e, testcontainers)**: web broken-reference 안전망의 전제·계약을 실제 REST로 dogfood — 누락 connection(sink/dlq/graph sql_exec) → dry-run ok=False + connection명 명시(ADC/ADL/ADV/ADW 전제 입증); config_json verbatim round-trip(source/sink/dlq connection + variables + ${var} 토큰 보존 → web walker 데이터 충실성). web extractConnectionNames(dlq/sql_exec 포함, ACL/ACM f/u)가 서버 referenced_connection_names와 전 shape 일치 입증. tests-only.
> - **AED**: 빌더 ValidationBanner 이슈 목록 max-height + 스크롤(많은 이슈 시 캔버스 잠식 방지).
> - **AEG**: 센서 config JSON도 Monaco JSON 에디터(실시간 squiggle 검증) — 빌더 JSON(AEA)과 일관.
>
> - **AEH/AEI/AEJ/AEN (서버 노출 ↔ web 미소비 갭 해소)**: dogfood sweep으로 서버가 반환하지만 web이 안 쓰던 필드 4건 발견·활용 — **AEH** 카탈로그 list `column_lineage_opaque`(UU 서버 추가분) → opaque 자산 칩(분석가 traceability 한눈에); **AEI** run 상세 `result_json`(retry_of/trigger_chain/backfill) → 리니지 필드(재시도 원본/자동트리거 출처/백필 범위 링크); **AEJ** audit 확장 행 `ip`/`user_agent` → provenance(포렌식); **AEN** dry-run `warnings`(DD column_mapping 권장/AAK auto_create 안내/FF typo) → 빌더 DryRunPanel + 마이그레이션 DryRunResultCard에 표시(DD가 "Builder UI 통합 별개 슬라이스"라 했으나 미구현이던 lint 권고 기능 전체).
> - **AEK/AEL/AEM (자동 트리거 가시화)**: schedule/user 둘 다 null인 시스템 트리거 run(센서/asset auto-materialize)이 "—"(runs list)/"manual"(대시보드 오표시)였음 → "auto" 칩/라벨(AEK, 대시보드 버그 수정) + 전 trigger-icon surface 전파(AEL: migrations/pipelines list, migration detail) + runs Trigger 필터 "auto" 옵션(AEM).
> - **AEO**: 빌더 dry-run warning(AEN) 클릭 시 해당 노드 포커스(location `graph.nodes.<id>` 파싱, ValidationBanner와 동일 UX) — 권고를 actionable하게.
> - **AEP (서버 e2e)**: custom_python(column_mapping 없음) → dry-run REST가 warnings 반환 검증 → AEN/AEO 전체 데이터 흐름(서버 lint→REST→web) 입증. 신규 서버 시나리오 파일 누적 5 it(AEC1/AEC2/AEE/AEF/AEP).
> - **AEQ**: connections name hover에 config 요약(비-secret만, `${SECRET}`→`***` 마스킹) — 같은 타입 connection 식별. 거의 미사용이던 config_json 안전 활용.
> - **AER**: run 상세 로그 레벨 필터(전체/info+/warning+/error) — RunLogEntry의 기존 level 필드로 클라이언트 측 "min level" 필터. 실패 run 수백 줄에서 error/warning만 즉시. 노드 필터(M)+cap 안내(ADB)와 합성, "X of Y" + 빈 레벨 메시지.
> - **AES**: run 상세 로그 텍스트 검색(message substring, 클라이언트 측) — 레벨(AER)+노드(M) 필터와 합성해 로그 디버깅 3축(검색/레벨/노드) 완성. "X of Y" + 빈 결과 메시지 generic화.
> - **AET**: run 상세 "Copy logs" 버튼 — 현재 필터(검색/레벨/노드)된 줄만 plain text로 클립보드 복사(ABR/ACC clipboard 패턴 재사용, toast 줄 수 안내). 이슈/Slack 붙여넣기.
> - **AEU**: run 상세 로그 error/warning 카운트 칩(전체 로그 기준, 0이면 숨김) — 실패 run 열자마자 신호, 클릭 시 해당 min-level 필터(AER) 즉시 적용(신호→액션). failed-node 칩 tone 정합.
> - **AEV/AEW/AEX (실패 유형 triage 4 surface 정합)**: RunSummary가 이미 반환하던 `error_class`를 status 배지 옆/아래 칩(truncate + title=full)으로 — **AEV** runs list, **AEW** pipelines list Last run, **AEX** migrations list Last run. 대시보드 Recent failures(기존)와 합쳐 4 surface에서 클릭 없이 실패 종류(WriteError/ZombieReaped 등) triage. error_class 있을 때만 노출, i18n 키 0.
> - **AEY**: run 상세 오류 카드 Copy 버튼 — error_class + error_message + 노드 root cause 합쳐 클립보드 복사(AET 패턴 확장, copy 미니테마 로그/오류/config 3종 완성).
> - **AEZ**: run 상세 records read−written 차이 표시 — 두 값이 다르면 "−N개 필터됨" muted 힌트 + tooltip(필터/중복제거/DLQ 경로 설명). X/Y 쌍에 묻히던 조용한 데이터 손실 가시화.
> - **AFA**: migration detail Recent runs에 error_class 칩 — AEV/AEW/AEX 리스트 칩의 detail-page 평행. 실패 유형 triage가 list 3종 + 대시보드 + migration detail = 5 surface 정합.
> - **AFB**: run 상세에 DLQ 라우팅 행 수 표시 — 코어 `etl_plugins.errors`(routed="dlq") 카운터를 run metrics `attrs_json`에서 합산해 Summary에 "N개 DLQ로 라우팅됨"(>0일 때) + tooltip. AEZ의 generic 필터 중 DLQ 몫 명시(나쁜 행이 버려진 게 아니라 포착). 기존 metrics 데이터 활용(서버 변화 0).
> - **AFC**: schedules list에 Last run 컬럼 — 정시 fire해도 매 run 실패 시 schedules 페이지에서 안 보이던 갭. pipelines(ACS)/migrations(AAP) 패턴(workspace runs 단일 fetch + 10s 폴링 + StatusBadge + relative time + error_class 칩). Last run 컬럼 4 surface(pipelines/migrations/schedules/대시보드) 정합.
> - **AFD**: 대시보드 Active schedules 카드 "N개 실패 중" 신호 — 활성 스케줄 중 파이프라인 최근 run이 failed인 수를 sub-line 최우선(next-firing/paused보다 우선). 기존 fetch한 runs(50) 재사용, 추가 fetch 0. AFC의 대시보드 평행.
> - **AFE (schedules signal→action 루프 완성)**: schedules list Last run 축 필터(never/failed/ok, pipelines ADA 평행 + migrations 라벨 재사용) + URL preset `?lastRun=failed` + 대시보드 AFD 카드가 failing>0이면 그 subset으로 deep-link(ADH/ADI/ADK/ADT 패턴). preset 시 임계값 이하라도 필터 바 노출. AFC(컬럼)→AFD(신호)→AFE(필터+deeplink) 한 루프.
> - **AFF**: sensors list에 타겟 파이프라인 Last run 컬럼 — 센서가 fire해도 트리거된 run이 매번 실패하면 안 보이던 갭. pipelines(ACS)/schedules(AFC) 패턴(runs 단일 fetch + 10s 폴링 + StatusBadge + relative time + error_class). Last run 컬럼 **5 surface**(pipelines/migrations/schedules/sensors/대시보드) 정합.
> - **AFG**: 대시보드 Sensors 카드 "타겟 N개 실패 중" 신호 — 활성 센서 중 타겟 최근 run이 failed인 수를 sub-line 최우선(orphaned/paused보다 우선). 기존 runs(50) 재사용. AFD의 sensors 평행.
> - **AFH (sensors signal→action 루프 완성)**: sensors list 타겟 Last run 축 필터(schedules AFE 평행) + URL preset `?lastRun=failed` + 대시보드 AFG 카드 failing>0 시 subset deep-link. AFF(컬럼)→AFG(신호)→AFH(필터+deeplink) 한 루프. **운영 헬스 신호→액션이 schedules·sensors 양축 완비**.
> - **AFI**: run 상세 heartbeat liveness — absolute → relative time + tooltip, status=running인데 마지막 heartbeat >60s면 "멈췄을 수 있음" warning(ZombieReaper 실패 처리 전 stuck run 인지). 폴링마다 재평가.
> - **AFJ**: run 상세 running run live 경과 시간 — duration_seconds가 완료 전 null이라 "—"였던 것을 started_at→now 기준 "Xm Ys · 진행 중"으로(폴링 갱신). AFI와 함께 in-flight 모니터링.
> - **AFK/AFL**: running run live 경과 시간을 runs list(AFK, 5s 폴링)·migration detail Recent runs(AFL, 5s 폴링)에도 — AFJ 평행. running 경과 표기가 **runs list/run detail/migration detail 3 surface 정합**. AFJ i18n 키 재사용.
> - **AFM**: asset 상세 materializations 행수 delta — records_written 옆에 이전(더 오래된) materialization 대비 "+N/−N"(newest-first → 이전은 mats[i+1]) + tooltip. 분석가가 행 수 급감/급증(데이터 품질 이상)을 즉시 포착. 변화 없으면 숨김.
> - **AFN**: audit "My actions only" 필터 — 서버가 이미 받는 actor_user_id 파라미터 + useCurrentUser로 "내 작업만" 체크박스(toggle 시 offset 리셋, reset 포함, 미로그인 비활성). UUID 복붙 마찰 0. 서버 변화 0.
> - **AFO**: builder dry-run 헤더에 advisory 경고 수("권고 N개", warning tone) — connectorsChecked만 보이던 헤더에 AEN 경고 개수 추가(connector 실패 footer와 동일 calibration). >0일 때만.
> - **AFP**: run DAG 노드 카드 R/W record 수 천단위 포맷(toLocaleString) — 앱 전역 record 카운트 표기와 정합.
>
> **AET→AFP = 23 web 슬라이스(+문서 13)**: 단일 슬라이스-단위 세션. 코어/서버 코드 변화 0(매 슬라이스, 코어 905 회귀 없음), web tsc clean + dev 라우트 200(매 슬라이스), i18n en/ko 동기. 테마: run 디버그(로그/오류 copy·카운트·records delta·DLQ·heartbeat·running 경과) · 실패 triage error_class 5 surface · Last run 컬럼 5 surface · 헬스 signal→action 양축(schedules/sensors: 컬럼→대시보드 신호→필터+deeplink) · 분석가 asset 행수 delta · audit my-actions · builder dry-run 경고 수. **주요 surface 폴리시 포화 — 다음은 DLQ UI(서버+web) 등 더 큰 축 권장.**
>
> **세션 green 확정(2026-06-04)**: 코어 단위 905 passed(코어 production 미변경, 회귀 0) · 신규 서버 시나리오 5 it green(testcontainers) · web tsc clean(매 슬라이스) · 실행 중 dev 서버 전 라우트 200 · production build 21 routes(반복). 검증 방법: dev 동시 실행으로 `next build` 금지(메모리), `tsc` + dev curl(런타임) + 서버 contract는 testcontainers e2e. ruff-format 커밋 중단은 `uv run ruff format` 사전 실행으로 회피.
>
> **SQL/code IDE 통일(ADX→AEB,AEG)**: Python/SQL/JSON 모든 코드·데이터 필드(빌더 + 센서 config)가 Monaco(하이라이팅·줄번호·테마 동기화·전체화면·예시 힌트). 라이브 dev 서버 curl로 런타임 검증(build 금지 — dev 동시 실행 중, 메모리 규칙). 서버 계약은 testcontainers e2e로 dogfood.
>
> **누적 ABG→AEB = 76 슬라이스(+follow-up)** — **broken-reference 안전망 완전 라이프사이클**: 탐지(list ADC/ADD · detail ADL · builder ADU · dashboard ADS/ADT) + 능동 차단(ADV/ADW Run/Trigger 비활성화) + 정리 deeplink(ADH/ADI/ADK/ADT). 대시보드 4종 주의 신호 모두 deep-link actionable. 코어/서버 변화 0(매 슬라이스), web tsc clean, production build 반복 통과, 코어 단위 905 passed(회귀 0).
>
> **(이전 카운트)** ABG→ADR = 66 슬라이스(+follow-up) 단일 long-running 페르소나 dogfood 세션. 신규 pure 헬퍼 4(connection-usage/variable-usage/format-time/cron). 코어/서버 변화 0(매 슬라이스), web tsc clean, **production build 통과**(21 routes, 반복 인증). dogfood가 잡은 silent/실버그 4건(ACJ strategy 디폴트 / ACL·ACM walker / ACQ schedules 빈 목록 영구로딩 / ADP·variables 로딩 깜빡임). 코어 단위 905 passed(회귀 0). 테마: **시간 표기 8 surface 지역화 통일** · **usage/broken-reference 안전망**(connections·variables used-by + 삭제/리네임 경고 + 누락 connection 플래그 list+detail + orphaned sensors + cleanup 신호) · **signal→action deeplink 3종**(ADH/ADI/ADK) · **로딩/빈 상태 정합**(ACQ/ADP) · **검증 도구 확장**(ACT test-all, ADQ dry-run). i18n 신규 키 ~130개.
>
> **이전 마일스톤 (2026-06-01): Persona dogfood UX 폴리시 4th wave (Phase ACD → ACI).** 3rd wave 마무리 후 추가 6 슬라이스 — 마이그레이션 폼 smart-default + dashboard 정합 + audit click-to-filter + table picker 검색:
> - **ACD**: 마이그레이션 source 테이블 선택 시 dest 테이블이 비어 있으면 같은 이름으로 자동 채움. 'same name, different DB' 케이스 마찰 0.
> - **ACE**: 대시보드 'Pipelines' 카드 카운트에서 마이그레이션 제외 — \`/pipelines\` 페이지가 이미 마이그레이션 hide함, 카드와 list 정합.
> - **ACF**: Audit row의 resource_id chip을 클릭하면 필터(resource_type + resource_id) 자동 적용. UUID 복사·붙여넣기 마찰 0.
> - **ACG**: Mirror strategy 선택 + keyColumns 빈 경우, source에 'id' 컬럼 있으면 자동 채움 (case-insensitive).
> - **ACH**: Append strategy 선택 + cursorColumn 빈 경우, source에서 \`updated_at\` > \`created_at\` > 임의 \`*_at\` 순으로 자동 채움.
> - **ACI**: schema mode 테이블 picker에 검색 input (11개 이상일 때만 노출). enterprise schema (50+ 테이블) 스캔 마찰 해소. sourceSchema 변경 시 검색 자동 reset.
>
> **누적 ABG→ACI = 30 슬라이스** 단일 long-running 사용자 페르소나 dogfood 세션. 코어/서버 변화 0(매 슬라이스), web tsc clean, 신규 시각 컴포넌트 1(DryRunResultCard), i18n 신규 키 ~85개. 882 unit + 53 server auth/audit it green.
>
> **이전 마일스톤 (2026-06-01): Persona dogfood UX 폴리시 3rd wave (Phase ACB → ACC + ACA follow-up).** 2nd wave 직후 추가 3 슬라이스:
> - **ACB**: 대시보드 "Active schedules" 카드 next-firing sub-line. `cron-parser`로 모든 활성 cron 중 가장 가까운 firing 계산. ABV(list)/ABJ(migration card)와 함께 "where's the operator's attention?" 3 surface 정합.
> - **ACC**: Audit log JsonBlock에 Copy 버튼. ABR(run detail config) Copy 패턴 정합. operator가 before/after JSON을 diff/Slack/issue에 paste.
> - **ACA follow-up**: runs page client-side trigger filter가 narrow되었을 때 "Showing X of Y" 표시. "Load more 누르면 또 보이긴 하나?" 의문 해소.
>
> **누적 ABG→ACC = 24 슬라이스** 단일 long-running 사용자 페르소나 dogfood 세션. 코어/서버 변화 0(매 슬라이스), web tsc clean, 신규 시각 컴포넌트 1(DryRunResultCard), i18n 신규 키 ~75개. 882 unit + 53 server auth/audit it green.
>
> **이전 마일스톤 (2026-06-01): Persona dogfood UX 폴리시 2nd wave (Phase ABR → ACA).** ABG→ABQ 후 추가 10 슬라이스로 운영 surface 폴리시 강화. 마이그레이션 detail · 카탈로그 · runs · audit · connections · schedules · dashboard에 균일한 친화 표시 + 사전검증 + trigger 출처 가시화:
> - **ABR**: Run detail에 "실행된 config" 패널 (`PipelineVersionEntry` listVersions API + collapsible JSON + copy-to-clipboard). 디버깅 시 현재 pipeline 아닌 실제 실행된 version의 config_json 표시 — pipeline이 그 사이 편집됐어도 정확한 debug context.
> - **ABS**: Connections row에 세션 한정 test 결과 chip (ok/fail + title=error verbatim). 운영자가 5+ connection 중 어느 게 통과/실패했는지 한눈에.
> - **ABT**: Audit row에 'you' 친화 actor 표시 (`useCurrentUser` → actor_user_id 매치 → "you").
> - **ABU**: Runs trigger chip + dashboard Recent runs에 'by you' 친화 표시 (ABT의 자연 확장).
> - **ABV**: Schedules list에 "Next firing" 컬럼 (`cron-parser` reuse + relative time + title=절대 시각).
> - **ABW**: Run detail Summary에 "Triggered by" field (icon + 'by you'/'manual'/'schedule', ABG list-side chip과 정합).
> - **ABX**: 마이그레이션 detail에 dry-run inline 결과 카드 (DryRunResultCard — header summary + errors verbatim + connector 상태 dot 리스트 + dismiss). Toast 사라진 뒤에도 결과 곱씹기.
> - **ABY**: Migration detail Recent runs에 trigger icon-only chip (compact, aria-label/title로 의미 전달).
> - **ABZ**: Dashboard 'Recent failures' view-all link에 `?status=failed` preset (1-line fix, 운영자가 status 다시 잡을 필요 0).
> - **ACA**: Runs page에 'Trigger' 필터 (manual/scheduled/any, URL-synced via `?trigger=`, client-side over server's (status, pipeline) pre-filter).
>
> **friendly self 패턴 정착**: ABT(audit) → ABU(runs chip) → ABU/2(dashboard) → ABW(run detail Summary) 4 surface 균일. **trigger source 가시화**: ABG(runs list) → ABW(run detail) → ABY(migration recent runs) 3 surface 균일.
>
> 누적: ABG→ACA 21 슬라이스 단일 wave. 신규 시각 컴포넌트 1(DryRunResultCard ABX) + i18n 신규 키 ~60개. 코어/서버 변화 0. web tsc clean 매 슬라이스. 다음 axis: K4 graph 백필 / Snowflake/BigQuery connectors / DLQ UI / Step 11 운영 강화.
>
> **이전 마일스톤 (2026-06-01): Persona dogfood UX 폴리시 1st wave (Phase ABG → ABQ).** AAR→ABA의 마이그레이션 surface 위에 운영자 페르소나 흐름을 직접 dogfooding하며 발견한 11 슬라이스로 마감. 핵심 dogfood 발견: 사용자 워크스페이스에 70 마이그레이션 중 62개가 한 번도 실행 안 됨 + 0 스케줄 → "방금 만든 뒤 next step?" UX gap이 다수 노출. 단위 슬라이스:
> - **ABG**: Runs 페이지 Trigger 컬럼 (manual vs schedule chip — schedule_id/triggered_by_user_id 분기).
> - **ABH**: /assets 페이지 search + kind 필터 (6th list surface 균일화).
> - **ABI**: 마이그레이션 list에 "Last run" 축 필터 (never/failed/ok) — 'forgot to run' bulk catch + 'fix what's broken' triage.
> - **ABJ**: 대시보드 마이그레이션 카드에 `neverRun` 신호 (24h 활동 0 + never_run > 0 → "{n} never run" sub-line).
> - **ABK**: Retry 버튼 클릭 시 새 run으로 자동 이동 (운영자 모니터링 의도와 정렬).
> - **ABL**: runs banner + 컨텍스트 메뉴 + run detail '파이프라인 열기' → 마이그레이션이면 /migrations/[id] 자동 분기 (`migrationSummaryOf` predicate 공유).
> - **ABM**: 마이그레이션 bulk-create 후 `?lastRun=never` URL preset → 'never run' 자동 적용 → AAW bulk Run now 일직선 흐름.
> - **ABN**: Audit page 액션 dropdown production coverage (DB DISTINCT scan, 9종→27종, pipeline.triggers_set/connection.delete/schedule.toggled 등 그룹화).
> - **ABO**: 마이그레이션 strategy chip hover tooltip (snapshot/append/mirror/custom 각 1줄 동작 설명).
> - **ABP**: 마이그레이션 detail에 Dry run 버튼 (Run now 옆) — `DryRunService` 재사용으로 connection/secret/connector 인스턴스화 검증.
> - **ABQ**: 마이그레이션 list bulk Dry run (Run now 전 typo catch). action bar 순서 = Clear → Dry run → Run now → Schedule → Delete = 운영자 mental model 안전→실행→자동화→정리.
>
> **Live dogfood 검증**: vertica → postgres 마이그레이션 (`BDA_BI_DB.TB_BCDBAS601` 등 7종 schema mode) 실제 DB INSERT runs 통한 20행/6000행/1000행 write 모두 성공. AAR transaction recovery fix가 production worker에서 동작 입증.
>
> 결과: 한 운영자 흐름 = 대시보드 'never run' 신호 → 카드 클릭 → list (사전 적용된 'Last run: never' 필터) → 'Select all visible' → bulk Dry run (검증) → bulk Run now (실행) → bulk Schedule (자동화) — 모두 한 surface. 신규 시각 컴포넌트 0(span/chip/icon 재사용). 코어/서버 변화 0 (web tsc clean 매 슬라이스). i18n 신규 키 ~30개 (en/ko). 다음 axis: DLQ UI / K4 graph 백필 / Snowflake/BigQuery connectors / config viewer for failed runs.
>
> **이전 마일스톤 (2026-06-01): 마이그레이션 surface 완결 (Phase AAR → ABA).** Vertica/MSSQL 추가(AAQ) 직후 cross-DB 마이그레이션이 본격 활용되면서 사용자 요청 + dogfood 발견 이슈 14 슬라이스로 보강:
> - **AAR**: postgres `current transaction is aborted` root cause — `_auto_create_sink_tables`의 `contextlib.suppress`가 DDL 실패 삼킴 → transaction abort 상태 poison. 명시적 try/except + rollback + `postgres.ensure_table`이 schema-qualified 받으면 `CREATE SCHEMA IF NOT EXISTS` 먼저 emit. 사용자의 vertica → postgres(`BDA_BI_DB.TB_BCDBAS601`) 실패가 즉시 해결. + Pipelines 탭에서 마이그레이션 숨김 + 생성 후 리스트로 redirect.
> - **AAS family**: 스키마 단위 마이그레이션 — multi-select picker로 N개 한 번에 생성(`buildBulkMigrationConfigs`) + dropdown 소스 선택(typo 차단) + `makeNode`가 operators.ts의 `defaultValue`를 자동 주입(템플릿 default 누락 fix) + **템플릿 기능 완전 제거**(빈 그래프로 시작).
> - **AAT–ABA**: 리스트 검색/필터(name + From/To/Strategy + Schedule status) + Run now 버튼 + Last run 컬럼(workspace-wide runs 단일 fetch) + Schedule chip 컬럼(N+1 per pipeline, 10s 폴링, soft-fail) + 임계값 5 below 검색바 숨김.
> - **AAU**: detail page Quick Schedule — CronInput(preset chips + cronstrue + 다음 firing 미리보기) + Enable/Update/Pause/Resume/Clear, 단일 schedule 가정.
> - **AAV**: 대시보드에 Migrations StatCard(count + 24h 성공률) + flaky `_audit_rows` test 안정화(secondary `id` sort).
> - **AAW–AAY**: bulk multi-select + Delete/Run now/Schedule, `ConfirmDialog` 확장(`body?: ReactNode` slot + `confirmDisabled?` prop) — bulk schedule이 CronInput을 dialog 안에 통합.
> - **AAX follow-up**: Recent runs 카드 → "View all" + "View failures" (`/runs?pipeline={id}&status=failed`) deeplink.
>
> 결과: 한 사용자 시나리오 = `+ New migration` → 폼(single/schema 모드) → 저장(N개 자동 생성) → 리스트(검색/필터 + bulk schedule + bulk Run now + chip 모니터링) → detail(Run/Schedule/Recent runs+히스토리 링크) → dashboard(아이콘 카드 + 성공률) 한 surface 완결. 코어 unit 882 + 서버 it 485 회귀 0(flaky 영구 해결). 신규 시각 컴포넌트 0(ConfirmDialog body 확장 외 모두 재사용). 이 turn 동안 ADR 없음(server contract 변경 0). 향후 axis: DLQ UI / K4 graph 백필 / additional connectors.
>
> **이전 마일스톤 (2026-05-29): ConnectorRegistry 빌트인 fallback — stale entry_points 대응 (AAQ post-mortem).** 사용자 신고 *"vertica: RegistryError: Connector 'vertica' not registered"*. 원인: dev 서버가 pyproject.toml 변경 전 시작 → 옛 entry_points.txt 메타데이터. 방어 코드: `_BUILTIN_MODULES` 매핑 + `_load_builtin(name)` — entry_points로 못 찾으면 module path로 직접 import 시도(decorator가 등록). 외부 플러그인은 entry_points 유지(기존 동작 동일). list_connectors도 exhaustive. 5 신규 unit. 코어 unit 850→854. mypy/ruff clean.
>
> **이전 마일스톤 (2026-05-29): Vertica + SQL Server (MSSQL) 커넥터 추가 — 마이그레이션 5×5 매트릭스 (Phase AAQ, ADR-0073).** 사용자 *"연결 유형 좀 더 추가해줘. vertica 포함해주고"*. ① 신규 RDBMS 커넥터 2종(vertica.py / mssql.py): BatchSource/Sink/SchemaInspector/SchemaWriter/SqlExecutor 풀 구현 + auto_create_table + PRIMARY KEY. lazy import. ② core/type_mapping.py vertica/mssql dialect + vendor 파싱 확장(nvarchar/bit/datetime2/long varchar/money/uniqueidentifier 등). ③ Web operators.ts 4종(source/sink × 2). ④ migration-config.ts MIGRATION_SUPPORTED_TYPES 확장 → 5×5 = 25 페어. ⑤ connector-schemas.ts 폼. ⑥ pyproject.toml extras + entry-points. ⑦ server audit set 확장. 코어 unit 812→850(+38 type_mapping). mypy/ruff/tsc clean. backward-compat 완전.
>
> **이전 마일스톤 (2026-05-29): Migrations 리스트에 최근 실행 상태 컬럼 — health at a glance (Phase AAP).** 운영자가 클릭 없이 모든 마이그레이션 건강 상태 한눈. ① 새 컬럼 Last run: StatusBadge + 상대 시간. ② Never run 명시. ③ 전체 workspace runs.list({limit:200}) 단일 요청 + pipeline_id별 최근 1개 추출(N+1 회피). ④ 5초 폴링. 신규 시각 컴포넌트 0. 코어/서버 변화 0. web tsc clean. i18n en/ko 각 2 키.
>
> **이전 마일스톤 (2026-05-29): Migration form wire-shape 서버 e2e — 3 strategies worker 통과 (Phase AAO).** `migration-config.ts`가 emit하는 3 JSON shape를 hand-build → worker 실행 → 결과 검증 → web↔server drift catch. ① AAO1 Full snapshot 2-pass schema drift. ② AAO2 Append 2-pass growing source 양 batch 보존. ③ AAO3 Live mirror PK 자동 emit + upsert merge, 3 rows 중복 0. 서버 it 482→485(+3). 코어 unit 812 unchanged.
>
> **이전 마일스톤 (2026-05-29): Migration detail page를 닫힌 surface로 — Run now + Recent runs (Phase AAN4).** AAN3 폼 옆에 실행 + 모니터링이 한 페이지에. ① 헤더 `Run now` 버튼(pipelinesApi.trigger, optimistic insert, current_version 없으면 disabled). ② Recent runs 카드: 파이프라인 한정 최근 5(StatusBadge + run id + records_written + duration + 상대 시간). 5s 폴링. ③ 빈 상태 안내. 운영 UX: 생성→저장→실행→결과 한 페이지 완결. 코어/서버 변화 0. web tsc clean. i18n en/ko 각 6 추가 키.
>
> **이전 마일스톤 (2026-05-29): Migration 폼을 마이그레이션-shaped으로 재설계 (Phase AAN3).** 사용자 *"명확하게 마이그레이션 같이 만들어줘. 지금 형태는 ETL과 별 차이가 없잖아"*. ① 테이블 picker(쿼리 textarea 제거) + connectionsApi.tables datalist. ② Source→Destination 카드 좌우 + ▶ 화살표. ③ Strategy radio 3가지(Full snapshot=overwrite+drop / Append new rows=append+cursor / Live mirror=upsert+keys) — mode/if_exists 기술 라벨 제거. ④ 소스 스키마 미리보기 + (mirror) PK 컬럼 배지. ⑤ migration-config.ts 재설계: MigrationStrategy enum + strategyToSinkShape + parseSelectStarTable. ⑥ list page: From→To 화살표 + Strategy chip. ⑦ safe-exit: 복잡 쿼리는 빌더로. ⑧ i18n en/ko 각 22 추가 키. 신규 시각 컴포넌트 0. 코어/서버 변화 0. web tsc clean.
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
