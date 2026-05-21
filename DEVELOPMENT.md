# DEVELOPMENT — 신규 환경 인계 문서

> 처음 이 저장소를 클론한 사람(혹은 다른 PC로 옮긴 본인)이 **막힘없이 같은 상태에서 작업을 이어가도록** 만드는 문서.
> 세션 시작 시는 [`CLAUDE.md`](./CLAUDE.md) → [`ROADMAP.md`](./ROADMAP.md) → 최근 커밋 로그 순서로 확인.

---

## 1. Prerequisites

이 저장소는 **모노레포** 입니다 (ADR-0017 / ADR-0022). 코어 라이브러리만 만지는 경우는 (1)~(4)면 충분하고, 서비스(`services/etlx-server`, `services/etlx-web`)까지 만지려면 (5)(6)도 필요합니다.

| 항목 | 버전 / 비고 |
|---|---|
| OS | Linux / macOS / WSL2 (Windows 네이티브는 미검증) |
| (1) Python | `.python-version` 기준 (현재 3.11) |
| (2) `uv` | **유일한 Python 패키지 매니저**. `pip`/`poetry`/`conda` 혼용 금지 |
| (3) Docker | dev 컨테이너 + 통합 테스트(testcontainers) |
| (4) Git | 2.30+ |
| (5) Node.js | **22+** (services/etlx-web). `nvm install 22` 권장 |
| (6) pnpm | **9+** (services/etlx-web). `npm i -g pnpm@9` 또는 `corepack enable && corepack prepare pnpm@9 --activate` |
| `make` | 표준 타깃 실행용 (선택) |

설치 안내:
```bash
# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Node + pnpm (services/etlx-web 만 만질 때 필요)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/master/install.sh | bash
nvm install 22
npm i -g pnpm@9
```

---

## 2. Bootstrap (원커맨드)

```bash
git clone git@github.com:genebir/ETL.git
cd ETL
./scripts/bootstrap.sh
```

`scripts/bootstrap.sh`가 하는 일 (현재 구현):
1. `uv` 설치 확인 (없으면 설치)
2. **`uv sync --all-packages`** — 코어 + `services/etlx-server` workspace member 의존성 일괄 설치
3. `.env.example` → `.env` 복사 (이미 있으면 스킵)
4. `uv run pre-commit install`
5. `.secrets.baseline` 없으면 생성
6. **`uv run lint-imports`** — 단방향 의존 검증 (ADR-0017/0022, `.importlinter`)
7. Node ≥22 + pnpm 설치돼 있으면 **`pnpm install --frozen-lockfile`** (services/etlx-web 의존성)
8. Docker 있으면 `docker compose -f docker/docker-compose.dev.yml up -d` (코어 통합 테스트용 인프라)

---

## 3. .env 설정

- `.env.example`를 복사해 `.env` 생성 (`.env`는 **절대 커밋 금지**, `.gitignore` 처리).
- 시크릿(비밀번호/토큰/키 경로)만 채운다. 구조 정보는 `configs/connections.yaml`.
- 시크릿 백엔드를 쓰려면 `SECRET_BACKEND=vault|aws_sm|gcp_sm` 설정.

---

## 4. 표준 명령어

### 4.1 코어 라이브러리 (`etl_plugins/`)

```bash
# 의존성 (workspace 전체)
uv sync --all-packages               # 락파일 기준, 코어 + services/etlx-server
uv add <pkg>                         # 코어에 의존성 추가
uv lock --upgrade                    # 락파일 갱신

# 품질 게이트
uv run pre-commit run --all-files
uv run ruff check . && uv run ruff format .
uv run mypy etl_plugins
uv run lint-imports                  # ADR-0017/0022 단방향 의존 검증
uv run pytest -m "not it"            # 코어 unit (321 + 3 skip)
uv run pytest -m it                  # 코어 통합 (111, Docker 필요)
uv run pytest --cov=etl_plugins --cov-report=term-missing

# CLI
uv run etlx run        configs/pipelines/<x>.yaml --connections configs/connections.yaml
uv run etlx run-stream configs/pipelines/<x>.yaml --connections configs/connections.yaml --stop-after-records 1000
uv run etlx test-connection --all                  --connections configs/connections.yaml
uv run etlx list-connectors
uv run etlx --log-format console --log-level DEBUG run ...
```

### 4.2 services/etlx-server (FastAPI, Step 7~)

