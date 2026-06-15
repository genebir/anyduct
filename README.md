# anyduct

> **anyduct** — Backend-agnostic, orchestrator-agnostic **Python ETL plugin library**.
> 모든 데이터 소스/싱크(RDBMS, NoSQL, DW, Stream, Object Storage)에 대해 통일된 인터페이스를 제공하고, 어떤 오케스트레이터(Airflow / Dagster / Prefect / 사내 워크플로우) 위에서도 그대로 재사용할 수 있다.

🌐 **[랜딩 페이지 (소개 사이트)](https://genebir.github.io/ETL/)** — 다른 ETL과의 차이점을 한눈에.

[![CI Core](https://github.com/genebir/ETL/actions/workflows/ci-core.yml/badge.svg)](https://github.com/genebir/ETL/actions/workflows/ci-core.yml)
[![CI Server](https://github.com/genebir/ETL/actions/workflows/ci-server.yml/badge.svg)](https://github.com/genebir/ETL/actions/workflows/ci-server.yml)
[![CI Web](https://github.com/genebir/ETL/actions/workflows/ci-web.yml/badge.svg)](https://github.com/genebir/ETL/actions/workflows/ci-web.yml)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.11-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](#라이선스)
[![uv](https://img.shields.io/badge/managed%20by-uv-purple)](https://github.com/astral-sh/uv)

---

## 현재 상태

**코어 라이브러리 + 웹 서비스 풀스택 동작. 1,320 코어 단위 + 227 통합 + 454 서버 통합 테스트, 97 ADR, 커넥터 20종.**

두 층으로 이뤄진다. 코어는 서비스를 모른다(단방향 의존) — **서비스 패키지가 통째로 사라져도 라이브러리는 그대로 동작한다.**

**① 코어 라이브러리 (`etl_plugins`)**
- [x] **통일 커넥터 추상화** — BatchSource/Sink, StreamSource/Sink, `Record` 정규화 + 공통 contract test.
- [x] **커넥터 20종** — RDBMS/DW(postgres·mysql·sqlite·vertica·mssql·snowflake·bigquery·redshift·clickhouse), NoSQL(mongodb·dynamodb·cassandra), Streaming(kafka·kinesis·sqs·redis·rabbitmq·nats), Object/HTTP(s3·http).
- [x] **파이프라인 실행 모델** — 단일 task / Task-DAG(`depends_on`) / 데이터플로우 graph(join·aggregate·branch) + Retry·DLQ·재시도.
- [x] **데이터 플레인** — DuckDB `sql` 변환(JOIN/GROUP BY/윈도우) + Arrow 벡터 fast-path(postgres·mysql·vertica·mssql, Record 플레인 우회) + same-connection / ELT 푸시다운(데이터 무이동, dbt식).
- [x] **자산·리니지** — derived-first 카탈로그 + 컬럼 레벨 리니지(sqlglot 풀 AST) + OpenLineage export + 증분/백필 커서.
- [x] **오케스트레이터 어댑터** — Airflow / Dagster / Prefect (PEP 562 lazy) + `anyduct` CLI.
- [x] **관측성** — structlog + OTel trace + Prometheus exporter.

**② 서비스 패키지 (`services/anyduct-server` FastAPI + `services/anyduct-web` Next.js)** — 비개발자도 쓰는 웹 UI ETL 서비스
- [x] **API + 실행 엔진** — OIDC/로컬 인증 + RBAC + 워크스페이스 격리 + 감사 로그. PostgreSQL run-큐(SKIP LOCKED) 멀티-replica 워커 + cron 스케줄러 + 센서 프레임워크.
- [x] **웹 UI** — 비주얼 파이프라인 빌더(React Flow) + 연결/스케줄/마이그레이션 관리 + run 모니터링 + 카탈로그/리니지 그래프 + ERD 디자이너.
- [x] **배포** — `docker-compose.prod.yml` + Helm 차트(kind 실배포 검증) + Grafana 대시보드.

> ⚠️ **성숙도**: postgres·mysql·sqlite·vertica·mongodb·s3·kafka·dynamodb·kinesis·sqs는 실컨테이너(testcontainers/LocalStack) 통합 검증됨. snowflake·bigquery·redshift·clickhouse·cassandra·redis·rabbitmq·nats·mssql은 fake-client 단위 테스트(드라이버 API 모델 검증) — 프로덕션 전 실서버 스모크 권장. PyPI `v0.1.0` 정식 릴리스는 준비 중.

상세 진행은 [`ROADMAP.md`](./dev-docs/ROADMAP.md), 설계 결정은 [`DECISIONS.md`](./dev-docs/DECISIONS.md)(97 ADR), 변경 이력은 [`CHANGELOG.md`](./CHANGELOG.md).

---

## 문서 지도

| 문서 | 용도 |
|---|---|
| [`CLAUDE.md`](./CLAUDE.md) | 세션 시작 시 읽는 요약 컨텍스트 |
| [`SPEC.md`](./dev-docs/SPEC.md) | 마스터 설계 명세서 (원칙·아키텍처·인터페이스·설정) |
| [`ROADMAP.md`](./dev-docs/ROADMAP.md) | Step 단위 진행 상태 |
| [`DECISIONS.md`](./dev-docs/DECISIONS.md) | ADR — 모든 설계 결정 |
| [`DEVELOPMENT.md`](./dev-docs/DEVELOPMENT.md) | 신규 환경 인계 / bootstrap / 트러블슈팅 |
| [`CHANGELOG.md`](./CHANGELOG.md) | Keep a Changelog 형식 변경 이력 |
| [`DESIGN.md`](./dev-docs/DESIGN.md) | `anyduct-web` 디자인 시스템 SSOT (토큰·컴포넌트·a11y) |

---

## 빠른 시작

```bash
git clone git@github.com:genebir/ETL.git
cd ETL
./scripts/bootstrap.sh        # uv sync + .env 복사 + pre-commit + docker compose up
```

자세한 절차와 트러블슈팅은 [`DEVELOPMENT.md`](./dev-docs/DEVELOPMENT.md).

---

## 설치

```bash
# 코어만 (CLI / runtime / Pipeline ABC) — PyPI 정식 릴리스 준비 중,
# 그 전에는 소스에서: uv sync  (또는 pip install git+https://github.com/genebir/ETL)
pip install etl-plugins

# 필요한 커넥터/오케스트레이터 extra 추가 (커넥터 20종 — 전체 목록은 pyproject.toml)
pip install 'etl-plugins[postgres]'              # psycopg 3
pip install 'etl-plugins[mysql]'                 # PyMySQL
pip install 'etl-plugins[s3]'                    # boto3 + pyarrow
pip install 'etl-plugins[kafka]'                 # aiokafka
pip install 'etl-plugins[snowflake]'             # snowflake-connector-python
pip install 'etl-plugins[duckdb]'                # DuckDB sql 변환 + Arrow 플레인
pip install 'etl-plugins[airflow]'               # apache-airflow
pip install 'etl-plugins[dagster]'               # dagster
pip install 'etl-plugins[prefect]'               # prefect
# sqlite는 stdlib — extra 불필요
```

---

## 사용 예 1: YAML + `anyduct` CLI

```yaml
# configs/connections.yaml
connections:
  pg_prod:    { type: postgres, host: db, port: 5432, database: app,    user: etl, password: ${PG_PASSWORD} }
  warehouse:  { type: postgres, host: dw, port: 5432, database: analytics, user: etl, password: ${DW_PASSWORD} }
  dlq_sink:   { type: sqlite,   database: /var/etl/dlq.db }
```

```yaml
# configs/pipelines/orders_to_dw.yaml
name: orders_to_dw
mode: batch
source: { connection: pg_prod, query: "SELECT * FROM orders WHERE updated_at > :ts" }
transforms:
  - { type: rename, mapping: { user_id: customer_id } }
  - { type: cast,   columns: { order_total: float, created_at: timestamp } }
  - { type: filter, expr: "data['order_total'] > 0" }
sink: { connection: warehouse, table: analytics.orders, mode: upsert, key_columns: [order_id] }
retry: { max_attempts: 3, backoff: exponential, initial_delay_seconds: 1.0 }
dlq:   { connection: dlq_sink, table: failed_orders }
```

```bash
uv run anyduct list-connectors                              # 설치된 extra의 커넥터 목록 (20종 중)
uv run anyduct validate  configs/pipelines/orders_to_dw.yaml  --connections configs/connections.yaml
uv run anyduct test-connection --all                         --connections configs/connections.yaml
uv run anyduct run       configs/pipelines/orders_to_dw.yaml --connections configs/connections.yaml
uv run anyduct --log-format console run configs/pipelines/orders_to_dw.yaml --connections configs/connections.yaml

# 스트리밍
uv run anyduct run-stream configs/pipelines/events_to_kafka.yaml \
  --connections configs/connections.yaml \
  --stop-after-records 10000
```

---

## 사용 예 2: in-Python API

```python
from collections.abc import Iterable, Iterator
from etl_plugins import (
    BatchSink, BatchSource, ConnectorRegistry, Pipeline, Record, Task,
)


@ConnectorRegistry.register("inmem-src")
class MySource(BatchSource):
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def health_check(self) -> bool: return True

    def read(self, query=None, **opts) -> Iterator[Record]:
        yield Record(data={"id": 1, "name": "Alice"})
        yield Record(data={"id": 2, "name": "Bob"})


@ConnectorRegistry.register("inmem-snk")
class MySink(BatchSink):
    def __init__(self): self.rows: list[Record] = []
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def health_check(self) -> bool: return True

    def write(self, records: Iterable[Record], *, mode="append", key_columns=None, **opts):
        before = len(self.rows); self.rows.extend(records); return len(self.rows) - before


src, snk = MySource(), MySink()
pipeline = (
    Pipeline("orders_to_dw")
    .add(
        Task.extract("inmem-src", "SELECT * FROM orders")
            .transform(lambda r: Record(data={**r.data, "name": r.data["name"].upper()}))
            .load("inmem-snk", mode="upsert", key_columns=["id"])
    )
    .on("post_run", lambda ctx, result:
        ctx.logger.info("done", rows=result.records_written, took=result.duration_seconds))
)
result = pipeline.run(connectors={"inmem-src": src, "inmem-snk": snk})
```

---

## 사용 예 3: Orchestrator adapters

```python
# Airflow
from airflow import DAG
from etl_plugins.adapters.airflow import ETLPluginsOperator

with DAG("etl_demo", schedule="@hourly", ...) as dag:
    ETLPluginsOperator(
        task_id="orders_to_dw",
        pipeline_yaml="configs/pipelines/orders_to_dw.yaml",
        connections="configs/connections.yaml",
    )

# Dagster
from dagster import job
from etl_plugins.adapters.dagster import EtlPluginsResource, etl_plugins_op

@job(resource_defs={"etl_plugins": EtlPluginsResource(connections_path="configs/connections.yaml")})
def my_job():
    etl_plugins_op()

# Prefect
from etl_plugins.adapters.prefect import run_etl_pipeline_flow
run_etl_pipeline_flow("configs/pipelines/orders_to_dw.yaml", "configs/connections.yaml")
```

각 adapter는 PEP 562 lazy 로딩 — orchestrator가 설치되지 않은 환경에서도 모듈 import는 성공한다. 심볼 접근 시점에 helpful `ImportError`로 안내.

---

## 아키텍처 개요

상하 단방향 의존 — 위 층이 아래 층을 import할 뿐 그 반대는 금지. **서비스 층(`services/`)이 통째로 사라져도 코어 라이브러리는 그대로 동작한다.**

```
┌─ 서비스 패키지 (services/) ─────────────────────────────┐
│  Web UI (Next.js)        빌더 / 카탈로그 / 모니터링 / ERD │
│        ↑                                                  │
│  REST API (FastAPI)      인증·RBAC·워크스페이스·감사       │
│        ↑                                                  │
│  Metadata + Execution    PG run-큐(SKIP LOCKED) 워커 /    │
│                          스케줄러 / 센서 / 시크릿 백엔드   │
└──────────────────── 서비스 / 코어 경계 (단방향) ─────────┘
        ↑
Orchestrator Adapters  (Airflow / Dagster / Prefect / anyduct CLI)
        ↑
Pipeline Runtime       (YAML builder / transforms / 데이터플레인:
                        DuckDB sql · Arrow · pushdown / retry / DLQ)
        ↑
Pipeline Core          (Pipeline / Task / Graph DAG / Context / Hooks)
        ↑
Connector Abstraction  (BatchSource/Sink, StreamSource/Sink, Record)
        ↑
Connectors (plugins)   (postgres · mysql · vertica · snowflake · mongodb
                        · kafka · s3 · … 20종)
        ↑
Foundation             (Config / Secrets / Logging / Metrics
                        / Tracing / Retry / Chunk / async_io)
```

상세 다이어그램은 [`SPEC.md`](./dev-docs/SPEC.md) §2, 디렉토리 규약은 [`CLAUDE.md`](./CLAUDE.md) §3.

---

## 핵심 설계 원칙

| 원칙 | 설명 |
|---|---|
| **Backend-agnostic** | Connector 구현체만 추가하면 어떤 DB/스트림도 지원 |
| **Orchestrator-agnostic** | 어떤 오케스트레이터에도 종속되지 않는 순수 Python 코어 |
| **Config-driven** | 코드 변경 없이 `.env` + YAML만으로 동작 변경 |
| **Batch + Stream 통합** | 동일한 추상화 위에서 배치/스트리밍 모두 지원 |
| **Pluggable** | Entry point / Registry 패턴으로 외부 패키지에서 확장 가능 |
| **Type-safe** | Pydantic 스키마 검증 + mypy strict |
| **Observable** | 표준 로깅·메트릭·트레이싱 hook 내장 |
| **Portable (Harness)** | 어떤 PC/환경에서든 동일하게 재현·이어작업 가능 |

---

## 개발

```bash
make help        # 사용 가능한 타깃 목록
make test        # uv run pytest -m "not it"
make lint        # ruff check + mypy
make fmt         # ruff format + autofix
make up / down   # docker compose dev infra
```

새 커넥터를 추가하는 절차는 [`DEVELOPMENT.md`](./dev-docs/DEVELOPMENT.md) §7.

---

## 라이선스

Apache License 2.0 — 전문은 [`LICENSE`](LICENSE) 참조.
