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

## ADR-0004: Step 1.5 Config 로더 — 설계 결정

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  SPEC.md §5는 `.env` / `connections.yaml` / `pipelines/*.yaml` 세 종류의 설정 파일을 정의한다. 시크릿이 평문으로 YAML에 들어가면 안 되며, `${VAR}`와 `!secret <path>` 두 형식을 모두 지원해야 한다. 동시에 connections.yaml의 각 connection은 커넥터별로 임의 필드를 가지며(host/account/bootstrap_servers/...) Pydantic 검증을 강제하면 유연성을 잃는다.
- **Decision**:
  1. **로드 순서**: `yaml.load` (custom loader, `!secret` 태그 인식) → `expand_env` (`${VAR}` 치환, 미설정 시 ConfigError) → `resolve_secrets` (SecretBackend 호출, 옵션) → Pydantic `model_validate`. 이 순서로 SecretRef 객체가 env 치환을 거치고도 안전하게 보존된다.
  2. **`${VAR}` 문법은 단순화**: `[A-Z_][A-Z0-9_]*`만 매치, 미설정 시 즉시 ConfigError. bash의 `${VAR:-default}` 등은 일단 미지원 (필요해지면 ADR로 추가).
  3. **`!secret` 태그는 SecretRef로 머문다**. `SecretBackend`를 받아야만 plaintext로 변환. backend가 없고 SecretRef가 남아있으면 ConfigError (`require_secrets_resolved=True`가 기본).
  4. **`extra=` 정책의 이중 기준**:
     - 최상위 (`ConnectionsConfig`, `PipelineConfig`): `extra="forbid"` — 오탐 차단.
     - 커넥터/스텝별 (`ConnectionConfig`, `SourceConfig`, `SinkConfig`, `TransformConfig`): `extra="allow"` — 커넥터별 임의 필드 통과.
     `ConnectionConfig.options()`는 `type` 제외 dict를 반환해 Connector 인스턴스화에 그대로 전달 가능.
  5. **SecretBackend는 ABC**. 내장: `EnvSecretBackend` (path를 env 이름으로 취급), `StaticSecretBackend` (테스트/스크립트용). `vault` / `aws_sm` / `gcp_sm`는 `NotImplementedError` 스텁 — 인터페이스는 동결, 구현은 이후 단계.
  6. **`get_secret_backend(name=None)` 팩토리** — name 인자 → `$SECRET_BACKEND` env → `"env"` 순으로 결정.
  7. **`load_dotenv`는 thin wrapper**. 라이브러리는 자동 로드하지 않음 — 호출자가 명시적으로 부른다.
  8. **`python-dotenv` 직접 의존성으로 승격** (이전엔 testcontainers의 transitive).
- **Consequences**:
  - (+) 시크릿이 YAML에 평문으로 들어갈 통로가 두 곳뿐 (`${VAR}`로 env 우회, `!secret`으로 backend 우회) — 사고 가능성 작음.
  - (+) 새 커넥터를 추가해도 ConnectionConfig 모델을 건드릴 필요 없음 (extra="allow").
  - (+) Pydantic validator로 입력 형식 변경 발견이 즉시 가능.
  - (−) `${VAR:-default}` 미지원 — 일부 사용자에게는 불편할 수 있음. 필요 시 ADR로 확장.
  - (−) SecretRef를 그대로 보존하는 흐름 때문에 expand_env가 SecretRef도 descend해야 함 — 단순화 위해 `SecretRef.path`도 env 치환 대상.
- **References**: SPEC.md §5, §9.3 · `etl_plugins/config/` · `tests/unit/config/`

---

## ADR-0005: Step 1.6 Observability 베이스 — 설계 결정

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  SPEC.md §9.2는 structlog 로깅 + OpenTelemetry/Prometheus 메트릭 + OTLP 트레이싱을 요구한다. 동시에 §9.3은 시크릿 로그 노출 금지. Step 1.6에서 어디까지 구현할지, 외부 의존성(opentelemetry-sdk ~수십 MB)을 어떻게 다룰지가 결정 필요.
- **Decision**:
  1. **ABC + NoOp 기본 패턴 유지** (core/Connector와 동일 결). `Metrics`/`Counter`/`Histogram` ABC, `Tracer`/`Span` ABC, 기본은 NoOp. 호출자가 `set_metrics`/`set_tracer`로 실제 백엔드 주입.
  2. **structlog는 기본 의존성** (이미 deps에 있음). JSON 렌더러를 기본, dev 콘솔 렌더러는 선택. `configure_logging`은 idempotent.
  3. **시크릿 마스킹은 키 substring 매칭** (regex word-boundary 대신). 보안은 보수적으로 — 거짓 양성(과도 마스킹)이 거짓 음성(누설)보다 안전. 기본 키워드: password, passphrase, secret, token, api_key, apikey, access_key, accesskey, private_key, privatekey, credential, credentials, authorization, sasl_password. `make_secret_masker(keywords)`로 커스터마이즈 가능.
  4. **Counter + Histogram만 제공**. Gauge는 Step 1.6 범위에 포함하지 않음 — 필요 시점에 추가. 이름 상수(`RECORDS_READ_TOTAL` 등)는 SPEC.md §9.2를 따르며 `etl_plugins.` 네임스페이스 prefix.
  5. **실제 OTel/Prometheus 백엔드 구현은 Step 6(강화)로 미룸**. 인터페이스만 동결하고 `[project.optional-dependencies] observability` 자리를 `pyproject.toml`에 비워둠. 이렇게 해야 라이브러리 코어가 무거운 의존성을 끌고 다니지 않음.
  6. **Pipeline 내부 통합도 후순위**. 현재 `Pipeline.run`이 records_read/written을 RunResult로 반환하므로 외부에서 metrics emit 가능. 자동 emit/span 시작은 Step 3 retry+observability 통합 시 다룸.
  7. **`Span.__exit__`가 자동으로 `record_exception` 호출**. 호출자가 try/except로 명시 호출하지 않아도 예외 경로 캡처. 안전한 디폴트.
