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

## (이후 ADR 작성 시 위 양식을 복사해서 추가)
