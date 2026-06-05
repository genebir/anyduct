# ETL Plugins 설계 명세서 (SPEC)

> 이 문서는 프로젝트의 **마스터 설계 명세서**다. 세션 시작 시 읽는 요약 컨텍스트는 `CLAUDE.md`에 있다.
> 항목 변경 시 **반드시 `DECISIONS.md`에 ADR로 사유를 남기고** 이 문서를 갱신한다.

> **목적**: 모든 데이터 소스/싱크(RDBMS, NoSQL, DW, Stream, Object Storage 등)에 대해 통일된 인터페이스를 제공하고, 어떤 파이프라인 솔루션(Airflow, Dagster, Prefect, Luigi, 사내 워크플로우 등) 위에서도 그대로 재사용할 수 있는 **Python 기반 ETL 플러그인 라이브러리**를 구축한다. 추가로, 이 라이브러리 위에 **웹 UI 기반 ETL 서비스**(파이프라인·연결·스케줄을 시각적으로 만들고 모니터링)를 별도 패키지로 얹어 비개발자도 사용 가능하게 한다. 또한 **여러 개발 환경에서 끊김 없이 작업을 이어갈 수 있도록** 모든 상태·결정·진행상황을 코드와 함께 관리한다.

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
| **Service-layer optional** | 웹 UI/API/실행엔진은 별도 패키지. **코어는 service 패키지를 import하지 않는다.** 라이브러리 단독으로 완전히 동작. |

---

## 2. 전체 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│  Web UI (services/etlx-web, Next.js)            ← Step 10│
│  파이프라인 빌더 · 스케줄 · 모니터링 · 연결 관리          │
├─────────────────────────────────────────────────────────┤
│  REST API (services/etlx-server, FastAPI)       ← Step 8 │
│  Auth / RBAC / Audit / CRUD / Test-connection           │
├─────────────────────────────────────────────────────────┤
│  Metadata Store (PostgreSQL + Alembic)          ← Step 7 │
│  + Secret Backend (Vault / AWS SM / GCP SM)             │
├─────────────────────────────────────────────────────────┤
│  Execution Engine                               ← Step 9 │
│  ├─ Batch:  Dagster or Prefect (임베드, 미확정)         │
│  └─ Stream: 자체 워커 매니저 (K8s Deployment)           │
├─════════════════════════════════════════════════════════┤
│  ↑ 위 4개 층은 선택적. 아래 5개 층(= etl_plugins 코어)는 │
│    서비스 없이도 단독으로 완전 동작한다.                │
├─════════════════════════════════════════════════════════┤
│  Orchestrator Adapters (선택적)                          │
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
│  ├─ RDBMS:  postgres, mysql, sqlite, oracle, mssql      │
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

상하 단방향 의존: 위 층은 아래 층을 import해도 되지만, 아래 층은 위 층을 **절대** import하지 않는다. 즉 `etl_plugins/*`는 `services/etlx-server/*`나 `services/etlx-web/*`를 모른다. 서비스 패키지가 통째로 사라져도 코어 라이브러리는 그대로 동작해야 한다.

---

## 3. 디렉토리 구조

