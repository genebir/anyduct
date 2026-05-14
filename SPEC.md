# ETL Plugins 설계 명세서 (SPEC)

> 이 문서는 프로젝트의 **마스터 설계 명세서**다. 세션 시작 시 읽는 요약 컨텍스트는 `CLAUDE.md`에 있다.
> 항목 변경 시 **반드시 `DECISIONS.md`에 ADR로 사유를 남기고** 이 문서를 갱신한다.

> **목적**: 모든 데이터 소스/싱크(RDBMS, NoSQL, DW, Stream, Object Storage 등)에 대해 통일된 인터페이스를 제공하고, 어떤 파이프라인 솔루션(Airflow, Dagster, Prefect, Luigi, 사내 워크플로우 등) 위에서도 그대로 재사용할 수 있는 **Python 기반 ETL 플러그인 라이브러리**를 구축한다. 또한 **여러 개발 환경에서 끊김 없이 작업을 이어갈 수 있도록** 모든 상태·결정·진행상황을 코드와 함께 관리한다.

---

## 1. 핵심 설계 원칙

| 원칙 | 설명 |
|---|---|
| **Backend-agnostic** | Connector 구현체만 추가하면 어떤 DB/스트림도 지원 |
| **Orchestrator-agnostic** | Airflow/Dagster/Prefect 등에 종속되지 않는 순수 Python 코어 |
| **Config-driven** | 코드 변경 없이 `.env` + `YAML`만으로 동작 변경 가능 |
| **Batch + Stream 통합** | 동일한 추상화 위에서 배치/스트리밍 모두 지원 |
| **Pluggable** | Entry point / Registry 패턴으로 외부 패키지에서 확장 가능 |
| **Type-safe** | Pydantic 기반 스키마/설정 검증 |
| **Observable** | 표준 로깅·메트릭·트레이싱 hook 내장 |
| **Portable (Harness)** | 어떤 PC/환경에서든 동일하게 재현·이어작업 가능 |

---

