# Architecture decisions

Every non-trivial design choice in this repo is recorded as a numbered
ADR in [`DECISIONS.md`](https://github.com/genebir/anyduct/blob/main/dev-docs/DECISIONS.md)
at the repo root. This page is a short index — click through for the
full rationale, alternatives considered, and follow-up actions.

## Foundation (Steps 1–4)

| ADR | Title |
|-----|-------|
| ADR-0001 | SPEC.md와 CLAUDE.md 역할 분리 |
| ADR-0002 | Foundation 기술 스택 확정 (uv, Pydantic v2, structlog, ruff, mypy strict) |
| ADR-0003 | Step 1.4 Core 추상화 — `Connector` / `Record` / `Schema` |
| ADR-0004 | Step 1.5 Config 로더 — YAML + `${env}` + `${SECRET:…}` |
| ADR-0005 | Step 1.6 Observability 베이스 — `Metrics` / `Tracer` 인터페이스 + NoOp |
| ADR-0006 | Step 1.7 Utils — `chunked` / `retryable` / `iter_to_async` |
| ADR-0007 | Step 1.8 테스트 인프라 — `tests/{unit,integration,contracts}` |

## Reference connectors (Step 2)

| ADR | Title |
|-----|-------|
| ADR-0008 | Step 2.1 PostgreSQL — psycopg v3, copy-based loads |
| ADR-0009 | Step 2.2 S3 / MinIO — boto3 + pyarrow, parquet first |
| ADR-0010 | Step 2.3 Kafka — aiokafka + Stream Contract |

## Pipeline + CLI + adapters (Steps 3–4)

| ADR | Title |
|-----|-------|
| ADR-0011 | Step 3.1 Pipeline runtime — YAML builder, Transforms, `anyduct` CLI |
| ADR-0012 | Step 3.2 Stream runtime — `arun_stream`, async commit, buffering |
| ADR-0013 | Step 3.3 Retry / DLQ / 자동 메트릭 / CLI logging |
| ADR-0014 | Step 4 Orchestrator Adapters — Airflow / Dagster / Prefect lazy |

## Connector expansion (Step 5)

| ADR | Title |
|-----|-------|
| ADR-0015 | Step 5.1 MySQL — PyMySQL + executemany |
| ADR-0016 | Step 5.1b SQLite — stdlib 드라이버, Docker 없는 유닛 테스트 |

## Service-layer foundation (Step 7+)

| ADR | Title |
|-----|-------|
| ADR-0017 | 서비스화 전략 — 코어 위에 `services/anyduct-{server,web}` 별도 패키지 |
| ADR-0018 | 디자인 시스템 — Arc 영감, 네이비 + 팝핑크 강조 |
| ADR-0019 | API 프레임워크 = FastAPI |
| ADR-0020 | 메타데이터 저장소 = PostgreSQL 16+ + SQLAlchemy 2.x async + Alembic |
| ADR-0021 | 실행 엔진 = 자체 PostgreSQL-backed worker queue |
| ADR-0022 | 모노레포 구조 — uv workspace + pnpm workspace, CI 3분리 |
| ADR-0023 | 인증·인가 = OIDC + 로컬 fallback + JWT RS256 + 4-role RBAC |

## Core 1급 모델 격상 (Step 6)

| ADR | Title |
|-----|-------|
| ADR-0024 | Asset / Lineage / Cursor를 코어의 1급 모델로 (v0.1.0 = Cursor + OTel + docs + PyPI / v0.2.0 = Asset + Lineage) |

## Conventions

* ADRs are numbered sequentially from `ADR-0001`, never reused.
* A decision is reversed by writing a *new* ADR that supersedes the old
  one — the original entry stays intact for archaeological context.
* SPEC.md changes must cite the relevant ADR.

## Where to find them

The canonical source is one file:

```
DECISIONS.md   ←  read this for the actual rationale
```

This page only indexes it because mkdocstrings doesn't render Markdown
outside `docs/`.