```
etl-plugins/                          # 리포 루트 (모노레포)
├── CLAUDE.md                         # Claude Code 컨텍스트 (필수)
├── DESIGN.md                         # etlx-web 디자인 시스템 (Step 10 SSOT)
├── DEVELOPMENT.md                    # 개발 환경 인계 문서
├── ROADMAP.md                        # 단계별 작업 계획 + 진행 상태
├── DECISIONS.md                      # 설계 결정 기록 (ADR)
├── CHANGELOG.md
├── README.md
├── pyproject.toml                    # 코어 라이브러리 메타
├── uv.lock
├── .env.example
├── .editorconfig
├── .pre-commit-config.yaml
├── .gitignore
├── Makefile
├── .devcontainer/
├── docker/
│   ├── docker-compose.dev.yml        # 로컬 DB/Stream 일괄 기동
│   └── Dockerfile
├── .github/workflows/                # ci.yml, release.yml
├── configs/
│   ├── connections.yaml
│   ├── pipelines/
│   └── logging.yaml
├── scripts/
│   ├── bootstrap.sh
│   └── reset_state.sh
│
├── etl_plugins/                      # ─── 코어 라이브러리 (Steps 1~6)
│   ├── __init__.py
│   ├── core/                         # connector, record, schema, pipeline, context, registry, exceptions
│   ├── config/                       # loader, models, secrets
│   ├── connectors/
│   │   ├── rdbms/                    # postgres.py, mysql.py, sqlite.py, ...
│   │   ├── nosql/
│   │   ├── warehouse/
│   │   ├── stream/                   # kafka.py, kinesis.py, ...
│   │   └── object_storage/
│   ├── adapters/                     # airflow.py, dagster.py, prefect.py (PEP 562 lazy)
│   ├── observability/                # logging, metrics, tracing
│   ├── runtime/                      # transforms, builder, runner
│   ├── utils/                        # retry, chunk, async_io
│   └── cli.py                        # `etlx ...` CLI
│
├── services/                         # ─── 서비스 패키지 (Steps 7~11, 별도 pyproject.toml)
│   ├── etlx-server/                  # FastAPI 백엔드
│   │   ├── pyproject.toml            # etl-plugins(코어)에 의존, 코어는 이걸 모름
│   │   ├── etlx_server/
│   │   │   ├── __init__.py
│   │   │   ├── main.py               # FastAPI app
│   │   │   ├── api/                  # /connections, /pipelines, /runs, /schedules, /auth
│   │   │   ├── db/                   # SQLAlchemy 모델 + Alembic 마이그레이션
│   │   │   ├── auth/                 # OIDC/JWT, RBAC, audit
│   │   │   ├── scheduler/            # 스케줄러 동기화 (DB → 실행엔진)
│   │   │   ├── workers/              # Stream worker manager
│   │   │   └── execution/            # Dagster/Prefect 통합 또는 자체 실행기
│   │   └── tests/
│   ├── etlx-web/                     # Next.js 프론트엔드
│   │   ├── package.json
│   │   ├── tailwind.config.ts        # DESIGN.md §11.2 토큰 import
│   │   ├── app/                      # App Router
│   │   ├── components/
│   │   │   ├── ui/                   # shadcn/ui 기반 (토큰 치환됨)
│   │   │   ├── pipeline-builder/     # React Flow 기반 노드 에디터
│   │   │   ├── schedule-editor/      # cron 빌더
│   │   │   ├── connection-form/
│   │   │   └── run-viewer/
│   │   ├── styles/globals.css        # DESIGN.md §11.1 CSS variables
│   │   ├── .storybook/               # 컴포넌트 카탈로그
│   │   ├── lib/                      # API client (OpenAPI generated)
│   │   └── tests/
│   └── docker-compose.services.yml   # server + web + metadata DB 일괄 기동
│
└── tests/                            # 코어 테스트 (단위/통합/contracts/fixtures)
    ├── unit/
    ├── integration/                  # testcontainers 기반
    ├── contracts/                    # _BatchSourceContract, _StreamSourceContract, ...
    ├── fixtures/
    └── conftest.py
```

**중요한 의존 규칙**:
- `etl_plugins/`는 `services/`를 `import` 금지.
- `services/etlx-server/`는 `etl_plugins`(PyPI 패키지로)를 의존성으로 install.
- `services/etlx-web/`는 백엔드와 HTTP로만 통신. Python 코어 직접 호출 금지.

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
    async def commit(self, offsets=None) -> None: ...   # async per ADR-0012

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

> **서비스화 이후**: UI에서 만든 파이프라인은 metadata DB에 저장되지만, YAML import/export는 양방향 지원해야 한다 (개발자는 git, 비개발자는 UI). 시크릿은 UI에서 입력받아도 metadata DB에는 **참조만** 저장하고 실제 값은 시크릿 백엔드에 위임. 자세한 내용은 §12.

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
schedule: "0 */1 * * *"        # 오케스트레이터/스케줄러가 사용
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