```bash
uv sync --all-packages                                            # 코어와 함께 sync
uv run --package etlx-server uvicorn etlx_server.main:app --reload --port 8000
uv run pytest services/etlx-server/tests                          # placeholder 2 tests
curl localhost:8000/health   # {"status":"ok"}
curl localhost:8000/version  # {"server":"0.0.0","core":"0.0.1"}
```

> Step 7.2부터 `alembic upgrade head` 등이 추가됩니다.

### 4.3 services/etlx-web (Next.js, Step 10)

```bash
pnpm install --frozen-lockfile                                    # 모노레포 루트에서
pnpm --filter @etlx/web dev                                       # http://localhost:3000
pnpm --filter @etlx/web typecheck
pnpm --filter @etlx/web build
```

### 4.4 로컬 dev 인프라

```bash
# 코어 통합 테스트용 (postgres/kafka/minio/redis)
docker compose -f docker/docker-compose.dev.yml up -d
docker compose -f docker/docker-compose.dev.yml down

# 서비스 풀스택 (Step 11에서 본격화)
docker compose -f services/docker-compose.services.yml --profile server --profile web up
```

`Makefile` 단축 (Step 1.1에서 구현):
```bash
make setup       # bootstrap.sh
make test        # pytest
make lint        # ruff + mypy
make fmt         # ruff format
make up / down   # docker compose
make clean       # __pycache__, .pytest_cache, dist 삭제
```

---

## 5. 새 작업 시작 체크리스트 (다른 PC에서 이어받을 때 포함)