## 2. 전체 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│  Orchestrator Adapters (선택적)                         │
│  ├─ AirflowOperator   ├─ DagsterResource                │
│  ├─ PrefectBlock      └─ Plain Python (CLI/Function)    │
├─────────────────────────────────────────────────────────┤
│  Pipeline Core                                          │
│  ├─ Pipeline / Task   ├─ Context (run_id, logger…)      │
│  └─ Hooks (pre/post, on_error, on_retry)                │
├─────────────────────────────────────────────────────────┤
│  Connector Abstraction                                  │
│  ├─ BatchSource / BatchSink                             │
│  ├─ StreamSource / StreamSink                           │
│  └─ Record / Schema / Cursor                            │
├─────────────────────────────────────────────────────────┤
│  Connector Implementations (플러그인)                   │
│  ├─ RDBMS:  postgres, mysql, oracle, mssql, sqlite      │
│  ├─ NoSQL:  mongo, redis, dynamodb, cassandra           │
│  ├─ DW:     snowflake, bigquery, redshift, clickhouse   │
│  ├─ Stream: kafka, kinesis, pulsar, rabbitmq, nats      │
│  └─ Object: s3, gcs, azure_blob, local_fs               │
├─────────────────────────────────────────────────────────┤
│  Foundation                                             │
│  ├─ Config Loader (.env + YAML + Jinja templating)      │
│  ├─ Secret Resolver (env / Vault / AWS SM / GCP SM)     │
│  ├─ Logging / Metrics / Tracing                         │
│  └─ Retry / Circuit Breaker / Rate Limiter              │
└─────────────────────────────────────────────────────────┘
```

---

## 3. 디렉토리 구조

```
etl-plugins/
├── CLAUDE.md                     # Claude Code 컨텍스트 (필수)
├── DEVELOPMENT.md                # 개발 환경 인계 문서
├── ROADMAP.md                    # 단계별 작업 계획 + 진행 상태
├── DECISIONS.md                  # 설계 결정 기록 (ADR)
├── CHANGELOG.md
├── README.md
├── pyproject.toml
├── uv.lock                       # 또는 poetry.lock
├── .env.example
├── .editorconfig
├── .pre-commit-config.yaml
├── .gitignore
├── Makefile                      # 또는 Taskfile.yaml
├── .devcontainer/
│   └── devcontainer.json
├── docker/
│   ├── docker-compose.dev.yml    # 로컬 DB/Stream 일괄 기동
│   └── Dockerfile
├── .github/workflows/
│   ├── ci.yml
│   └── release.yml
├── configs/
│   ├── connections.yaml          # 모든 연결 정의
│   ├── pipelines/
│   │   └── orders_to_dw.yaml
│   └── logging.yaml
├── scripts/
│   ├── bootstrap.sh              # 원커맨드 환경 셋업
│   └── reset_state.sh
├── etl_plugins/
│   ├── __init__.py
│   ├── core/
│   │   ├── connector.py
│   │   ├── record.py
│   │   ├── schema.py
│   │   ├── pipeline.py
│   │   ├── context.py
│   │   ├── registry.py
│   │   └── exceptions.py
│   ├── config/
│   │   ├── loader.py
│   │   ├── models.py
│   │   └── secrets.py
│   ├── connectors/
│   │   ├── rdbms/                # postgres.py, mysql.py, ...
│   │   ├── nosql/
│   │   ├── warehouse/
│   │   ├── stream/               # kafka.py, kinesis.py, ...
│   │   └── object_storage/
│   ├── adapters/                 # Orchestrator 통합
│   │   ├── airflow.py
│   │   ├── dagster.py
│   │   └── prefect.py
│   ├── observability/
│   │   ├── logging.py
│   │   ├── metrics.py
│   │   └── tracing.py
│   ├── utils/
│   │   ├── retry.py
│   │   ├── chunk.py
│   │   └── async_io.py
│   └── cli.py                    # `etlx ...` CLI
└── tests/
    ├── unit/
    ├── integration/              # testcontainers 기반
    ├── fixtures/
    └── conftest.py
```

---

## 4. 핵심 추상화 (Core Abstractions)

### 4.1 Connector 계층 구조

```python
# etl_plugins/core/connector.py
from abc import ABC, abstractmethod
from typing import AsyncIterator, Iterator

class Connector(ABC):
    """모든 커넥터의 최상위 베이스."""
    name: str                       # 레지스트리 키 (예: "postgres")

    @abstractmethod
    def connect(self) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    @abstractmethod
    def health_check(self) -> bool: ...

    def __enter__(self): self.connect(); return self
    def __exit__(self, *exc): self.close()

# --- Batch ---
class BatchSource(Connector):
    @abstractmethod
    def read(self, query: str | None = None, *, chunk_size: int = 10_000
            ) -> Iterator["Record"]: ...

class BatchSink(Connector):
    @abstractmethod
    def write(self, records: Iterator["Record"], *,
              mode: str = "append",            # append | overwrite | upsert
              key_columns: list[str] | None = None) -> int: ...

# --- Stream ---
class StreamSource(Connector):
    @abstractmethod
    def subscribe(self, topic: str, *, group_id: str | None = None
                 ) -> AsyncIterator["Record"]: ...
    @abstractmethod
    def commit(self, offsets) -> None: ...

class StreamSink(Connector):
    @abstractmethod
    async def publish(self, topic: str, record: "Record",
                      key: bytes | None = None) -> None: ...
    @abstractmethod
    async def flush(self) -> None: ...
```

> **Hybrid 커넥터**: Kafka처럼 produce/consume 모두 가능한 경우 `StreamSource` + `StreamSink`를 다중 상속.

### 4.2 표준 Record 모델

```python
# etl_plugins/core/record.py
from pydantic import BaseModel
from typing import Any

class Record(BaseModel):
    data: dict[str, Any]                 # 정규화된 페이로드
    metadata: dict[str, Any] = {}        # source, ingested_at, offset, key…
    schema_version: str | None = None