dlq:
  connection: dlq_sink
  table: failed_orders

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
  format: json
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
| **RDBMS** | PostgreSQL ✅, MySQL ✅, SQLite ✅, Vertica ✅, MSSQL ✅, Oracle | 네이티브 드라이버 lazy import |
| **NoSQL** | MongoDB ✅, Redis, Cassandra, DynamoDB | |
| **Data Warehouse** | Snowflake ✅, BigQuery ✅, Redshift ✅, ClickHouse ✅ | bulk load API 우선 (ClickHouse는 append/overwrite만 — 행단위 upsert 미지원; load job·COPY는 후속) |
| **Streaming** | Kafka ✅, Kinesis, Pulsar, RabbitMQ, NATS | exactly-once는 best-effort, at-least-once 보장 |
| **CDC** | Debezium(Kafka 위), PostgreSQL logical replication | StreamSource로 래핑 |
| **Object Storage** | S3 ✅, GCS, Azure Blob, Local FS | parquet/csv/jsonl 지원 |
| **HTTP/REST** | 일반 REST API source | pagination 추상화 포함 |

✅ = 2026-05-14 현재 구현 완료. 자세한 진행은 `ROADMAP.md`.

---

## 7. 파이프라인 솔루션 통합 (Orchestrator 무관성)

코어는 **순수 Python**이며 아래 어댑터는 얇은 래퍼만 제공한다. 세 어댑터 모두 PEP 562 lazy 로딩 — orchestrator 미설치 시에도 모듈 import는 성공.

### 7.1 Airflow

```python
from airflow import DAG
from etl_plugins.adapters.airflow import ETLPluginsOperator

with DAG("etl_demo", schedule="@hourly", ...) as dag:
    ETLPluginsOperator(
        task_id="orders_to_dw",
        pipeline_yaml="configs/pipelines/orders_to_dw.yaml",
        connections="configs/connections.yaml",
    )
```

### 7.2 Dagster

```python
from dagster import job
from etl_plugins.adapters.dagster import EtlPluginsResource, etl_plugins_op

@job(resource_defs={"etl_plugins": EtlPluginsResource(connections_path="configs/connections.yaml")})
def my_job():
    etl_plugins_op()
```

### 7.3 Prefect

```python
from etl_plugins.adapters.prefect import run_etl_pipeline_flow
run_etl_pipeline_flow("configs/pipelines/orders_to_dw.yaml", "configs/connections.yaml")
```

### 7.4 CLI / Plain Python

```bash
etlx run configs/pipelines/orders_to_dw.yaml
etlx run-stream configs/pipelines/events_stream.yaml
etlx test-connection pg_prod
etlx list-connectors
etlx validate <config.yaml>
etlx --log-format console run ...
```

---

## 8. 하네스 코딩 (Harness / Portability)

> **목표**: 노트북·데스크탑·서버·CI 어느 환경에서 클론해도 **동일한 상태에서 즉시 이어 작업** 가능해야 한다. 사람이든 Claude Code든 마찬가지.

### 8.1 환경 재현 (Reproducibility)

1. **단일 패키지 매니저 고정**: `uv`. `uv.lock`은 **반드시 커밋**.
2. **Python 버전 고정**: `.python-version` (3.11) + `pyproject.toml`의 `requires-python>=3.11`.
3. **Devcontainer 제공** (optional): `.devcontainer/devcontainer.json`.
4. **Docker Compose for dev**: `docker/docker-compose.dev.yml`에 Postgres, MySQL, Kafka, Redis, MinIO 일괄 정의.
5. **`scripts/bootstrap.sh`** 원커맨드 셋업.
6. **Makefile** 표준 타깃: `setup`, `test`, `lint`, `fmt`, `up`, `down`, `clean`, `docs`.

### 8.2 작업 상태의 코드화 (State as Code)

- **`CLAUDE.md`** — 세션 시작 시 컨텍스트.
- **`ROADMAP.md`** — Step 진행 체크박스.
- **`DECISIONS.md`** — ADR.
- **`DEVELOPMENT.md`** — 신규 환경 인계.
- **`CHANGELOG.md`** — Keep a Changelog.

### 8.3 Claude Code 친화 규약

- `CLAUDE.md` 루트 배치.
- 커밋 단위 작게, 메시지에 Step 번호.
- TODO 주석에 Step 번호.
- 장기 중단 시 `← 작업 중 (YYYY-MM-DD, 다음 할 일: ...)`.
- 세션 종료 시: ① ROADMAP 갱신 ② DECISIONS 추가 ③ CHANGELOG 메모 ④ 커밋·푸시.

