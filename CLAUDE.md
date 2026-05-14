# CLAUDE.md — ETL Plugins 세션 컨텍스트

> Claude Code가 매 세션 시작 시 읽는 요약 컨텍스트. **전체 설계 명세는 [`SPEC.md`](./SPEC.md)** 에 있다.
> 세션 시작 순서: ① 이 파일 → ② [`ROADMAP.md`](./ROADMAP.md) → ③ 최근 커밋 로그.

---

## 1. 프로젝트 목적

모든 데이터 소스/싱크(RDBMS, NoSQL, DW, Stream, Object Storage)에 대해 **통일된 인터페이스**를 제공하고, **어떤 오케스트레이터(Airflow/Dagster/Prefect/사내 워크플로우) 위에서도 재사용 가능한 Python ETL 플러그인 라이브러리**를 만든다.

핵심 가치:
- **Backend-agnostic** + **Orchestrator-agnostic** + **Config-driven** + **Batch/Stream 통합**
- **Harness 코딩**: 어느 환경/누가 이어받아도 동일하게 재현·이어작업 가능

---

## 2. 아키텍처 요약

```
Orchestrator Adapters  (Airflow / Dagster / Prefect / CLI)
        ↑
Pipeline Core          (Pipeline / Task / Context / Hooks)
        ↑
Connector Abstraction  (BatchSource/Sink, StreamSource/Sink, Record)
        ↑
Connectors (plugins)   (postgres, s3, kafka, snowflake, ...)
        ↑
Foundation             (Config / Secrets / Logging / Metrics / Retry)
```

상세 다이어그램과 인터페이스 정의는 `SPEC.md` §2, §4 참조.

---

## 3. 디렉토리 규약 (목표 구조)

```
etl-plugins/
├── CLAUDE.md / SPEC.md / ROADMAP.md / DECISIONS.md / DEVELOPMENT.md / CHANGELOG.md
├── pyproject.toml / uv.lock / .env.example / .pre-commit-config.yaml / Makefile
├── configs/{connections.yaml, pipelines/*.yaml, logging.yaml}
├── docker/docker-compose.dev.yml
├── scripts/bootstrap.sh
├── etl_plugins/
│   ├── core/          # connector, record, schema, pipeline, context, registry
│   ├── config/        # loader, models, secrets
│   ├── connectors/    # rdbms / nosql / warehouse / stream / object_storage
│   ├── adapters/      # airflow / dagster / prefect
│   ├── observability/ # logging / metrics / tracing
│   ├── utils/         # retry / chunk / async_io
│   └── cli.py
└── tests/{unit, integration, fixtures}
```

전체 트리는 `SPEC.md` §3.

---

## 4. 자주 쓰는 명령어 (확정 후 갱신)

```bash
# 환경 셋업
./scripts/bootstrap.sh                  # 원커맨드: uv sync + pre-commit + docker compose up
make setup / test / lint / fmt / up / down / clean

# 실행
uv run etlx run configs/pipelines/<x>.yaml
uv run etlx run-stream configs/pipelines/<x>.yaml
uv run etlx test-connection <name|--all>
uv run etlx list-connectors
uv run etlx validate <config.yaml>

# 품질 게이트
uv run pytest                            # unit
uv run pytest tests/integration -m it    # testcontainers
uv run ruff check . && uv run ruff format .
uv run mypy etl_plugins
```

> Bootstrap, Makefile, CLI는 Step 1~3에서 구현 예정. 구현되면 이 섹션을 업데이트한다.

---

## 5. 현재 단계

**Step 2 — Reference Connectors** (다음 시작 예정). **Step 1 (Foundation) 완료 (2026-05-14)** — 193 단위 테스트 통과, 7개 ADR.

다음 할 일은 항상 `ROADMAP.md`의 첫 번째 **미체크 + "← 작업 중" 표시** 항목.