```

- DB row, Kafka message, S3 line 등 **모든 입력을 `Record`로 정규화**.
- 커넥터별 raw 객체나 위치 정보(offset, lsn, sequence_number 등)는 `metadata`에 보관.

### 4.3 Plugin Registry

```python
# etl_plugins/core/registry.py
class ConnectorRegistry:
    _registry: dict[str, type[Connector]] = {}

    @classmethod
    def register(cls, name: str):
        def deco(klass): cls._registry[name] = klass; return klass
        return deco

    @classmethod
    def get(cls, name: str) -> type[Connector]:
        if name not in cls._registry:
            raise KeyError(f"Connector '{name}' not registered")
        return cls._registry[name]
```

- 내장 커넥터: `@ConnectorRegistry.register("postgres")`로 import 시점에 자동 등록.
- 외부 패키지: `pyproject.toml`의 **entry points**로 자동 발견.
  ```toml
  [project.entry-points."etl_plugins.connectors"]
  clickhouse = "my_pkg.clickhouse:ClickhouseConnector"
  ```

### 4.4 Pipeline / Task

```python
# 순수 Python에서 그대로 사용 가능, 모든 어댑터가 이 위에 얇게 얹힘
pipeline = Pipeline("orders_to_dw")
pipeline.add(
    Task.extract(source="pg_prod", query="SELECT * FROM orders WHERE updated_at > :ts")
        .transform(lambda r: ...)
        .load(sink="snowflake_dw", table="ANALYTICS.ORDERS", mode="upsert",
              key_columns=["order_id"])
)
pipeline.run(context=Context(run_id="...", logger=...))
```

---

## 5. 설정 파일 관리

### 5.1 역할 분리 원칙

| 파일 | 담는 것 | 담지 않는 것 |
|---|---|---|
| `.env` | **시크릿 값**: 비밀번호, API 키, 토큰, 인증서 경로 | 구조·스키마·로직 |
| `connections.yaml` | **연결 구조**: host, port, db명, 옵션, 시크릿 **참조** | 평문 시크릿 |
| `pipelines/*.yaml` | **파이프라인 정의**: source → transform → sink | 연결 세부정보 |
| `logging.yaml` | 로깅 핸들러/포맷/레벨 | 비즈니스 설정 |

원칙: **시크릿은 `.env` 또는 외부 시크릿 백엔드에만**, **구조는 YAML에만**. YAML은 git에 커밋, `.env`는 절대 커밋 금지(`.env.example`만 커밋).

### 5.2 `.env.example`

```bash
# === RDBMS ===
PG_PROD_PASSWORD=changeme
MYSQL_ANALYTICS_PASSWORD=changeme
ORACLE_LEGACY_PASSWORD=changeme

# === Cloud DW ===
SNOWFLAKE_PRIVATE_KEY_PATH=/secrets/sf_key.p8
BIGQUERY_SERVICE_ACCOUNT_JSON=/secrets/bq-sa.json
REDSHIFT_PASSWORD=changeme

# === NoSQL ===
MONGO_URI_PASSWORD=changeme
REDIS_PASSWORD=changeme

# === Stream ===
KAFKA_SASL_USERNAME=
KAFKA_SASL_PASSWORD=
KINESIS_AWS_ACCESS_KEY_ID=
KINESIS_AWS_SECRET_ACCESS_KEY=
PULSAR_TOKEN=

# === Object Storage ===
S3_ACCESS_KEY=
S3_SECRET_KEY=
GCS_SERVICE_ACCOUNT_JSON=

# === Secret Backend (선택) ===
SECRET_BACKEND=env            # env | vault | aws_sm | gcp_sm
VAULT_ADDR=
VAULT_TOKEN=

# === Runtime ===
ETLX_ENV=local                # local | dev | stg | prod
LOG_LEVEL=INFO
```

### 5.3 `configs/connections.yaml`

```yaml
# ${VAR} 는 .env / 환경변수에서 치환
# !secret <path> 는 시크릿 백엔드에서 조회

connections:
  pg_prod:
    type: postgres
    host: db.prod.internal
    port: 5432
    database: orders
    user: etl_user
    password: ${PG_PROD_PASSWORD}
    options:
      sslmode: require
      application_name: etl-plugins
      pool_size: 10

  snowflake_dw:
    type: snowflake
    account: my-org.ap-northeast-2.aws
    user: ETL_SVC
    private_key_path: ${SNOWFLAKE_PRIVATE_KEY_PATH}
    warehouse: ETL_WH
    database: ANALYTICS
    role: ETL_ROLE

  kafka_events:
    type: kafka
    bootstrap_servers:
      - kafka-1.internal:9092
      - kafka-2.internal:9092
    security_protocol: SASL_SSL
    sasl_mechanism: SCRAM-SHA-512
    sasl_username: ${KAFKA_SASL_USERNAME}
    sasl_password: ${KAFKA_SASL_PASSWORD}
    consumer:
      auto_offset_reset: earliest
      enable_auto_commit: false
      max_poll_records: 500
    producer:
      acks: all
      compression_type: zstd

  s3_lake:
    type: s3
    region: ap-northeast-2
    bucket: my-data-lake
    access_key: ${S3_ACCESS_KEY}
    secret_key: ${S3_SECRET_KEY}
```

### 5.4 `configs/pipelines/orders_to_dw.yaml`

```yaml
name: orders_to_dw
schedule: "0 */1 * * *"        # 오케스트레이터가 사용
mode: batch                    # batch | stream