### 8.4 품질 게이트 (모든 환경에서 동일)

- `.pre-commit-config.yaml`: `ruff`, `mypy`, `detect-secrets`, `yamllint`.
- CI: lint → type check → unit → integration(testcontainers) → 커버리지.
- `.editorconfig`, 버전 동기화 검증.

### 8.5 서비스 패키지 격리 원칙 (서비스화 이후)

`services/etlx-server`와 `services/etlx-web`이 추가되어도 다음 원칙을 강제한다:

1. **단방향 의존**: 서비스 → 코어만 허용. 코어 → 서비스 금지. CI에서 import-graph 검사로 자동 검증.
2. **별도 pyproject.toml**: `services/etlx-server/pyproject.toml`이 `etl-plugins`을 의존성으로 install. 모노레포지만 패키지 경계는 명확.
3. **독립 실행 가능**: 서비스 없이도 `etlx` CLI / orchestrator adapter / 라이브러리 import만으로 모든 ETL 기능 사용 가능해야 함.
4. **CI 분리**: 코어 CI는 서비스 dep 설치 없이 통과. 서비스 CI는 별도 잡으로 분리.
5. **버전 호환**: 서비스가 의존하는 코어 버전은 `etl-plugins>=X.Y,<X+1`로 SemVer 명시. 코어 breaking change는 major 버전 bump.

---

## 9. 추가 고려 사항

### 9.1 신뢰성

- **Retry**: 지수 백오프 + jitter. 데코레이터 `@retryable`. Pipeline에 자동 통합.
- **Circuit Breaker**: 외부 시스템 연속 실패 시 차단.
- **Idempotency**: 모든 sink write에 `idempotency_key` 옵션. upsert는 `key_columns` 필수.
- **Checkpoint / Cursor**: BatchSource cursor + StreamSource offset commit. (Step 6 강화)
- **Dead Letter Queue**: 변환 실패 레코드를 별도 sink로 라우팅. ✅ Step 3.3 구현 완료.

### 9.2 관찰성 (Observability)

- **로깅**: `structlog` JSON 구조화 + 시크릿 마스킹.
- **메트릭**: OpenTelemetry/Prometheus. 표준: `records_read_total`, `records_written_total`, `errors_total`, `lag_seconds`, `duration_seconds`. Pipeline 자동 emit ✅.
- **트레이싱**: OTLP exporter. 각 Task가 하나의 span.
- **데이터 라인리지**: OpenLineage 이벤트 emit (Step 6).

### 9.3 보안

- 시크릿은 절대 로그/예외 메시지에 노출 금지 — 로깅 필터로 마스킹.
- TLS/SSL 기본 활성화.
- 시크릿 백엔드 추상화: `env` / `vault` / `aws_sm` / `gcp_sm`.
- `detect-secrets` pre-commit hook.

### 9.4 성능

- Chunk/Batch 사이즈 설정 가능, 메모리 안전한 iterator/generator.
- 대용량 sink는 **bulk load API 우선** (`COPY`, `MERGE`, Snowflake `PUT/COPY INTO`, BigQuery load job).
- I/O 바운드 작업은 `asyncio` 또는 thread pool.
- Stream consumer 백프레셔 지원.

### 9.5 테스트 전략

- **Unit**: 추상 인터페이스 / 변환 / 설정 로더. 외부 의존 없이.
- **Integration**: `testcontainers-python`으로 실제 Postgres/Kafka/MinIO/MySQL 검증.
- **Contract test**: 새 커넥터는 공통 contract 통과 필수. Batch + Stream 두 패밀리.
- **Fixture**: `tests/fixtures/`에 표준 샘플 데이터셋 공유.

### 9.6 CLI (`etlx`)

```bash
etlx run <pipeline.yaml>             # 배치 실행
etlx run-stream <pipeline.yaml>      # 스트림 실행
etlx test-connection <name|--all>    # 연결 점검
etlx list-connectors                 # 등록된 커넥터 목록
etlx schema <connection> <table>     # 원격 스키마 조회 (Step 5+)
etlx validate <config.yaml>          # 설정 검증만
etlx --log-format console|json
etlx --log-level DEBUG|INFO|...
etlx version
```

### 9.7 서비스화 추가 고려 (Steps 7~11)