Step별 산출물 요약:
- ✅ Step 1: 스캐폴딩, Harness 문서, Core/Config/Observability/Utils/테스트 인프라
- Step 2: 레퍼런스 커넥터 3종 (`postgres`, `s3`, `kafka`)
- Step 3: YAML→Pipeline 빌더, `etlx` CLI, Retry/DLQ/Checkpoint, Pipeline observability 통합
- Step 4: Orchestrator adapter (Airflow/Dagster/Prefect)
- Step 5: 커넥터 확장 (DW/NoSQL/Stream/Object 추가)
- Step 6: OpenLineage, OTel/Prometheus 실구현, mkdocs, v0.1.0 릴리스

---

## 6. 금기사항 (Hard Rules)

- ❌ `.env`에 들어갈 값(시크릿)을 **코드/YAML/커밋 메시지/로그/예외 메시지**에 하드코딩·노출 금지. 로깅은 반드시 마스킹 필터 거칠 것.
- ❌ `.env`는 절대 git에 커밋하지 말 것 (`.env.example`만 커밋). `.gitignore`로 차단.
- ❌ 신규 커넥터를 만들 때 **`@ConnectorRegistry.register(...)` 데코레이터 없이** 등록 금지.
- ❌ 신규 커넥터는 **공통 contract test 통과 없이** 머지 금지.
- ❌ Connector의 raw 객체(드라이버 row, Kafka msg 등)를 `Record` 정규화 없이 Pipeline 외부로 흘리지 말 것. 위치 정보(offset/lsn/seq)는 `Record.metadata`.
- ❌ 단일 PR/커밋에서 **여러 Step을 섞지 말 것**. 한 커넥터/한 기능 단위.
- ❌ 설계상 의사결정을 `DECISIONS.md` ADR 기록 없이 변경 금지. SPEC.md 변경도 ADR 동반 필수.
- ❌ Pydantic 없이 임의 dict로 설정 처리 금지 (Type-safe 원칙).
- ❌ 패키지 매니저는 **`uv` 한 종류**만 사용. `pip install`/`poetry`/`conda` 혼용 금지.

---

## 7. Claude Code 작업 규약

- **세션 시작**: `CLAUDE.md` → `ROADMAP.md` → 최근 커밋 로그 순서로 확인.
- **세션 종료**: ① `ROADMAP.md` 체크박스/"← 작업 중" 마커 갱신 ② 필요 시 `DECISIONS.md`에 ADR 추가 ③ `CHANGELOG.md`에 메모 ④ 커밋 메시지에 Step 번호 포함 (`feat(connector): add postgres source [Step 2.1]`).
- **TODO 주석**에는 Step 번호와 컨텍스트 포함: `# TODO(Step 3.2): chunked upsert via MERGE`.
- **장기 중단** 시 ROADMAP 해당 항목에 `← 작업 중 (YYYY-MM-DD, 다음 할 일: ...)` 메모.
- **모든 결정**은 `DECISIONS.md`에 ADR로 기록. ADR 번호는 0001부터 증가.
- 새 커넥터를 추가하면 **Registry 등록 + contract test 추가 + `SPEC.md` §6 지원 목록 갱신**.

---

## 8. 관련 문서 빠른 링크

| 문서 | 용도 |
|---|---|
| [`SPEC.md`](./SPEC.md) | 마스터 설계 명세서 (원칙·아키텍처·인터페이스·설정·로드맵 전체) |
| [`ROADMAP.md`](./ROADMAP.md) | Step 단위 진행 상태 (체크박스) |
| [`DECISIONS.md`](./DECISIONS.md) | ADR — 모든 설계 결정 기록 |
| [`DEVELOPMENT.md`](./DEVELOPMENT.md) | 신규 환경 인계 / 부트스트랩 / 트러블슈팅 |
| [`CHANGELOG.md`](./CHANGELOG.md) | Keep a Changelog 포맷 변경 이력 |