source:
  connection: pg_prod
  query: |
    SELECT * FROM public.orders
    WHERE updated_at > :last_run_at
  chunk_size: 50000

transforms:
  - type: rename
    mapping: { order_id: ORDER_ID, updated_at: UPDATED_AT }
  - type: cast
    columns: { ORDER_ID: int64, UPDATED_AT: timestamp }
  - type: python
    callable: my_project.transforms:enrich_orders

sink:
  connection: snowflake_dw
  table: ANALYTICS.ORDERS
  mode: upsert
  key_columns: [ORDER_ID]

retry:
  max_attempts: 3
  backoff: exponential
  initial_delay_seconds: 5

observability:
  metrics: { enabled: true, namespace: etl_plugins }
  tracing: { enabled: true, exporter: otlp }
```

### 5.5 `configs/pipelines/events_stream.yaml` (Stream 예시)

```yaml
name: events_stream
mode: stream

source:
  connection: kafka_events
  topic: user.events.v1
  group_id: etl-plugins-events
  format: json                 # json | avro | protobuf
  schema_registry:
    url: https://schemas.internal
    subject: user.events.v1

transforms:
  - type: filter
    expr: "data['event_type'] in ['signup', 'purchase']"

sink:
  connection: snowflake_dw
  table: RAW.USER_EVENTS
  mode: append
  buffer:
    max_records: 10000
    max_seconds: 30

commit:
  strategy: after_sink_flush   # at_least_once 보장
```

---

## 6. 지원 시스템 (최소 목표)

| 카테고리 | 대상 | 비고 |
|---|---|---|
| **RDBMS** | PostgreSQL, MySQL/MariaDB, Oracle, MSSQL, SQLite | SQLAlchemy + 네이티브 드라이버 병행 |
| **NoSQL** | MongoDB, Redis, Cassandra, DynamoDB | |
| **Data Warehouse** | Snowflake, BigQuery, Redshift, ClickHouse | bulk load API 우선 |
| **Streaming** | Kafka, Kinesis, Pulsar, RabbitMQ, NATS | exactly-once는 best-effort, at-least-once 보장 |
| **CDC** | Debezium(Kafka 위), PostgreSQL logical replication | StreamSource로 래핑 |
| **Object Storage** | S3, GCS, Azure Blob, Local FS | parquet/csv/jsonl 지원 |
| **HTTP/REST** | 일반 REST API source | pagination 추상화 포함 |

---

## 7. 파이프라인 솔루션 통합 (Orchestrator 무관성)

코어는 **순수 Python**이며 아래 어댑터는 얇은 래퍼만 제공한다.

### 7.1 Airflow

```python
# etl_plugins/adapters/airflow.py
from airflow.models import BaseOperator
from etl_plugins.core.pipeline import Pipeline

