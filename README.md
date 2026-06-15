<div align="center">

# anyduct

### 어떤 데이터든, 당신의 방식대로.

**여러 곳에 흩어진 데이터를 하나의 인터페이스로 옮기고·변환하고·관리하는 오픈소스 데이터 파이프라인 플랫폼.**
20여 종의 데이터베이스·웨어하우스·스트림을 동일하게 연결하고, 코드로든 노코드로든, 당신의 인프라 안에서.

🌐 **[소개 사이트 (랜딩 페이지)](https://genebir.github.io/anyduct/)**

[![CI Core](https://github.com/genebir/anyduct/actions/workflows/ci-core.yml/badge.svg)](https://github.com/genebir/anyduct/actions/workflows/ci-core.yml)
[![CI Server](https://github.com/genebir/anyduct/actions/workflows/ci-server.yml/badge.svg)](https://github.com/genebir/anyduct/actions/workflows/ci-server.yml)
[![CI Web](https://github.com/genebir/anyduct/actions/workflows/ci-web.yml/badge.svg)](https://github.com/genebir/anyduct/actions/workflows/ci-web.yml)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.11-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](#라이선스)
[![uv](https://img.shields.io/badge/managed%20by-uv-purple)](https://github.com/astral-sh/uv)

</div>

---

## anyduct란?

데이터 팀은 보통 **여러 도구를 짜맞춰** 일합니다 — 수집은 SaaS(Fivetran), 변환은 dbt, 스케줄은 Airflow, 계보는 또 다른 카탈로그 도구. 각자 한 조각은 잘하지만, 그 사이를 잇는 건 늘 사용자 몫입니다.

**anyduct는 그 경계를 없앱니다.** PostgreSQL·Snowflake·Kafka·S3·MongoDB 등 20여 종의 시스템을 *하나의 인터페이스*로 연결하고, 수집→변환→적재→자동화→모니터링→데이터 계보까지 한 플랫폼에서 처리합니다. 그러면서도 **특정 DB에도, 특정 오케스트레이터에도 묶이지 않고**, **당신의 인프라 안에서** 동작합니다.

두 가지 모습으로 쓸 수 있습니다.

- **라이브러리 · CLI** — 개발자가 YAML이나 Python으로 파이프라인을 정의하고, Airflow·Dagster·Prefect 위에서, 또는 단독 CLI로 실행.
- **웹 서비스** — 비개발자도 브라우저에서 연결을 만들고, 화면에서 파이프라인을 그리고, 실행·모니터링.

> **핵심 약속:** 코어 라이브러리는 웹 서비스를 전혀 모릅니다(단방향 의존). **웹 서비스가 통째로 사라져도 라이브러리는 그대로 동작합니다.** 필요한 만큼만 골라 쓰세요.

---

## 다른 ETL과 무엇이 다른가

| | anyduct |
|---|---|
| **① 종속이 없다 (Agnostic)** | 특정 DB·오케스트레이터에 묶이지 않는 순수 Python 코어. 같은 파이프라인을 Airflow·Dagster·Prefect 위에서, 또는 CLI로 그대로 실행. |
| **② 서비스는 선택 (Service-optional)** | 코어는 웹을 모른다(단방향 의존). 웹 UI 없이 라이브러리만으로도 완전 동작. |
| **③ 가장 싼 길을 자동 선택 (무이동 ELT)** | 소스=싱크면 **데이터를 옮기지 않고** DB 안에서 한 문장으로 처리(푸시다운). 안 되면 컬럼형(Arrow) 벌크, 그것도 아니면 행 단위 — 작업마다 자동 선택하고 실제 경로를 보여줌. |
| **④ 리니지가 공짜 (Zero-config)** | 실행만 하면 테이블·컬럼 단위 계보 자동 생성(SQL은 JOIN·CTE·집계·윈도우까지 분석). OpenLineage로 내보내기. |
| **⑤ 당신의 데이터는 당신의 인프라에** | Apache 2.0 오픈소스. Docker Compose 한 줄 또는 Helm으로 셀프호스팅. 비밀정보는 Vault·AWS/GCP Secret Manager에 위임. |

SaaS ETL은 자사 플랫폼에, dbt는 SQL 변환에, Airflow는 오케스트레이션에 머뭅니다. anyduct는 이들의 장점을 하나로 모읍니다. 자세한 비교는 [소개 사이트](https://genebir.github.io/anyduct/#different)를 참고하세요.

---

## 한눈에 보는 기능

- 🔌 **커넥터 20여 종** — RDBMS·DW·NoSQL·스트리밍·오브젝트 스토리지를 동일한 컨트랙트로. 새 커넥터도 쉽게 확장.
- ⚡ **자동 데이터 경로** — 푸시다운(무이동) · Arrow 벌크 · 행 단위를 작업마다 자동 선택 (PG→PG 실측 약 3.4×).
- 🧬 **컬럼 리니지** — 설정 없이 테이블·컬럼 계보 자동 생성 + OpenLineage 내보내기.
- 🧩 **코드 & 노코드** — YAML·Python 또는 브라우저 비주얼 빌더. 같은 엔진, 같은 결과.
- 🛡️ **멱등성 & DLQ** — 두 번 돌려도 안전한 적재(덮어쓰기·병합) + 실패 행만 격리.
- ⏱️ **스케줄 · 센서** — cron 정시 실행, 상류 도착·신선도 기반 자동 트리거, 자산 기반 오케스트레이션.
- 📈 **수평 확장** — PostgreSQL 큐(SKIP LOCKED) 멀티-워커 + 범위 분할 병렬 백필.
- 🔭 **관측성** — 실행·로그·실패 분류·데이터 경로 가시화 + OTel/Prometheus + Grafana 대시보드.
- 🚀 **배포** — Docker Compose · Helm(쿠버네티스), 워커·스케줄러·센서 모두 멀티-replica 안전.

---

## 지원 커넥터 (20종)

| 분류 | 커넥터 |
|---|---|
| **RDBMS · DW** | PostgreSQL, MySQL/MariaDB, SQLite, Vertica, SQL Server, Snowflake, BigQuery, Redshift, ClickHouse |
| **NoSQL** | MongoDB, DynamoDB, Cassandra |
| **스트리밍** | Kafka, Kinesis, SQS, Redis Streams, RabbitMQ, NATS |
| **오브젝트 · HTTP** | S3 / MinIO, HTTP/REST |

> 서로 다른 종류의 DB로 옮길 때 컬럼 타입을 대상 방언에 맞게 자동 변환하고(예: PostgreSQL `BIGINT` → SQLite `INTEGER`), 싱크에 `auto_create_table`을 켜면 대상 테이블을 소스 스키마로부터 자동 생성합니다.

---

## 빠른 시작

### 비개발자 — 웹 서비스

```bash
git clone git@github.com:genebir/anyduct.git && cd anyduct
./scripts/bootstrap.sh        # uv sync + .env + pre-commit + docker compose up
./start.sh                    # API · 워커 · 스케줄러 · 웹 UI 기동
```

브라우저에서 워크스페이스 → 연결 등록 → 비주얼 빌더로 파이프라인 작성 → 실행·모니터링.

### 개발자 — 라이브러리 · CLI

```bash
git clone https://github.com/genebir/anyduct && cd anyduct
uv sync

# 필요한 커넥터 extra만 (전체 목록은 pyproject.toml)
pip install 'etl-plugins[postgres]'    # psycopg 3
pip install 'etl-plugins[duckdb]'      # DuckDB sql 변환 + Arrow 플레인
pip install 'etl-plugins[snowflake]'   # 클라우드 DW

uv run anyduct run configs/pipelines/my.yaml --connections configs/connections.yaml
```

> PyPI 정식 릴리스(`pip install etl-plugins`)는 준비 중입니다. 그 전에는 소스에서 `uv sync`로 사용하세요.

---

## 사용 예

### YAML + `anyduct` CLI

```yaml
# configs/connections.yaml
connections:
  pg_prod:   { type: postgres, host: db, database: app,       user: etl, password: ${PG_PASSWORD} }
  warehouse: { type: postgres, host: dw, database: analytics, user: etl, password: ${DW_PASSWORD} }
  dlq_sink:  { type: sqlite,   database: /var/etl/dlq.db }
```

```yaml
# configs/pipelines/orders_to_dw.yaml
name: orders_to_dw
mode: batch
source: { connection: pg_prod, query: "SELECT * FROM orders WHERE updated_at > :ts" }
transforms:
  - { type: rename, mapping: { user_id: customer_id } }
  - { type: filter, expr: "data['order_total'] > 0" }
sink: { connection: warehouse, table: analytics.orders, mode: upsert, key_columns: [order_id] }
retry: { max_attempts: 3, backoff: exponential }
dlq:   { connection: dlq_sink, table: failed_orders }
```

```bash
uv run anyduct validate configs/pipelines/orders_to_dw.yaml --connections configs/connections.yaml
uv run anyduct run      configs/pipelines/orders_to_dw.yaml --connections configs/connections.yaml
uv run anyduct run-stream configs/pipelines/events.yaml --stop-after-records 10000   # 스트리밍
uv run anyduct list-connectors
```

### in-Python API

```python
from etl_plugins import Pipeline, Task, Record

pipeline = Pipeline("orders_to_dw").add(
    Task.extract("pg_prod", "SELECT * FROM orders")
        .transform(lambda r: Record(data={**r.data, "name": r.data["name"].upper()}))
        .load("warehouse", mode="upsert", key_columns=["id"])
)
result = pipeline.run(connectors={...})
print(result.records_written, result.data_paths)
```

### 오케스트레이터 어댑터

```python
# Airflow — 기존 DAG에 그대로 얹기
from etl_plugins.adapters.airflow import ETLPluginsOperator
ETLPluginsOperator(task_id="orders", pipeline_yaml="...", connections="...")
```

각 어댑터는 PEP 562 지연 로딩 — 해당 오케스트레이터가 설치되지 않은 환경에서도 import가 깨지지 않습니다.

---

## 아키텍처

상하 단방향 의존 — 위 층이 아래 층을 import할 뿐 그 반대는 금지. **서비스 층(`services/`)이 통째로 사라져도 코어 라이브러리는 그대로 동작합니다.**

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
Foundation             (Config / Secrets / Logging / Metrics / Retry / async_io)
```

| 설계 원칙 | 설명 |
|---|---|
| **Backend-agnostic** | Connector 구현체만 추가하면 어떤 DB/스트림도 지원 |
| **Orchestrator-agnostic** | 어떤 오케스트레이터에도 종속되지 않는 순수 Python 코어 |
| **Config-driven** | 코드 변경 없이 `.env` + YAML만으로 동작 변경 |
| **Batch + Stream 통합** | 동일한 추상화 위에서 배치/스트리밍 모두 지원 |
| **Pluggable** | Entry point / Registry 패턴으로 외부 패키지에서 확장 가능 |
| **Type-safe** | Pydantic 스키마 검증 + mypy strict |

---

## 프로젝트 상태

**코어 라이브러리 + 웹 서비스 풀스택 동작.** 코어 단위 1,320 + 통합 227 + 서버 통합 454 테스트, 97 ADR, 커넥터 20종.

> ⚠️ **커넥터 성숙도** — PostgreSQL·MySQL·SQLite·Vertica·MongoDB·S3·Kafka·DynamoDB·Kinesis·SQS는 실컨테이너(testcontainers/LocalStack)로 통합 검증됨. Snowflake·BigQuery·Redshift·ClickHouse·Cassandra·Redis·RabbitMQ·NATS·SQL Server는 fake-client 단위 테스트(드라이버 API 모델 검증)이므로 프로덕션 전 실서버 스모크를 권장합니다. PyPI 정식 릴리스는 준비 중입니다.

---

## 문서

| 문서 | 내용 |
|---|---|
| [`dev-docs/SPEC.md`](./dev-docs/SPEC.md) | 마스터 설계 명세 (원칙·아키텍처·인터페이스·설정) |
| [`dev-docs/DECISIONS.md`](./dev-docs/DECISIONS.md) | ADR — 모든 설계 결정 (97건) |
| [`dev-docs/ROADMAP.md`](./dev-docs/ROADMAP.md) | Step 단위 진행 상태 |
| [`dev-docs/DEVELOPMENT.md`](./dev-docs/DEVELOPMENT.md) | 환경 셋업 · 커넥터 추가 절차 · 트러블슈팅 |
| [`dev-docs/DESIGN.md`](./dev-docs/DESIGN.md) | 웹 디자인 시스템 SSOT (토큰·컴포넌트·a11y) |
| [`CHANGELOG.md`](./CHANGELOG.md) | 변경 이력 (Keep a Changelog) |
| `docs/` | 커넥터·파이프라인·커서·관측성·배포 가이드 (mkdocs) |

---

## 개발

```bash
make help     # 사용 가능한 타깃
make test     # uv run pytest -m "not it"
make lint     # ruff check + mypy
make fmt      # ruff format + autofix
make up/down  # docker compose dev infra
```

새 커넥터 추가 절차는 [`dev-docs/DEVELOPMENT.md`](./dev-docs/DEVELOPMENT.md) §7.

---

## 라이선스

Apache License 2.0 — 전문은 [`LICENSE`](LICENSE) 참조.
