# DEVELOPMENT — 신규 환경 인계 문서

> 처음 이 저장소를 클론한 사람(혹은 다른 PC로 옮긴 본인)이 **막힘없이 같은 상태에서 작업을 이어가도록** 만드는 문서.
> 세션 시작 시는 [`CLAUDE.md`](./CLAUDE.md) → [`ROADMAP.md`](./ROADMAP.md) → 최근 커밋 로그 순서로 확인.

---

## 1. Prerequisites

| 항목 | 버전 / 비고 |
|---|---|
| OS | Linux / macOS / WSL2 (Windows 네이티브는 미검증) |
| Python | `.python-version` 파일 기준 (Step 1.1에서 확정) |
| 패키지 매니저 | **`uv` 단일 사용**. `pip`/`poetry`/`conda` 혼용 금지 |
| Docker | dev 컨테이너 및 통합 테스트용 (Postgres/Kafka/MinIO 등) |
| Git | 2.30+ |
| `make` | 표준 타깃 실행용 (선택) |

`uv` 미설치 시:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 2. Bootstrap (원커맨드)

```bash
git clone <repo>
cd ETL
./scripts/bootstrap.sh        # uv sync + .env 복사 + pre-commit + docker compose up
```

`scripts/bootstrap.sh`가 하는 일:
1. `uv` 설치 확인 (없으면 설치)
2. `uv sync --all-extras` (의존성 + 락파일 적용)
3. `.env.example` → `.env` 복사 (이미 있으면 스킵)
4. `uv run pre-commit install`
5. `docker compose -f docker/docker-compose.dev.yml up -d`
6. `uv run etlx test-connection --all`

> Step 1.2에서 구현 예정. 그 전까지는 수동:
> ```bash
> uv venv && source .venv/bin/activate
> uv pip install -e .
> cp .env.example .env
> ```

---

## 3. .env 설정

- `.env.example`를 복사해 `.env` 생성 (`.env`는 **절대 커밋 금지**, `.gitignore` 처리).
- 시크릿(비밀번호/토큰/키 경로)만 채운다. 구조 정보는 `configs/connections.yaml`.
- 시크릿 백엔드를 쓰려면 `SECRET_BACKEND=vault|aws_sm|gcp_sm` 설정.

---

## 4. 표준 명령어

```bash
# 의존성
uv sync                              # 락파일 기준 동기화
uv add <pkg>                         # 의존성 추가
uv lock --upgrade                    # 락파일 갱신

# 품질 게이트
uv run pre-commit run --all-files    # 전체 lint/format/secret 검사
uv run ruff check . && uv run ruff format .
uv run mypy etl_plugins
uv run pytest                        # unit only
uv run pytest tests/integration -m it
uv run pytest --cov=etl_plugins --cov-report=term-missing

# 로컬 dev 인프라
docker compose -f docker/docker-compose.dev.yml up -d
docker compose -f docker/docker-compose.dev.yml down
docker compose -f docker/docker-compose.dev.yml logs -f <service>

# CLI (Step 3 이후)
uv run etlx run configs/pipelines/orders_to_dw.yaml
uv run etlx test-connection pg_prod
uv run etlx list-connectors
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

## 5. 새 작업 시작 체크리스트

1. `git pull` 후 `uv sync`로 의존성 동기화.
2. `CLAUDE.md` § 5 "현재 단계" 확인.
3. `ROADMAP.md`에서 "← 작업 중" 표시 항목 또는 다음 미체크 항목 식별.
4. 브랜치 생성 (Step 번호 포함 권장): `git checkout -b feat/step-2.1-postgres-source`
5. 작업.
6. 종료 시 §8.3 체크리스트.

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