class ETLPluginsOperator(BaseOperator):
    def __init__(self, pipeline_yaml: str, **kw):
        super().__init__(**kw); self.pipeline_yaml = pipeline_yaml
    def execute(self, context):
        Pipeline.from_yaml(self.pipeline_yaml).run()
```

### 7.2 Dagster

```python
@op(required_resource_keys={"etl_plugins"})
def run_etl(context, pipeline_yaml: str):
    context.resources.etl_plugins.run(pipeline_yaml)
```

### 7.3 Prefect

```python
@flow
def etl_flow(pipeline_yaml: str):
    Pipeline.from_yaml(pipeline_yaml).run()
```

### 7.4 CLI / Plain Python

```bash
etlx run configs/pipelines/orders_to_dw.yaml
etlx run-stream configs/pipelines/events_stream.yaml
etlx test-connection pg_prod
etlx list-connectors
```

---

## 8. 하네스 코딩 (Harness / Portability)

> **목표**: 노트북·데스크탑·서버·CI 어느 환경에서 클론해도 **동일한 상태에서 즉시 이어 작업** 가능해야 한다. 사람이든 Claude Code든 마찬가지.

### 8.1 환경 재현 (Reproducibility)

1. **단일 패키지 매니저 고정**: `uv`(권장) 또는 `poetry`. `uv.lock` / `poetry.lock`은 **반드시 커밋**.
2. **Python 버전 고정**: `.python-version` 파일 + `pyproject.toml`의 `requires-python`.
3. **Devcontainer 제공**: `.devcontainer/devcontainer.json` 으로 VS Code / Cursor에서 원클릭 동일 환경.
4. **Docker Compose for dev**: `docker/docker-compose.dev.yml` 에 Postgres, MySQL, Kafka, Redis, MinIO(S3 호환) 등 일괄 정의 → 로컬 통합 테스트 즉시 가능.
5. **`scripts/bootstrap.sh`** (원커맨드 셋업):
   ```bash
   #!/usr/bin/env bash
   set -euo pipefail
   command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
   uv sync --all-extras
   cp -n .env.example .env || true
   uv run pre-commit install
   docker compose -f docker/docker-compose.dev.yml up -d
   uv run etlx test-connection --all
   echo "✅ Ready. See ROADMAP.md for next steps."
   ```
6. **Makefile** 표준 타깃: `setup`, `test`, `lint`, `fmt`, `up`, `down`, `clean`, `docs`.

### 8.2 작업 상태의 코드화 (State as Code)

다른 환경/다른 작업자가 이어가려면 **머릿속에 있는 컨텍스트를 파일로 남겨야** 한다.

- **`CLAUDE.md`** — Claude Code가 매 세션 처음에 읽는 컨텍스트.
  - 프로젝트 목적, 아키텍처 요약, 명령어, 디렉토리 규약, 현재 단계, 금기사항.
  - 예: "현재 Step 3 진행 중. 새 커넥터 추가 시 반드시 Registry 데코레이터 사용. `connections.yaml`에 평문 시크릿 금지."
- **`ROADMAP.md`** — 단계별 작업 + 체크박스로 진행 상태.
  ```markdown
  ## Step 1: Foundation
  - [x] Core abstractions (Connector, Record, Registry)
  - [x] Config loader (.env + YAML)
  - [ ] Logging / metrics
  ## Step 2: RDBMS connectors
  - [ ] PostgreSQL  ← **현재 작업 중 (2025-05-14)**
  - [ ] MySQL
  ```
- **`DECISIONS.md`** — ADR(Architecture Decision Records).
  ```markdown
  ## ADR-0003: Pydantic v2 채택
  - Date: 2025-05-10
  - Status: Accepted
  - Context: 설정 검증 라이브러리 선택
  - Decision: Pydantic v2
  - Consequences: Python 3.10+ 필수
  ```
- **`DEVELOPMENT.md`** — 다른 PC에서 처음 시작하는 사람을 위한 인계 문서.
  - prerequisite, bootstrap, troubleshooting, 자주 쓰는 명령어.
- **`CHANGELOG.md`** — Keep a Changelog 포맷.

### 8.3 Claude Code 친화 규약

- **`CLAUDE.md`는 반드시 루트에 둘 것** — Claude Code가 자동으로 인지.
- **작업 단위(commit)는 작게**: 하나의 커넥터 / 하나의 기능 단위. 이어받는 쪽이 git log만 봐도 흐름 파악 가능.
- **모든 PR/커밋 메시지에 ROADMAP의 Step 번호 참조**: `feat(connector): add postgres source [Step 2.1]`.
- **TODO 주석에는 컨텍스트 포함**: `# TODO(Step 3.2): chunked upsert via MERGE` 처럼.
- **장기 작업 중단 시**: `ROADMAP.md`의 해당 항목에 `← 작업 중 (YYYY-MM-DD, 다음 할 일: ...)` 메모 남기기.
- **세션 종료 시 체크리스트**: ① ROADMAP 갱신 ② DECISIONS 추가 ③ CHANGELOG 메모 ④ 커밋·푸시.

