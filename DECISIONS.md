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

## ADR-0007: Step 1.8 테스트 인프라 — 설계 결정

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  SPEC.md §9.5는 "새 커넥터는 공통 contract test suite 통과 필수 (read 후 write 후 read 일치 등)"와 "표준 샘플 데이터셋을 `tests/fixtures/`에 두고 모든 커넥터 테스트가 동일 데이터 사용"을 요구한다. 동시에 기존 InMemory 커넥터는 `tests/unit/core/conftest.py`에 묻혀 있어 contract suite와 공유하기 어려웠다.
- **Decision**:
  1. **Contract test = subclass-able mixin 클래스**. 베이스 클래스 이름이 `_`로 시작하면 pytest가 자동 수집하지 않으므로 추상 base가 됨. 신규 커넥터 테스트는 `class TestPostgresSource(_BatchSourceContract): @pytest.fixture def source(self): ...`만 작성하면 모든 inherited 테스트가 새 fixture로 실행됨.
  2. **세 가지 contract**:
     - `_BatchSourceContract` (7 tests) — interface / lifecycle / read() iterator + Record yield + seeded data 일치 + 반복 호출
     - `_BatchSinkContract` (5 tests) — interface / lifecycle / write return count / empty input / generator 입력
     - `_BatchRoundTripContract` (3 tests) — write → read 동등성 (정렬 무관)
  3. **`normalize_payloads`로 order-independent 비교**. payload를 `(key, repr(value))` 튜플로 정규화 후 정렬 → 두 record 리스트가 순서 무관하게 같으면 같은 결과. nested dict/list도 repr로 직렬화하므로 동작.
  4. **`InMemoryBatchSourceSink` (combined 커넥터)**. 단일 shared store 위에 read+write 모두 구현 — round-trip 테스트의 자연스러운 fixture. 실제 커넥터(예: postgres single connection)와 일치하는 패턴.
  5. **`tests/fixtures/`로 데이터 + 커넥터 분리**. `records.py`는 plain factories (pytest 비의존), `connectors.py`는 InMemory 구현체. pytest fixture 래퍼는 conftest에 둠.
  6. **`sample_records`를 `tests/conftest.py`(top-level)로 승격**. 전 디렉토리에서 사용 가능. 기존 `tests/unit/core/conftest.py`의 동일 fixture는 제거 (자동 상속).
- **Consequences**:
  - (+) 신규 커넥터를 추가할 때 contract 통과 = 한 줄짜리 subclass + fixture 두 개로 끝. 진입 비용 매우 낮음.
  - (+) `tests/contracts/test_inmem.py`가 contract suite 자체의 검증 역할 — 깨지면 즉시 발견.
  - (+) 표준 데이터셋(`sample_records`, `large_records`, `mixed_types_records`)으로 커넥터 간 동작 일관성 비교 가능.
  - (−) 새 커넥터가 mixed_types_records의 모든 타입을 지원해야 한다는 의무는 없음 — 각 커넥터가 어떤 contract를 통과해야 할지는 그 커넥터의 책임 (Step 2 PR 단위 결정).
  - (−) Stream contract (`_StreamSourceContract` 등)은 아직 없음 — Step 2.3 (Kafka) 도입 시 추가 예정.
- **References**: SPEC.md §9.5 · `tests/contracts/` · `tests/fixtures/`

---

## ADR-0008: Step 2.1 PostgreSQL 커넥터 — 드라이버 및 설계 결정

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  Step 2.1은 SPEC.md §6에 따라 PostgreSQL용 BatchSource + BatchSink 구현. 드라이버 선택(`psycopg[binary]` vs `asyncpg`), bulk insert 전략, write 모드(append/overwrite/upsert) 의미론, 신규 커넥터를 contract suite에 어떻게 끼울지가 결정 필요.
- **Decision**:
  1. **드라이버: psycopg 3 (`psycopg[binary]>=3.1`)**. 이유:
     - SPEC.md의 batch 모드는 동기 iterator → 동기 드라이버가 자연스러움.
     - psycopg 3는 binary wheel + 네이티브 COPY 지원 + named cursor (server-side) 지원.
     - 필요해지면 psycopg 자체에 async API도 있으므로 추후 stream 모드에 활용 가능.
     - asyncpg는 빠르지만 sync wrapper 없음 + COPY 인터페이스 더 번거로움.
  2. **읽기는 server-side cursor (`cur(name=...)` + `itersize=chunk_size`) 사용**. 메모리 안전 — 백만 행 SELECT도 안전. cursor 이름은 uuid4 hex로 충돌 방지.
  3. **쓰기 모드별 구현**:
     - `append` (default) — `COPY <table> FROM STDIN` (가장 빠름)
     - `overwrite` — `TRUNCATE` + COPY
     - `upsert` — `INSERT ... ON CONFLICT (<keys>) DO UPDATE SET ...`. `key_columns` 필수.
     - 모든 행을 메모리에 적재하지 않고 iterator로 스트리밍.
     - 실패 시 commit 안 하고 rollback → 부분 적재 없음.
  4. **`sql.Identifier` 기반 식별자 quoting** — SQL 인젝션 방지. `public.orders`처럼 점 구분 이름도 분리해서 quote.
  5. **`@ConnectorRegistry.register("postgres")` + `pyproject.toml` entry-point 등록 병행**. 사용자가 `from etl_plugins.connectors.rdbms.postgres import ...`로 직접 import할 수도, `ConnectorRegistry.get("postgres")`로 lazy 발견할 수도 있음.
  6. **`[project.optional-dependencies] postgres = ["psycopg[binary]>=3.1"]`** — 사용자는 `pip install etl-plugins[postgres]`로만 드라이버 설치. 코어는 가벼움 유지. dev group에도 별도 추가 — 통합 테스트 실행 위해.
  7. **`contracts/batch.py`에 `read_kwargs` / `write_kwargs` fixture 추가**. 후방 호환 (default `{}`). postgres는 `query` / `table`을 fixture로 주입.
  8. **`tests/integration/`로 testcontainers 기반 통합 테스트 격리**. 모듈 단위 `pytestmark = pytest.mark.it`. 단위 테스트에서는 자동 제외. CI는 별도 잡으로 실행.
  9. **`pg_conn_params` vs `pg_raw_kwargs` 두 fixture 분리** — 우리 커넥터는 `database=`를, 원시 psycopg는 `dbname=`를 받음. 번역 fixture로 명시적 처리.
  10. **`PostgresConnector.connection` property** — 테스트/마이그레이션에서 원시 cursor가 필요할 때 escape hatch 제공. 파이프라인 코드는 read/write만 사용 권장.
- **Consequences**:
  - (+) 29 통합 테스트 모두 통과 (~5.6s). BatchSource/Sink/RoundTrip contract + upsert/overwrite/error/schema-qualified/streaming.
  - (+) `Contract subclass + 2개 fixture` 패턴이 검증됨 — 다음 커넥터(s3, kafka)도 동일 방식으로 추가 가능.
  - (+) optional-dependencies 덕분에 etl-plugins 코어 설치 시 psycopg 안 끌고 옴.
  - (−) asyncpg 사용자에게는 별도 어댑터가 필요할 수 있음 (현재 미지원).
  - (−) `_extra` libpq 옵션은 검증 없이 그대로 전달됨 — 오타 시 psycopg가 런타임에 에러. SPEC compliant.
- **References**: SPEC.md §6, §9.5 · `etl_plugins/connectors/rdbms/postgres.py` · `tests/integration/test_postgres.py`

---

## ADR-0009: Step 2.2 S3 (object storage) 커넥터 — 설계 결정

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  Step 2.2는 SPEC.md §6 따라 S3 (object storage) BatchSource + BatchSink. 핵심 결정: 드라이버, 포맷, S3-compatible 백엔드 통합 방식, write 모드의 의미론(S3는 row 정체성 없음), 통합 테스트 백엔드 (MinIO).
- **Decision**:
  1. **드라이버: boto3 (`boto3>=1.34`)**. 가장 널리 쓰이는 AWS SDK. 모든 S3-compatible 서비스(MinIO, R2, DO Spaces, ...)와 호환. async 필요해지면 `aioboto3` 어댑터 별도 추가 가능.
  2. **포맷: jsonl / csv / parquet 모두 지원**. SPEC.md §6 명시. 모듈 단위 `SUPPORTED_FORMATS` 상수 + `detect_format(key)` (확장자 매핑). 키 확장자에서 자동 감지 + `format=...` 명시적 override 모두 지원.
  3. **포맷별 직렬화/역직렬화는 순수 함수로 분리** (`_serialize_*`, `_parse_*`). boto3 client 없이 단위 테스트 가능. 21 unit tests (`tests/unit/connectors/object_storage/test_s3_helpers.py`).
  4. **`query`를 prefix로 재해석**. BatchSource ABC의 `query` 파라미터는 RDBMS에는 SQL, 객체 스토리지에는 키 prefix로 자연스럽게 매핑. 동일 ABC 시그니처 유지.
  5. **`mode` 의미론**: `append`(default), `overwrite` 두 가지만 허용. `upsert`는 S3에 row 정체성이 없으므로 명시적으로 거부 (WriteError). PUT은 본질적으로 idempotent overwrite이지만 사용자 의도를 분명히 하기 위해 mode 인자 유지.
  6. **한 번의 write() = 한 개의 object PUT**. 파티셔닝 / 멀티 파일 분할은 호출자가 여러 번 write 호출로 처리 — 단순한 책임 분리. multipart upload는 큰 파일이 필요해지면 Step 6에서 검토.
  7. **`endpoint_url` 파라미터로 S3-compatible 서비스 통합**. MinIO/R2/DO Spaces 모두 동일 코드. AWS S3 사용 시 None.
  8. **`pyarrow>=16.0`을 `[s3]` extra의 일부로**. parquet 지원에 필수. 가벼움을 원하면 jsonl/csv만 쓰면 됨 (pyarrow import 시점에만 발생).
  9. **통합 테스트: MinIO via `testcontainers[minio]`**. 진짜 S3 API와 호환되므로 AWS 비용 없이 모든 동작 검증 가능. 29 postgres tests에 더해 35 S3 integration tests (총 64 it).
  10. **MinIO + 최신 boto3 호환성 패치**: boto3 1.36+가 batch `DeleteObjects`의 Content-MD5를 기본 생략 → MinIO가 거부. fixture cleanup을 batch delete 대신 per-object `delete_object` 루프로 우회. 우리 커넥터의 PUT 자체는 영향 없음.
  11. **`@pytest.mark.it` 모듈 단위 마커 + testcontainers fixture chain** 기존 postgres 패턴과 동일 — 일관성.
