# Changelog

이 파일은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 형식을 따르며,
[Semantic Versioning](https://semver.org/spec/v2.0.0.html)을 따른다.

릴리스 시 `pyproject.toml`의 버전과 이 파일의 헤더가 일치해야 한다 (release workflow에서 검증).

---

## [Unreleased]

### Added
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