1. `git pull --rebase` — origin/main 최신화.
2. **`./scripts/bootstrap.sh`** — 환경 차이 흡수 (uv sync, pnpm install, docker compose up 등). 처음이라면 (5)(6) prereq를 먼저 설치.
3. **`CLAUDE.md §5 "현재 단계"** 확인** — 어디까지 갔는지 한 화면에 보임.
4. **`ROADMAP.md`** — "← 작업 중" 표시가 있으면 그 항목, 없으면 첫 번째 미체크 항목.
5. **최근 3~5개 커밋 로그** — `git log --oneline -8` 로 최근 컨텍스트.
6. 의존 검증 한 번 — `uv run lint-imports`, `uv run pytest -m "not it" -q` 가 모두 green인지.
7. 작업 시작. 브랜치는 Step 번호 포함 권장 (`git checkout -b feat/step-7.2-meta-db-schema`).
8. 종료 시 §6 체크리스트.

---

## 6. 세션 종료 체크리스트 (CLAUDE.md §7 강제)

- [ ] `ROADMAP.md` 체크박스 갱신, "← 작업 중 (YYYY-MM-DD, 다음 할 일: ...)" 메모 남김
- [ ] 설계 결정 발생 시 `DECISIONS.md`에 ADR 추가
- [ ] `CHANGELOG.md`의 `[Unreleased]`에 항목 추가
- [ ] 커밋 메시지에 Step 번호 포함 (예: `feat(connector): add postgres source [Step 2.1]`)
- [ ] `git push`

---

## 7. 새 커넥터 추가 절차

1. `etl_plugins/connectors/<category>/<name>.py` 생성.
2. 적절한 베이스(`BatchSource`/`BatchSink`/`StreamSource`/`StreamSink`) 상속.
3. **`@ConnectorRegistry.register("<name>")` 데코레이터 필수**.
4. 외부 패키지면 `pyproject.toml` entry point 등록:
   ```toml
   [project.entry-points."etl_plugins.connectors"]
   <name> = "<module>:<Class>"
   ```
5. **공통 contract test**(`tests/contracts/`) 통과 필수.
6. `tests/integration/test_<name>.py`에 testcontainers 기반 통합 테스트 추가.
7. `SPEC.md` §6 지원 시스템 표 갱신.
8. `ROADMAP.md` 해당 항목 체크.

---

## 8. 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| `uv sync` 실패 (`Resolution failed`) | `.python-version`과 시스템 Python 불일치 → `uv python install <ver>` |
| `pre-commit` hook이 너무 느림 | `pre-commit run --files <변경파일>`로 부분 실행 |
| `docker compose up` 포트 충돌 | `docker compose -f docker/docker-compose.dev.yml down` 후 호스트의 5432/9092 등 사용 프로세스 확인 |
| testcontainers `permission denied` | docker socket 권한: `sudo usermod -aG docker $USER` 후 재로그인 |
| `etlx test-connection` 실패 | `.env`의 시크릿, `configs/connections.yaml`의 host/port, 방화벽 순으로 확인 |
| pre-commit `detect-secrets` 오탐 | `.secrets.baseline` 갱신: `uv run detect-secrets scan > .secrets.baseline` |
| `mypy` 외부 라이브러리 stub 부재 | `uv add --dev types-<pkg>` 또는 `[[tool.mypy.overrides]]`로 `ignore_missing_imports` |

---

## 9. 자주 보는 파일

| 파일 | 용도 |
|---|---|
| `CLAUDE.md` | 세션 컨텍스트 (먼저 읽기) |
| `SPEC.md` | 마스터 설계 명세 |
| `ROADMAP.md` | 진행 상태 |
| `DECISIONS.md` | ADR |
| `configs/connections.yaml` | 모든 연결 정의 (시크릿 참조만, 평문 금지) |
| `configs/pipelines/*.yaml` | 파이프라인 정의 |
| `docker/docker-compose.dev.yml` | 로컬 DB/Stream 일괄 기동 |
| `scripts/bootstrap.sh` | 원커맨드 셋업 |

---

## 10. Spark 워커 배포 (TB급/분산, ADR-0031/0032)

파이프라인은 `engine`으로 실행 백엔드를 고른다: `local`(기본, 인프로세스 행 스트리밍) / `spark`(분산). `engine: "spark"` 파이프라인은 **Spark 워커**가 실행한다.

### 핵심: "전부 번들, 최소 설정"
- **pyspark가 Spark 런타임 jar를 포함**한다 → 별도 Spark 다운로드 불필요. 시스템 의존성은 **JRE 하나뿐**.
- **JDBC 드라이버는 자동 fetch**: Spark 백엔드가 connection 종류에서 Maven 좌표를 뽑아 `spark.jars.packages`로 받는다(`etl_plugins/runtime/spark/backend.py`의 `_JDBC`). → 사용자가 JAR을 손수 배치할 필요 없음. (단, **첫 실행 시 Maven Central 접근(egress) 필요**.)

### 워커 이미지 빌드/실행
전용 이미지 `services/etlx-server/Dockerfile.worker`(= 기존 server 이미지 + JRE17 + `[spark]` extra). 빌드 컨텍스트는 **repo 루트**(uv workspace).

```bash
# 빌드
docker build -f services/etlx-server/Dockerfile.worker -t etlx-worker:dev .

# 번들 단일노드(local[*])로 실행 — 추가 클러스터 불필요
docker run --rm \
  -e DATABASE_URL=postgresql+asyncpg://etl:...@db:5432/etl \
  -e SECRET_BACKEND=vault -e VAULT_ADDR=... \
  etlx-worker:dev

# 외부 클러스터에 driver로 붙기 (마스터만 바꾸면 됨)
docker run … etlx-worker:dev etlx-server worker run \
  --spark-master "spark://spark-master:7077"     # 또는 yarn / k8s://…
```

- 기본 CMD = `etlx-server worker run`(spark-master 기본 `local[*]`).
- 평범한 API/로컬 워커는 기존 `services/etlx-server/Dockerfile`을 쓴다(이미지가 가볍다 — JRE/pyspark 없음). Spark 워커만 이 이미지를 쓴다.
- 워커는 HTTP 포트가 없다(큐 소비자). 살아있음은 `runs.heartbeat_at` + ZombieReaper(9.3b)로 관측.

### 시크릿(분산)
드라이버가 런타임에 `SecretBackend`(Vault/AWS SM/GCP SM)에서 재해석한다(평문 인자/Spark conf 금지). 클러스터/컨테이너에는 시크릿 backend 접근 권한(IAM/role)만 부여(ADR-0032).

### 제약 / 미지원 (v1)
- 선언적 transform만 pushdown(rename/cast/select/drop/add_constant/dedupe/filter). **임의 `python` transform·raw Python 필터식은 Spark 거부** — 빌더 UI가 경고하고, 그런 파이프라인은 `local`로 돌린다.
- 현재는 **번들 in-process(worker-as-driver)** 모델. spark-submit/Databricks Jobs로 *제출*하는 thin-worker 모드는 후속(ADR-0032 P3.4b).

### Air-gapped (Maven egress 불가)
JAR을 빌드 시 `/app/jars/`에 받아두고 `spark.jars`로 지정(향후 `--spark-jars` 플래그 예정). 좌표는 `backend.py`의 `_JDBC` 참조. `Dockerfile.worker` 하단 주석 참고.