- **Consequences**:
  - (+) Contract suite + 2개 fixture로 모든 표준 케이스 자동 통과. postgres + s3 두 커넥터가 동일 패턴 → s3, GCS, Azure Blob 등 후속 object storage 추가 시 모델로 활용.
  - (+) 같은 코드가 AWS S3 / MinIO / R2 등에 그대로 작동 (검증된 부분: MinIO).
  - (+) CSV/Parquet/JSONL 세 포맷 모두 round-trip 검증됨. parquet은 타입 보존 (int/bool/null), CSV는 string으로 변환 (예상 동작, 문서화됨).
  - (−) parquet 지원으로 pyarrow가 `[s3]` extra에 포함 — 약 30 MiB. jsonl/csv만 쓰는 사용자는 약간의 오버헤드.
  - (−) `mode="upsert"`를 거부함 — 사용자가 row-level 업데이트가 필요하면 별도 처리 필요 (e.g., 임시 영역에 partial 쓰고 sweep). 의도된 제한.
  - (−) parquet 읽기는 전체 객체를 메모리에 적재 (pyarrow가 random access 요구). 매우 큰 parquet은 row group 단위 처리가 필요 → 필요해지면 Step 6.
- **References**: SPEC.md §6, §9.4 · `etl_plugins/connectors/object_storage/s3.py` · `tests/integration/test_s3.py` · `tests/unit/connectors/object_storage/test_s3_helpers.py`

---

## ADR-0010: Step 2.3 Kafka 커넥터 — 드라이버 + Stream Contract 도입

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  Step 2.3은 SPEC.md §4.1의 StreamSource/StreamSink 첫 구현. 결정: 드라이버(`confluent-kafka` sync vs `aiokafka` async), Stream Contract 도입 여부, 비동기 lifecycle(connector ABC는 sync `connect/close`만 정의), at-least-once vs at-most-once 디폴트.
- **Decision**:
  1. **드라이버: aiokafka (`aiokafka>=0.11`)**. 이유:
     - SPEC.md §4.1의 `subscribe(...) -> AsyncIterator[Record]`와 native async 매칭.
     - asyncio 통합이 자연스러움 — `asyncio.to_thread` 같은 우회 없이 진짜 비동기 IO.
     - confluent-kafka는 성능 우위지만 sync → AsyncIterator 어댑팅이 번거롭다. 성능이 병목이 되면 Step 5+에서 confluent-kafka 어댑터 추가 검토.
  2. **Sync `connect/close`는 flag-only**. 실제 클라이언트(`AIOKafkaConsumer/Producer`)는 lazy: 첫 `subscribe()` / `publish()` 호출 때 시작. 이유: Connector ABC가 `def connect() -> None`을 강제(sync) → 비동기 시작을 sync 안에 넣으면 `asyncio.run` 호출이 필요하고 이벤트 루프 안에서 호출되면 깨짐.
  3. **`aclose()` 명시적 async 메서드**. 비동기 자원 정리(producer.stop) 필요한 호출자가 명시적으로 호출. async generator인 `subscribe()`는 `finally` 절에서 consumer.stop 자동 호출 — break/cancel 모두 안전.
  4. **`subscribe()`는 async generator function** (`async def` + `yield`). 호출 시점에는 await 불필요 — 그냥 `subscribe(topic)`이 `AsyncGenerator`를 반환. ABC 시그니처 `def subscribe(...) -> AsyncIterator[Record]`와 호환.
  5. **Stream Contract 도입** (`tests/contracts/stream.py`). 패턴은 batch와 동일(subclass-able `_`-prefix mixin). 차이점:
     - 모든 test 메서드가 `async def` (pytest-asyncio auto mode가 자동 처리).
     - `asyncio.wait_for(..., timeout=consume_timeout)`로 무한 블로킹 방지 (default 30s).
     - `_StreamRoundTripContract`는 source/sink 동일 인스턴스(`source is sink`) 허용 — 중간 close 없이 한 번에 정리.
  6. **at-least-once 디폴트**: `auto_offset_reset="earliest"` + `enable_auto_commit=False`. 명시적 offset commit은 Step 3 Pipeline runtime에서. 지금은 `commit()`이 `NotImplementedError` 발생.
  7. **메시지 직렬화: JSON UTF-8** (`json.dumps(default=str)`). 비-JSON-serializable 값은 repr fallback. Avro/Protobuf은 Step 5에서 Schema Registry와 함께.
  8. **메타데이터에 Kafka 위치 정보 포함**: `topic`/`partition`/`offset`/`key`/`timestamp`. SPEC.md §4.2 ("커넥터별 raw 객체나 위치 정보는 metadata에 보관") 준수.
  9. **`bootstrap_servers`는 str 또는 list 허용**. 리스트면 자동으로 콤마-조인. SPEC.md §5.3의 YAML 리스트 표기와 일관.
  10. **테스트: testcontainers KafkaContainer (KRaft mode)**. Zookeeper 없이 단일 컨테이너. `auto.create.topics.enable=true`로 토픽 자동 생성 — 테스트별 unique 이름.
- **Consequences**:
  - (+) 16 통합 테스트 (Stream Contract 5 + Kafka-specific 11) 모두 통과 (~11s).
  - (+) Stream Contract 패턴 확립 — 향후 Kinesis/Pulsar/RabbitMQ 추가가 같은 방식.
  - (+) Pipeline runtime이 sync인 batch와 async인 stream을 다르게 다룰 수 있는 인터페이스 정리됨.
  - (−) Sync `connect/close`가 flag-only라 다른 batch 커넥터와 의미가 다름 — 이중 정신모델 발생. 문서로 보완.
  - (−) `commit()`은 아직 NotImplementedError — Step 3 retry+observability 통합에서 구현.
  - (−) Schema Registry / Avro / Protobuf은 Step 5로 미룸.
- **References**: SPEC.md §4.1, §4.2, §5.3, §5.5 · `etl_plugins/connectors/stream/kafka.py` · `tests/contracts/stream.py` · `tests/integration/test_kafka.py`

---

## ADR-0011: Step 3.1 Pipeline runtime — YAML 빌더 + Transforms + `etlx` CLI

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  Step 3.1은 SPEC.md §10 Step 3의 첫 슬라이스다. 핵심 결정: 빌더가 어디까지 책임지는가(연결 lifecycle / 변환 로직 / CLI), transform 어떻게 등록되고 dispatch되는가, filter expression의 보안 모델, CLI 프레임워크.
- **Decision**:
  1. **Runtime layer를 별도 패키지로 분리** (`etl_plugins/runtime/`). `core/`는 순수 추상 + Pipeline 모델, `runtime/`은 YAML 파싱 + 변환 dispatch + 라이프사이클. 책임 분리 → core가 runtime을 import하지 않으므로 외부 작성자가 같은 Pipeline ABC를 다른 runtime으로 활용 가능.
  2. **`build_pipeline_from_yaml(...)` 한 함수가 composition root**. `(Pipeline, dict[str, Connector])` 튜플 반환. caller가 open/close 관리하거나 `run_pipeline_yaml(...)` 헬퍼 호출. 이유: 테스트에서 `extra_connectors=`로 InMemory mock 주입이 자연스럽고, orchestrator adapter(Airflow/Dagster/Prefect)가 자기네 라이프사이클로 감싸기 쉬움.
  3. **Transform 디스패치는 별도 registry** (`runtime/transforms.py:_REGISTRY`, decorator `@register_transform("name")`). 이유: Connector registry와 의미·수명이 달라(transform은 stateless function builder, Connector는 stateful instance) — 한 레지스트리에 섞으면 혼란. 외부 패키지가 자체 transform 추가 가능 (`pyproject.toml` entry-point는 Step 5에서 필요해질 때 추가).
  4. **빌트인 4종**: `rename`, `cast`, `filter`, `python`. SPEC.md §5.4의 예시와 정확히 일치. 추가는 사용자/외부가 register_transform으로.
  5. **`cast` 타입 시스템은 보수적**: int/int64/float/float64/str/string/bool/timestamp 만. timestamp는 `datetime.fromisoformat`로 ISO 8601만 받음 (다양한 포맷 파서는 사용자가 `python` transform으로). `None`은 패스스루, 누락 컬럼은 스킵 → 부분 스키마 안전.
  6. **`filter` expression은 sandboxed eval**: `eval(code, {"__builtins__": {}}, {"data": ..., "metadata": ...})`. 컴파일은 빌드 타임 1회 (`compile(..., "eval")`). 빌트인 차단 → `open`/`__import__` 등 사용 불가. 완전한 샌드박싱은 아니나(`().__class__` 같은 escape 기법은 막지 못함), 의도된 위협 모델은 "실수로 잘못 쓴 YAML이 OS 명령을 실행하는 사고"를 막는 것 — 신뢰할 수 없는 YAML을 실행하려면 별도 정책 필요. 진짜 임의 코드가 필요한 use case는 `python` transform으로 갈 것을 권장.
  7. **`python` transform은 `module:function` 문자열로 import** — Airflow Operator import_string 같은 관습. import 실패는 ConfigError(빌드 시), 런타임 예외는 TransformError로 래핑.
  8. **Transform이 `None` 반환 시 record drop** — Pipeline.run에서 이미 그렇게 처리되므로 filter/python 모두 자연스럽게 작동.
  9. **CLI 프레임워크: Typer (`typer>=0.12`)**. argparse 대비 타입 어노테이션 기반 API가 깔끔하고, `Annotated[Path, typer.Argument(...)]` 패턴이 mypy strict와 호환. Click 기반이라 풀스택 검증 충분.
  10. **CLI 서브커맨드 (Step 3.1 MVP)**: `version`, `list-connectors`, `validate`, `run`, `test-connection`. `run-stream`은 Step 3.2에서, `schema`는 Step 5+에서. `validate`는 YAML 파싱 + 연결 구성자 인자 검증까지만 — 실제 connect는 `test-connection`에서.
  11. **`run` 명령은 자동으로 connectors.connect() → pipeline.run() → close()**. close()는 `contextlib.suppress(Exception)`로 감싸서 cleanup 실패가 결과 전파 막지 않게. 모든 ETLError는 빨간 에러 메시지로 exit code 1, 예상치 못한 예외는 클래스 이름 포함.
  12. **`test-connection`은 `--all`로 일괄 점검 가능**. 실패 개수만큼 exit code 결정. 단순.
  13. **`pyproject.toml`에 `etlx = "etl_plugins.cli:app"` entry-point**. `uv pip install -e .` 후 `etlx version`이 동작. Typer `app`은 callable이라 그대로 entry-point로 사용 가능. 테스트는 `typer.testing.CliRunner`로 in-process 호출 — 빠르고 sys.exit 없음.
  14. **Test fixture에서 InMemory 커넥터를 stable name으로 등록 + 복원** (`ConnectorRegistry.register("cli-inmem-source", replace=True)(InMemoryBatchSource)`). `replace=True`로 idempotent. yield 후 원상복구. 다른 테스트 파일과 충돌 회피.
