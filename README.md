# ETL Plugins

> Backend-agnostic, orchestrator-agnostic **Python ETL plugin library**.
> 모든 데이터 소스/싱크(RDBMS, NoSQL, DW, Stream, Object Storage)에 대해 통일된 인터페이스를 제공하고, 어떤 오케스트레이터(Airflow / Dagster / Prefect / 사내 워크플로우) 위에서도 그대로 재사용할 수 있다.

[![CI](https://github.com/genebir/ETL/actions/workflows/ci.yml/badge.svg)](https://github.com/genebir/ETL/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-%E2%89%A53.11-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](#라이선스)
[![uv](https://img.shields.io/badge/managed%20by-uv-purple)](https://github.com/astral-sh/uv)

---

## 현재 상태 (2026-05-14)

**Step 1 — Foundation 완료. 193 단위 테스트 / 7 ADR.** 다음은 Step 2 (레퍼런스 커넥터).

- [x] Harness 문서 · 스캐폴딩 · 환경 재현 · CI [Step 1.0–1.3]
- [x] **Core 추상화** [Step 1.4] — `Connector` / `BatchSource` / `BatchSink` / `StreamSource` / `StreamSink`, `Record` (Pydantic), `Schema`, `Pipeline` + `Task` (fluent builder), `Context`, `ConnectorRegistry`
- [x] **Config 로더** [Step 1.5] — `${VAR}` + `!secret <path>` YAML + Pydantic 모델 + SecretBackend (env/static/vault·aws·gcp 스텁)
- [x] **Observability 베이스** [Step 1.6] — structlog + 시크릿 마스킹 / `Metrics`·`Tracer` ABC + NoOp
- [x] **Utils** [Step 1.7] — `@retryable` (sync+async), `chunked`, `gather_with_concurrency`, `iter_to_async`
- [x] **테스트 인프라** [Step 1.8] — `tests/fixtures/` 표준 데이터셋 + `tests/contracts/` subclass-able contract mixin (새 커넥터는 fixture 두 개로 contract 통과)
- [ ] 레퍼런스 커넥터 3종 (postgres, s3, kafka) [Step 2]
- [ ] CLI + Orchestrator adapters + 추가 커넥터 + 강화 [Step 3–6]

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

---

## 빠른 시작

```bash
git clone git@github.com:genebir/ETL.git
cd ETL
./scripts/bootstrap.sh        # uv sync + .env 복사 + pre-commit + docker compose up
```

자세한 절차와 트러블슈팅은 [`DEVELOPMENT.md`](./DEVELOPMENT.md).

---

## 사용 예 (Step 1.4 기준 — 동작하는 in-Python API)

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

    def read(self, query: str | None = None, **opts) -> Iterator[Record]:
        yield Record(data={"id": 1, "name": "Alice"})
        yield Record(data={"id": 2, "name": "Bob"})


@ConnectorRegistry.register("inmem-snk")
class MySink(BatchSink):
    def __init__(self) -> None: self.rows: list[Record] = []
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def health_check(self) -> bool: return True

    def write(self, records: Iterable[Record], *, mode="append",
              key_columns=None, **opts) -> int:
        before = len(self.rows)
        self.rows.extend(records)
        return len(self.rows) - before


src, snk = MySource(), MySink()

pipeline = (
    Pipeline("orders_to_dw")
    .add(
        Task.extract("inmem-src", "SELECT * FROM orders")
            .transform(lambda r: Record(data={**r.data, "name": r.data["name"].upper()}))
            .load("inmem-snk", mode="upsert", key_columns=["id"])
    )
    .on("post_run",
        lambda ctx, result: ctx.logger.info(
            "done", rows=result.records_written, took=result.duration_seconds))
)

result = pipeline.run(connectors={"inmem-src": src, "inmem-snk": snk})
assert result.success
assert [r.data["name"] for r in snk.rows] == ["ALICE", "BOB"]
```

YAML 파이프라인 정의와 `etlx` CLI는 Step 3에서 도입 예정:

```yaml
# configs/pipelines/orders_to_dw.yaml — Step 3 이후 동작
name: orders_to_dw
mode: batch
source: { connection: pg_prod, query: "SELECT * FROM orders WHERE updated_at > :ts" }
sink: { connection: snowflake_dw, table: ANALYTICS.ORDERS, mode: upsert, key_columns: [ORDER_ID] }
```

```bash
uv run etlx run configs/pipelines/orders_to_dw.yaml     # Step 3
uv run etlx test-connection --all                        # Step 3
```

Airflow / Dagster / Prefect 어댑터는 `SPEC.md` §7 참조 (Step 4).

---

## 아키텍처 개요

```
Orchestrator Adapters  (Airflow / Dagster / Prefect / CLI)        ← Step 4 / Step 3
        ↑
Pipeline Core          (Pipeline / Task / Context / Hooks)        ← Step 1.4 완료
        ↑
Connector Abstraction  (BatchSource/Sink, StreamSource/Sink)      ← Step 1.4 완료
        ↑
Connectors (plugins)   (postgres, s3, kafka, snowflake, ...)      ← Step 2~
        ↑
Foundation             (Config / Secrets / Logging / Metrics
                        / Tracing / Retry / Chunk / async_io)     ← Step 1.5-1.7 완료
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
