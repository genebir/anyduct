# ROADMAP

> Step 단위 진행 상태. 체크박스로 관리. 작업 중단 시 해당 항목에 `← 작업 중 (YYYY-MM-DD, 다음 할 일: ...)` 표시.
> 모든 커밋 메시지에 Step 번호 포함 (예: `feat(connector): add postgres source [Step 2.1]`).
> 전체 설계 근거는 [`SPEC.md`](./SPEC.md) §10 참조.

---

## Step 1 — Foundation  ← **현재 단계 (착수: 2026-05-14)**

### 1.0 Harness 문서
- [x] `SPEC.md` 분리 (기존 CLAUDE.md → SPEC.md)
- [x] `CLAUDE.md` 작성 (세션 컨텍스트)
- [x] `ROADMAP.md` 작성
- [x] `DECISIONS.md` 작성 (ADR-0001 포함)
- [x] `DEVELOPMENT.md` 작성
- [x] `CHANGELOG.md` 초기화

### 1.1 프로젝트 스캐폴딩
- [x] `pyproject.toml` (uv, `requires-python>=3.11`, 패키지 메타, ADR-0002)
- [x] `.python-version` (3.11)
- [x] `uv.lock` 커밋
- [x] `.gitignore` (`.env`, `__pycache__`, `dist/`, `.venv`, ...)
- [x] `.editorconfig`
- [x] `.pre-commit-config.yaml` + `.yamllint.yaml`
- [x] `Makefile` (`setup`, `test`, `lint`, `fmt`, `up`, `down`, `clean`, ...)
- [x] `README.md`
- [x] `etl_plugins/__init__.py`, `py.typed` (패키지 placeholder)

### 1.2 환경 재현
- [x] `.env.example`
- [x] `scripts/bootstrap.sh`
- [x] `docker/docker-compose.dev.yml` (postgres 16, kafka 3.7 KRaft, minio, redis 7)
- [ ] `.devcontainer/devcontainer.json`  *(optional, 나중에)*

### 1.3 CI
- [x] `.github/workflows/ci.yml` (lint, mypy, yamllint, detect-secrets, unit matrix py3.11/3.12, integration, build)
- [x] `.github/workflows/release.yml` (태그 트리거, 버전 검증, uv build, PyPI Trusted Publishing, GitHub release)
- [x] `.secrets.baseline` 초기 생성
- [x] `tests/` 초기 구조 + 패키지 sanity test (`tests/test_package.py`)
- [x] Dynamic version (`etl_plugins/__init__.py`가 단일 진실)

### 1.4 Core 추상화 (`etl_plugins/core/`)
- [x] `exceptions.py` — ETLError 계층 (Config/Registry/Connector/Pipeline/...)
- [x] `record.py` — Pydantic `Record` (extra=forbid)
- [x] `schema.py` — `Field`, `Schema` (minimal, frozen)
- [x] `connector.py` — `Connector`, `BatchSource/Sink`, `StreamSource/Sink` (ABC)
- [x] `registry.py` — `ConnectorRegistry` + entry-point 자동 로드 (fail-soft)
- [x] `context.py` — `Context` (uuid run_id, structlog bound logger)
- [x] `pipeline.py` — `Pipeline` + `Task` (fluent builder) + `RunResult` + hooks
- [x] `etl_plugins/{__init__.py, core/__init__.py}` re-exports
- [x] 단위 테스트 57개 (`tests/unit/core/`) — mypy strict / ruff / pytest 통과

### 1.5 Config (`etl_plugins/config/`)
- [x] `models.py` — Pydantic 설정 모델 (Connections/Pipeline/Source/Sink/Transform/Retry/Observability/Commit/Buffer)
- [x] `secrets.py` — SecretBackend ABC + Env/Static + vault/aws_sm/gcp_sm 스텁 + 팩토리
- [x] `loader.py` — YAML + `${VAR}` 치환 + `!secret <path>` 태그 + load_connections/load_pipeline/load_dotenv
- [x] `python-dotenv`, `types-PyYAML` 의존성 추가
- [x] 단위 테스트 49개

### 1.6 Observability 베이스 (`etl_plugins/observability/`)
- [x] `logging.py` — structlog JSON/dev 콘솔 + 시크릿 마스킹 processor (`make_secret_masker`) + `configure_logging`
- [x] `metrics.py` — `Metrics`/`Counter`/`Histogram` ABC + NoOp + `get/set/reset_metrics` + 표준 메트릭 이름 상수
- [x] `tracing.py` — `Tracer`/`Span` ABC + NoOp + 자동 record_exception on __exit__
- [x] 단위 테스트 30개 (총 136)
- [ ] OTel/Prometheus 실제 백엔드 구현 — **Step 6 강화로 이동**
- [ ] Pipeline.run 내부 자동 metrics/span emit — **Step 3 retry+observability 통합**

