# DECISIONS — Architecture Decision Records (ADR)

> 모든 설계 결정은 여기에 ADR로 기록한다. **`SPEC.md` 변경은 반드시 ADR 동반.**
>
> 양식: 한 결정 = 한 섹션. 번호는 0001부터 순차 증가. Status는 `Proposed` / `Accepted` / `Superseded by ADR-XXXX` / `Deprecated`.

---

## ADR 양식

```markdown
## ADR-XXXX: <한 줄 결정 요약>

- **Date**: YYYY-MM-DD
- **Status**: Proposed | Accepted | Superseded by ADR-YYYY | Deprecated
- **Context**: 왜 이 결정이 필요했는가 (문제 상황, 제약, 대안)
- **Decision**: 무엇을 정했는가
- **Consequences**: 이 결정의 결과 (긍정/부정, 향후 영향)
- **References**: 관련 ADR, SPEC 섹션, 외부 링크
```

---

## ADR-0001: SPEC.md와 CLAUDE.md 역할 분리

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  프로젝트 초기에 전체 설계 명세서 한 파일을 `CLAUDE.md`로 두었으나, `SPEC.md` §8.2가 정의하는 CLAUDE.md는 "세션 시작 시 빠르게 읽는 요약 컨텍스트(프로젝트 목적, 아키텍처 요약, 명령어, 디렉토리 규약, 현재 단계, 금기사항)"에 해당한다. 명세서 전체(수십 KB)를 매 세션 로드하면 토큰 비용이 크고 정작 필요한 "현재 상태"가 묻힌다.
- **Decision**:
  - **`SPEC.md`**: 마스터 설계 명세서 (원칙·아키텍처·인터페이스·설정·로드맵 전체). 변경 시 ADR 동반 필수.
  - **`CLAUDE.md`**: §8.2 형식의 세션 컨텍스트. 목적/아키텍처 요약/명령어/디렉토리/현재 단계/금기사항/링크. 빠르게 훑고 `SPEC.md`·`ROADMAP.md`로 깊이 들어간다.
- **Consequences**:
  - (+) 매 세션 로드 비용 절감. "현재 상태"가 첫눈에 보임.
  - (+) SPEC 변경 → ADR 강제 → 설계 표류 방지.
  - (−) 두 파일의 일관성 유지 책임 발생. 변경 시 둘 다 갱신해야 하는 케이스 존재.
- **References**: `SPEC.md` §8.2, `CLAUDE.md` §8

---

## ADR-0002: Foundation 기술 스택 확정

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  Step 1 Foundation 스캐폴딩을 진행하면서 Python 버전, 패키지 매니저, 코어 라이브러리, 개발 도구, 빌드 백엔드, 라이선스를 한꺼번에 결정해야 했다. SPEC.md §8.1/§8.4가 이미 일부(uv, ruff, mypy, detect-secrets, yamllint, structlog, Pydantic v2)를 가이드하고 있으며, 나머지(Python 버전, CLI 프레임워크, 빌드 백엔드, 라이선스)는 결정이 필요했다.
- **Decision**:
  | 영역 | 선택 | 근거 |
  |---|---|---|
  | Python | `>=3.11` | `Self`, exception groups, structural pattern match, TaskGroup 등 모던 typing/async 활용. 3.10은 2026년 말 EoL 임박. |
  | 패키지 매니저 | **`uv` 단일** | 빠른 resolve/sync, lockfile 표준화, dependency-groups(PEP 735) 지원. 혼용 금지(CLAUDE.md §6). |
  | 데이터 검증 | Pydantic v2 | SPEC.md §4.2가 명시. v2의 성능/strict 모드 활용. |
  | CLI | **Typer** | type-hint 기반(Click 상위 호환), `etlx` 서브커맨드 구조와 자연스럽게 매핑. |
  | 로깅 | structlog | SPEC.md §9.2가 명시. JSON 구조화 로그, contextvar 친화. |
  | Retry | tenacity | 데코레이터 + 지수 백오프/jitter, async 지원. |
  | 템플릿팅 | Jinja2 | SPEC.md §2/§5.3 (`${VAR}` 외 조건/루프 필요 시). |
  | 테스트 | pytest + pytest-asyncio + testcontainers | SPEC.md §9.5. |
  | Lint/Format | ruff (단일 도구) | 빠르고 isort+pycodestyle+flake8 룰 통합. |
  | Type check | mypy `strict = true` | Pydantic 플러그인 활용. |
  | 시크릿 가드 | detect-secrets (pre-commit) | SPEC.md §8.4. |
  | YAML lint | yamllint | SPEC.md §8.4. |
  | 빌드 백엔드 | hatchling | PEP 517, uv 친화, setup.py 불필요. |
  | 라이선스 | Apache-2.0 | ETL/오케스트레이션 생태계(Airflow, Beam 등)와 일치. patent grant 포함. |