- **Consequences**:
  - (+) CLI smoke test 7개 + builder 8개 + transforms 23개 = 38 unit tests 추가 (총 252). 외부 Docker 없이 YAML → Pipeline 라이프사이클 전체 검증됨.
  - (+) Orchestrator adapter는 이제 단 3줄로 작성 가능: `pipeline, conns = build_pipeline_from_yaml(...); pipeline.run(connectors=conns)`. Step 4 작업 부담 대폭 감소.
  - (+) `etlx validate <yaml>`이 CI에서 모든 파이프라인 YAML 검증 가능 — 수정 즉시 피드백.
  - (+) 외부 패키지가 자체 transform 추가는 한 줄: `@register_transform("my-transform")`.
  - (−) Stream mode는 아직 미구현 (Pipeline.run이 batch만). `mode: stream` YAML은 빌드는 되지만 `run`은 NotImplementedError. **Step 3.2**.
  - (−) Retry/DLQ/Checkpoint 미구현. `RetryConfig`는 빌드되지만 무시됨. **Step 3.3**.
  - (−) Filter sandbox는 완벽하지 않음 — `python` transform이 더 명시적이므로 신뢰 안 되는 입력은 그쪽으로 안내. 문서로 보완.
  - (−) CLI logging은 기본 stderr만 — JSON 구조화 로그 통합은 `--log-format` 옵션 형태로 Step 3.3에서.
- **References**: SPEC.md §5.4, §5.5, §9.6 · `etl_plugins/runtime/{transforms,builder,runner}.py` · `etl_plugins/cli.py` · `tests/unit/runtime/` · `tests/unit/test_cli.py`

---

## ADR-0012: Step 3.2 Stream runtime — `Pipeline.arun_stream`, async commit, buffering

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  Step 3.2는 SPEC.md §4.4/§5.5의 streaming pipeline 실현. 결정: sync `Pipeline.run`과 async stream 실행을 어떻게 공존시킬지, `StreamSource.commit` 시그니처(sync vs async), buffer/commit 정책을 어디에 두는지, integration test가 진짜 Kafka에서 commit 동작을 검증하는지.
- **Decision**:
  1. **`Pipeline.run`(sync) + `Pipeline.arun_stream`(async) 분리**. 단일 메서드에 mode 분기 후 `asyncio.run()` 내부 호출하는 패턴은 이벤트 루프 안에서 깨지므로 거부. caller(Airflow/Dagster/CLI)가 자기 컨텍스트에 맞춰 둘 중 하나 호출. `Pipeline.run`이 mode='stream'이면 `arun_stream`을 안내하는 `PipelineError`만 발생.
  2. **`StreamSource.commit`을 async로 변경** (이전 ADR-0010에서 sync로 정의). 이유: `subscribe`/`publish`가 모두 async인데 commit만 sync면 Kafka처럼 진짜 비동기 client에서 `asyncio.run()` wrapping이 필요 → 깨지기 쉽다. 또한 `offsets` 파라미터를 옵셔널(`None`은 "현재 위치 commit")로 기본값 부여 → 일반적 사용은 `await source.commit()`. ABC 변경은 SPEC.md §4.1 시그니처 한 줄 보정과 함께(ADR가 기록).
  3. **`KafkaConnector._consumer` 참조 보존**. `subscribe()` 진입 시 `self._consumer = consumer`, 종료 또는 `aclose()`에서 정리. `commit()`은 이 ref가 None이면 `ConnectError` ("active subscribe 없음"). 단일 subscribe 가정 — 동시 다중 subscribe는 Step 5의 multi-task pipeline에서 필요해지면 dict화.
  4. **`Pipeline.commit_strategy: str = "after_sink_flush"`** 필드를 추가. PipelineConfig.commit.strategy → builder.py가 채움. stream task의 buffer flush 후 `commit_strategy == "after_sink_flush"`일 때만 `await source.commit()` 호출. 그 외 값(예: "manual")은 commit skip — 미래의 다른 전략(예: "at_least_once_per_batch")과 호환되는 확장 지점.
  5. **Buffer 정책은 `sink_options["buffer"]` dict**: `{max_records: int, max_seconds: float}`. SinkConfig가 `extra=allow`이므로 추가 모델 정의 없이도 YAML에서 사용 가능. `max_records=1` (기본)이면 record-당 즉시 flush+commit. `max_seconds=0` (기본)이면 시간 기반 flush 비활성.
  6. **`stop_after_records` / `stop_after_seconds` 명시적 stop 조건**. SIGINT 외에 "테스트에서 확정적으로 종료"가 필요 → CLI(`--stop-after-*`) + 테스트 호출 모두에서 사용. 실 운영에서는 둘 다 `None`(무한 실행). 인터럽트는 별도 KeyboardInterrupt 분기로 CLI에서 처리(exit 130).
  7. **`finally` 블록에서 잔여 pending flush + commit**. stop 조건으로 break하면 `pending > 0`이 남을 수 있음 — at-least-once 보장을 위해 마지막 flush 보장. `contextlib.suppress(Exception)`으로 cleanup 안전.
  8. **`commit()`의 `NotImplementedError`는 `contextlib.suppress`로 흡수**. 다른 StreamSource 구현체가 commit을 지원하지 않을 때도 pipeline은 동작. Kafka는 실제로 raise하지 않음 (ADR-0010의 NIE 약속을 이번 ADR이 superseded).
  9. **`arun_stream_pipeline_yaml` async 헬퍼**: sync `run_pipeline_yaml`의 mirror. cleanup이 `aclose()`(async, Kafka가 제공) → `close()`(sync) fallback 순서.
  10. **`etlx run-stream` CLI 서브커맨드**: `asyncio.run(arun_stream_pipeline_yaml(...))`. KeyboardInterrupt를 catch → exit 130 + "interrupted" 메시지. ETLError는 빨간 에러로 exit 1.
  11. **`InMemoryStreamSource/Sink` 추가** (`tests/fixtures/connectors.py`). InMemory의 `commit()`은 `self.commits.append(offsets)` — 호출 횟수/내용 검증에 사용. 17 신규 unit tests (16 stream pipeline + 1 CLI smoke).
  12. **Kafka commit 통합 검증**: `test_stream_pipeline_commits_offsets`가 (a) pipeline run, (b) 동일 group_id로 새 consumer attach, (c) 메시지 없음(즉 offset 모두 commit됨)을 확인. 진짜 Kafka에서 at-least-once + commit이 작동하는지 직접 검증.
- **Consequences**:
  - (+) YAML 한 줄(`mode: stream` + `commit.strategy`)로 stream pipeline 가능. CLI `etlx run-stream <yaml> --stop-after-records 1000`로 dry-run 친화.
  - (+) Kafka commit이 실제로 작동(integration test) — at-least-once delivery 보장 첫 마일스톤.
  - (+) 17 new unit tests (총 269 unit), 2 new integration tests (총 82 it). All green incl. mypy strict + ruff.
  - (+) `Pipeline.arun_stream`은 transform/filter/buffer/commit/stop을 한 함수에서 처리 — 다음 슬라이스(Retry/DLQ)는 명확한 hook 지점을 가진다.
  - (−) SPEC.md §4.1의 `def commit(...) -> None` 시그니처 보정 필요. SPEC 업데이트는 별도 small PR로.
  - (−) ADR-0010이 commit을 "Step 3로 이동"이라고 했으나 그땐 sync로 가정. 이번 ADR가 그 결정 보정.
  - (−) 단일 consumer 가정 — 다중 task / 다중 partition 병렬 subscribe는 Step 5+에서. 현재 stream task는 한 source/sink/topic이 표준 use case.
  - (−) Retry/DLQ/Checkpoint는 여전히 미구현. Step 3.3에서.
- **References**: SPEC.md §4.1, §4.4, §5.5 · `etl_plugins/core/{pipeline,connector}.py` · `etl_plugins/connectors/stream/kafka.py` · `etl_plugins/runtime/runner.py` · `etl_plugins/cli.py` · `tests/unit/runtime/test_pipeline_stream.py` · `tests/integration/test_stream_pipeline.py`

---

