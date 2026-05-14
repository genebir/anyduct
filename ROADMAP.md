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

### 1.8 테스트 인프라  ← **다음 작업 (2026-05-14)**
- [ ] `tests/conftest.py` (공통 fixture)
- [ ] `tests/fixtures/` (표준 샘플 데이터셋)
- [ ] Contract test 베이스 (`tests/contracts/`)

---

## Step 2 — Reference Connectors (각 카테고리 1개씩 먼저)

### 2.1 `postgres` (BatchSource + BatchSink)
- [ ] 드라이버 결정 (psycopg vs asyncpg) — ADR
- [ ] `BatchSource.read` (chunked, server-side cursor)
- [ ] `BatchSink.write` (append/overwrite/upsert via `INSERT ... ON CONFLICT`)
- [ ] health_check
- [ ] Registry 등록
- [ ] Contract test 통과
- [ ] Integration test (testcontainers)

### 2.2 `s3` (BatchSource + BatchSink)
- [ ] parquet / csv / jsonl 포맷
- [ ] MinIO 호환 (로컬 테스트)
- [ ] BatchSource.read (key prefix → stream)
- [ ] BatchSink.write (multipart upload)
- [ ] Contract + integration test

### 2.3 `kafka` (StreamSource + StreamSink)
- [ ] 드라이버 결정 (confluent-kafka vs aiokafka) — ADR
- [ ] StreamSource.subscribe (group_id, offset 관리)
- [ ] StreamSource.commit (`at_least_once` 보장)
- [ ] StreamSink.publish / flush
- [ ] Schema Registry 연동 (avro/protobuf)
- [ ] Contract + integration test

---

## Step 3 — Pipeline 실행기 + CLI

- [ ] YAML → Pipeline 빌더 (`Pipeline.from_yaml`)
- [ ] `transforms` 처리기 (rename/cast/filter/python callable)
- [ ] `etlx` CLI (`run`, `run-stream`, `test-connection`, `list-connectors`, `schema`, `validate`, `version`)
- [ ] Retry / Circuit Breaker 적용
- [ ] Dead Letter Queue 라우팅
- [ ] Checkpoint / Cursor 저장 hook
- [ ] Stream sink 버퍼링 (`max_records`/`max_seconds`)
- [ ] `commit.strategy: after_sink_flush` 구현

---

## Step 4 — Orchestrator Adapters

- [ ] Airflow `ETLPluginsOperator`
- [ ] Dagster resource / op
- [ ] Prefect block / flow
- [ ] 각 어댑터 통합 예시 + 테스트

---

## Step 5 — Connector 확장

### 5.1 RDBMS
- [ ] MySQL/MariaDB
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