웹 UI / API / 실행엔진을 얹을 때 추가되는 횡단 관심사:

- **인증**: OIDC/SAML SSO + 로컬 계정 fallback. JWT 세션.
- **인가 (RBAC)**: Owner / Editor / Viewer / Runner. 워크스페이스 단위.
- **멀티테넌시**: Workspace/Team 단위 데이터 격리. 모든 리소스에 workspace_id.
- **감사 로그**: 모든 변경(connection/pipeline/schedule CRUD, run trigger)에 actor + before/after.
- **시크릿**: UI에서 입력받아도 metadata DB에는 평문 저장 금지. 입력 즉시 시크릿 백엔드(Vault/AWS SM/GCP SM)에 저장하고 ref만 보관. 코어 §9.3 원칙을 서비스 계층에서도 유지.
- **스케줄러**: 메타데이터 DB의 schedules → 실행엔진 동기화. 외부 cron이 아닌 내부 관리.
- **실행 이력**: runs 테이블 + 로그/메트릭 별도 저장(Loki/Prometheus 또는 DB).
- **API 안정성**: REST + OpenAPI 자동 생성. 프론트는 generated client만 사용.
- **디자인 시스템**: `DESIGN.md`가 단일 진실. 토큰(컬러·타이포·간격·반경·모션) → Tailwind config → shadcn/ui 컴포넌트로 흐름. 디자인 의사결정은 ADR-0018부터. 토큰 외 임의 값(arbitrary color/spacing/font) 사용 금지.

---

## 10. 구현 로드맵 (`ROADMAP.md` 시드)

**현재 상태 (2026-05-14)**: Steps 1~4 완료 + Step 5.1 (MySQL, SQLite) 완료. 16 ADR. 435 테스트.

### Step 1 — Foundation ✅
- 스캐폴딩, Harness 문서, Core/Config/Observability/Utils, Contract test infra.

### Step 2 — Reference Connectors ✅
- `postgres`, `s3`, `kafka` (각 카테고리 1개).

### Step 3 — Pipeline 실행기 + CLI ✅
- YAML→Pipeline 빌더, `etlx` CLI, Stream runtime, Retry/DLQ/자동 메트릭.

### Step 4 — Orchestrator Adapters ✅
- Airflow / Dagster / Prefect, PEP 562 lazy.

### Step 5 — Connector 확장 🔄
- 5.1 RDBMS: MySQL ✅, SQLite ✅, MSSQL/Oracle 남음
- 5.2 DW: Snowflake / BigQuery / Redshift / ClickHouse
- 5.3 NoSQL: MongoDB / Redis / DynamoDB / Cassandra
- 5.4 Streaming: Kinesis / Pulsar / RabbitMQ / NATS
- 5.5 CDC: Debezium / PG logical replication
- 5.6 Object: GCS / Azure Blob
- 5.7 HTTP/REST

### Step 6 — 강화
- OpenLineage 이벤트
- OTel/Prometheus 실제 백엔드 구현
- Checkpoint/Cursor abstraction + Pipeline span emit
- Contract test suite 완성
- mkdocs 문서 사이트
- v0.1.0 PyPI 릴리스

---

> **여기까지가 코어 라이브러리 (etl_plugins).** Step 7부터는 그 위에 얹는 **별도 서비스 패키지**다. ADR-0017 참고.

### Step 7 — Service Foundation (Metadata + Secret + YAML 양방향)
- 7.0 기술 스택 결정 ADR (FastAPI / Postgres / Alembic / Dagster vs Prefect vs 자체)
- 7.1 `services/etlx-server/` 모노레포 패키지 스캐폴딩 (별도 pyproject)
- 7.2 메타데이터 DB 스키마: `workspaces`, `users`, `connections`, `pipelines`, `pipeline_versions`, `schedules`, `runs`, `run_logs`, `audit_log`
- 7.3 Alembic 마이그레이션
- 7.4 YAML ↔ DB 양방향 변환 (`etlx import`, `etlx export` CLI 확장)
- 7.5 Secret backend UI 통합 (UI 입력 → Vault/AWS SM, metadata DB에는 ref만)

