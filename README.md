# ETL Plugins

> Backend-agnostic, orchestrator-agnostic **Python ETL plugin library**.
> 모든 데이터 소스/싱크(RDBMS, NoSQL, DW, Stream, Object Storage)에 대해 통일된 인터페이스를 제공하고, 어떤 오케스트레이터(Airflow / Dagster / Prefect / 사내 워크플로우) 위에서도 그대로 재사용할 수 있다.

**현재 단계**: Step 1 — Foundation (in progress, 2026-05-14 착수). 상세는 [`ROADMAP.md`](./ROADMAP.md) 참조.

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
git clone <repo-url>
cd ETL
./scripts/bootstrap.sh        # uv sync + .env 복사 + pre-commit + docker compose up
```

자세한 절차와 트러블슈팅은 [`DEVELOPMENT.md`](./DEVELOPMENT.md).

---

## 사용 예 (목표 인터페이스 — Step 2~3에서 구현)

**파이프라인 정의**(`configs/pipelines/orders_to_dw.yaml`):
```yaml
name: orders_to_dw
mode: batch
source: { connection: pg_prod, query: "SELECT * FROM orders WHERE updated_at > :ts" }
sink: { connection: snowflake_dw, table: ANALYTICS.ORDERS, mode: upsert, key_columns: [ORDER_ID] }
```

**CLI**:
```bash
uv run etlx run configs/pipelines/orders_to_dw.yaml
uv run etlx test-connection --all
```

**Python**:
```python
from etl_plugins import Pipeline
Pipeline.from_yaml("configs/pipelines/orders_to_dw.yaml").run()
```

**Airflow / Dagster / Prefect**: 얇은 어댑터로 그대로 호출. SPEC.md §7 참조.

---

## 라이선스

Apache License 2.0. 자세한 내용은 (추후 추가될) `LICENSE` 파일 참조.