### 1.7 Utils (`etl_plugins/utils/`)
- [x] `retry.py` — `@retryable` (sync+async, 지수 백오프 + jitter, tenacity 래핑, observability 자동 연동)
- [x] `chunk.py` — `chunked` / `take` / `drop`
- [x] `async_io.py` — `run_sync_in_thread`, `gather_with_concurrency`, `iter_to_async`
- [x] 단위 테스트 42개 (총 178)

### 1.8 테스트 인프라
- [x] `tests/conftest.py` 글로벌 `sample_records` fixture
- [x] `tests/fixtures/records.py` (sample / large / mixed_types) + `tests/fixtures/connectors.py` (InMemory 3종)
- [x] `tests/contracts/batch.py` — `_BatchSourceContract` / `_BatchSinkContract` / `_BatchRoundTripContract` (subclass-able mixin)
- [x] `tests/contracts/_helpers.py` — `normalize_payloads` (order-independent equality)
- [x] `tests/contracts/test_inmem.py` — InMemory로 contract suite 자체 검증 (15 tests)
- [x] 기존 `tests/unit/core/conftest.py` 슬림화 (InMemory를 fixtures로 이동)
- [ ] Stream contract (`_StreamSourceContract` 등) — **Step 2.3 (Kafka)에서 추가**

---

## **Step 1 — Foundation: 완료 (2026-05-14)**

총 193 단위 테스트, 7개 ADR, 라이브러리 코어/Config/Observability/Utils/테스트 인프라까지 완비.
**다음은 Step 2: Reference Connectors.**

---

## Step 2 — Reference Connectors (각 카테고리 1개씩 먼저)

### 2.1 `postgres` (BatchSource + BatchSink)
- [x] 드라이버 결정 (psycopg 3) — ADR-0008
- [x] `BatchSource.read` (server-side named cursor + chunk_size + uuid 충돌 회피)
- [x] `BatchSink.write` (append=COPY, overwrite=TRUNCATE+COPY, upsert=INSERT ON CONFLICT)
- [x] health_check / connect / close / context manager
- [x] `sql.Identifier` 기반 안전한 식별자 quoting (schema.table 지원)
- [x] Registry `@register("postgres")` + entry-point 자동 발견
- [x] Contract test 통과 (BatchSource 7 + BatchSink 5 + RoundTrip 3)
- [x] Integration test 14개 (testcontainers + postgres:16-alpine) — 총 29 통합 테스트
- [x] `[postgres]` extra + dev group + `contracts/batch.py`에 `read_kwargs`/`write_kwargs` fixture 추가

### 2.2 `s3` (BatchSource + BatchSink)
- [x] 드라이버 결정 (boto3) — ADR-0009
- [x] parquet / csv / jsonl 포맷 (pyarrow + 순수 함수 `_serialize_*` / `_parse_*`)
- [x] `detect_format(key)`로 확장자 기반 자동 감지 + 명시적 override
- [x] MinIO 호환 (`endpoint_url` 파라미터 — testcontainers로 통합 테스트)
- [x] BatchSource.read (`query`=prefix, paginated list + per-object parse)
- [x] BatchSink.write (single object PUT, mode=append/overwrite, upsert 거부)
- [x] Registry `@register("s3")` + entry-point 자동 발견
- [x] Contract test 통과 (BatchSource 7 + BatchSink 5 + RoundTrip 3)
- [x] 21 unit tests (포맷 helpers, no boto3) + 35 integration tests (MinIO)
- [x] `[s3]` extra = `boto3>=1.34, pyarrow>=16.0`
- [ ] multipart upload (큰 파일) — **Step 6 강화로 이동**

### 2.3 `kafka` (StreamSource + StreamSink)
- [x] 드라이버 결정 (aiokafka — native async) — ADR-0010
- [x] StreamSource.subscribe (group_id, auto_offset_reset, async generator with finally cleanup)
- [x] StreamSink.publish / flush (lazy producer)
- [x] Sync `connect/close` flag-only + async `aclose()` 명시적 cleanup
- [x] 메타데이터에 Kafka 위치(topic/partition/offset/key/timestamp) 포함
- [x] Registry + entry-point 자동 발견
- [x] **Stream Contract 신규 도입** (`tests/contracts/stream.py`): `_StreamSourceContract`/`_StreamSinkContract`/`_StreamRoundTripContract`
- [x] 16 통합 테스트 (testcontainers Kafka KRaft mode)
- [x] `[kafka]` extra = `aiokafka>=0.11`
- [ ] StreamSource.commit (`at_least_once` 보장) — **Step 3 Pipeline runtime에서 구현**
- [ ] Schema Registry 연동 (Avro/Protobuf) — **Step 5로 이동**