- **Consequences**:
  - (+) 라이브러리 사용자는 observability 백엔드 미설정 시에도 동일한 코드 작동 (NoOp).
  - (+) opentelemetry 의존성을 핵심에서 분리해 install footprint 작음.
  - (+) 시크릿 마스킹이 기본 활성 — 실수 노출 위험 작음.
  - (−) 마스킹 키워드가 너무 광범위하면 정상 필드도 가려짐 (예: `customer_credentials_id`). 사용자가 `make_secret_masker`로 좁힐 수 있음.
  - (−) Pipeline 자동 계측이 없어 사용자가 명시적으로 metrics/tracer 호출해야 — Step 3에서 보완.
- **References**: SPEC.md §9.2, §9.3 · `etl_plugins/observability/` · `tests/unit/observability/`

---

## ADR-0006: Step 1.7 Utils — 설계 결정

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  SPEC.md §9.1은 `@retryable(max_attempts, on=...)` 데코레이터 + 지수 백오프 + jitter를 요구한다. §9.4는 메모리 안전 청크 처리 + asyncio/스레드 풀 혼용을 요구한다. 동기/비동기 양쪽을 한 데코레이터로 지원해야 하며, observability와의 연동 위치(데코레이터 안 vs 호출자)도 결정 필요.
- **Decision**:
  1. **tenacity 위에 얇은 래퍼**: `tenacity.Retrying` / `AsyncRetrying`을 직접 노출하지 않고, `@retryable(...)`이라는 우리 시그니처로 감싼다. tenacity 직접 의존을 호출자 코드에서 제거 가능.
  2. **단일 데코레이터로 sync + async 모두 지원**: `inspect.iscoroutinefunction`으로 분기. 사용자 코드에서 별도 import 불필요.
  3. **`reraise=True` 강제**: tenacity 기본은 `RetryError`로 래핑하지만 우리 데코레이터는 원래 예외 타입 그대로 propagate. 호출자가 except 절을 평소처럼 작성 가능.
  4. **`@retryable` 무인자 형태도 지원** (`@retryable` 그대로 데코레이션). 기본 설정 사용. `overload` 시그니처로 mypy strict 호환.
  5. **observability 자동 연동**: `before_sleep` 콜백에서 structlog warning + `ERRORS_TOTAL` 카운터 증가. metrics 미설정 시(NoOp) 비용 0. utils → observability 단방향 의존만 허용 (역방향은 순환 위험).
  6. **chunked는 list 청크만 제공**. `chunked_lazy`(iterator-of-iterators)는 footgun이라 보류. take/drop은 itertools.islice 보조.
  7. **async_io는 stdlib 위 얇은 wrapper**. `run_sync_in_thread`는 `asyncio.to_thread`, `gather_with_concurrency`는 semaphore 패턴, `iter_to_async`는 옵션으로 `in_thread=True` 지원 (blocking iterator → 이벤트 루프 안 막힘).
  8. **Circuit Breaker / Rate Limiter / DLQ는 Step 1.7 범위 밖** — Step 3 retry+observability 통합에서 다룸.
- **Consequences**:
  - (+) 사용자는 한 데코레이터로 sync/async 통일. tenacity 학습 없이도 사용 가능.
  - (+) 재시도 시 자동으로 메트릭/로그 — observability 설정만 하면 추적 가능.
  - (+) `reraise=True` 덕분에 기존 try/except 패턴이 그대로 작동.
  - (−) tenacity의 고급 기능(`retry_if_result`, `before`, `after` 등) 노출 안됨. 필요해지면 ADR로 확장.
  - (−) `iter_to_async(in_thread=True)`는 thread 생성 비용이 항목당 발생 — 진짜 무거운 blocking 호출에만 사용 권장.
- **References**: SPEC.md §9.1, §9.4 · `etl_plugins/utils/` · `tests/unit/utils/`

---

## (이후 ADR 작성 시 위 양식을 복사해서 추가)