### 8.4 품질 게이트 (모든 환경에서 동일)

- **`.pre-commit-config.yaml`**: `ruff`(lint+format), `mypy`, `detect-secrets`, `yamllint`.
- **CI (`.github/workflows/ci.yml`)**: lint → type check → unit → integration(testcontainers) → 커버리지 리포트.
- **`.editorconfig`**: 들여쓰기/개행 통일.
- **버전 동기화**: `pyproject.toml`의 버전과 `CHANGELOG.md`가 일치하도록 release workflow에서 검증.

---

## 9. 추가 고려 사항

### 9.1 신뢰성

- **Retry**: 지수 백오프 + jitter. 데코레이터 `@retryable(max_attempts=, on=...)`.
- **Circuit Breaker**: 외부 시스템 연속 실패 시 차단.
- **Idempotency**: 모든 sink write에 `idempotency_key` 옵션. upsert는 `key_columns` 필수.
- **Checkpoint / Cursor**: BatchSource는 `last_run_at`/`max_id` 등 커서 저장 hook 제공. StreamSource는 offset commit 전략 명시.
- **Dead Letter Queue**: 변환·sink 실패 레코드를 별도 sink로 라우팅.

### 9.2 관찰성 (Observability)

- **로깅**: `structlog` 기반 JSON 구조화 로그. `run_id`, `pipeline`, `task`, `connector`를 모든 라인에 포함.
- **메트릭**: OpenTelemetry / Prometheus. 표준 메트릭: `records_read_total`, `records_written_total`, `errors_total`, `lag_seconds`, `duration_seconds`.
- **트레이싱**: OTLP exporter. 각 Task가 하나의 span.
- **데이터 라인리지** (선택): OpenLineage 이벤트 emit.

### 9.3 보안

- 시크릿은 절대 로그/예외 메시지에 노출 금지 — 로깅 필터로 마스킹.
- TLS/SSL 기본 활성화, 옵션으로만 비활성.
- 시크릿 백엔드 추상화: `env` / `vault` / `aws_sm` / `gcp_sm` 인터페이스 통일.
- `detect-secrets` pre-commit hook으로 사고 방지.

### 9.4 성능

- Chunk/Batch 사이즈 설정 가능, 메모리 안전한 iterator/generator 기반.
- 대용량 sink는 **bulk load API 우선** (`COPY`, `MERGE`, Snowflake `PUT/COPY INTO`, BigQuery load job).
- I/O 바운드 작업은 `asyncio` 또는 thread pool 선택 가능.
- Stream consumer는 백프레셔(backpressure) 지원.

