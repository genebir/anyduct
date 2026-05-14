# ETL Plugins

> Backend-agnostic, orchestrator-agnostic **Python ETL plugin library**.
> 모든 데이터 소스/싱크(RDBMS, NoSQL, DW, Stream, Object Storage)에 대해 통일된 인터페이스를 제공하고, 어떤 오케스트레이터(Airflow / Dagster / Prefect / 사내 워크플로우) 위에서도 그대로 재사용할 수 있다.

[![CI](https://github.com/genebir/ETL/actions/workflows/ci.yml/badge.svg)](https://github.com/genebir/ETL/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.11-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](#라이선스)
[![uv](https://img.shields.io/badge/managed%20by-uv-purple)](https://github.com/astral-sh/uv)

---

## 현재 상태 (2026-05-14)

**Steps 1–4 완료 + Step 5 진행 중. 321 단위 + 3 skip + 111 통합 = 435 테스트, 16 ADR.**

- [x] **Step 1 — Foundation**: 스캐폴딩, Harness 문서, Core/Config/Observability/Utils, Contract test infra
- [x] **Step 2 — Reference Connectors**: `postgres`(psycopg3), `s3`(boto3+pyarrow, MinIO 호환), `kafka`(aiokafka, Stream Contract 신규 도입)
- [x] **Step 3 — Pipeline 실행기 + CLI**
  - 3.1 YAML→Pipeline 빌더 + `etlx` CLI (`version`, `list-connectors`, `validate`, `run`, `run-stream`, `test-connection`) + transforms(rename/cast/filter sandbox/python)
  - 3.2 Stream runtime (`Pipeline.arun_stream` + Kafka async `commit()` + buffer policy + `--stop-after-*`)
  - 3.3 Retry + DLQ 라우팅 + 자동 메트릭 emit + 글로벌 `--log-format json|console` / `--log-level`
- [x] **Step 4 — Orchestrator Adapters**: Airflow `ETLPluginsOperator`, Dagster `EtlPluginsResource`+`etl_plugins_op`, Prefect `run_etl_pipeline_flow`+`task`. PEP 562 lazy 로딩으로 orchestrator 미설치 환경에서도 모듈 import 성공.
- [x] **Step 5.1 — Additional RDBMS**: MySQL(PyMySQL), SQLite(stdlib)
- [ ] Step 5.2~5.7: DW / NoSQL / Streaming / CDC / Object 추가 / HTTP — 미착수
- [ ] Step 6: OpenLineage, OTel/Prometheus 실구현, mkdocs, v0.1.0 릴리스
**다음 분기**: 서비스 계층 착수(Step 7~) 예정. UI 디자인은 `DESIGN.md`에 확정 — Arc Browser 영감, 깊은 네이비 베이스 + 팝한 핫핑크 강조.

상세 진행은 [`ROADMAP.md`](./ROADMAP.md), 설계 결정은 [`DECISIONS.md`](./DECISIONS.md).

---

## 문서 지도

| 문서 | 용도 |
|---|---|
| [`CLAUDE.md`](./CLAUDE.md) | 세션 시작 시 읽는 요약 컨텍스트 |
| [`SPEC.md`](./SPEC.md) | 마스터 설계 명세서 (원칙·아키텍처·인터페이스·설정) |
| [`ROADMAP.md`](./ROADMAP.md) | Step 단위 진행 상태 |
| [`DECISIONS.md`](./DECISIONS.md) | ADR — 모든 설계 결정 |
| [`DEVELOPMENT.md`](./DEVELOPMENT.md) | 신규 환경 인계 / bootstrap / 트러블슈팅 |
| [`CHANGELOG.md`](./CHANGELOG.md) | Keep a Changelog 형식 변경 이력 |
| [`DESIGN.md`](DESIGN.md) | `etlx-web` 디자인 시스템 (Step 10 진행 시 SSOT) |

---

## 빠른 시작

```bash
git clone git@github.com:genebir/ETL.git
cd ETL
./scripts/bootstrap.sh        # uv sync + .env 복사 + pre-commit + docker compose up
```

자세한 절차와 트러블슈팅은 [`DEVELOPMENT.md`](./DEVELOPMENT.md).

---

## 설치

```bash
# 코어만 (CLI / runtime / Pipeline ABC)
pip install etl-plugins                          # PyPI 릴리스는 Step 6에서

# 필요한 커넥터/오케스트레이터 extra 추가
pip install 'etl-plugins[postgres]'              # psycopg 3
pip install 'etl-plugins[mysql]'                 # PyMySQL
pip install 'etl-plugins[s3]'                    # boto3 + pyarrow
pip install 'etl-plugins[kafka]'                 # aiokafka
pip install 'etl-plugins[airflow]'               # apache-airflow
pip install 'etl-plugins[dagster]'               # dagster
pip install 'etl-plugins[prefect]'               # prefect
# sqlite는 stdlib — extra 불필요
```

---

## 사용 예 1: YAML + `etlx` CLI

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
uv run etlx list-connectors                              # postgres, mysql, sqlite, s3, kafka
uv run etlx validate  configs/pipelines/orders_to_dw.yaml  --connections configs/connections.yaml
uv run etlx test-connection --all                         --connections configs/connections.yaml
uv run etlx run       configs/pipelines/orders_to_dw.yaml --connections configs/connections.yaml
uv run etlx --log-format console run configs/pipelines/orders_to_dw.yaml --connections configs/connections.yaml

# 스트리밍
uv run etlx run-stream configs/pipelines/events_to_kafka.yaml \
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

```
Orchestrator Adapters  (Airflow / Dagster / Prefect / etlx CLI)   ← ✅ Steps 3.1, 4
        ↑
Pipeline Runtime       (YAML builder / transforms / arun_stream /
                        retry / DLQ / auto-metrics)               ← ✅ Step 3
        ↑
Pipeline Core          (Pipeline / Task / Context / Hooks)        ← ✅ Step 1.4
        ↑
Connector Abstraction  (BatchSource/Sink, StreamSource/Sink)      ← ✅ Step 1.4
        ↑
Connectors (plugins)   (postgres, mysql, sqlite, s3, kafka, ...)  ← ✅ Steps 2 + 5.1
        ↑
Foundation             (Config / Secrets / Logging / Metrics
                        / Tracing / Retry / Chunk / async_io)     ← ✅ Steps 1.5–1.7
```

상세 다이어그램은 [`SPEC.md`](./SPEC.md) §2.

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

새 커넥터를 추가하는 절차는 [`DEVELOPMENT.md`](./DEVELOPMENT.md) §7.

---

## 라이선스

Apache License 2.0. (`LICENSE` 파일은 첫 릴리스 전 추가)