## ADR-0013: Step 3.3 Retry / DLQ / 자동 메트릭 / CLI logging

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  Step 3.3은 SPEC.md §9.1/§9.2의 신뢰성·관찰성을 Pipeline에 통합. 결정: retry는 어느 granularity에 적용하는가(task vs record), DLQ는 어떻게 라우팅(transform 실패만 vs sink 실패도), 자동 metrics 호출 시점, CLI logging은 global option vs 서브커맨드별.
- **Decision**:
  1. **Retry 적용 단위 — batch는 task-level, stream은 publish-level**.
     - Batch: `Pipeline.run`이 `_run_task`를 `retryable(...)`로 감싸 호출. 실패 시 task 전체 재실행(source 재read + sink 재write). idempotency는 caller 책임 — `mode=upsert` 또는 멱등 source 권장.
     - Stream: `_arun_stream_task` 안에서 `sink.publish`만 retry 래핑. 무한 스트림에서 task 전체 재시도는 의미 없음. transient publish 실패 흡수.
     - 동일 `RetryConfig` (max_attempts/backoff/initial_delay/max_delay) 사용. `on`은 기본 `Exception` (모든 예외 retry).
  2. **DLQ는 transform 실패 라우팅 전용** (sink 실패는 retry 또는 propagate). 이유: sink 실패는 batch 전체 영향 → DLQ가 무한 루프 위험. transform 실패는 per-record 격리 가능. SPEC.md §9.1의 "변환·sink 실패 레코드 별도 sink 라우팅" 중 변환만 우선.
  3. **DlqConfig를 `PipelineConfig.dlq`로 추가**: `{connection, table?, topic?, mode='append'}`. table은 BatchSink DLQ용, topic은 StreamSink DLQ용 — builder가 connection을 connectors dict에서 찾음, missing이면 ConfigError. Pipeline에는 `dlq: DlqConfig | None` 필드만 — 실제 sink 조회는 run 시점에 connectors dict에서.
  4. **`_dlq_route_batch` / `_dlq_route_stream`은 best-effort + `contextlib.suppress(Exception)`**. DLQ 자체가 실패해도 main run은 계속. 메트릭(`ERRORS_TOTAL{routed=dlq}`)으로 가시성 확보.
  5. **자동 메트릭 emit — Pipeline.run / arun_stream 둘 다**. 표준 메트릭 이름(observability.metrics 상수) 그대로 사용:
     - `RECORDS_READ_TOTAL` / `RECORDS_WRITTEN_TOTAL`: 각 task 끝에서 누적 add. attributes={pipeline, mode}.
     - `ERRORS_TOTAL`: 예외 캐치 시 phase 속성으로(run/transform). DLQ 라우팅 시 `routed=dlq` 추가.
     - `DURATION_SECONDS`: finally에서 histogram record.
     - 기본 backend는 NoOp이므로 cost는 zero. OTel/Prom backend 연결 시 그대로 작동.
  6. **CLI `--log-format` / `--log-level`은 Typer global callback**으로 도입. 모든 서브커맨드 전에 `configure_logging(level=..., json=...)` 호출. `json` (default) | `console`. 잘못된 값은 exit 2 + 빨간 에러. structlog `configure_logging`가 idempotent라 안전.
  7. **Pipeline 데이터클래스에 `retry: RetryConfig | None` + `dlq: DlqConfig | None` 필드 추가**. builder가 `PipelineConfig.retry` / `.dlq`를 그대로 복사 — config models를 core가 import. 이전엔 core가 config를 모르고 살았으나, retry/dlq 모델은 datatype-only로 사용하므로 순환 import 없음 (config는 core를 import하지 않음).
  8. **Cursor / Checkpoint는 Step 3.3 범위에서 제외**, Step 6 강화로 이동. 이유: 파일 기반 state 관리, ETag/lsn/sequence 등 source-specific 추적, schedule 통합(다음 run의 `last_run_at` 주입) 모두 별도 슬라이스. 현재 `Pipeline.on("post_run", ...)` 훅으로 사용자가 커스텀 cursor 저장 가능.
- **Consequences**:
  - (+) Production 신뢰성 첫 마일스톤: transient 실패는 retry, bad 레코드는 DLQ, observability 자동. 13 신규 unit tests 검증.
  - (+) DLQ가 별도 sink → BatchSink든 StreamSink든 사용자가 선택(parquet to S3 / Kafka topic / Postgres table 등) — 기존 connector 재사용.
  - (+) CLI `--log-format console`로 로컬 디버깅이 편해짐, prod는 `json` 기본.
  - (+) 표준 메트릭 자동 emit → OTel/Prometheus backend 연결만 하면 즉시 dashboard 가능. Step 6 강화의 단축경로.
  - (−) Batch task 단위 retry는 부분 진행 후 재시도 시 idempotency 보장 필요 — sink가 upsert 또는 source가 cursor 기반이 아니면 중복 가능. 문서로 보완.
  - (−) Sink 실패 DLQ는 미구현 — 큰 batch가 무한 retry로 멈출 위험은 retry max_attempts로만 제어. Step 6에서 sink chunk-level DLQ 검토.
  - (−) Stream retry가 publish만 감쌈 — transform 실패는 즉시 DLQ로 가거나 raise. transform retry가 필요한 use case는 사용자가 transform 함수 내부에서 처리.
  - (−) Cursor/Checkpoint 부재 → 매 run이 전체 read 또는 사용자 hook 의존. Step 6에서 cursor abstraction (file/dict state + before/after_run hook 표준화).
- **References**: SPEC.md §9.1, §9.2, §9.6 · `etl_plugins/core/pipeline.py` · `etl_plugins/config/models.py` (DlqConfig) · `etl_plugins/cli.py` (global callback) · `tests/unit/runtime/test_resilience.py`

---

## ADR-0014: Step 4 Orchestrator Adapters — lazy-loaded thin wrappers

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  Step 4는 SPEC.md §7의 Airflow / Dagster / Prefect 통합. 세 orchestrator는 거대한 dep tree(apache-airflow 단독 ~250MB)를 가져 etl-plugins 본체에 강제 install하면 부담. 그러나 단순히 `try: import` 후 None 처리하면 사용자가 `from etl_plugins.adapters.airflow import ETLPluginsOperator`가 안 됨. 결정: 모듈 import는 항상 성공, 심볼 접근 시 lazy import.
- **Decision**:
  1. **각 orchestrator마다 별도 모듈**: `etl_plugins/adapters/{airflow,dagster,prefect}.py`. 한 줄짜리 README + 사용 예시 docstring 포함.
  2. **PEP 562 `__getattr__` 기반 lazy public symbol exposure**.
     - 모듈 자체는 orchestrator dep 없이도 import 성공
     - `from etl_plugins.adapters.airflow import ETLPluginsOperator` → `__getattr__("ETLPluginsOperator")` 실행 → 그 안에서 `from airflow.models import BaseOperator` 호출 → 미설치면 helpful `ImportError("... pip install 'etl-plugins[airflow]'")` 발생.
     - 한 번 빌드되면 `globals()[name] = cls`로 캐시.
  3. **Thin wrappers — 모두 `run_pipeline_yaml`에 위임**:
     - Airflow: `ETLPluginsOperator(BaseOperator)`. `execute()` → `run_pipeline_yaml(...)` → XCom-friendly dict 반환.
     - Dagster: `EtlPluginsResource(ConfigurableResource)` + `etl_plugins_op` (`required_resource_keys={"etl_plugins"}`). resource.run() → `run_pipeline_yaml(...)`.
     - Prefect: `@flow` + `@task` 페어. flow가 task 호출하는 표준 패턴. 두 심볼 모두 lazy.
  4. **`__all__`는 노출하지만 ruff F822 per-file ignore**. ruff는 `__getattr__`로 만든 심볼을 보지 못함 — false positive를 pyproject.toml의 `per-file-ignores`로 명시적 silence.
  5. **mypy override**: orchestrator 라이브러리는 `ignore_missing_imports = true`. adapter 모듈은 `disallow_untyped_decorators = false` + `warn_unused_ignores = false` — Airflow BaseOperator / Dagster @op / Prefect @flow는 strict 호환이 어렵다. 우리는 delegation 컨트랙트를 단위 테스트로 검증.
  6. **테스트: 두 단계**.
     - (a) **Structural** (orchestrator 미설치 상태에서도 실행): 모듈 import 성공, missing dep 시 helpful ImportError, unknown attr 시 정상 AttributeError. 5 tests, 항상 통과.
     - (b) **Real orchestrator** (`pytest.importorskip`로 자동 skip): operator 인스턴스화 + `execute()` 호출이 `run_pipeline_yaml`에 정확한 인자로 위임하는지 monkeypatch로 검증. 3 tests, 로컬에선 skip, CI가 extra 설치 시 활성.
  7. **Stream mode는 Airflow에선 일반적으로 안 맞음** (operator는 batch). `arun_stream_pipeline_yaml` adapter는 도입하지 않음 — Prefect는 async flow 지원하므로 미래 별 슬라이스에서 가능.
  8. **Optional-dependencies 추가**: `airflow = ["apache-airflow>=2.10"]`, `dagster = ["dagster>=1.9"]`, `prefect = ["prefect>=3.0"]`. dev 그룹에는 추가하지 않음 — 로컬 dev에서 install하고 싶은 사람만 install.
- **Consequences**:
  - (+) etl-plugins 본체 install이 가벼움(orchestrator 0). 사용자는 자기 환경에 맞는 extra만.
  - (+) 사용자 코드는 자연스러운 `from etl_plugins.adapters.X import Y` 그대로.
  - (+) Structural 테스트가 helpful ImportError 메시지를 검증 — 실수로 dep 없는 모듈을 쓰는 사용자에게 즉시 안내.
  - (+) 7 신규 structural unit tests + 3 conditional tests (real orchestrator 설치 시) → 289 unit + 3 skip + 82 it = 374.
  - (−) `__getattr__` 패턴은 IDE 자동완성에서 안 보일 수 있음(IDE가 PEP 562 지원 안 하면). 사용자가 명시적 import는 잘 작동하지만 dir() 결과에 안 보임. 문서에 사용 예시 명시.
  - (−) CI 미실행 — 3 conditional 테스트는 orchestrator extra 설치된 환경에서만. GitHub Actions에 별도 matrix job 추가 권장(Step 6 강화에서).
  - (−) Stream을 Airflow operator로 쓰려면 사용자가 직접 `arun_stream_pipeline_yaml`을 호출하는 PythonOperator로 우회.
- **References**: SPEC.md §7 · `etl_plugins/adapters/` · `tests/unit/adapters/test_lazy_imports.py` · `pyproject.toml` [project.optional-dependencies]