### 9.5 테스트 전략

- **Unit**: 추상 인터페이스 / 변환 / 설정 로더. 외부 의존 없이.
- **Integration**: `testcontainers-python`으로 실제 Postgres/Kafka/MinIO 컨테이너 띄워 검증.
- **Contract test**: 새 커넥터는 공통 contract test suite 통과 필수 (read 후 write 후 read 일치 등).
- **Fixture**: 표준 샘플 데이터셋을 `tests/fixtures/`에 두고 모든 커넥터 테스트가 동일 데이터 사용.

### 9.6 CLI (`etlx`)

```bash
etlx run <pipeline.yaml>            # 배치 실행
etlx run-stream <pipeline.yaml>     # 스트림 실행
etlx test-connection <name|--all>   # 연결 점검
etlx list-connectors                # 등록된 커넥터 목록
etlx schema <connection> <table>    # 원격 스키마 조회
etlx validate <config.yaml>         # 설정 검증만
etlx version
```

---

## 10. 구현 로드맵 (`ROADMAP.md` 시드)

### Step 1 — Foundation
- [ ] 프로젝트 스캐폴딩 (`pyproject.toml`, `uv.lock`, pre-commit, CI)
- [ ] `CLAUDE.md` / `DEVELOPMENT.md` / `ROADMAP.md` / `DECISIONS.md` 초기 작성
- [ ] `scripts/bootstrap.sh`, `docker-compose.dev.yml`
- [ ] Core 추상화 (Connector, Record, Registry, Pipeline, Context)
- [ ] Config loader (.env + YAML + 시크릿 리졸버)
- [ ] Logging / Metrics / Tracing 베이스

### Step 2 — Reference Connectors (각 카테고리 1개씩 먼저)
- [ ] `postgres` (BatchSource + BatchSink)
- [ ] `s3` (BatchSource + BatchSink, parquet/csv/jsonl)
- [ ] `kafka` (StreamSource + StreamSink)

### Step 3 — Pipeline 실행기 + CLI
- [ ] YAML → Pipeline 빌더
- [ ] `etlx` CLI
- [ ] Retry / DLQ / Checkpoint

### Step 4 — Orchestrator Adapters
- [ ] Airflow operator
- [ ] Dagster resource/op
- [ ] Prefect block/flow

### Step 5 — Connector 확장
- [ ] MySQL, MSSQL, Oracle, SQLite
- [ ] Snowflake, BigQuery, Redshift, ClickHouse
- [ ] MongoDB, Redis, DynamoDB, Cassandra
- [ ] Kinesis, Pulsar, RabbitMQ, NATS
- [ ] GCS, Azure Blob

### Step 6 — 강화
- [ ] OpenLineage 이벤트
- [ ] Contract test suite 완성
- [ ] 문서 사이트 (mkdocs)
- [ ] 첫 릴리스 (v0.1.0)

---

## 11. Claude Code 작업 지침 (요약)

이 문서를 Claude Code에 넘길 때 다음 순서를 따르도록 안내한다.

1. **`CLAUDE.md`부터 만들 것** — 이후 모든 세션의 컨텍스트가 됨.
2. **Step 단위로 PR 단위 작업** — 한 번에 여러 Step을 섞지 말 것.
3. **새 커넥터 추가 시 contract test 통과 필수**.
4. **`.env`에 들어갈 값은 절대 코드/YAML에 하드코딩 금지**.
5. **모든 결정 사항은 `DECISIONS.md`에 ADR로 기록**.
6. **작업 종료 시 `ROADMAP.md`의 체크박스와 "현재 작업 중" 마커 갱신** — 다음 환경/세션이 이어받을 수 있도록.
7. **세션 시작 시 반드시 `CLAUDE.md` → `ROADMAP.md` → 최근 커밋 로그 순서로 확인**.