- **Consequences**:
  - (+) 모든 환경에서 `uv sync` 한 번으로 동일한 의존성 트리 재현 (`uv.lock`).
  - (+) ruff 단일 도구로 lint/format 통합, pre-commit 속도 빠름.
  - (+) hatchling + `packages = ["etl_plugins"]` 만으로 빌드 가능 — setup 파일 불필요.
  - (−) Python 3.10 이하 사용자는 지원되지 않음. (`requires-python = ">=3.11"`)
  - (−) `uv` 미설치 환경(레거시 CI 등)에서는 `bootstrap.sh`가 자동 설치 시도.
- **References**: SPEC.md §8.1, §8.4, §9.2, §9.5 · `pyproject.toml` · `.pre-commit-config.yaml`

---

## ADR-0003: Step 1.4 Core 추상화 — 주요 설계 결정

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  SPEC.md §4가 정의하는 인터페이스를 구체화하면서 다음 결정들이 필요했다: Pipeline API 형태(클래스 상속 vs 빌더), 커넥터 생명주기(누가 open/close), Record/Schema의 검증 수준, 등록 방식, hooks 모델.
- **Decision**:
  1. **Pipeline은 fluent builder 패턴.** `Pipeline("p").add(Task.extract("src", "q").transform(fn).load("snk", mode="upsert")).run(...)`. 클래스 상속이나 데코레이터 기반이 아닌 메서드 체이닝으로 가독성 우선.
  2. **`Pipeline.run`은 caller-managed connector lifecycle.** `connectors: dict[str, Connector]`를 명시적으로 받는다. open/close는 호출자 책임. Config-driven instantiation은 Step 1.5에서 별도로 다룬다.
  3. **`Record`는 Pydantic v2 + `extra="forbid"`.** payload는 항상 `data: dict[str, Any]`. 위치 정보(offset/lsn/seq)·source 메타·schema_version은 별도 필드. 임의 추가 필드 금지로 실수 차단.
  4. **`Connector.name`은 `ClassVar[str]`, `@ConnectorRegistry.register("name")` 데코레이터가 설정.** 클래스 정의에 이름을 박지 않고 등록 시점에 단일 진실로 부여 — 잘못 매핑 시 즉시 발견.
  5. **Hooks는 `.on(event, fn)`으로 등록.** 메서드 오버라이드(서브클래싱)가 아닌 외부 함수 등록 방식. 이벤트: `pre_run`, `post_run`, `on_error`, `on_task_start`, `on_task_end`. 비례적 정보를 위치 인자로 전달.
  6. **Step 1.4는 batch 모드만 구현.** `mode="stream"`은 `PipelineError`로 명시적 NotImplemented. Stream 실행 + retry + DLQ + checkpoint는 Step 3.
  7. **`Connector.__exit__`는 예외를 suppress하지 않음** (`-> None` 반환). 표준 contextmanager 관례.
  8. **레지스트리의 entry-point 플러그인 로드는 fail-soft.** 한 플러그인 import 실패가 전체 레지스트리를 깨지 않도록 warning 로그 후 skip.
  9. **`ConnectError`로 이름 변경** (SPEC.md §4.1 원안의 `ConnectionError`). Python 빌트인 `ConnectionError` shadowing 방지.
  10. **Schema는 최소 구현** (`fields: tuple[Field, ...]`). pyarrow/SQLAlchemy 매핑·nested 타입은 Step 1.7+.
- **Consequences**:
  - (+) Pipeline API가 SPEC.md §4.4의 예시 코드와 일치 — 학습 곡선 낮음.
  - (+) Pipeline 단위 테스트가 외부 의존 없이 in-memory connector만으로 가능 (57 tests, 0.28s).
  - (+) Connector 명세는 짧고 ABC에 강제됨 — 새 커넥터는 4~6개 메서드만 구현하면 됨.
  - (−) Pipeline.run의 connectors 인자는 Step 1.5의 config 통합 전까지 다소 verbose. CLI/YAML 빌더에서 자동화 예정.
  - (−) Step 1.4에서 batch만 동작 — stream 파이프라인은 Step 3까지 대기.
- **References**: SPEC.md §4 · `etl_plugins/core/` · `tests/unit/core/`

---

## (이후 ADR 작성 시 위 양식을 복사해서 추가)