---

## ADR-0015: Step 5.1 MySQL 커넥터 — PyMySQL + executemany 기반

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  Step 5는 SPEC.md §10 Step 5의 커넥터 확장. 첫 번째 슬라이스인 5.1은 RDBMS 카테고리에 MySQL 추가. 결정: 드라이버 선택, write 전략(LOAD DATA vs executemany), boolean 처리.
- **Decision**:
  1. **드라이버: PyMySQL (`pymysql>=1.1`)**. 순수 Python — 시스템 deps 불필요. mysqlclient(C)보다 느리지만 install 간편하고 testcontainers / CI 환경에서 안정적. 성능이 병목되면 mysqlclient adapter는 별도 슬라이스.
  2. **Read: server-side `SSDictCursor` + `fetchmany(chunk_size)` 루프**. 대용량 결과에서도 메모리 bounded. 결과는 dict로 즉시 받아 `Record(data=dict(row))` 변환.
  3. **Write 전략 — executemany 기반 multi-row INSERT** (default `batch_size=1000`).
     - `append`: `INSERT INTO ... VALUES ...` batched
     - `overwrite`: `TRUNCATE TABLE` + 동일 INSERT
     - `upsert`: `INSERT ... ON DUPLICATE KEY UPDATE col = VALUES(col), ...` — MySQL native upsert. `key_columns`는 사용자 명시 + 테이블에 매칭되는 UNIQUE/PK 키가 있어야 동작(MySQL 의미론).
     - LOAD DATA LOCAL INFILE은 더 빠르지만 (a) 서버에 `local_infile=1` 옵션 필요, (b) CSV 직렬화 필요. **추후 최적화로** — `local_infile=True`를 connector kwargs에 통과 가능하게 두었으나 자동 사용은 안 함.
  4. **Identifier quoting은 backtick (`` ` ``)**. PostgreSQL의 `sql.Identifier`처럼 라이브러리 헬퍼는 없으므로 `_ident(name)` 헬퍼가 backtick으로 감싸고 내부 backtick은 `` `` ``로 escape — SQL 인젝션 방지. `db.table` 같은 schema-qualified 이름도 `_table_ident`이 처리.
  5. **Boolean 의미론 mismatch — MySQL은 TINYINT(1)으로 저장**. 그래서 contract test에서 read-back되는 `active`는 0/1 int. 두 가지 선택:
     - (a) connector가 boolean 추측해서 변환 — 위험(어떤 컬럼이 bool인지 알 수 없음).
     - (b) 사용자/테스트가 mismatch 인지하고 처리. **(b) 채택**. 통합 테스트는 `_mysqlify(sample_records)` 헬퍼로 `active: bool → int`로 변환 후 contract 호출 — 명시적 trade-off.
  6. **`@ConnectorRegistry.register("mysql")` + entry-point** — postgres와 동일 패턴. `[mysql]` extra = `pymysql>=1.1`.
  7. **통합 테스트: testcontainers `MySqlContainer("mysql:8.0")` (session-scoped)**. postgres와 동일 구조 — `mysql_container` / `mysql_conn_params` / `mysql_table` / `mysql_seeded` / `mysql_connector` 5 fixtures. contract subclasses (Source 7 + Sink 5 + RoundTrip 3) + MySQL-specific 14 = **29 통합 테스트**.
- **Consequences**:
  - (+) RDBMS 슬롯에 두 번째 커넥터 — Contract 패턴이 다른 DB에서도 작동함을 증명(앞으로 MSSQL/Oracle/SQLite 추가가 모델로 활용).
  - (+) 두 connector(postgres / mysql)는 정확히 같은 ABC를 구현 → orchestrator/pipeline layer는 차이 없이 다룸.
  - (+) Backtick quoting 테스트(`a``b` 컬럼명)가 통과 — SQL 인젝션 방어 검증.
  - (−) Boolean 의미론 차이는 contract test에서 수동으로 매핑 — 미래 다른 type 미스매치(DECIMAL → Python float 정확도 손실 등) 발생 시 동일 패턴 필요. 문서로 보완.
  - (−) executemany는 LOAD DATA보다 느림 — 진짜 대용량(>1M rows)이 들어오면 성능 이슈. Step 6에서 LOAD DATA 자동 전환 검토.
  - (−) MariaDB 호환성 명시적 테스트 안 함 — PyMySQL이 동일 wire protocol을 쓰므로 작동할 가능성 높으나 미검증.
- **References**: SPEC.md §6 · `etl_plugins/connectors/rdbms/mysql.py` · `tests/integration/test_mysql.py` · `tests/integration/conftest.py` (mysql fixtures)

---

## ADR-0016: Step 5.1b SQLite 커넥터 — stdlib 드라이버, Docker 없는 유닛 테스트

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  Step 5.1b는 RDBMS 카테고리의 세 번째 슬라이스. SQLite는 (a) 파이썬 stdlib `sqlite3`로 제공되어 외부 dep 0, (b) 파일 기반이라 Docker 불필요, (c) PostgreSQL과 거의 호환되는 ON CONFLICT 문법을 지원. 결정: 어디까지 stdlib을 의지할지, in-memory vs file 기본, mypy의 sqlite3.connect 시그니처 충돌.
- **Decision**:
  1. **드라이버: stdlib `sqlite3`** — 추가 install 0. `[sqlite]` extra 불필요. 모든 사용자가 즉시 활용.
  2. **기본 database=`":memory:"`** — quick start / 테스트 친화. 실 사용은 파일 경로 지정.
  3. **Read: `cursor.fetchmany(chunk_size)` 루프** + `row_factory = sqlite3.Row`로 dict-like 변환. `Record(data=dict(row))` 직접 사용.
  4. **Write 전략은 mysql과 동일** (executemany batched, 기본 `batch_size=1000`):
     - `append`: `INSERT INTO ... VALUES ...`
     - `overwrite`: `DELETE FROM <table>` (`TRUNCATE`는 SQLite 미지원) + INSERT
     - `upsert`: `INSERT ... ON CONFLICT (keys) DO UPDATE SET col = excluded.col, ...` (SQLite 3.24+ 필요 — 모든 모던 Python 인스톨에 포함)
  5. **Identifier quoting은 double-quote**(`"name"`). 내부 `"`는 `""`로 escape. PostgreSQL과 동일 SQL 표준 문법.
  6. **mypy `sqlite3.connect`가 `isolation_level: Literal[...]`을 요구** → `cast`로 우회. 우리는 사용자가 `"DEFERRED"` (기본)/ `"IMMEDIATE"` / `"EXCLUSIVE"` / `None`을 전달할 수 있게 허용 — cast 한 줄로 stdlib 시그니처 만족.
  7. **모든 테스트는 unit tests** (`tests/unit/connectors/rdbms/test_sqlite.py`). Docker 불필요. `tmp_path` fixture로 매 테스트마다 새 db 파일. Contract 3종 (Source 7 + Sink 5 + RoundTrip 3) + SQLite-specific 17 = **32 tests**.
  8. **Boolean은 INT(0/1)** — MySQL과 동일 이슈. `_sqlitify()` 헬퍼로 contract test에서 bool→int 매핑.
  9. **Entry-point 등록 (no extra)** — `etlx list-connectors`에 자동 노출.
- **Consequences**:
  - (+) RDBMS 슬롯 3개째 (postgres / mysql / sqlite). Contract suite가 매번 그대로 재사용 — 한 connector 추가에 평균 ~200 LOC + 30 tests.
  - (+) 가장 가벼운 RDBMS 옵션 — 로컬 dev / quick scripts / 테스트 fixture로 활용 가능 (예: pipeline의 dlq sink로 SQLite 파일 지정).
  - (+) Unit tests로 CI에서 빠르게 실행 (~0.3s). 통합 테스트 잡에 부담 없음.
  - (+) ON CONFLICT 구문이 PostgreSQL과 거의 동일 → SQLite 학습이 Postgres 친화.
  - (−) SQLite는 동시 쓰기 제약(파일 lock) — 멀티스레드 파이프라인에서 주의 필요. 문서로 보완.
  - (−) `TRUNCATE` 부재 → `DELETE FROM`으로 대체. 큰 테이블에서 `overwrite`가 postgres/mysql보다 느림 (1M+ rows에서 차이 체감).
  - (−) `:memory:` 기본은 connect/close 후 데이터 사라짐 — 실 use case에선 항상 파일 경로 지정 필요. docstring으로 명시.
- **References**: SPEC.md §6 · `etl_plugins/connectors/rdbms/sqlite.py` · `tests/unit/connectors/rdbms/test_sqlite.py`


---


## ADR-0017: 서비스화 전략 — 코어 라이브러리 위에 별도 패키지로 웹 UI/API 분리

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  Steps 1–4 + Step 5.1까지 완료된 시점에 "UI로 파이프라인·스케줄을 시각적으로 만들고 모니터링하는 ETL 서비스"를 얹고 싶다는 요구가 생겼다. 세 가지 선택지:
  1. **하지 않는다** — 현재처럼 CLI + YAML + git만 사용. orchestrator UI(Airflow/Dagster/Prefect)와 Grafana로 모니터링 위임.
  2. **코어에 합쳐서 만든다** — `etl_plugins`에 FastAPI 서버 + 프론트 묶어 단일 패키지.
  3. **별도 패키지로 만든다** — `etl_plugins`(라이브러리) + `services/etlx-server`(FastAPI) + `services/etlx-web`(Next.js)을 모노레포의 별도 패키지로.

  비개발자(분석가/기획자/운영팀)가 직접 파이프라인을 만들고 모니터링하길 원하고, 파이프라인이 수십 개를 넘어가면 YAML + git만으로는 가시성이 떨어진다 — 1번은 장기적으로 부적합. 2번은 코어의 "라이브러리로서의 가치"(orchestrator-agnostic, Service-layer optional)를 손상시킨다.

- **Decision**:
  1. **3번(별도 패키지) 채택.** 모노레포 구조로 다음 세 패키지를 둔다:
     - `etl_plugins/` — PyPI 배포 가능한 코어 라이브러리. 자체 pyproject.toml. Steps 1~6.
     - `services/etlx-server/` — FastAPI 백엔드. 자체 pyproject.toml. 코어를 `etl-plugins>=X.Y,<X+1`로 의존.
     - `services/etlx-web/` — Next.js + TypeScript 프론트엔드. 자체 package.json. 백엔드와 HTTP REST로만 통신.
  2. **단방향 의존 강제.** `etl_plugins/*` 어디서도 `services/*`를 import 금지. CI의 import-graph 검사가 자동 검증.
  3. **CI 분리.** `ci-core.yml` + `ci-server.yml` + `ci-web.yml`.
  4. **서비스 단계는 Steps 7~11**로 ROADMAP에 추가.
  5. **세부 기술 스택은 Step 7.0에서 별도 ADR로 확정** (ADR-0019~0023 예약).
  6. **시크릿 처리 원칙 유지·강화.** UI에서 입력받아도 metadata DB에는 ref만, 평문은 시크릿 백엔드에.
  7. **YAML과 DB 양방향 지원.** workspace 단위 SSOT 명시.
  8. **버전 호환.** 서비스는 코어 SemVer `~=X.Y`로 의존.

- **Consequences**:
  - (+) 코어의 "Orchestrator-agnostic" 정체성 보존.
  - (+) 점진적 도입 가능 — 라이브러리만 쓰다가 서비스로 옮길 수 있음.
  - (+) 기존 ADR-0001~0016 영향 없음.
  - (+) 코어 재사용 (Pipeline/Connector/Config models/SecretBackend/adapters).
  - (−) 모노레포 복잡도. uv workspace + pnpm workspace 학습.
  - (−) 첫 서비스 릴리스까지 분기 단위 작업.
  - (−) import-graph 검사 운영 비용.
  - (−) YAML/DB 동기화 충돌 가능 — workspace 단위 SSOT 정책으로 완화.
  - (−) 실행 엔진 결정 보류 (Step 9.1에서 ADR-0021).

- **References**:
  - `SPEC.md` §1, §2, §3, §8.5, §9.7, §10 (Steps 7~11)
  - `ROADMAP.md` Steps 7~11
  - `CLAUDE.md` §1, §6, §7

---

## ADR-0018: 디자인 시스템 — Arc 영감, 네이비 베이스 + 팝핑크 강조

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  Step 10(Web UI)을 시작하기 전에 디자인 방향을 확정해야 한다. ETL 도구의 기존 웹 UI(Airflow / Dagster / NiFi / Prefect)는 기능 위주로 디자인 후순위였고, 비개발자가 진입하기 어려워 보인다. 반대로 디자인이 장난스럽거나 캐주얼하면 "데이터 신뢰성"이라는 본질에 어긋난다.

  세 가지를 동시에 만족해야 한다:
  1. **신뢰감** — 운영팀이 매일 보는 콘솔. 진지함.
  2. **간결함** — 정보 밀도 높지만 한눈에 잡힘.
  3. **차별화** — 기존 데이터 도구와 시각적으로 다르고 매일 보고 싶음.

  레퍼런스 후보: Linear(엔지니어링 도구), Raycast(키보드 친화), Cron(차분함), Arc Browser(공간 메타포 + Command Palette + Spaces). 톤 결정도 필요했다: 다크/라이트 둘 다 vs 다크 우선, 강조 컬러 단일 vs 그라데이션 다용.

- **Decision**:
  1. **Arc Browser 디자인 언어 차용** (DESIGN.md §2):
     - 좌측 사이드바 중심, 상단 메뉴 최소화
     - Workspace = Arc의 Space — 좌측 4px 컬러 인디케이터로 컨텍스트 구분
     - Command Palette (Cmd+K) — 모든 액션의 진입점
     - 부드러운 translucent surface (모달·드롭다운), 단 본 캔버스는 불투명
     - 그림자 대신 surface 톤 차이로 깊이감
     - 큰 corner radius (카드 14px, 모달 20px)
     - 200ms 부드러운 마이크로 인터랙션
     - Arc의 자유로운 색 커스터마이즈(Boost), squircle, 캐주얼한 톤은 **버린다** — ETL 도구에는 과함
  2. **컬러 톤**:
     - 베이스 = 깊은 네이비 (`#0A1228`, `#0F1730`, `#15203F`로 위계)
     - 강조 = 팝한 핫핑크 단일색 (`#FF3D8B`). 그라데이션은 메인 CTA / 로딩만.
     - 의미 컬러(success/warning/error/info)는 핑크와 충돌하지 않는 차분한 톤 (mint/amber/coral/sky).
     - 다크 모드가 기본, 라이트도 지원.
  3. **타이포**: Inter Variable + Pretendard Variable (한글) + JetBrains Mono (코드). self-host. 1.25 modular scale, body 14/22.
  4. **레이아웃**: 8pt grid, 사이드바 240/64, 헤더 56, 본 컨텐츠 max 1280.
  5. **컴포넌트 베이스**: shadcn/ui — 토큰만 우리 것으로 갈아 끼움. 새 컴포넌트도 토큰만 사용.
  6. **모션**: Framer Motion. fast 120 / default 200 / slow 320ms. `prefers-reduced-motion` 존중.
  7. **단일 진실(SSOT)**: `DESIGN.md`. Figma Variables → Tailwind config로 sync. 토큰 외 임의 색·간격·폰트는 PR reject. Storybook + Chromatic/Playwright visual regression + axe-core a11y가 CI 게이트.
  8. **A11y AA 의무, 핵심 페이지 AAA 권장.** 작은 본문에 핑크 색 글씨 금지(대비 부족). 색만으로 의미 전달 금지.

- **Consequences**:
  - (+) 기존 ETL 도구(Airflow/Dagster) 대비 명확한 시각 차별화. 비개발자가 진입하기 편하면서도 데이터 도구의 진지함 유지.
  - (+) 토큰 → Tailwind → shadcn/ui 체인이 자동화돼 디자인 표류 방지.
  - (+) Storybook이 단일 진실로 살아있어 PR마다 visual diff.
  - (+) Step 10 진행 시 컴포넌트만 조립하면 됨 — 토큰을 매번 정하지 않음.
  - (+) Command Palette + 키보드 우선은 ETL 같은 반복 작업 도구에 자연스럽다.
  - (−) 디자이너 1명이 시안을 끌고 가야 일관성 유지. 토큰 변경이 잦으면 Tailwind config + Figma variables 양쪽 sync 비용 발생.
  - (−) 핑크가 강해서 워크스페이스 컬러를 핑크로 두면 강조 충돌 — default workspace는 핑크 외 색 권장.
  - (−) Arc style은 "공간 메타포"가 강해서 단일 워크스페이스 사용자에게는 과한 추상화. Step 10.2에서 single-workspace UX 별도 검증.
  - (−) Tailwind v4 가정. shadcn/ui도 v4 호환 버전 필요 (2026-05 기준 호환 가능).
  - (−) Inter/Pretendard self-host 시 폰트 파일 용량 추가(약 250KB compressed). 다만 CDN 의존 제거로 안정성 ↑.

- **References**:
  - `DESIGN.md` (전체)
  - `SPEC.md` §9.7, §10 (Step 10 적용 우선순위)
  - `ROADMAP.md` Step 10.0~10.8
  - Arc Browser 디자인 언어, Linear, Raycast, Cron, Refactoring UI

## ADR-0019: API 프레임워크 = FastAPI

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  `services/etlx-server`(Step 7~8)는 다음 요건을 동시에 만족해야 한다:
  - **OpenAPI 자동 생성** — `etlx-web`이 type-safe API client를 자동 생성(openapi-typescript)할 수 있어야 함
  - **Pydantic 통합** — 코어의 `etl_plugins.config.models.ConnectionConfig` / `PipelineConfig`를 그대로 REST body로 재사용
  - **async 네이티브** — SQLAlchemy 2.x async + 외부 시크릿 백엔드(Vault) + Kafka commit hook 등 IO-bound
  - **의존성 주입** — `Depends(require_role("editor"))` 같은 RBAC 가드를 선언적으로
  - **production-ready** — 충분히 검증된 ASGI 프레임워크
  후보: FastAPI / Starlette + 자체 라우팅 / Litestar / Flask + flask-smorest / Django REST framework / Quart.

- **Decision**:
  1. **FastAPI (`>=0.115`)** 채택. 위 5개 요건 모두 만족 + 생태계 성숙(authlib / fastapi-pagination / SQLAlchemy 통합 패턴 확립).
  2. ASGI 서버: **uvicorn workers + gunicorn 진입점**. 로컬은 `uvicorn --reload`.
  3. Pydantic v2 명시 — 코어와 동일 버전(`pydantic>=2.7`).
  4. OpenAPI 스키마는 `/openapi.json`, 인터랙티브 문서는 `/docs`(Swagger) + `/redoc`. CI에서 `etlx-web`이 `openapi-typescript`로 client 코드 생성.
  5. RBAC / 워크스페이스 격리는 의존성 주입으로 표현 — `Depends(get_current_user)` → `Depends(require_workspace_role("editor"))` 체이닝.
  6. **코어를 import해서 직접 사용** (예: `from etl_plugins.runtime import run_pipeline_yaml`) — adapter 추가 불필요.
  7. 에러 핸들링: `etl_plugins.core.exceptions.ETLError` → HTTP 4xx/5xx 매핑은 FastAPI exception handler 한 곳에 집중.

- **Consequences**:
  - (+) 코어의 Pydantic 모델을 REST body로 그대로 재사용 — 단일 진실, 검증 로직 한 번만 작성.
  - (+) OpenAPI → TS client 자동 생성으로 프론트/백 시그니처 drift 방지.
  - (+) `Depends(...)` 체이닝이 깔끔 — RBAC 가드를 보고서 권한 모델 추론 가능.
  - (+) Step 4의 Orchestrator adapter 패턴과 동일하게 코어를 wrapping — 코어 API 표면이 다시 검증됨.
  - (−) Starlette / Pydantic / FastAPI 3-way 버전 호환 관리 필요 — `pyproject.toml`에 pin 범위 명시.
  - (−) async DB 드라이버(`asyncpg`) 필요 — 코어가 쓰는 `psycopg`(sync)와 별도 의존. 코어 코드 재사용 시 `run_in_thread`로 우회하는 경우 있을 수 있음.
  - (−) WebSocket 기반 실시간 로그 스트리밍(Step 8.6)은 FastAPI도 지원하지만 별도 패턴 필요.

- **References**: `SPEC.md` §7 (서비스 인프라), ROADMAP Step 8 · 코어의 `etl_plugins.config.models`, `etl_plugins.runtime.runner`

---

## ADR-0020: 메타데이터 저장소 = PostgreSQL 16+ + SQLAlchemy 2.x async + Alembic

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  `services/etlx-server`가 관리할 도메인:
  - `workspaces`, `users`, `roles`, `memberships`, `connections` (config_json + secret_refs), `pipelines`, `pipeline_versions` (이력), `schedules`, `runs`, `run_logs`, `run_metrics`, `audit_log`
  특성:
  - JSON-shaped config가 핵심(코어의 PipelineConfig dump 통째). 부분 인덱싱·검색 필요.
  - audit_log는 시계열 + 텍스트 검색 일부 (`pg_trgm`).
  - 단일 노드로 시작, 추후 HA(Step 11 운영 강화)에서 standby/replica 검토.
  후보: PostgreSQL / MySQL / SQLite / CockroachDB / SurrealDB.

- **Decision**:
  1. **PostgreSQL 16+** 채택. 이유:
     - **JSONB**로 ConnectionConfig / PipelineConfig 그대로 직렬화 + JSONB GIN 인덱스로 부분 쿼리 효율.
     - **pg_trgm**으로 audit_log 본문/리소스 이름 검색.
     - **advisory lock + `SELECT FOR UPDATE SKIP LOCKED`**가 Step 9 워커 큐의 기반(외부 큐 의존 회피, ADR-0021과 짝).
     - 코어의 통합 테스트에 이미 `testcontainers[postgres]` 사용 — 인프라 학습·운영 재활용.
  2. **SQLAlchemy 2.x async + `asyncpg`** — FastAPI(ADR-0019)와 자연스러운 짝.
  3. **Alembic** 마이그레이션 — `services/etlx-server/alembic/`. 초기 마이그레이션은 `alembic revision --autogenerate`로 시작.
  4. **워크스페이스 격리**는 모든 도메인 테이블의 `workspace_id` 외래키 + 쿼리 시 자동 필터. 응용 레벨로 보장 (RLS는 첫 릴리스 범위 밖).
  5. UUID PK (uuid7 권장 — 시간순 정렬 + 인덱스 친화). `created_at` / `updated_at` 자동 stamping.
  6. 시크릿은 **절대 DB에 평문 저장 금지** (ADR-0017 §6). `connections.config_json`에는 `${SECRET_REF}` 형태 placeholder만 보관. 실제 값은 backend(Vault / AWS SM / GCP SM, ADR-0017).
  7. 백업: Step 11에서 `pg_dump` 가이드. 메타 DB 자체는 ETL 대상 postgres와는 다른 인스턴스 권장.

- **Consequences**:
  - (+) 코어 / 메타 DB / 워커 큐 모두 PostgreSQL — 인프라 단순화. testcontainers 재활용.
  - (+) JSONB로 스키마 진화에 유연 (예: connector별 옵션 추가가 alembic migration 없이 동작).
  - (+) `SKIP LOCKED`로 Celery/Redis 없이 멀티 워커 fan-out (ADR-0021).
  - (−) PG 운영 부담 — 백업·인덱스·VACUUM 등. Step 11에서 Helm chart + 가이드.
  - (−) 첫 릴리스에 멀티 리전 / HA 없음. 단일 노드 + cold standby 권장.
  - (−) SQLAlchemy 2.x async가 sync 코드 대비 학습 곡선 있음 — 기존 코어가 sync라 mental model 두 개 공존.

- **References**: ROADMAP §7.2 메타 DB 스키마, §9.2 스케줄러, §9.3 Run 라이프사이클 · 코어 통합 테스트 `tests/integration/conftest.py`(postgres fixtures)

---

## ADR-0021: 실행 엔진 = 자체 PostgreSQL-backed worker queue (Dagster/Prefect 임베드 거부)

- **Date**: 2026-05-14
- **Status**: Accepted (Step 7.0 직후 재검토 후 확정 — 아래 Considered alternatives 참조)
- **Context**:
  Step 9가 풀어야 할 문제:
  - **스케줄링**: cron 표현식 → 다음 실행 시각 계산 + 트리거.
  - **Fan-out**: 동시 N개 워커가 큐에서 작업을 잡아 실행.
  - **Run 라이프사이클**: 큐잉 → 실행 → 상태/로그/메트릭 수집 → 상태 갱신.
  - **Stream pipeline**: 장기 실행, 별도 worker process / Pod (Step 9.4).
  - **재시도 / DLQ**: 코어가 이미 처리(ADR-0013). 워커는 invoke만.
  세 가지 선택:
  1. **Dagster 임베드** — 풍부한 UI/스케줄러/observability. 단점: 우리 UI(Step 10)와 중복. dagster의 Asset/Op 모델로 다시 풀어야 함. 거대 dep.
  2. **Prefect 임베드** — flow API + Cloud/OSS server. 단점: 1번과 유사.
  3. **자체 실행기** — `etl_plugins.runtime.{run_pipeline_yaml, arun_stream_pipeline_yaml}`을 직접 호출하는 워커. 큐는 PostgreSQL `SKIP LOCKED`.

  코어가 이미 **Pipeline + run + retry + DLQ + metrics emit**을 갖춰서 외부 엔진의 task graph가 불필요하다. UI(Step 10)가 별도로 있어 외부 엔진 UI는 중복.

- **Decision**:
  1. **자체 실행기 채택**. 핵심 컴포넌트:
     - **`runs` 테이블** = 큐 역할도 겸함. 상태: `pending` → `running` → `succeeded`/`failed`/`cancelled`.
     - **워커 프로세스 풀** — `services/etlx-server/etlx_server/workers/batch_worker.py`. 루프:
       ```
       SELECT ... FROM runs
       WHERE status='pending' AND scheduled_at <= now()
       ORDER BY scheduled_at
       FOR UPDATE SKIP LOCKED LIMIT 1;
       ```
       → 잡으면 상태 `running`으로 → `run_pipeline_yaml(...)` 직접 호출 → 결과를 runs/run_logs/run_metrics에 기록.
     - **스케줄러** — 별도 process. cron 표현식(croniter) 평가 → 다음 실행 시각이 도래한 schedule의 새 run row 생성.
     - **Stream worker manager** — `arun_stream_pipeline_yaml`을 별도 process(K8s `Deployment` per pipeline)로 띄움. supervisor가 graceful shutdown + health 관리.
  2. **외부 엔진 어댑터는 그대로 유지** — Step 4의 `etl_plugins.adapters.{airflow, dagster, prefect}`. 사용자가 자기 Airflow에 우리 pipeline을 얹고 싶으면 그대로 동작.
  3. **DB 큐 폴링 간격**: 1초(기본). 짧지만 PG의 `SKIP LOCKED`는 컨테션 없이 fan-out하므로 부담 없음.
  4. **장기 실행 / heartbeat**: `runs.heartbeat_at`을 워커가 30초마다 갱신. 누락된 row는 다른 워커가 picks up (zombie reclaim).
  5. **PoC 우선** — Step 9.1에서 자체 실행기 PoC(소규모 부하 테스트) 후 본 구현. 부정적 결과가 나오면 본 ADR을 supersede하고 Prefect 임베드로 전환.

- **Consequences**:
  - (+) 거대 의존 0. PG 하나로 메타·큐·도메인 DB 통합.
  - (+) 코어의 `Pipeline.run` / `arun_stream` / retry / DLQ / metrics가 그대로 작동 — 코드 중복 없음.
  - (+) UI(Step 10)와의 매핑이 1:1 — runs 테이블이 SSOT.
  - (+) Step 4의 adapter들은 그대로 유지 → 사용자가 외부 엔진을 선호하면 우회 가능.
  - (−) Distributed scheduling 정교화(우선순위 큐, fair-share, 자원 예약)는 우리가 직접 만들어야. 첫 릴리스는 단순 FIFO + per-workspace concurrency cap.
  - (−) Cron이 정확히 한 번 트리거되는지(스케줄러 SPOF)는 운영 이슈. advisory lock으로 단일 leader 보장.
  - (−) 추후 100k+ runs/day 규모에서 PG 큐 한계가 보이면 별도 broker(Redis Streams / NATS JetStream)로 마이그레이션 필요.

- **Considered alternatives** (Step 7.0 직후 재검토 결과 — 같은 기준의 후보 비교):

  | 옵션 | broker 의존 | task model | 코어 재사용 | UI 중복 | 거부 사유 |
  |---|---|---|---|---|---|
  | **자체 PG queue (채택)** | PG만 | Pipeline 직접 호출 | 100% | 없음 | — |
  | Dagster OSS 임베드 | dagster-postgres | Asset/Op로 재모델링 | Op 안에서만 | Dagster UI vs 우리 UI | UI/모델 이중화. Step 4 adapter로도 동일 사용성 확보 가능 |
  | Prefect 3.x server 임베드 | Prefect server (PG) | Flow/Task로 재모델링 | Task 안에서만 | Prefect UI vs 우리 UI | 위와 동일 사유 |
  | Celery + Redis | **Redis 추가** | task | 100% | 없음 | Redis 인프라 추가 부담 + 우리 use case 부하(~수 runs/sec)가 broker 분리 이득을 정당화 못 함 |
  | arq (async + Redis) | **Redis 추가** | task | 100% | 없음 | 위와 유사 + 생태계가 Celery 대비 작음 |
  | APScheduler 4 + 자체 worker | PG | task | 100% | 없음 | `croniter` 한 줄로 동등 기능 가능 → 의존성 추가 이득 없음 |
  | Temporal | **Temporal cluster** | workflow + activity | activity 안에서만 | 없음 | 첫 릴리스 과함. 추후 거대 규모 시 재검토 |

  결정적 기준 = **(1) 코어 재사용 100%** + **(2) 인프라 추가 0** + **(3) UI 중복 없음**. 자체 PG queue만 세 가지 모두 만족.

- **Operating envelope, exit ramp, PoC gates**:
  1. **Operating envelope** (목표 부하): ~50 runs/sec, ~10k runs/day, p95 enqueue→start < 2s, 동시 워커 ~50. 그 너머는 broker 전환 검토.
  2. **Exit ramp** — 미래에 broker로 옮길 때를 위한 격리: 워커가 큐를 폴링하는 코드는 `etlx_server/workers/queue.py` 한 파일에 격리 (interface = `claim_next() / heartbeat() / complete() / fail()`). 큐 백엔드 교체 = 이 한 파일 + Alembic 마이그레이션 한 번. `runs` 테이블의 도메인 컬럼(workspace_id / pipeline_id / status / started_at / finished_at)은 백엔드와 무관하게 유지.
  3. **PoC 합격 기준** (Step 9.1 — 통과 못 하면 본 ADR을 supersede하고 Celery+Redis 또는 Temporal 재검토):
     - 50 동시 워커 + 1000 enqueued runs 부하에서 deadlock·zombie 없이 모두 종료
     - p95 enqueue→start latency < 2s
     - 스케줄러(advisory lock leader) 강제 종료 시 30초 내 다른 인스턴스로 페일오버
     - Worker 강제 SIGKILL 시 heartbeat 만료(60s) 후 다른 워커가 zombie run 회수해서 재시도

- **References**: ROADMAP §9 (Execution Engine), §11 (운영 강화) · 코어의 `etl_plugins/runtime/runner.py`, ADR-0013(retry/DLQ)

---

## ADR-0022: 모노레포 구조 = uv workspace + pnpm workspace, CI 3분리, import-graph 검사

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  ADR-0017에서 세 패키지 분리를 결정했다:
  - `etl_plugins/` (코어, Python)
  - `services/etlx-server/` (FastAPI, Python)
  - `services/etlx-web/` (Next.js, TypeScript)
  실현 방법 선택지: 단일 pyproject + 서브패키지 / uv workspace + pnpm workspace / Bazel·Pants 등 거대 빌드 시스템 / 분리된 repo 3개.

- **Decision**:
  1. **단일 git repo + uv workspace + pnpm workspace** 채택. 다음 구조:
     ```
     etl-plugins/
     ├── pyproject.toml                # 코어 (etl-plugins)
     ├── etl_plugins/
     ├── services/
     │   ├── etlx-server/
     │   │   ├── pyproject.toml        # uv workspace member, deps: etl-plugins~=0.X
     │   │   └── etlx_server/
     │   ├── etlx-web/
     │   │   └── package.json          # pnpm workspace member
     │   └── docker-compose.services.yml
     ├── pnpm-workspace.yaml           # 서비스 web만 포함
     └── tests/                        # 코어 테스트
     ```
  2. **uv workspace** (`[tool.uv.workspace] members = ["services/etlx-server"]`) — server는 코어를 path dep로 개발 시 사용, 릴리스 후엔 PyPI version으로 핀.
  3. **CI 3 분리**:
     - `.github/workflows/ci-core.yml` — `pyproject.toml`(root) / `etl_plugins/` / `tests/` 변경 시 트리거. unit + integration + lint + mypy + build.
     - `.github/workflows/ci-server.yml` — `services/etlx-server/` 변경 시. 코어 wheel을 install 후 server 테스트.
     - `.github/workflows/ci-web.yml` — `services/etlx-web/` 변경 시. pnpm install + typecheck + test + Storybook build + Chromatic.
     `paths:` 필터로 cross-package 트리거 회피.
  4. **import-graph 검사** — `ci-core.yml`에 [`import-linter`](https://github.com/seddonym/import-linter) step. 규칙:
     - `etl_plugins` 패키지는 `services` import 금지.
     - `etl_plugins.core`는 `etl_plugins.connectors` / `etl_plugins.adapters` / `etl_plugins.runtime` import 금지(역방향만 허용).
     - 위반 시 CI 실패.
  5. **버전 정책**:
     - 코어 `etl-plugins`: SemVer. 0.x 동안은 minor 단위 breaking 허용.
     - `etlx-server` / `etlx-web`: 별도 SemVer. 코어는 `~=X.Y`로 의존(같은 minor 라인).
     - 서비스 첫 안정 릴리스 = `etlx-server 1.0` + `etlx-web 1.0` (코어가 0.x여도).
  6. **릴리스 워크플로** 분리:
     - `release-core.yml` — git tag `core-vX.Y.Z` → PyPI Trusted Publishing.
     - `release-server.yml` — `server-vX.Y.Z` → Docker image push.
     - `release-web.yml` — `web-vX.Y.Z` → Docker image push.
  7. **공통 dev 도구**(ruff / mypy / pre-commit / .editorconfig)는 root에서 관리.

- **Consequences**:
  - (+) 단일 PR로 코어+서비스 동시 변경 가능 (예: 코어 API 추가 + server에서 사용).
  - (+) IDE에서 코어 코드로 즉시 점프 (path dep).
  - (+) CI 3분리로 비관련 변경에 불필요한 잡 실행 없음.
  - (+) import-graph 검사로 ADR-0017 §6 "단방향 의존"이 사람의 규율이 아닌 CI 자동 검증.
  - (−) uv workspace 학습 — 팀이 처음.
  - (−) Renovate / Dependabot이 monorepo path 인식 설정 필요.
  - (−) 릴리스 채널 3개 운영 비용 — 매뉴얼·자동화 충분히 정비 필요.

- **References**: ADR-0017 §6 단방향 의존 · ROADMAP §7.1 모노레포 스캐폴딩 · `pyproject.toml` (코어)

---

## ADR-0023: 인증·인가 정책 = OIDC + 로컬 fallback + JWT RS256 + 4-role RBAC

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  비개발자 사용자가 직접 들어와 파이프라인·연결·스케줄을 만들고 monitoring할 예정. 요건:
  - **SSO**: 회사가 Google Workspace / Azure AD / Okta / GitHub Enterprise 등을 쓸 가능성 → OIDC 일반화.
  - **로컬 fallback**: 사내 환경 / 로컬 dev / SSO 미도입 조직.
  - **워크스페이스 다중 테넌시**: 모든 도메인 리소스가 workspace에 속함.
  - **역할 분리**: 운영자가 실행만 트리거하고 설계 변경은 못 하게.
  - **감사**: 누가 언제 무엇을 바꿨는지 추적 (audit_log).
  - **API 호출**: 머신·CI/CD 사용도 가정 — bearer token / service account.

- **Decision**:
  1. **OIDC 일반화** ([authlib](https://docs.authlib.org/) 사용). Google / Azure AD / Okta / GitHub / 커스텀 OIDC provider 모두 동일 코드 경로.
  2. **로컬 fallback**: email + password (bcrypt cost 12). `users.password_hash`는 `auth_method='local'`일 때만 채움.
  3. **세션 = JWT RS256**:
     - Access token TTL 15분, Refresh token TTL 7일(rotation).
     - 키페어는 secret backend(Vault / file)에 보관. 키 회전은 운영 매뉴얼.
     - 클레임: `sub`(user_id) / `email` / `name` / `auth_method` / `iat` / `exp`. 워크스페이스/역할은 매 요청 DB에서 조회(stale token 안전).
  4. **API 호출용 PAT** (Personal Access Token) — 별도 테이블, prefix `etlx_pat_*`. `Authorization: Bearer <pat>`. UI에서 발급/회수.
  5. **RBAC 4 역할** (워크스페이스 단위):
     | 역할 | 가능한 작업 |
     |---|---|
     | **Owner** | 멤버 관리 + 모든 리소스 CRUD + 워크스페이스 삭제 |
     | **Editor** | 모든 리소스 CRUD (멤버 관리 제외) |
     | **Runner** | Pipeline 트리거 + Run 조회 + DLQ 재처리. CRUD 불가. |
     | **Viewer** | 읽기 전용 (대시보드/로그/메트릭 조회) |
  6. **글로벌 역할** = `SuperAdmin` (서비스 운영자) — 모든 워크스페이스에 접근 가능. audit_log에서 명확히 구분.
  7. **FastAPI 의존성**:
     ```python
     Depends(get_current_user)
       → Depends(get_current_workspace)
         → Depends(require_workspace_role("editor"))
     ```
     모든 엔드포인트가 명시적으로 역할 선언. 미선언 = CI 실패(별도 lint check).
  8. **시크릿/패스워드 로깅 금지** — 기존 secret masker(`etl_plugins.observability.logging.mask_sensitive_values`)에 `password` / `token` / `pat` / `authorization` 추가.
  9. **Audit log**: 모든 mutating endpoint가 `audit_log` row를 남김. actor / workspace / action / resource_type / resource_id / before_json / after_json / ip / user_agent / created_at.

- **Consequences**:
  - (+) SSO와 로컬 모두 가능 — 사내·외 모두 대응.
  - (+) RBAC를 FastAPI Depends로 선언적 표현 → 권한 모델이 코드 리뷰 시 한 줄로 보임.
  - (+) audit_log가 ADR-0017의 "감사 추적" 요건 충족.
  - (+) PAT로 CI/CD 통합 자연스러움.
  - (−) JWT 키 회전 / refresh rotation / blacklist 처리는 직접 구현해야 — 첫 릴리스는 단순 회전(서버 재시작 시 모든 토큰 무효화) + 향후 강화.
  - (−) OIDC provider별 미세한 차이(예: Azure AD의 `tid` 클레임 처리) — 테스트 케이스 추가 필요.
  - (−) 4 역할이 부족한 사용자가 있을 수 있음 — 첫 릴리스 후 피드백 수렴.

- **References**: ROADMAP §8.2 인증, §8.3 RBAC, §8.4 Audit · ADR-0017 §6 시크릿 처리 · 코어의 `etl_plugins.observability.logging.make_secret_masker`

---

## (이후 ADR 작성 시 위 양식을 복사해서 추가)