---

## **Step 2 — Reference Connectors: 완료 (2026-05-14)**

세 카테고리 각 1종 (RDBMS=postgres, Object=s3, Stream=kafka) 모두 구현 및 통합 테스트 통과.
80 통합 테스트 (postgres 29 + s3 35 + kafka 16). Batch Contract + Stream Contract 양쪽 패턴 확립.
**다음은 Step 3: Pipeline 실행기 + CLI.**



---

## Step 3 — Pipeline 실행기 + CLI  ← **현재 단계 (착수: 2026-05-14)**

### 3.1 YAML 빌더 + Transforms + CLI (batch only)
- [x] `etl_plugins/runtime/transforms.py` — `@register_transform` + 빌트인 4종 (rename / cast / filter sandbox / python)
- [x] `etl_plugins/runtime/builder.py` — `build_connector` / `build_connectors` / `build_pipeline` / `build_pipeline_from_yaml`
- [x] `etl_plugins/runtime/runner.py` — `run_pipeline_yaml`
- [x] `etl_plugins/cli.py` — Typer app + 5 서브커맨드 (`version`, `list-connectors`, `validate`, `run`, `test-connection`)
- [x] `pyproject.toml` console_scripts: `etlx = "etl_plugins.cli:main"`
- [x] 38 unit tests (transforms 23 + builder 8 + CLI 7) — 총 252 unit
- [x] ADR-0011

### 3.2 Stream runtime
- [x] `Pipeline.arun_stream` async 메서드 (mode='stream')
- [x] `etlx run-stream` 서브커맨드 (+ `--stop-after-records` / `--stop-after-seconds`)
- [x] `commit.strategy: after_sink_flush` (Kafka `StreamSource.commit` 구현 — async commit signature)
- [x] Stream sink 버퍼링 (`buffer.max_records` / `buffer.max_seconds` in sink_options)
- [x] InMemoryStreamSource/Sink 추가 (`tests/fixtures/connectors.py`)
- [x] 17 신규 unit tests + 2 Kafka integration tests (commit이 실제로 group에 반영되는지 검증)
- [x] ADR-0012 (commit ABC sync→async SPEC 보정 포함)

### 3.3 Retry / DLQ / Observability
- [x] Retry `@retryable` 통합 (RetryConfig → tenacity). Batch는 task-level, stream은 publish-level.
- [x] Dead Letter Queue 라우팅 (DlqConfig: connection + table/topic + mode). Transform 실패만 라우팅 (sink 실패는 retry/propagate). Best-effort with `contextlib.suppress`.
- [x] Pipeline.run 내부 자동 metrics emit (RECORDS_READ/WRITTEN_TOTAL, ERRORS_TOTAL, DURATION_SECONDS) — NoOp 기본
- [x] CLI logging 통합 (`--log-format json|console` + `--log-level`) — Typer global callback
- [x] 13 신규 unit tests (총 282 unit + 82 it = 364)
- [x] ADR-0013
- [ ] Checkpoint / Cursor 저장 hook (BatchSource cursor, StreamSource offset) — **Step 6 강화로 이동** (현재는 `Pipeline.on("post_run", ...)` 훅으로 사용자가 처리)
- [ ] Pipeline span emit (Tracer 통합) — **Step 6 강화로 이동**

---

## Step 4 — Orchestrator Adapters — **완료 (2026-05-14)**

- [x] Airflow `ETLPluginsOperator` (lazy via PEP 562 `__getattr__`)
- [x] Dagster `EtlPluginsResource` + `etl_plugins_op`
- [x] Prefect `run_etl_pipeline_flow` + `run_etl_pipeline_task`
- [x] 3 optional-dependencies extras (`airflow`, `dagster`, `prefect`)
- [x] Structural tests (7) + orchestrator-conditional delegation tests (3) — `pytest.importorskip` pattern
- [x] ADR-0014

---

## Step 5 — Connector 확장

### 5.1 RDBMS
- [x] MySQL/MariaDB (PyMySQL, server-side cursor, executemany INSERT, ON DUPLICATE KEY UPDATE, 29 it tests) — ADR-0015
- [ ] MSSQL
- [ ] Oracle
- [ ] SQLite

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
- [ ] Contract test suite 완성 (read→write→read 일치 등)
- [ ] mkdocs 문서 사이트
- [ ] v0.1.0 릴리스 (CHANGELOG + GitHub release workflow)

---

## 변경 이력