### Step 8 — API Server (FastAPI)
- 8.1 FastAPI 앱 부트스트랩 + OpenAPI 스펙 자동 생성
- 8.2 인증: OIDC + 로컬 계정 + JWT
- 8.3 RBAC: Owner/Editor/Viewer/Runner, 워크스페이스 단위
- 8.4 Audit log (모든 변경에 actor + before/after)
- 8.5 CRUD: `/connections`, `/pipelines`, `/schedules`, `/runs`
- 8.6 Action 엔드포인트: `/test-connection`, `/dry-run`, `/trigger-run`
- 8.7 통합 테스트 (httpx + testcontainers Postgres)

### Step 9 — Execution Engine 통합
- 9.1 실행 엔진 선택 ADR 확정 (Dagster 임베드 / Prefect 임베드 / 자체)
- 9.2 스케줄러: metadata DB schedules → 실행 엔진 동기화
- 9.3 Run 라이프사이클: 트리거 → 실행 → 상태/로그/메트릭 수집 → DB 기록
- 9.4 Stream worker manager: long-running stream pipeline을 K8s Deployment 또는 docker-compose process로 관리
- 9.5 실패/재시도/타임아웃 정책의 UI 노출

### Step 10 — Web UI (Next.js, DESIGN.md 기반)

> 디자인 시스템 단일 진실은 `DESIGN.md`. 토큰 외 임의 값 사용 금지.

- 10.0 디자인 토큰 구현
  - `services/etlx-web/styles/globals.css` (CSS variables, DESIGN.md §11.1)
  - `tailwind.config.ts` (DESIGN.md §11.2)
  - 폰트 self-host (Inter Variable + Pretendard Variable + JetBrains Mono)
  - Storybook 초기화 + 토큰 검증 stories
- 10.1 기초 컴포넌트 (shadcn/ui 토큰 치환)
  - Button / Input / Card / Badge / Tooltip / Tabs / Select / Dropdown / Separator / Toast(Sonner)
  - Sidebar (Arc-style, workspace 컬러 인디케이터)
  - Header (검색 + 액션 + theme toggle)
  - Command Palette (cmdk, Cmd+K, DESIGN.md §7.5)
- 10.2 레이아웃 셸 + 워크스페이스 전환 + 다크/라이트 토글
- 10.3 Connection 관리 화면 (CRUD + Test connection)
- 10.4 Pipeline Builder (React Flow + 커스텀 노드, DESIGN.md §7.6)
- 10.5 Schedule 에디터 + Run 모니터링 (Data Table, DESIGN.md §7.8)
- 10.6 관리자 화면 (Workspace / RBAC / Audit log)
- 10.7 빈 상태 / 에러 / 로딩 패스 점검 (전 페이지)
- 10.8 A11y AA 통과 + visual regression baseline (Chromatic 또는 Playwright)

### Step 11 — 운영 강화
- 11.1 멀티 테넌시 검증 (workspace 격리 부담 테스트)
- 11.2 Helm chart + Docker Compose 배포본 (`services/docker-compose.services.yml`)
- 11.3 백업·복구 가이드 (metadata DB + secret backend)
- 11.4 운영 메트릭 대시보드 (Grafana 템플릿)
- 11.5 첫 서비스 릴리스 (v0.x → v1.0)

---

## 11. Claude Code 작업 지침 (요약)

이 문서를 Claude Code에 넘길 때 다음 순서를 따르도록 안내한다.

1. **`CLAUDE.md`부터 읽을 것** — 이후 모든 세션의 컨텍스트가 됨.
2. **Step 단위로 PR 단위 작업** — 한 번에 여러 Step을 섞지 말 것.
3. **새 커넥터 추가 시 contract test 통과 필수**.
4. **`.env`에 들어갈 값은 절대 코드/YAML에 하드코딩 금지**.
5. **모든 결정 사항은 `DECISIONS.md`에 ADR로 기록**.
6. **작업 종료 시 `ROADMAP.md`의 체크박스와 "현재 작업 중" 마커 갱신**.
7. **세션 시작 시 반드시 `CLAUDE.md` → `ROADMAP.md` → 최근 커밋 로그 순서로 확인**.
8. **서비스 패키지 작업(`services/`) 시 §8.5 격리 원칙 준수** — 코어가 서비스를 import하지 않도록 import-graph 검증.
