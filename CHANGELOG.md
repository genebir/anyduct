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