- 2026-05-14: 최초 작성. Step 1 착수.
- 2026-05-14: Step 1.0 (Harness 문서), 1.1 (스캐폴딩), 1.2 (환경 재현) 완료. 다음은 1.3 (CI).
- 2026-05-14: Step 1.3 (CI) 완료. CI 워크플로 + release 워크플로 + tests 초기 구조 + dynamic version. 다음은 1.4 (Core 추상화).
- 2026-05-14: Step 1.4 (Core 추상화) 완료. exceptions/record/schema/connector/registry/context/pipeline + 57 unit tests. ADR-0003 추가. 다음은 1.5 (Config 로더).
- 2026-05-14: Step 1.5 (Config 로더) 완료. models/secrets/loader + 49 unit tests (총 106). ADR-0004 추가. 다음은 1.6 (Observability).
- 2026-05-14: Step 1.6 (Observability 베이스) 완료. logging/metrics/tracing ABC + NoOp + 30 unit tests (총 136). ADR-0005 추가. OTel 실제 구현은 Step 6로 이동, Pipeline 자동 계측은 Step 3로 이동. 다음은 1.7 (Utils).
- 2026-05-14: Step 1.7 (Utils) 완료. retry/chunk/async_io + 42 unit tests (총 178). ADR-0006 추가. Circuit Breaker/Rate Limiter/DLQ는 Step 3으로 이동. 다음은 1.8 (테스트 인프라).
- 2026-05-14: **Step 1.8 (테스트 인프라) + Step 1 전체 완료.** tests/fixtures/ + tests/contracts/ + 15 contract tests (총 193). ADR-0007 추가. 다음은 Step 2.1 (postgres 커넥터).
- 2026-05-14: **Step 2.1 (PostgreSQL 커넥터) 완료.** psycopg 3 기반 BatchSource + BatchSink (append=COPY / overwrite / upsert). 29 통합 테스트 (193 unit + 29 it = 222 total). ADR-0008 추가. contracts/batch.py에 read_kwargs/write_kwargs fixture 추가. 다음은 Step 2.2 (s3).
- 2026-05-14: **Step 2.2 (S3 커넥터) 완료.** boto3 기반 BatchSource + BatchSink (jsonl/csv/parquet). 21 unit + 35 integration tests (총 214 unit + 64 it = 278 tests). ADR-0009 추가. MinIO testcontainers 통합 — 같은 코드가 AWS S3/MinIO/R2 호환. 다음은 Step 2.3 (kafka).
- 2026-05-14: **Step 2.3 (Kafka 커넥터) + Step 2 전체 완료.** aiokafka 기반 StreamSource + StreamSink. Stream Contract 신규 도입(`_StreamSourceContract`/`_StreamSinkContract`/`_StreamRoundTripContract`). 16 통합 테스트 (총 214 unit + 80 it = 294 tests). ADR-0010 추가. 다음은 Step 3 (Pipeline 실행기 + CLI).
- 2026-05-14: **Step 3.1 (YAML 빌더 + Transforms + `etlx` CLI) 완료.** `etl_plugins/runtime/` (transforms/builder/runner) + Typer CLI 5 서브커맨드. 38 신규 unit tests (총 252 unit + 80 it = 332). ADR-0011 추가. 다음은 Step 3.2 (stream runtime + commit).
- 2026-05-14: **Step 3.2 (Stream runtime) 완료.** `Pipeline.arun_stream` + Kafka async `commit()` (ABC sync→async 보정) + buffer + `etlx run-stream`. 17 신규 unit + 2 Kafka integration tests (총 269 unit + 82 it = 351). ADR-0012 추가. 다음은 Step 3.3 (Retry / DLQ / Checkpoint).
- 2026-05-14: **Step 3.3 (Retry / DLQ / 자동 메트릭 / CLI logging) + Step 3 전체 완료.** Pipeline 자동 retry+DLQ+metrics, CLI global `--log-format/--log-level`. 13 신규 unit tests (총 282 unit + 82 it = 364). ADR-0013 추가. Checkpoint/Span은 Step 6 강화로 이동. 다음은 Step 4 (Orchestrator Adapters) 또는 Step 5 (커넥터 확장).
- 2026-05-14: **Step 4 (Orchestrator Adapters) 완료.** Airflow/Dagster/Prefect adapters with PEP 562 lazy loading. 10 신규 tests (총 289 unit + 3 skip + 82 it = 374). ADR-0014 추가. 다음은 Step 5 (커넥터 확장).
- 2026-05-14: **Step 5.1 (MySQL 커넥터) 완료.** PyMySQL 기반 BatchSource + BatchSink (append=executemany / overwrite=TRUNCATE+INSERT / upsert=ON DUPLICATE KEY UPDATE). 29 신규 통합 테스트 (총 289 unit + 3 skip + 111 it = 403). ADR-0015 추가. 다음은 Step 5.x (다른 RDBMS / NoSQL / DW).
