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

## ADR-0024: Step 6 격상 — Asset / Lineage / Cursor를 코어의 1급 모델로

- **Date**: 2026-05-14
- **Status**: Accepted
- **Context**:
  ADR-0021(자체 PG-backed worker queue) 확정 직후 "장기적으로 데이터 통합 및 파이프라인 플랫폼으로서 어떤 방향이 나을까"를 재검토했다. 결론은 명확했다:
  - **부하 수치(runs/sec)는 후순위 문제** — broker 교체로 풀린다 (ADR-0021 Exit ramp 참조).
  - **진짜 비싼 비용은 모델 결정**:
    1. **Asset(데이터 산출물) 1급 모델** — "이 테이블은 어떤 파이프라인이 만들었나"
    2. **Lineage(계보)** — upstream/downstream 추적
    3. **Cursor / Watermark** — 멱등 재실행 / 백필
  - 1~2년 뒤 사용자가 lineage·sensor·백필을 요구하기 시작하면, 그 시점에 코어를 뜯어 위 3개를 박는 비용은 매우 크다.
  - 시장 정통(Dagster Asset, Airflow Datasets, OpenLineage)도 이 방향.

  현재 ROADMAP의 Step 6는 "강화" 일반 항목들(OpenLineage emit / OTel/Prometheus 백엔드 / Checkpoint / mkdocs / v0.1.0)로 묶여 있어 위 3개 모델 결정이 명시되지 않았다.

- **Decision**:
  1. **Step 6를 "Core 강화 — Asset/Lineage/Cursor 1급 모델 추가"로 격상** (ROADMAP §6 재구성). 강화 일반 항목(OTel 백엔드, mkdocs, v0.1.0)은 그 안에 흡수.
  2. **세 가지 추상화를 코어에 1급으로 추가** (`etl_plugins/`):
     - **Cursor**: `Cursor` protocol + `CursorState` (in-memory / file / DB backend). `BatchSource.read_since(cursor=...)`를 옵션 메서드로 추가 (기본 구현은 query 그대로). Pipeline은 `cursor_from` / `cursor_to`로 백필 가능.
     - **Asset**: `Asset(name, schema, partitions, freshness_policy, deps)` 데이터클래스 + `AssetGroup`(의존 그래프). 기존 `Pipeline`은 "default Asset 1개를 materialize"하는 wrapper로 매핑되어 **backward compatible**. `@Asset.register("orders")` 데코레이터.
     - **Lineage**: `etl_plugins.observability.lineage` 모듈. `Pipeline.run` / `arun_stream`이 OpenLineage RunEvent(START/COMPLETE/FAIL/ABORT)를 자동 emit. Inputs/outputs는 source/sink connector + table/topic에서 추출. 기본 backend는 NoOp + Marquez + 우리 자체 catalog API.
  3. **`etl_plugins.catalog` 모듈** — read-only API (`list_assets / get_asset / lineage(asset_name)`). 서비스(Step 8)가 이 API를 wrap해서 REST로 노출.
  4. **기존 Pipeline + Task + Record + Connector API는 그대로 유지** — Asset/Cursor/Lineage는 모두 위에 얹는 layer. v0 사용자는 알 필요 없음.
  5. **외부 의존 최소화**: OpenLineage 클라이언트(`openlineage-python>=1.0`)는 `[lineage]` extra. OTel은 `[observability]` extra(이미 있음). 코어 본체는 추가 의존 0.
  6. **버전 분할**:
     - **v0.1.0** (Step 6 1차 완료) = Pipeline + 5 connectors + adapters + retry/DLQ + **Cursor abstraction** + mkdocs + PyPI 릴리스. Asset/Lineage는 미포함.
     - **v0.2.0** (Step 6 2차) = Asset 1급 모델 + Lineage emit + Catalog API. 1차 사용자 피드백 후 확정.
  7. **인터페이스 우선, 구현 점진적**: Asset ABC + Cursor protocol을 먼저 못박고, 실제 BatchSource 구현체는 점진적으로 cursor 채택(postgres/mysql 우선). 한 번에 다 안 해도 됨.

- **Consequences**:
  - (+) 1~2년 뒤 lineage / 백필 / sensor 요구에 코어 변경 없이 대응. 시장 정통(Dagster/Airflow) 모델과 매핑 가능.
  - (+) Step 4 Dagster adapter에 Asset ↔ Dagster Asset 양방향 변환을 추가할 수 있는 기반(향후 슬라이스).
  - (+) "단순한 ETL 도구"에서 **"데이터 카탈로그까지 가진 플랫폼"**으로 포지셔닝 가능. ETL 시장의 Hightouch/Census/Fivetran/Airbyte와 다른 차별점.
  - (+) Cursor abstraction은 retry/DLQ만큼 자주 쓰이는 기능 → v0.1.0에 들어가는 게 사용자 가치 큼.
  - (−) Step 6 작업 범위가 2~3배. v0.1.0이 늦어질 수 있어 두 단계(v0.1=Cursor만, v0.2=Asset+Lineage)로 쪼갬.
  - (−) Asset 모델 추상화는 한 번 잘못 잡으면 후세에 부담. v0.2 진입 전 PoC 슬라이스로 검증.
  - (−) OpenLineage 표준이 진화 중 — 우리 구현이 spec 변경에 따라 갱신 필요.
  - (−) Asset/Cursor/Lineage가 들어오면 코어가 무거워져 "라이브러리로만 쓰는 단순 사용자"가 학습할 표면이 늘어남. 문서에서 "기본 사용은 Pipeline까지만"을 명시.

- **References**:
  - ADR-0021 (실행 엔진 — 부하는 broker로 풀고 모델은 별도) · ADR-0017 (서비스화 — Catalog API가 서비스 REST의 베이스가 됨)
  - Dagster Software-Defined Assets, Airflow Datasets, OpenLineage spec
  - ROADMAP §6 (재구성됨)

---

## ADR-0025: etlx-web 기본 폰트 = Pretendard Variable (self-host) + 무의존 i18n(ko/en)

- **Date**: 2026-05-20
- **Status**: Accepted
- **Context**:
  사용자가 UI 전체 개편을 요청하며 두 가지를 명시했다: ① "한글, 영어 선택해서 볼 수 있도록", ② "디자인을 Arc Browser 느낌으로, 특히 폰트는 Apple 산돌고딕(SD Gothic Neo) 느낌의 폰트로". 기존 DESIGN.md(ADR-0018)는 Sans를 `Inter Variable`(Latin) + `Pretendard Variable`(Korean)로 정의했으나, 실제 코드(`globals.css`)는 Inter를 선두로 두고 있어 한국어 화면에서 시스템 폰트로 떨어졌다. 또한 i18n 메커니즘은 전무했다.
- **Decision**:
  1. **Sans 1순위를 `Pretendard Variable`로 확정** — 한국어 우선 가변 폰트로 Apple SD Gothic Neo의 느낌을 웹에서 재현하고, Latin/Hangul을 한 폰트로 일관 처리. Inter는 폴백으로만 유지. `globals.css` `@font-face`(woff2-variations, weight 45 920) + `body`/`@theme --font-sans` 체인 선두 교체.
  2. **폰트 self-host** — `services/etlx-web/public/fonts/PretendardVariable.woff2`. CDN 의존 금지(프로덕션 컨테이너 런타임 외부 의존 0). 빌드시 jsdelivr `npm/pretendard@1.3.9`에서 1회 받아 리포에 포함.
  3. **i18n는 라이브러리 없이 자체 구현** — `lib/i18n/messages.ts`(typed flat dictionary, `en`이 SSOT이고 `ko: Messages`가 미러 → 키 누락은 TS 컴파일 에러) + `LocaleProvider`/`useLocale()` Context + `t(key, vars?)` `{name}` 보간. 2개 언어 UI에 next-intl 등 풀 i18n 라이브러리는 과함 — 번들 가볍게 유지.
  4. **언어 설정은 localStorage(`etlx.locale`) 영속 + 초기값은 `navigator.language` 추정(en→en, 그 외 ko), 기본 ko**. Header에 토글 버튼.
- **Consequences**:
  - (+) 한·영 혼용 화면이 일관된 자형/자간으로 Arc-like 차분함. 사용자 요청 직접 충족.
  - (+) 키 누락이 빌드 타임 타입 에러 → 번역 누락 방지.
  - (+) 런타임 CDN/외부 i18n 서비스 의존 0 — 컨테이너 단독 배포에 적합.
  - (−) woff2 ~2MB를 리포에 커밋(LFS 미사용). subsetting 안 함 — 한글 전체 글리프 필요해 trade-off.
  - (−) 자체 `t()`는 복수형/성별/날짜 현지화 미지원. 2개 언어·단순 문자열 범위에서만 유효 — 확장 시 라이브러리 재검토.
- **References**:
  - ADR-0018 (etlx-web 디자인 시스템 — 본 ADR이 §4.1 폰트 결정을 갱신) · DESIGN.md §4.1
  - 사용자 요청(2026-05-19): "한글/영어 선택 + Apple 산돌고딕 느낌 폰트 + Arc 느낌"

---

## ADR-0026: 파이프라인 fan-out — 1 source → N sinks (batch 전용, source 재읽기 방식)

- **Date**: 2026-05-20
- **Status**: Accepted
- **Context**:
  사용자가 "파이프라인에서 분기처리 가능하도록 추가"를 요청했고, 범위로 **fan-out(하나의 source → 여러 sink)** 을 우선 채택했다(fan-in/멀티 source DAG는 후순위). 코어 `PipelineConfig`는 엄격히 선형이었다: `source` 1 + `transforms[]` + `sink` 1. 실행기(`Pipeline._run_task`)도 task당 정확히 sink 1개를 요구. 코어를 서비스 편의로 바꾸지 않는다는 원칙(CLAUDE.md §6) 하에, 기존 단일-sink 동작을 한 바이트도 바꾸지 않으면서 다중 sink를 추가해야 했다.
- **Decision**:
  1. **설정 모델**: `PipelineConfig.sink: SinkConfig | None`(기존 단수형 유지) + `sinks: list[SinkConfig]` 신설. `@model_validator`로 **정확히 하나만** 지정(`sink` XOR `sinks`)을 강제. `effective_sinks()`가 둘을 단일 리스트로 정규화.
  2. **런타임 모델**: `SinkSpec`(name/table/mode/key_columns/options) dataclass + `Task.sinks: list[SinkSpec]` 신설. `Task.sink*` 플랫 필드는 단일-sink 레거시 경로로 그대로 유지. `Task.effective_sinks()`가 플랫 필드를 1-원소 `SinkSpec` 리스트로 승격.
  3. **실행 방식 = sink별 source 재읽기(re-read), 버퍼링 안 함**: `_run_task`가 sink마다 `source` iterator를 새로 열어 transform 체인을 재적용 후 write. **버퍼링(메모리에 전체 적재) 대신 재읽기**를 택해 무한/대용량 스트림에서도 메모리 bound 유지. `records_read`/`new_cursor`는 **첫 패스에서만** 카운트(이중 집계 방지), `cursor_to` 상한 필터링은 **매 패스** 적용. `records_written`은 sink 합산.
  4. **단일-sink 무변경 보장**: 빌더는 sink가 1개면 기존처럼 플랫 `Task.sink*`를 채우고, 2개 이상일 때만 `Task.sinks`를 채운다. 단일 경로 런타임은 `effective_sinks()`로 1-원소 리스트를 만들어 동일 코드로 통과 → 관측 가능한 동작 동일.
  5. **stream fan-out은 v1 범위 외**: stream sink는 offset commit 시맨틱이 있어 1→N 다중 stream sink는 commit 정책 설계가 선행돼야 한다. `build_pipeline`이 `mode=="stream" and len(sinks)>1`이면 `ConfigError`로 조기 거부. `_arun_stream_task`도 방어적으로 다중 sink면 `TaskError`.
- **Consequences**:
  - (+) 동일 추출+변환 결과를 여러 목적지(예: warehouse + S3 아카이브)로 한 파이프라인에서 분기. core 변경은 가산적(additive)이라 기존 사용자/테스트 영향 0(459 unit green).
  - (+) public API(`etl_plugins.SinkSpec`, `PipelineConfig.sinks`, `Task.sinks`)로만 노출 — 서비스(Step 7~)가 내부 손대지 않고 사용.
  - (−) **재읽기 비용**: sink가 N개면 source를 N회 읽고 transform을 N회 실행. source가 비싸거나 비결정적이면 부담. trade-off로 메모리 안전을 택함(향후 옵션으로 buffer 모드 추가 여지).
  - (−) 트랜잭션 원자성 없음 — sink A 성공 후 sink B 실패 시 A는 이미 write됨. at-least-once/멱등 sink(upsert+key_columns) 전제. DLQ는 transform 단계만 커버.
  - (−) stream fan-out 미지원 — Kafka→(Kafka,Kafka) 같은 케이스는 별도 ADR 필요.
- **References**:
  - 사용자 요청(2026-05-20): "파이프라인에서 분기처리" + 범위 선택 "Fan-out 분기부터"
  - CLAUDE.md §6 (코어를 서비스 편의로 바꾸지 말 것 — 신규 public API + ADR로) · ADR-0024 (코어 1급 모델 확장 방향)

---

## ADR-0027: 조건부 라우팅 — sink별 `when` 술어 + first-match(switch) 분기

- **Date**: 2026-05-20
- **Status**: Accepted
- **Context**:
  사용자가 Airflow `BranchPythonOperator`처럼 **결과값에 따라 후행 작업이 분기**되는 기능을 요청했다. ADR-0026의 fan-out은 *모든* 레코드를 *모든* sink로 보낸다(tee). 여기서 필요한 건 레코드의 값에 따라 **어느 sink로 갈지 결정**되는 content-based routing이다. 이 라이브러리는 레코드 스트리밍 모델이라 Airflow의 task-level 분기보다 **레코드 단위 라우팅**이 자연스러운 대응이다.
- **Decision**:
  1. **sink별 선택적 `when` 술어**: `SinkConfig.when: str | None` + `SinkSpec.when`. filter transform과 동일한 샌드박스(`compile`+`eval`, `{"__builtins__": {}}`, locals `data`/`metadata`)로 평가. transform 적용 *후*의 레코드에 대해 평가(계산된 컬럼으로도 라우팅 가능).
  2. **first-match(switch) 시맨틱**: 각 레코드는 `when`을 가진 conditional sink들을 **선언 순서대로** 평가해 **처음 truthy인 sink 하나**로만 간다. 어디에도 매칭 안 되면 `when` 없는 **default sink(들) 전부**로 간다(else 분기). 매칭도 default도 없으면 **드롭**(라우팅의 통상 동작).
  3. **하위 호환**: 어떤 sink에도 `when`이 없으면 = 순수 fan-out(ADR-0026, 모든 sink가 모든 레코드 수신). 즉 routing은 기존 동작의 가산적 superset.
  4. **실행은 ADR-0026의 sink별 source 재읽기 위에 얹음**: sink i의 패스에서 각 레코드의 라우팅 타깃을 계산해(첫 매칭 conditional 또는 default 집합) i에 속할 때만 write. `records_read`는 첫 패스만, 라우팅 필터는 transform 뒤 적용.
  5. **batch 전용**: stream sink는 offset commit 시맨틱이 있어 per-record 라우팅 commit 정책이 선결돼야 함. `build_pipeline`이 `mode=="stream"`에 `when`이 하나라도 있으면 `ConfigError`. 빌드 시점에 `when` 구문도 `compile`로 검증(dry-run에서 조기 실패).
  6. **단일 sink + `when`**: 플랫 `Task.sink*` 경로는 `when`을 못 실으므로, sink가 1개여도 `when`이 있으면 `Task.sinks` 리스트 경로 사용(드롭-필터 효과).
- **Consequences**:
  - (+) "고객 등급별로 다른 테이블", "이벤트 타입별 분기" 같은 라우팅을 코드 없이 표현. fan-out과 동일한 메모리-bound 재읽기 모델 재사용.
  - (+) first-match는 "분기=한 경로 선택"이라는 직관과 BranchPythonOperator의 mutually-exclusive 선택에 부합. default 분기로 catch-all 가능.
  - (−) **재읽기 + 술어 재평가 비용**: sink가 N개면 각 패스마다 모든 conditional 술어를 재평가(O(records×conditional×sinks)). 술어는 싸지만 비결정적 source/술어는 부담.
  - (−) 매칭 안 된 레코드 드롭은 **메트릭 미집계**(v1) — 관측 보강은 후속. transform 단계 DLQ만 커버.
  - (−) all-match(multicast-with-filter) 시맨틱은 채택 안 함 — 필요하면 `when`+fan-out 조합 또는 별도 옵션으로 후속 검토. stream 라우팅도 범위 외.
- **References**:
  - 사용자 요청(2026-05-20): "BranchPythonOperator와 같이 결과값에 따라 후행 작업이 분기"
  - ADR-0026 (fan-out — 본 ADR이 실행 모델/`sinks` 구조를 재사용) · filter transform(`runtime/transforms.py`, 동일 샌드박스 eval)

---

## ADR-0028: Task-orchestration DAG — Airflow류 의존성 그래프 (단계적, P1.1 = depends_on + 위상 실행)

- **Date**: 2026-05-20
- **Status**: Accepted (P1.1 구현, P1.2~P1.4 + Phase 2 dataflow 예정)
- **Context**:
  사용자가 "1:1 단순 구조 말고 정말 Airflow의 DAG처럼 정밀하게" 처리되길 요청했다. 현재 코어는 사실상 **Pipeline = Task 1개**(1 source → transforms → N sinks, fan-out/조건분기는 sink 단에서만)이고, `Pipeline.tasks`는 리스트지만 task 간 **의존성 개념이 없어** 단순 순차 실행이다. "Airflow DAG처럼"은 두 가지로 갈린다: ① **Task 오케스트레이션 DAG**(task가 단위, 엣지=의존성, 데이터는 저장소 경유 — 실제 Airflow), ② **Dataflow DAG**(노드=오퍼레이터, 레코드가 엣지로 흐름, join/split — Dagster/Beam/NiFi). 사용자가 BranchPythonOperator를 언급했고 "Airflow"를 명시 → 의미상 ①에 가깝다. 사용자 선택: **단계적 — 먼저 Task DAG(①), 이후 dataflow(②)**.
- **Decision**:
  **Phase 1 = Task-orchestration DAG**, 4개 슬라이스로 분할:
  - **P1.1 (본 구현)**: `Task.depends_on: list[str]`(upstream task 이름) + `Pipeline._ordered_tasks()` 위상 정렬 실행. Kahn 알고리즘, ready 집합은 **원래 리스트 순서로 안정 tie-break**(부분 순서 DAG도 예측 가능). 검증: 미존재 의존 / 중복·공백 task 이름 / 자기 의존 / 사이클 → `PipelineError`. **하위 호환**: 어떤 task도 `depends_on`이 없으면 `self.tasks` 원본 순서 그대로 반환(기존 동작 무변경). 실행은 단일 스레드 순차(병렬은 후속). batch 전용.
  - **P1.2**: `PipelineConfig`를 다중 task(`tasks: list[TaskConfig]` + `depends_on`)로 확장하되 기존 단일 source/transforms/sink shape 하위 호환. `build_pipeline` 다중 Task Pipeline 빌드. 서버 `referenced_connection_names`가 모든 task 순회.
  - **P1.3**: 분기(BranchPythonOperator류 — task가 후행 task 선택, 미선택 분기 skip) + per-task **trigger rule**(all_success/all_done/one_success/none_failed) + per-task RunResult 상태(success/skipped/failed/upstream_failed).
  - **P1.4**: 빌더 UI — React Flow 자유 연결 task 노드 + 의존성 엣지 + 분기/trigger rule 설정. 기존 선형 단일-task 전제 대체.
  **Phase 2 = Dataflow DAG**(레코드 엣지 흐름, join 버퍼링/머티리얼라이즈, 위상 스트리밍 실행)는 Phase 1 안정화 후 별도 ADR.
- **Consequences**:
  - (+) "B는 A 다음", 분기, 복잡한 의존 그래프 등 Airflow의 핵심 정밀도를 코어에서 표현. 기존 `Task` 추상화 재사용이라 P1.1은 가산적·하위 호환(478 unit green).
  - (+) task 간 데이터는 저장소(테이블/객체) 경유 — 코어에 XCom류 신규 메커니즘 불필요(필요 시 후속).
  - (−) P1.1만으로는 사용자 가치가 제한적(다중 task를 표현할 config/UI가 P1.2~P1.4에 있음) — 슬라이스 단위로만 출시 가능.
  - (−) 순차 실행만(병렬 task 미지원) — 독립 분기 동시 실행은 후속 슬라이스.
  - (−) Phase 1(task DAG)과 Phase 2(dataflow)는 실행 모델이 달라, 빌더 UX가 두 패러다임을 어떻게 통합/구분할지 P1.4/Phase2 진입 시 재검토 필요.
- **References**:
  - 사용자 요청(2026-05-20): "1:1 구조 말고 정말 Airflow DAG처럼 정밀하게" + 방향 선택 "단계적: 먼저 Task DAG, 이후 dataflow"
  - ADR-0026/0027 (fan-out / 조건 라우팅 — sink 단 분기. 본 ADR은 task 단 분기로 상위 레이어) · ADR-0024 (코어 1급 모델 확장 — Asset/Lineage와 DAG의 관계는 Phase 2에서 합류 가능)

### Addendum (2026-05-20, P1.3 구현) — branch 결정 모델 + trigger rule + 실패 격리

ADR 본문이 P1.3에 남겨둔 미해결 포인트 "record-task 시스템에서 무엇이 분기를 결정하는가"를 다음으로 확정(사용자 "추천 방향으로 진행" 승인):

- **branch 결정 = task 결과(outcome)에 대한 predicate**. branch task 실행 후 `BranchRule(when, to)` 목록을 순서대로 평가 — `records_read`/`records_written`/`success`를 scope에 둔 샌드박스 `eval`(filter transform/ADR-0027 라우팅과 동일 패턴, no builtins). **first-match**: 처음 truthy인 rule의 `to`(직접 후행 task 이름들)가 선택되고, `when=None`은 default/else. Airflow의 "task 1회 실행 후 분기"와 정합. 매칭 없으면 직접 후행 전부 미선택.
- **선택 안 된 직접 후행은 skip**, 그 후손은 **trigger rule로 skip 전파**(별도 전파 로직 없이 `all_success`가 자연히 처리). branch target은 **직접 후행이어야** 함(builder가 빌드 시점 검증).
- **trigger rule**(per-task, 기본 `all_success`): `all_success`(upstream 실패→`upstream_failed`, skip→skip), `all_done`(무조건 실행), `one_success`(≥1 성공시 실행), `none_failed`(실패 없으면 실행). 위상 순서라 평가 시점에 upstream은 모두 terminal.
- **실패 격리**: DAG 경로(`_run_dag`)는 한 task 실패해도 **독립 task는 계속 실행**하고 마지막에 첫 에러를 raise(전체 run은 실패로 보고). per-task 상태는 `RunResult.task_states`(success/failed/skipped/upstream_failed).
- **게이팅**: `depends_on`/`branch`/non-default `trigger_rule`이 하나도 없으면 DAG가 아니므로 기존 순차 경로(첫 실패 즉시 raise, `task_states` 미기록) 유지 — 하위 호환.
- 미해결(후속): 병렬 task 실행, per-task retry, branch predicate에 노출할 컨텍스트 확장(현재 read/written/success만).

- **추가 References**: 코어 495 unit green(P1.3 +17 DAG/branch/trigger + builder 검증). `etl_plugins.BranchRule` public export. `TaskConfig.trigger_rule`/`branch`(BranchRuleConfig).

---

## ADR-0029: "call pipeline" — 파이프라인 간 호출(서비스 레벨 downstream 트리거, fire-and-forget)

- **Date**: 2026-05-20
- **Status**: Accepted (v1 fire-and-forget; wait-for-completion은 후속)
- **Context**:
  사용자가 빌더 UI를 "한 화면에서 구성"으로 유지하길 원하고(2단계 drill-down 폐기), 복잡한 흐름은 **"다른 파이프라인을 호출하는 operator"** 로 조합하길 원했다. 즉 각 파이프라인은 단순 단일-캔버스 흐름으로 두고, 오케스트레이션은 파이프라인끼리의 호출로 emergent하게 구성. 핵심 제약: **코어는 서비스를 모른다(단방향 의존, CLAUDE.md §6)** — 코어 커넥터/태스크가 서비스의 파이프라인을 트리거할 수 없다. 따라서 "call pipeline"은 **서비스 레벨** 기능이어야 한다.
- **Decision**:
  1. **저장**: 신규 테이블 `pipeline_triggers(source_pipeline_id, target_pipeline_id)` — "source가 성공하면 target을 enqueue"하는 방향성 엣지. 코어 `PipelineConfig`(extra=forbid)에 넣지 않음 — config_json은 코어 record-flow 전용이고 "call pipeline"은 오케스트레이션이라 별도 메타데이터로 분리. Alembic `0003_pipeline_triggers`.
  2. **실행 = fire-and-forget downstream 트리거**: 워커(`RunExecutor`)가 run을 성공으로 기록한 *직후 같은 트랜잭션*에서 source의 downstream target들의 current version으로 PENDING Run을 enqueue(`triggered_by_user_id=NULL`, `schedule_id=NULL`). source는 target 완료를 기다리지 않음(Airflow TriggerDagRunOperator류). 사용자 선택(2026-05-20): 먼저 fire-and-forget, 대기 옵션은 후속.
  3. **사이클 차단**: 각 run의 `result_json.trigger_chain`이 lineage(거쳐온 pipeline_id들)를 누적. target이 chain에 이미 있으면 skip(A→B→A 무한 트리거 방지). 추가로 `_MAX_TRIGGER_CHAIN=50` 하드 캡. current version 없는 target은 skip+log.
  4. **API**: `GET/PUT /workspaces/{ws}/pipelines/{pid}/triggers`(Viewer+/Editor+) + audit(`pipeline.triggers_set`). PUT은 전체 교체(idempotent), self-트리거 400 / workspace 밖 target 404.
  5. **UI**: 빌더는 단일-캔버스 유지(drill-down 폐기). "downstream pipelines" 선택 패널로 노출(별도 API 저장 — config_json과 분리).
- **Consequences**:
  - (+) 단순 파이프라인 + 호출 조합으로 복잡한 워크플로우 구성. 코어/서비스 경계 보존(코어 무변경). 기존 runs/trigger 인프라(ADR-0021) 재사용.
  - (+) 사이클 안전(visited-set + depth cap). fire-and-forget이라 워커 로직 단순.
  - (−) 완료 대기/조건부 트리거(성공 외 상태) 미지원 — v1 범위 외. 실패 시 downstream 안 돎(성공 트리거만).
  - (−) 트리거는 source 성공 트랜잭션에 묶임 — target enqueue 실패 시 source 커밋도 롤백(현재는 같은 세션). target이 많거나 DB 이슈 시 영향 가능(후속에 분리 검토).
  - (−) 코어 task-DAG(ADR-0028)와 "call pipeline"이 둘 다 분기/조합 수단 — 역할 중복. 사용자 방향은 후자(파이프라인 간 호출)이며 task-DAG는 코어에 유지(가산적·무해)하되 UI 1차 노출은 안 함.
- **References**:
  - 사용자 요청(2026-05-20): "한 화면에서 구성 + Operator에서 다른 pipeline 호출" + 시맨틱 "먼저 fire-and-forget, 나중에 대기 옵션"
  - ADR-0021(자체 PG worker queue — runs 테이블이 큐) · ADR-0028(task-DAG — 같은 분기 목적의 코어 레이어) · CLAUDE.md §6(코어→서비스 import 금지)

---

## ADR-0030: Dataflow DAG — 레코드 단위 그래프 실행 + 어느 노드에서나 분기 (Phase 2, tree v1)

- **Date**: 2026-05-20
- **Status**: Accepted (P2.1 코어 + P2.2 서버 구현; P2.3 빌더 UI 예정)
- **Context**:
  사용자가 "끝이 sink로 고정된 선형이 아니라, 자유롭게 순서를 정하고 **모든 작업에서 분기**되게" 요청했다. 기존 코어는 `source → transforms → sink(들)` 선형이고 분기는 sink 단 라우팅(ADR-0027)만 가능했다. 사용자 선택(2026-05-20): **레코드 단위 dataflow DAG** — 오퍼레이터 노드를 자유 연결하고 임의 노드에서 조건으로 레코드를 분기. (ADR-0028의 task-DAG와 달리 task가 아니라 레코드 흐름 그래프; ADR-0028 Phase 2에 해당.)
- **Decision**:
  1. **설정 = `graph`(nodes + edges)**, `PipelineConfig`의 세 번째 상호배타 shape(single-task / tasks / graph). `GraphNodeConfig`(id, type=source/transform/sink, 커넥터 필드 또는 nested `TransformConfig`), `GraphEdgeConfig`(from_node, to_node, 선택적 `when` predicate). edge의 `when`(filter/라우팅과 동일 샌드박스)으로 **분기**: 노드에 outgoing edge 여러 개 → 각 edge의 `when`이 통과한 레코드만 그 경로로.
  2. **v1 = source-rooted tree**: 단일 source, 분기(fan-out)만, fan-in/join 미지원. 검증으로 강제(비-source 노드는 incoming edge 정확히 1개). → 각 sink(leaf)는 source까지 **유일 경로**.
  3. **실행 = sink별 source 재읽기 + 경로 재생**(ADR-0026 fan-out 모델 일반화): 각 sink에 대해 source→sink 유일 경로의 transform·edge predicate를 순서대로 적용하며 source를 재읽기. records_read는 첫 sink pass에서만, records_written은 합산. `_run_graph_task`가 `_run_task`에서 `task.graph_nodes`가 있으면 디스패치.
  4. **배치 전용**: stream graph는 거부(`ConfigError`). cursor 백필도 graph v1 미지원(`TaskError`).
  5. **서버**: `referenced_connection_names`가 graph shape면 graph 노드의 source/sink connection 순회. config_json은 `PipelineConfig` validate/dump로 자동 round-trip. 워커는 `Pipeline.run`이 graph task를 그대로 실행.
- **Consequences**:
  - (+) "어느 노드에서나 조건 분기 + 자유 순서"를 레코드 단위로 표현. 기존 fan-out 재읽기 실행 모델 재사용이라 메모리 bound 유지·구현 추가가 가산적(코어 510 unit / 서버 265 db green).
  - (+) public API(`GraphConfig`/`GraphNode`/`GraphEdge`)로만 노출. 단일-task/task-DAG 무변경(하위 호환).
  - (−) **재읽기 비용**: sink가 N개면 source를 N회 읽고 공유 prefix transform도 N회 재실행. 비싸거나 비결정적 source엔 부담(fan-out과 동일 trade-off).
  - (−) **fan-in/join 미지원**(tree 강제) — 두 경로 합류, 멀티 source는 후속. edge `when`은 multicast(매칭 경로 모두) 시맨틱이며 first-match 스위치는 별도.
  - (−) cursor 백필·stream·DLQ(graph) 미지원 v1. 빌더 UI(P2.3, 자유 연결 캔버스)는 미구현 — config/API/실행만 우선.
- **References**:
  - 사용자 요청(2026-05-20): "자유 순서 + 모든 작업에서 분기" + 선택 "레코드 단위 dataflow DAG"
  - ADR-0026(fan-out 재읽기 — 실행 모델 일반화의 기반) · ADR-0027(sink 라우팅 — edge `when`과 동일 샌드박스) · ADR-0028(task-DAG — 같은 분기 목적의 task 레이어; 본 ADR은 Phase 2 레코드 레이어)

---

## ADR-0031: TB급/분산 실행 — ExecutionBackend 추상화 + Spark/Databricks pushdown (단계적)

- **Date**: 2026-05-20
- **Status**: **Superseded by ADR-0040** (2026-05-21 — Spark 백엔드 + ExecutionBackend 추상화 전면 제거). 아래는 역사적 기록.
- **Context**:
  사용자가 TB급/분산을 진짜 목표로 잡았다. 현재 실행 모델은 row-by-row Python `Record` 스트리밍이라 근본적으로 단일 프로세스·행 단위 — TB급에선 불가. 게다가 dataflow graph(ADR-0030)의 "sink별 source 재읽기"는 분기 시 source를 N번 읽고 공유 prefix를 N번 재계산해 대용량에 부적합. 핵심 인식: **TB급에선 바이트를 Python으로 끌어오지 않는다** — 라이브러리 역할이 "행 이동"에서 **"파이프라인 정의 → 타깃 엔진 연산으로 컴파일 → 실행 위임(pushdown/ELT)"** 으로 이동. 타깃 환경: **Spark/Databricks**(사용자 선택).
- **Decision**:
  1. **`ExecutionBackend` 추상화 신설** — 파이프라인(config; linear/graph)이 백엔드로 컴파일·실행된다. 백엔드 레지스트리 + `PipelineConfig.engine`(기본 `local`).
     - **`local`**: 현재 row-streaming 실행기를 감싼다(개발·소규모). 동작 무변경.
     - **`spark`**: DAG → Spark DataFrame 연산으로 컴파일. source→`spark.read.jdbc/parquet/...`, transform→`withColumnRenamed`/`cast`/`filter`/`select`/`drop`/`dropDuplicates`/`withColumn(lit)`, 분기(edge `when`)→`df.filter` (+공유 노드 `cache()`), sink→`df.write...`. **Spark Catalyst가 source 스캔 공유·분기 최적화·분산을 처리 → ADR-0030의 재읽기/스케일 문제 해소.**
  2. **transform·predicate는 백엔드 중립 + 선언적**이어야 pushdown 가능. no-code 빌더의 구조화 조건(field/op/value)을 정식 표현으로 삼아 local=Python, spark=Spark SQL로 렌더. **임의 `python` transform과 raw-Python 고급식은 Spark 백엔드에서 거부(v1)** — local에서만 실행. (후속에 Spark UDF opt-in 가능, 느림.)
  3. **connection → Spark IO 매핑** 레이어: postgres→JDBC, s3→parquet/csv 등. connection 메타데이터를 Spark read/write 옵션으로 변환.
  4. **Spark 실행 위치**: dev=로컬 `SparkSession(local[*])`, prod=클러스터/Databricks를 config(`spark.master`/Databricks Connect)로. 워커가 in-process run 대신 Spark 잡 submit하는 통합은 후반(P3.4).
  - **단계**: **P3.1**(본 구현) ExecutionBackend ABC + LocalBackend + `engine` 필드 + `run_config` 디스패치(동작 무변경). **P3.2** Spark 백엔드(선형, 선언적 transform, JDBC/parquet, 로컬 SparkSession, `pyspark` optional extra). **P3.3** Spark 분기/graph(터미널별 filter + cache). **P3.4** 클러스터/Databricks 잡 submit + 서비스/워커 통합. **P3.5** UI(엔진 선택 + pushdown 불가 노드 표시).
- **Consequences**:
  - (+) 라이브러리가 "정의·컴파일·오케스트레이션"으로 포지셔닝(ELT). 대용량은 Spark가, 소규모·개발은 local이. 같은 파이프라인 정의가 백엔드만 바꿔 양쪽에서.
  - (+) Spark 백엔드는 재읽기·분기·분산을 엔진이 해결 → ADR-0026/0030의 trade-off 소멸(대용량 경로).
  - (−) **대형 다단계 작업**(몇 주). pyspark 의존(optional extra) + Spark 환경 테스트 필요(dev는 `local[*]`).
  - (−) **임의 Python transform은 pushdown 불가** — 선언적 transform만 Spark. 표현력 제약(또는 느린 UDF).
  - (−) connection→Spark IO 매핑을 커넥터 종류별로 새로 작성. 모든 커넥터가 Spark 네이티브 IO를 갖진 않음(우선 JDBC/parquet/csv).
  - (−) `local`/`spark` 두 실행 경로 유지 부담 — 중립 표현으로 동작 일치를 보장해야 함(contract test 대상).
- **References**:
  - 사용자 요청(2026-05-20): "TB급/분산이 진짜 목표 — 다시 잡자" + 타깃 "Spark/Databricks"
  - ADR-0026(fan-out 재읽기)·ADR-0030(dataflow graph — 재읽기 한계가 본 ADR의 동기) · CLAUDE.md §2(orchestrator/backend-agnostic 원칙과 정합 — 실행 백엔드 다변화)

---

> **Superseded by ADR-0040** (2026-05-21). 아래는 역사적 기록.

## ADR-0032: Spark 백엔드 — 운영·배포·분산 실행 설계 (thin worker + 외부 클러스터 잡 제출)

- **Date**: 2026-05-20
- **Status**: Accepted (설계 확정; 구현은 Spark 환경 필요 — P3.2~P3.4)
- **Context**:
  ADR-0031에서 Spark/Databricks 백엔드 방향을 잡았다. 사용자가 "항상 운영·배포를 고려, 분산작업 시 어떻게 해야 할지도 설계"를 명시했다. Spark 백엔드는 "DataFrame 컴파일"만이 아니라 **프로덕션에서 어떻게 제출·실행·스케일·실패·관측·배포되는지**가 본질이다. (현 개발 환경엔 Java/pyspark가 없어 실행 검증 불가 — 설계를 먼저 못박는다.)
- **Decision**:
  1. **토폴로지 = thin worker + 외부 클러스터 잡 제출(권장)**. etlx 워커는 Spark를 in-process로 돌리지 않는다(프로덕션). 워커는 run을 claim → **Spark 잡을 외부 클러스터/Databricks에 제출 → 상태 폴링(heartbeat 유지) → 종료 상태/카운터/로그를 run에 기록**. 워커는 가볍게 유지되고 Spark 리소스는 독립 스케일. 기존 PG worker-queue 모델(ADR-0021)과 정합 — run terminal writer는 워커 단일.
     - 대안(거부): fat worker가 SparkSession을 들고 클러스터 driver 노릇 → 워커 수명이 잡에 묶이고 무거움. **dev/소규모만 in-process `local[*]` 허용.**
  2. **제출 모드(submission mode)** — config로 선택:
     - `local` (in-process `SparkSession(local[*])`): dev/CI.
     - `spark-submit` (`--master yarn|k8s://…|spark://…`): 표준 클러스터, 잡당 driver.
     - `databricks` (Databricks Jobs API / SDK): 매니지드. 로컬 Spark 불필요.
     - (k8s) `spark-submit --master k8s://`: driver/executor 파드.
  3. **제출 단위 = Spark entrypoint + 직렬화된 파이프라인**. 워커가 파이프라인 config(graph/linear) + connection config를 스테이징(객체 스토리지/컨피그)하고, 공용 entrypoint 모듈(`etlx_spark_job`)을 제출. driver가 그 안에서 DAG→DataFrame 컴파일·실행. 코드/엔진 분리.
  4. **분산 시크릿** — 평문 인자/Spark conf로 절대 안 보냄. **driver가 런타임에 SecretBackend(Vault/AWS SM/GCP SM)에서 재해석**(ADR-0023/Step 7.4 재사용). 클러스터엔 시크릿 backend 접근 권한(IAM/role)만 부여. Databricks는 Databricks Secrets 연동.
  5. **분산 병렬성 = 파티셔닝**. JDBC source는 `partitionColumn`/`numPartitions`/`lowerBound`/`upperBound` 없이는 단일 파티션(병렬성 0) — source/connection config에 파티셔닝 옵션 노출. object storage(parquet)는 자연 병렬. 셔플/조인은 Spark가 처리.
  6. **멱등성·재시도(분산 실패)**. Spark 잡은 부분 write 후 실패 가능 → sink는 멱등이어야 함: `overwrite`=스테이징 테이블/경로 write 후 **atomic swap(rename/`INSERT OVERWRITE`)**, `upsert`=MERGE, `append`=at-least-once(dedupe key 권장). 기존 retry(ADR-0021 `add_retry`)가 잡 재제출로 매핑.
  7. **관측** — driver가 구조화 로그 + 최종 카운터(records read/written via DataFrame count/accumulator)를 run_logs/run_metrics로 전송, Spark UI/잡 링크를 `result_json`에 보관. 폴링 중 heartbeat 갱신(ZombieReaper와 정합, ADR 9.3b).
  8. **배포·이미지·드라이버 JAR**:
     - Databricks 모드: 워커 이미지에 **Databricks SDK만**(로컬 Spark 불필요).
     - spark-submit/k8s 모드: 잡 이미지(driver/executor)에 Spark + 우리 라이브러리 + **커넥터별 JDBC 드라이버 JAR**(postgres/mysql 등 — Spark classpath에 필수, 운영 문서화 대상). 워커 이미지엔 spark-submit 클라이언트.
     - 우선 지원 IO: JDBC(rdbms) + parquet/csv(object storage). Kafka/Mongo 등은 후속.
  9. **config 표면** — workspace/pipeline 단위 "Spark 실행 프로파일": master/submission mode, 리소스(executors/메모리/cores), JDBC 파티셔닝 기본값, 스테이징 위치. 클러스터 접근 시크릿은 SecretBackend ref.
- **Consequences**:
  - (+) 워커는 가볍고, 컴퓨트는 클러스터가 스케일 — TB급 분산을 엔진에 위임. 기존 worker-queue/secret/retry/관측 인프라 재사용.
  - (+) 제출 모드 추상화로 dev(`local[*]`)→prod(Databricks/k8s) 같은 코드.
  - (−) **운영 표면 증가**: 클러스터/Databricks 프로비저닝, JDBC JAR 관리, 스테이징 스토리지, IAM/시크릿 접근. 문서·런북 필요.
  - (−) **현 dev 환경(Java/pyspark 없음)에서 구현 검증 불가** — `local[*]` 테스트조차 JVM 필요. 구현은 Spark 환경(또는 CI에 Java+pyspark)에서 진행/검증해야 함.
  - (−) at-least-once append·스테이징 swap 등 sink별 멱등 전략을 커넥터마다 정의해야 함.
- **References**:
  - 사용자 요청(2026-05-20): "항상 운영·배포 고려 / 분산작업 시 어떻게 해야 할지 고려"
  - ADR-0031(ExecutionBackend + Spark 방향) · ADR-0021(PG worker queue — 잡 제출/추적 모델의 베이스) · ADR-0023/Step 7.4(SecretBackend — driver 런타임 재해석) · 9.3b(heartbeat/ZombieReaper — 폴링 정합)

---

## ADR-0033: 커넥터 스키마 인트로스펙션 — `SchemaInspector` 옵셔널 capability ("딸깍" 빌더)

- **Date**: 2026-05-21
- **Status**: Accepted
- **Context**:
  no-code 빌더에서 사용자가 Postgres 등 DB 작업 시 스키마/테이블/컬럼을 **직접 타이핑**해야 했다. 사용자 요구: "스키마 및 테이블 목록을 읽어와서 선택, source/target 둘 다, 후행 작업도 선행 결과 컬럼을 불러와 '딸깍'". 즉 빌더가 연결에서 메타데이터를 읽어 드롭다운/체크박스로 제공해야 한다. 단, 모든 커넥터가 인트로스펙션 가능한 건 아니다(HTTP/Kafka/plain object store는 "테이블" 개념이 없음).
- **Decision**:
  1. 코어에 **옵셔널 capability** `etl_plugins.core.inspect.SchemaInspector`(`@runtime_checkable` Protocol) + `ColumnInfo`(frozen dataclass: `name`, `type`) 추가. `list_tables() -> list[str]` + `list_columns(table) -> list[ColumnInfo]`.
  2. **구조적(structural) 타이핑** — 커넥터는 상속 없이 두 메서드만 구현하면 capability 보유. 호출자는 `isinstance(conn, SchemaInspector)`로 가드. 인트로스펙션 불가 커넥터는 그냥 미구현 → UI는 자유 입력 fallback.
  3. **테이블 주소 형식 = 커넥터 natural form** 유지: Postgres `"schema.table"`(information_schema, pg_catalog/information_schema 제외), MySQL `"table"`(`table_schema = DATABASE()`), SQLite bare `"table"`(sqlite_master, `sqlite_%` 제외). `list_tables`가 돌려준 문자열을 그대로 sink `table` 필드에 쓸 수 있어야 한다.
  4. **SQL injection 가드**: 컬럼 조회는 가능한 한 파라미터 바인딩(information_schema). SQLite `PRAGMA table_info`는 파라미터 불가 → `_SAFE_IDENT` 정규식(`^[A-Za-z_][A-Za-z0-9_]*$`)으로 식별자 검증 후만 보간, 실패 시 `ReadError`.
  5. RDBMS 3종(postgres/mysql/sqlite)에 우선 구현. 서비스 계층(Step 8.5c+)이 `GET /connections/{id}/tables`·`/columns`로 노출하고 etlx-web 빌더가 소비(별도 슬라이스).
- **Consequences**:
  - (+) 빌더 UX 대폭 개선("딸깍"). 코어는 서비스를 모름 — 단방향 의존 유지.
  - (+) capability 패턴이라 미지원 커넥터에 부담 0, 향후 DW/NoSQL 확장 시 동일 패턴.
  - (−) `type` 라벨은 커넥터-native 문자열(정규화 안 함) — 빌더는 표시/힌트 용도로만. 타입 강제 매핑은 향후 과제.
  - (−) 인트로스펙션은 라이브 커넥션 필요 → 서비스 엔드포인트는 connect 비용·권한을 고려해야 함(read-only, audit 없음).
- **관련**: ADR-0008(postgres) · 5.1(mysql/sqlite) · Step 8.5c(connections API — 엔드포인트가 얹힐 자리) · Step 10.4(Pipeline Builder — 소비처)

---

## ADR-0034: 같은 연결 source+sink 데드락 — sink 전용 연결 인스턴스 분리

- **Date**: 2026-05-21
- **Status**: Accepted
- **Context**:
  파이프라인이 **같은 연결**을 source(읽기)와 sink(쓰기)에 동시에 쓰면(예: 한 DB 안에서 테이블 A→B 복사) 워커가 무한정 "running"에 멈추는 현상 발견. 원인: 런타임은 연결 *이름*당 커넥터를 1개만 만든다(`connectors: dict[str, Connector]`). 코어는 스트리밍으로 `sink.write(read_and_transform(...))` — sink의 `COPY ... FROM STDIN`이 source 제너레이터에서 행을 lazy하게 당겨온다. 그런데 source·sink가 같은 이름이면 **psycopg 연결 1개를 공유** → COPY가 연결의 non-reentrant 락을 쥔 채 같은 연결에서 server-side cursor `FETCH`를 시도 → **self-deadlock**(스레드 `futex_wait`, 서버측 `COPY … ClientRead` 영구 대기). ZombieReaper가 안 떠 있으면 자동 실패 처리도 안 됨.
- **Decision**:
  1. **sink가 source 연결을 재사용하면 sink에 전용 커넥터 인스턴스(별도 물리 연결)를 만든다.** `build_pipeline(..., connector_factory=...)`(옵셔널) 추가 — `factory(name) -> Connector`로 새 인스턴스 생성. 같은-이름 충돌 시 synthetic key `"<name>::sink"`로 등록하고 task/SinkSpec의 sink 참조를 그 키로 교체. 생성된 인스턴스는 반환 `connectors` dict에 추가돼 caller가 동일하게 connect/close.
  2. **스트리밍·메모리 보장 유지** — 전체 버퍼링(소스를 리스트로 모두 적재) 대안은 거부(코어 핵심 가치인 bounded-memory 위반). 연결을 하나 더 여는 비용만 든다.
  3. linear(`_build_task`) + dataflow graph(`_build_graph_task`) 양쪽 적용. fan-out에선 source 연결을 재사용하는 sink만 분리(나머지는 그대로).
  4. `connector_factory`가 없으면(미리 만든 커넥터만 넘긴 caller·테스트·동시 read/write 허용 드라이버) 기존 단일 인스턴스 동작 유지 — 하위호환. 워커(`RunExecutor._prepare`)와 `build_pipeline_from_yaml`은 config에서 재인스턴스화하는 factory를 주입.
- **Consequences**:
  - (+) 한 DB 안에서 읽고 쓰는 흔한 ETL 패턴이 데드락 없이 동작(검증: 실 Postgres에서 source=sink='test', read=2/written=2, 0.01s 완료).
  - (+) 코어 시그니처만 확장(옵셔널 인자), 기존 호출부·테스트 무변경 통과(코어 +3 unit, 536 전부 green).
  - (−) 같은 연결 read+write 시 물리 연결 2개 사용(설계상 정상 — ETL 도구 표준).
  - (−) graph/fan-out에서 source 연결을 공유하는 sink가 여럿이면 각각 `::sink` 1개를 공유(순차 쓰기라 안전). 동시 쓰기가 필요해지면 키 네이밍 확장 필요.
  - 참고: 별개 문제로 데이터 타입 불일치(예: UUID `id` → `time` 컬럼)는 데드락 제거 후 이제 **즉시 COPY 타입 에러로 실패**(무한 멈춤 아님).
- **관련**: ADR-0021(worker queue) · 9.3b(heartbeat/ZombieReaper) · ADR-0026(fan-out) · ADR-0030(dataflow graph)

---

## ADR-0035: 멱등성 — pre-load SQL 액션("Run SQL" transform)으로 delete-then-insert

- **Date**: 2026-05-21
- **Status**: Accepted
- **Context**:
  현재 `append` sink는 재실행 시 중복 적재(비멱등), `overwrite`는 멱등이나 비원자적(TRUNCATE 후 COPY 중간 실패 시 데이터 손실), `upsert`는 키+유니크 제약 필요. 사용자가 "파이프라인에서 멱등성이 보장 안 된다 → **transform 영역에서 DELETE 같은 SQL을 날릴 수 있게** 해달라"고 요청. 즉 적재 전에 대상 행을 지우는 delete-then-insert를 빌더에서 표현할 수단이 필요.
- **Decision**:
  1. **옵셔널 커넥터 capability `SqlExecutor`**(`etl_plugins.core.sql_exec`, `@runtime_checkable`) — `execute_statement(sql) -> int`. RDBMS 3종(postgres/mysql/sqlite)에 구현(문 실행 + commit, 실패 시 rollback + WriteError). 비-SQL 커넥터(S3/Kafka/HTTP)는 미구현 → `isinstance` 가드.
  2. **`Task.pre_sql: list[SqlAction]`**(`SqlAction(connection, statement)`). `Pipeline._run_task`가 **읽기 시작 전 1회씩 순서대로** 실행(`_run_pre_sql`). 레코드 수와 무관하게 항상 실행 → 0건이어도 DELETE 보장. 대상 커넥션이 `SqlExecutor` 미구현이면 `TaskError`.
  3. **빌더 표현 = transform 팔레트의 "Run SQL (before load)" 오퍼레이터**(`type: "sql_exec"`, 필드 connection+statement). `extra=allow`인 `TransformConfig`로 그대로 직렬화. `build_pipeline._build_task`가 `type=="sql_exec"`를 per-record transform에서 분리해 `task.pre_sql`로 적재(나머지는 평소대로 `build_transform`). 노드 위치와 무관하게 항상 load 전 실행("before load" 라벨로 의미 전달).
  4. **연결 해석** — `referenced_connection_names`에 sql_exec 연결 포함 → 워커가 해당 커넥터를 빌드. 빌더 connection 드롭다운은 `anyConnection` 플래그로 전체 연결 노출(타입 제한 없음).
  5. **스코프** — 선형 파이프라인 batch 경로만(graph/stream은 후속). ADR-0034(연결 분리)와 정합: pre_sql이 sink와 같은 연결명을 써도 sink는 `::sink` 별도 인스턴스라 무관, DELETE는 단일 문 commit 후 즉시 해제.
  6. **(Update 2026-05-21) 원자적 변형 — sink 트랜잭션 내 pre-write SQL**. 위 task-level pre_sql(별도 트랜잭션, 임의 연결, 비원자)에 더해, **sink에 `pre_sql`을 붙여 sink의 write 트랜잭션 *안에서* 첫 문으로 실행** → `DELETE + INSERT`가 한 트랜잭션으로 commit(원자적 delete-then-insert). `SinkSpec.pre_sql`/`Task.sink_pre_sql` + RDBMS `write(pre_sql=...)`(TRUNCATE/COPY/upsert 앞에서 실행, 빈 입력에도 실행해 파티션 클리어, 실패 시 rollback으로 DELETE까지 원복). 빌더는 RDBMS sink 노드에 "Pre-write SQL (atomic)" 필드. **MySQL은 DELETE(DML)만 원자적**(TRUNCATE는 DDL → implicit commit). 두 도구 구분: 같은 DB·원자 필요 → sink `pre_sql`; 다른 DB·setup → "Run SQL" transform.
- **Consequences**:
  - (+) 재실행 멱등성 확보(검증: sqlite e2e — 같은 파이프라인 2회 실행 후에도 대상 테이블 중복 없음, stale 행 삭제됨).
  - (+) **원자성 확보(sink pre_sql)** — 검증: insert 실패 시 pre_sql DELETE까지 rollback돼 기존 데이터 보존(데이터 공백 없음), 빈 입력에도 DELETE 실행.
  - (+) 코어 capability 패턴 재사용(ADR-0033 SchemaInspector와 동일 구조), 빌더는 transform 노드 + sink 필드로 노출.
  - (−) task-level "Run SQL" transform은 여전히 비원자(별도 트랜잭션) — 크로스-DB/setup 용도. 원자 멱등은 sink `pre_sql` 사용.
  - (−) statement는 검증 없이 그대로 실행(소스 query와 동일한 신뢰 모델). graph/stream 미지원. full-table rename swap(MySQL TRUNCATE 원자화)은 후속.
- **관련**: ADR-0033(capability 패턴) · ADR-0034(연결 분리) · ADR-0026(fan-out) · 향후: full-refresh rename-swap, upsert 키 빌더 강제

---

## ADR-0036: Asset/Lineage v0.2 — derived-first 모델 + 정적 리니지 (Airflow×Dagster 결합의 기반)

- **Date**: 2026-05-21
- **Status**: Accepted
- **Context**:
  사용자의 궁극 목표 확정: **"Airflow 오케스트레이션 + Dagster 리니지(asset)의 결합."** 현재 오케스트레이션축(task-DAG·스케줄러·worker queue·call-pipeline·Spark)은 성숙했으나 **asset/리니지축은 Cursor만** 있고 Asset 1급 모델·Lineage·Catalog가 비어 있음(ADR-0024가 방향만 못박음, v0.2 미착수). ADR-0024는 "인터페이스 우선, 좁은 PoC로 검증"을 명시 — Asset 추상화는 잘못 잡으면 가장 비싼 후회 지점.
- **Decision**:
  1. **Derived-first 리니지(zero-config)** — 사용자가 asset을 선언하지 않아도 **source = input asset, sink = output asset, `input→output` 엣지 = 리니지**를 config에서 자동 파생. 이게 Fivetran/Airbyte류와의 차별점이자 진입 장벽 0.
  2. **AssetKey = `(connection, target)`** path 튜플(`"conn/target"` 렌더). target = 커넥터의 자연 식별자(table > topic > key > collection > query 우선순위). 코어는 connection *이름* 기반 — 서비스(Phase B)가 실제 host/db로 enrich.
  3. **코어 모델**(`etl_plugins/core/asset.py`, config/runtime 무의존 leaf): `AssetKey`·`AssetSpec`(선언형, deps/group/kind/freshness 후속)·`LineageEdge`·`AssetLineage`(파이프라인 1개의 inputs/outputs/edges)·`AssetGraph`(전역 그래프, upstream/downstream/ancestors/descendants, cycle-safe).
  4. **정적 파생**(`etl_plugins/runtime/lineage.py` `derive_lineage(cfg)`): run 없이 config만으로 리니지 계산. single-task/Task-DAG/dataflow graph 모든 shape 지원. **크로스-파이프라인 리니지는 키 일치로 자동 발생**(파이프라인 A가 쓴 asset을 B가 읽으면 같은 AssetKey → 자동 연결 = Dagster의 핵심 마법).
  5. **선언형(AssetSpec)은 모델만 정의, 스케줄 연결은 Phase D**(upstream materialize 시 downstream 자동 트리거 = call-pipeline ADR-0029의 asset 의존 일반화).
  6. **backward compatible** — 기존 Pipeline/Connector/Task 무변경. Asset은 위에 얹는 layer. 단순 라이브러리 사용자는 몰라도 됨.
  7. **로드맵 단계**(ROADMAP §6 v0.2 반영): **A1=모델+정적파생(이번 ADR)** → A2=런타임 OpenLineage emit(`[lineage]` extra) → A3=catalog read API → B=메타DB 영속화(assets/materializations/edges + 워커 hook) → C=웹 카탈로그+리니지 그래프(`@xyflow/react` 재사용) → D=asset-aware 오케스트레이션(freshness/센서/auto-materialize) → E=백필 UI·컬럼 리니지·OpenLineage export.
- **Consequences**:
  - (+) zero-config 리니지 — 설정 0으로 "이 테이블 누가 만들었나/뭐가 깨지나" 답변. 정적이라 run 전에도 빌더/카탈로그가 표시 가능.
  - (+) 모델 최소화(키+그래프)로 후세 부담↓, OpenLineage/Marquez/DataHub 매핑 용이. AssetGraph가 전역 결합점.
  - (+) 기존 자산 재사용: derive_lineage가 fan-out/graph/task-DAG 다 흡수, 키 일치로 크로스-파이프라인 자동.
  - (−) 코어 AssetKey가 connection-*이름* 기반 → 같은 물리 테이블을 다른 연결명으로 가리키면 다른 asset으로 보임. 서비스 enrich(connection→host/db)로 정규화(Phase B).
  - (−) source query에 table 없으면(`SELECT ... JOIN ...`) 키가 connection 단위로 coarse — table 파싱 개선은 후속(SchemaInspector/SQL 파서 활용 가능).
  - (−) 런타임 emit·메타DB·UI·오케스트레이션은 아직 — 이번은 모델+정적 파생까지.
- **관련**: ADR-0024(Asset/Lineage 격상 원안) · ADR-0028(task-DAG) · ADR-0029(call-pipeline→asset 트리거로 일반화) · ADR-0030(dataflow graph) · ADR-0033(SchemaInspector capability 패턴) · [[ultimate-vision]]

---

## ADR-0037: Asset-driven 오케스트레이션 — auto-materialize (Airflow Dataset × Dagster)

- **Date**: 2026-05-21
- **Status**: Accepted
- **Context**:
  궁극 목표(Airflow 오케스트레이션 + Dagster 리니지)의 정점은 **리니지가 오케스트레이션을 구동**하는 것 — upstream asset이 새로 만들어지면 그걸 읽는 downstream 파이프라인이 자동 실행(= Dagster auto-materialize / Airflow Dataset trigger). A/B/C에서 asset 모델·리니지·카탈로그를 갖췄으니, 이제 "asset X가 materialize되면 X를 읽는 파이프라인을 큐에 넣는다"를 구현. 기존 call-pipeline(ADR-0029, 명시적 pipeline→pipeline 링크)을 **asset 의존**으로 일반화한 것.
- **Decision**:
  1. **Opt-in** — `PipelineConfig.auto_materialize: bool`(기본 false). 자동이면 "테이블 쓸 때마다 그걸 읽는 모든 파이프라인이 실행"되는 폭주/서프라이즈가 생기므로 명시적 opt-in. 코어는 이 플래그를 무시(서비스 전용 동작).
  2. **트리거 시점 = run 성공 직후**(워커 `_trigger_asset_consumers`). materialize된 output asset 키들을 구해, **워크스페이스 내 current 버전이 그 키를 input으로 갖고 `auto_materialize:true`인 batch 파이프라인**을 찾아 PENDING Run enqueue. lineage 영속화(B2)와 같은 트랜잭션.
  3. **키 일치가 핵심** — source 키와 sink 키가 같은 테이블을 가리켜야 매칭됨. 그래서 ADR-0036의 derived 규칙을 보강(D1a): SQL source 쿼리에서 `FROM <table>`을 파싱해 `(connection, table)`로 키잉 → `SELECT … FROM orders` source가 `orders` sink와 같은 키. 워크스페이스 스코프 + 연결명 유니크라 안전.
  4. **사이클·폭주 가드** — 기존 `trigger_chain`(result_json) 재사용: 체인에 이미 있는 파이프라인은 재enqueue 안 함(A→B→A 차단), `_MAX_TRIGGER_CHAIN` 깊이 캡. stream 파이프라인 제외(runs 큐가 아닌 stream worker가 처리). self 제외.
  5. **정적 스캔(현 버전)** — 트리거 시 워크스페이스 파이프라인들의 current config를 `derive_lineage`로 그때그때 매칭. O(pipelines)이지만 단순·항상 정확. 인덱스 테이블(asset→consuming pipelines)은 규모 커지면 후속(E).
  6. **빌더 UI** — 헤더에 "Auto-materialize" 체크박스 → config_json `auto_materialize` emit/restore.
- **Consequences**:
  - (+) cron 없이 **데이터 흐름대로 자동 실행** — raw 적재 → staging → mart가 키 일치로 자동 연쇄. Airflow Dataset/Dagster SDA의 핵심 UX.
  - (+) 기존 worker-queue·trigger_chain·lineage 인프라 재사용, 코어 변경 최소(플래그 1개).
  - (+) opt-in이라 안전 — 켠 파이프라인만 자동 실행.
  - (−) 정적 스캔은 파이프라인 많으면 run마다 비용↑(인덱스 테이블로 후속 최적화). 같은 microsecond 다중 트리거의 중복 enqueue 방지(같은 키로 여러 upstream)는 아직 — pending 중복 가드는 후속.
  - (−) FROM 파싱은 첫 테이블만(JOIN/subquery 근사). 정확 매칭이 필요하면 명시적 asset 선언(AssetSpec deps) 경로가 후속.
  - (−) freshness/센서(시간 기반 트리거)는 D2로 미룸. 지금은 "upstream 성공 시" 이벤트 기반만.
- **관련**: ADR-0024/0036(asset/lineage) · ADR-0029(call-pipeline — asset 의존으로 일반화) · ADR-0021(worker queue) · [[ultimate-vision]]

---

## ADR-0038: Freshness 기반 auto-materialize — 시간 기반 센서 (Dagster freshness policy)

- **Date**: 2026-05-21
- **Status**: Accepted
- **Context**:
  D1(ADR-0037)은 "upstream이 성공하면" 이벤트 기반 트리거였다. 데이터 흐름이 멈춰 있을 때(소스가 외부에서 갱신되는데 우리 파이프라인이 안 돌면) asset이 stale해진다. Dagster의 freshness policy처럼 "이 asset은 N분 이상 오래되면 안 된다 → 오래되면 만드는 파이프라인을 재실행"하는 시간 기반 트리거가 필요. 이미 `assets.last_materialized_at`(B)와 cron `Scheduler.tick_once`(9.2)가 있어 재활용.
- **Decision**:
  1. **Opt-in `PipelineConfig.freshness_sla_minutes: int | None`**(기본 None=off, 서비스 전용). "내 출력 asset은 이 분 수보다 오래되면 안 됨"을 파이프라인이 선언(asset이 아닌 producing 파이프라인에 선언 — 매핑 단순).
  2. **스케줄러 틱에 freshness 패스 추가**(`Scheduler._tick_freshness`, cron 패스 뒤). current 버전 config에 `freshness_sla_minutes`가 있는 batch 파이프라인을 스캔 → `derive_lineage` output 키들의 `last_materialized_at`을 조회 → 하나라도 None(미생성)이거나 `now - SLA`보다 오래되면 **stale → PENDING Run enqueue**(`result_json.triggered_by="freshness"`).
  3. **폭주 가드 2종**: ① in-flight — pending/running run이 있으면 skip(쌓지 않음). ② 쿨다운 — 가장 최근 run의 `created_at`이 SLA 윈도우 안이면 skip(실패하는 파이프라인이 매 틱 재시도하는 storm 방지 → SLA당 최대 1회 시도).
  4. **D1과 합성** — freshness가 root를 신선하게 유지하면, 그 run의 output이 D1(auto-materialize)로 downstream을 연쇄. freshness=시간 기반 root 트리거, auto_materialize=이벤트 기반 전파.
  5. **빌더 UI** — 헤더에 "Freshness (min)" 숫자 입력 → config `freshness_sla_minutes` emit/restore. **스케줄러 프로세스 필요**(`etlx-server scheduler run`) — start.sh에 추가.
- **Consequences**:
  - (+) "데이터를 항상 N분 이내 신선하게" 선언적 보장 — cron 시각을 직접 안 짜도 됨. Dagster freshness policy UX.
  - (+) 기존 스케줄러·assets.last_materialized_at·worker-queue 재사용, 코어 변경은 플래그 1개.
  - (+) in-flight + 쿨다운 가드로 storm/중복 방지.
  - (−) 정적 스캔(모든 current 버전 + asset 조회)이 틱마다 — 파이프라인 많으면 비용↑(D1과 같은 한계, 인덱스 후속). multi-replica는 schedule처럼 `FOR UPDATE SKIP LOCKED` 필요(Step 11).
  - (−) SLA는 분 단위 정수(초/유연 정책 없음). 출력 여러 개면 "하나라도 stale" 기준(가장 보수적).
- **관련**: ADR-0037(이벤트 기반 auto-materialize — 합성) · ADR-0036(asset/lineage) · 9.2(cron scheduler — 틱 재사용) · [[ultimate-vision]]

---

## ADR-0039: 백필 — cursor 범위 재실행을 서비스/UI에 노출

- **Date**: 2026-05-21
- **Status**: Accepted
- **Context**:
  코어는 Step 6.1부터 `Pipeline.run(cursor_from, cursor_to)` + `BatchSource.read_since`로 증분/백필을 지원했지만, **서비스·UI에 노출 안 됨**. 게다가 config의 `cursor_column`이 `build_pipeline`에서 `Task.cursor_column`으로 전파되지 않아(누락) config-driven 백필이 동작 불가였다. 과거 구간 재적재(백필)는 ETL 운영의 기본 기능.
- **Decision**:
  1. **코어 배선(D3a)**: `SourceConfig.cursor_column` 필드 추가 + `_build_task`가 `Task.cursor_column`으로 전파(source_options에서 제외해 read() kwarg 누수 방지).
  2. **Run이 백필 파라미터 운반(D3b)**: 스키마 변경 없이 `runs.result_json.backfill = {cursor_from, cursor_to}`(스칼라, 사용자 입력값)에 저장. 워커 `_prepare`/`_run_pipeline_in_thread`가 읽어 `pipeline.run(cursor_from=, cursor_to=)`로 전달 → source가 `read_since`로 `cv > from AND cv <= to` 범위만 읽음. **local 엔진만**(Spark backfill 미지원).
  3. **엔드포인트(D3b)**: `POST /workspaces/{ws}/pipelines/{pid}/backfill`(Runner+) — body `{cursor_from?, cursor_to?}`. current 버전 config에 source `cursor_column`이 없으면 **400**(워커가 TaskError로 죽는 대신 명확한 거부). `add_manual(result_json=...)` 재사용, audit `run.backfill`.
  4. **빌더/UI(D3c)**: RDBMS source에 `cursor_column` 필드(옵션). 파이프라인 목록에 "Backfill" 버튼 → from/to 입력 다이얼로그 → 엔드포인트 호출.
- **Consequences**:
  - (+) 과거 구간 재적재가 UI에서 가능. 코어 cursor 인프라(read_since/CursorState) 재사용, 메타DB 스키마 무변경(result_json 활용).
  - (+) cursor_column 없으면 400으로 즉시 피드백.
  - (−) cursor 값은 스칼라(str/int/float) — DB가 커서 컬럼과 비교 시 coerce(예: ISO 문자열 ↔ timestamp). 타입 안전성은 DB에 위임.
  - (−) Spark 엔진·graph shape 백필 미지원. CursorState 자동 진행(다음 resume의 base)과 수동 백필의 상호작용은 별도(수동 백필은 result_json만 사용, CursorState 미갱신).
- **관련**: Step 6.1(cursor/read_since/CursorState) · ADR-0021(worker queue/result_json) · ADR-0029(add_manual)

---

## ADR-0040: Spark 실행 백엔드 + ExecutionBackend 추상화 전면 제거 (오케스트레이션 집중)

- **Date**: 2026-05-21
- **Status**: Accepted
- **Supersedes**: ADR-0031, ADR-0032
- **Context**:
  Asset/Lineage 축(A→D3, ADR-0036~0039)을 얹고 나서, 사용자가 다음 방향으로 **"pipeline 전면 개선 — 오케스트레이션 위주"**([[ultimate-vision]]: Airflow 오케스트레이션 + Dagster 리니지)를 지정했다. 이 맥락에서 Spark는 **분산 컴퓨트 엔진**으로 결이 다른 축이다:
  - 궁극 비전(Airflow+Dagster asset-centric)에 분산 컴퓨트는 들어있지 않다. 오케스트레이션·리니지가 차별점이지, 실행 엔진이 아니다.
  - ADR-0031이 도입한 **세 번째 실행 모델 분기**(`engine: local|spark`)가 linear/task-DAG(ADR-0028)/dataflow graph(ADR-0030)와 공존하며 실행 모델을 복잡하게 만들었다. 오케스트레이션 집중을 위해 단순화가 필요.
  - ADR-0032 P3.4b(외부 클러스터 잡 submit, thin worker)는 **Java+클러스터 환경이 없어 미구현으로 멈춰** 있었고, 번들 in-process(worker-as-driver) 모델만 검증됐다 — 끌고 가도 검증 불가한 죽은 코드.
  - 결합도 조사 결과 Spark는 lazy-import로 격리돼 있어 제거 blast radius가 작았다(코어 ~445줄 + 서비스 dispatch + 테스트, `local` 기본 경로는 pyspark를 import조차 안 함).
- **Decision**:
  1. **Spark 백엔드 삭제** — `etl_plugins/runtime/spark/`(backend.py/predicate.py) 전부 제거.
  2. **`ExecutionBackend` 추상화 삭제** — `etl_plugins/runtime/backends.py`(ABC + 레지스트리 + `LocalBackend` + `run_config` 디스패치) 제거. (사용자 선택: seam 유지가 아닌 **완전 제거** — local 단일 경로.) 실행은 `Pipeline.run`(in-process)이 유일.
  3. **`PipelineConfig.engine` 필드 삭제** — 단, `extra="forbid"`이므로 제거 전 저장된 config_json(P3.5 직렬화는 spark일 때만 `engine` emit)이 로드 실패하지 않도록 **`model_validator(mode="before")`로 legacy `engine` 키를 흘려보낸다**(값 무시).
  4. **서비스 정리** — 워커 `RunExecutor`/`RunWorker`/CLI의 `engine` 분기·`_run_spark`·`spark_master`·`--spark-master` 제거. `Dockerfile.worker`(Spark 워커 이미지) 삭제, `docker-compose.prod.yml`의 `etlx-spark-worker`(--profile spark) 제거. 양 pyproject의 `[spark]` extra + pyspark mypy override 제거.
  5. **웹 정리** — 빌더 엔진 select·Spark 경고 배너·`isSparkUnsupported`·`Engine` 타입·serialize의 `engine` emit·파이프라인 목록 Spark 배지·i18n `engine.*` 제거.
- **Consequences**:
  - (+) 실행 모델이 **로컬 인프로세스 단일 경로**로 단순화 — linear / task-DAG / dataflow graph만 남고, 모두 `Pipeline.run`. 오케스트레이션 개선의 깨끗한 출발점.
  - (+) JVM/pyspark 의존성·전용 워커 이미지·air-gapped JAR 운영 부담 소멸. CI/문서/이미지 단순화.
  - (−) TB급/분산 pushdown 기능 상실. 다시 필요하면 ADR-0031의 `ExecutionBackend` seam을 **새 ADR로 재도입**(과거 구현은 git 히스토리 + 본 ADR가 supersede한 0031/0032 참조).
  - (−) `engine: "spark"`로 저장된 기존 파이프라인은 이제 로컬로 실행된다(선언적 transform은 그대로 동작; Spark 전용 동작에 의존했다면 동작 차이 가능). v0.1.0 미릴리스·서비스 user-gated라 실사용 영향 최소.
- **관련**: ADR-0028(task-DAG) · ADR-0030(dataflow graph) · [[ultimate-vision]] · [[pipeline-overhaul-intent]]

---

## ADR-0041: 파이프라인 전면 개선 — graph-first 통합 DAG + 노드 머티리얼라이즈 + 노드 레벨 PG 스케줄링 + 커스텀 operator + 컬럼 리니지

- **Date**: 2026-05-21
- **Status**: Accepted (방향 합의 완료; 구현은 Phase G1부터 단계적)
- **Builds on**: ADR-0028(task-DAG) · ADR-0030(dataflow graph) · ADR-0021(PG worker queue) · ADR-0036(asset/lineage) · ADR-0040(Spark 제거 — 단순화된 출발점)
- **Context**:
  Spark 제거(ADR-0040)로 데크를 정리한 뒤, 사용자가 지정한 "pipeline 전면 개선 — 오케스트레이션 위주"([[ultimate-vision]]: Airflow + Dagster)의 본 작업. 요건: ①graph로 파이프라인 구성 전면화 ②강제 source/sink 아닌 자유 구성 ③커스텀 operator(커넥션 템플릿 + Python IDE) ④데이터 리니지 ⑤테이블+컬럼 리니지 ⑥부가기능 ⑦의존성/검증 점검. 현재 한계: dataflow graph(ADR-0030)가 **단일 source·트리 강제**(fan-in 금지)·batch 전용·cursor 미지원, linear/task-DAG/graph 3모델 공존, 리니지는 테이블 레벨·SQL 파싱은 정규식 첫 FROM뿐, 브라우저 코드 에디터 0, 병렬은 "1 run=파이프라인 전체" 단위.
- **Decision** (사용자와 fork별 합의):
  1. **단일 graph DAG로 모델 통합** — operator=노드, data 엣지(+ 선택적 trigger rule). linear/task-DAG/기존 graph는 normalizer로 흡수해 **하위호환** 유지(기존 config 그대로 로드/실행).
  2. **노드 머티리얼라이즈 실행** — 각 노드가 staging 타깃에 출력하고 downstream이 읽는다. fan-in/join/다중 source를 가능케 하고(메모리 바운드 스트리밍 깨짐 문제 해소), 노드별 retry·idempotency·리니지 경계가 자연스러워짐(Airflow/dbt식). 순수 인-메모리 스트리밍은 단일 노드 내부로 국한.
  3. **노드 레벨 PG 스케줄링(병렬)** — ADR-0021의 PG worker queue를 **노드 단위**로 확장(`node_runs` + depends_on, `FOR UPDATE SKIP LOCKED` 멀티 워커). 독립 노드는 워커들이 병렬 claim. **Celery/브로커 도입 안 함** — 이미 수평확장되는 PG 큐가 있고, Spark 제거로 단순화한 직후 브로커 추가는 역행. control plane(메타 Postgres) ↔ data plane(커넥터, 임의 DB)은 분리되어 큐 백엔드와 파이프라인이 만지는 DB는 무관.
  4. **커스텀 operator** — 커넥션 종류별 템플릿(이미 connector-schemas 존재) + 브라우저 Python IDE(Monaco/CodeMirror, FieldDef `pythonCode` 신설, 서버 `custom_python` operator). **보안: 신뢰 모델(인프로세스 실행) + RBAC 게이트(Editor+만 코드 작성, audit) + 단일 실행 seam**(`run_custom_transform`)으로 향후 샌드박스 교체 가능. 현 `python` transform이 이미 임의 코드 인프로세스 실행이라 위협 모델 불변. 멀티테넌트/외부 사용자로 트러스트 경계 바뀌면 샌드박스(subprocess/seccomp/gVisor) 필수 — seam에서 교체.
  5. **컬럼 리니지(하이브리드)** — 테이블 레벨(기존) 유지 + 컬럼 레벨 신설. **선언적 transform(rename/cast/select/drop/add_constant)이 컬럼 매핑을 이미 인코딩** → SQL 파싱 없이 공짜. SQL 노드는 **sqlglot**로 컬럼 의존 추출, `python`/임의 코드는 불투명 passthrough. 코어 컬럼 모델 + `asset_columns`/`column_lineage_edges` 스키마 + API + 웹 drill-down.
- **단계(각 = 단일 슬라이스 PR)**:
  - **G** graph-first 통합 코어: G1 통합 config 모델(트리 제약 해제·join 노드·normalizer, 검증만) → G2 노드 머티리얼라이즈 실행 엔진(위상 실행·hash join) → G3 stateful operator 추상화(N입력→1출력).
  - **H** 노드 레벨 오케스트레이션: H1 `node_runs` 스키마+repo → H2 스케줄러 노드 enqueue + 워커 노드 claim(병렬·retry/heartbeat/reaper 재사용) → H3 노드별 리니지 훅 + Run 상세 DAG 뷰.
  - **I** 빌더: I1 자유구성 캔버스(트리 제약 해제·join UI) → I2 커스텀 op + Python IDE + 서버 custom_python + RBAC/audit.
  - **J** 컬럼 리니지: J1 sqlglot + `derive_column_lineage` + 코어 모델 → J2 스키마+API → J3 웹 drill-down.
  - **K** 부가(#6, 추후 스코프): graph 백필(다중source cursor) · OpenLineage export(Marquez/DataHub) · 데이터 품질/assertion operator · 센서.
- **검증/리스크(요건 #7)**:
  - **메모리 모델**: join/fan-in은 머티리얼라이즈로 해결(스트리밍 보장은 노드 내부로 한정).
  - **하위호환**: 기존 linear/task-DAG/graph 파이프라인 무중단 — normalizer + 좁은 PoC 우선(ADR-0024 "모델 오설계 비용" 경고 준수).
  - **기존 ADR 상호작용**: 멱등성(ADR-0035, staging swap/MERGE와 정합) · 동일연결 데드락(ADR-0034, 다중 source/sink 증폭 주의) · cursor/backfill(ADR-0039, 다중 source 백필 의미는 Phase K) · stream 모드(graph는 batch, stream 적용은 후속).
  - **stateless 한계**: `TransformFn=Record→Record` → G3에서 N입력 operator로 확장.
  - **웹**: 신규 컴포넌트 Storybook story + a11y AA + DESIGN 토큰 강제. Monaco 번들 비용(에디터 라우트 first-load JS 예산).
  - **DB 카디널리티**: 컬럼 리니지 고카디널리티 → 인덱싱 설계.
- **Consequences**:
  - (+) 실행 모델이 진짜 DAG로 — 자유 구성·다중 source·join·노드 병렬. ultimate-vision(Airflow+Dagster)에 직결.
  - (+) 노드 머티리얼라이즈로 retry/리니지/관측이 노드 단위로 정밀해짐.
  - (−) 머티리얼라이즈는 staging I/O 비용 + 임시 객체 정리 필요. 순수 스트리밍 대비 오버헤드.
  - (−) 큰 변경 — 다슬라이스·다세션. 각 슬라이스 단위 PR + 테스트로 리스크 분산.
- **관련**: [[ultimate-vision]] · [[pipeline-overhaul-intent]] · [[dag-direction]]

---

## ADR-0042: 파이프라인 빌더 UX — 두 페르소나(데이터 엔지니어 + 비개발 분석가) 동시 만족

- **Status**: Accepted (2026-05-26)
- **Context**: 사용자 요청 — "3년차 데이터 엔지니어가 보는 UX 와 비개발 데이터 분석가가 보는 UX 양쪽에서 만족스럽게 보완해줘. 둘 다 만족이어야 함." K3 sensor framework + 4 빌트인까지 닫힌 직후, 빌더 본체의 UX 깊이가 두 페르소나 어느 쪽에도 충분하지 않은 상태. Explore 에이전트로 두 페르소나 별 잘된점/HIGH/MEDIUM 페인포인트/Nice-to-have + 공통/충돌 영역까지 분류해 transparent audit 결과 확보.
- **Decision**: 두 페르소나의 페인이 *대부분 중첩*된다는 audit 발견에 따라 **둘 다 만족시키는 9개 슬라이스 한 묶음** 구현. 충돌 영역 3개(빠른 액션 vs 안내 wizard / 에러 상세도 / default 노드 채움)는 **각각 sweet-spot 선택**으로 해결.
- **9 슬라이스 (모두 한 슬라이스로 묶음)**:
  1. **Undo/Redo** — `useGraphHistory` hook(100-snapshot, identity short-circuit) + Cmd+Z/Cmd+Shift+Z/Ctrl+Y. 헤더 ↶/↷ 버튼. 양 페르소나 공통 페인 — 실수 복구 불능.
  2. **실시간 validation 배너** — 저장 시점 → 매 edit. multi-issue 리스트, 노드 단위 issue 클릭 시 노드 포커스(`focusRequest: {nodeId, nonce}`). 분석가는 누락 즉시 알고, 엔지니어는 한 번에 모두 본다.
  3. **Edge 라벨 always-visible** — "All records" vs "if X" 항상 표시. 분기 기능 발견율 100%.
  4. **노드 incomplete UX 강화** — "Needs: connection, table"로 노드 카드에 누락 필드 명시, properties panel에 "Next: <field>" 배지 + 빈 required에 빨간 ring. 분석가가 "뭐 채워야 하나?" 즉시 인지.
  5. **다중선택 + bulk delete + duplicate-with-edges** — React Flow native multi-select(Shift/Meta marquee), 단일 commit으로 undo 폭주 방지, Cmd+D는 selection 내부 edge까지 함께 복제 + id remap. 엔지니어 power-user 핵심.
  6. **키보드 단축키 다이얼로그** — `?` 키로 cheat-sheet(mac=⌘, win=Ctrl 자동). 엔지니어=속도, 분석가=발견성.
  7. **Variables 타입 인지 에디터** — silent JSON fallback 폐기, type select + 명시적 검증 에러. 양쪽 공통 페인(`42`가 number인지 string인지 헷갈림).
  8. **Deleted-connection 배너** — load 시 참조 깨진 노드 감지 → 빨간 배너. 양쪽 공통 — 저장 직전에야 알게 되던 문제.
  9. **Glossary tooltips + Empty-canvas 안내** — SOURCE/SINK/TRANSFORM에 점선 underline + title 정의. 빈 캔버스 중앙에 "Drag a Source from the left palette". 분석가 핵심.
- **충돌 영역 sweet-spot**:
  - **빠른 액션 vs 안내**: 우클릭 컨텍스트 메뉴(엔지니어 power) + properties panel의 "Next: X" 배지/empty canvas hint(분석가 wizard)를 *동시 제공*. 분석가가 wizard만 따라가도 완성, 엔지니어는 우클릭/단축키로 즉시.
  - **에러 상세도**: 짧은 toast("2 issues to fix — see banner above") + 풍부한 inline 배너(클릭 가능 노드 포커스). 한 곳에 모든 정보 X, 두 layer로 정보 위계.
  - **default 노드 채움**: 적용 안 함 — 분석가가 "기본값으로 저장하면 잘못된 connection을 가리키게 됨" 위험이 더 큼. 대신 ④의 "Next: <field>" 배지로 안내.
- **Consequences**:
  - (+) 양 페르소나 페인 9개 동시 해소. 충돌 영역에서도 합의 가능한 sweet-spot.
  - (+) 신규 시각 컴포넌트 1개(ShortcutsDialog) — ConfirmDialog 토큰 재사용으로 DESIGN 부담 0.
  - (+) `validateGraphStructured` 분리로 server 검증 contract는 string[] 그대로 유지(backwards compat).
  - (−) 코드량 증가(`use-graph-history` 130 LOC + shortcuts-dialog 90 LOC + page 분기 +200 LOC). 빌더 페이지가 커지므로 향후 추출 후보(P10 잠재 슬라이스).
  - (−) Cmd+S / Cmd+D / Cmd+Z 같은 글로벌 단축키가 다른 페이지에 마운트되면 영향 줄 수 있음 — 모두 builder edit 페이지 컴포넌트 unmount 시 listener 해제(useEffect cleanup), guard로 editable element 보호.
- **결정 보류 (다음 슬라이스)**: 노드 그룹화(subgraph) · auto-layout(Cmd+L) · 노드 annotation · operator description의 video link.
- **관련**: [[ux-preferences]] · [[autonomous-progression]] · ADR-0018(DESIGN tokens)

### ADR-0042 follow-up (2026-05-26, 사용자 즉시 회신)

L1 출시 직후 사용자가 5개 회신:
1. 링크 라벨(All records / if X) 배경 투명 — `labelBgStyle.fillOpacity=0`로 그리드 위에 떠 있는 느낌.
2. Undo 가능 없으면 dirty=false — `useGraphHistory.index` 노출 + `savedGraphIndex` 추적으로 `dirty = metaDirty || (savedIdx !== currentIdx)`.
3. 각 operator 설명 i18n — `getOperatorLabel/Description(spec, t)` 헬퍼 + `op.<id>.{label,description}` 50+ 키 en/ko. caller 4개(palette, pipeline-node, properties-panel, graph-editor) 일괄 변환.
4. **SOURCE 시작 / SINK 끝 강제 제거** — 코어 `GraphConfig._check_graph`에서 "≥1 source / ≥1 sink" 검증 삭제 + 클라이언트 동일 완화. Standalone-source-only / sink-only / single-action 파이프라인 정당화. 구조적 per-node 규칙(transform/sink indegree=1, join ≥2)은 유지 — 짝 잃은 sink는 여전히 issue.
5. **Run SQL을 Source와 통합** — `sql_exec`를 6번째 `GRAPH_NODE_TYPE`으로 격상. 코어 `GraphNode.kind="sql_exec"` + `sql_statement` 필드 + `execute_graph_node` 분기(SqlExecutor capability 체크 + `execute_statement` 호출 + 0-record emit). 빌더 + 서버 connection resolver + 클라이언트 카탈로그(`source:sql_exec` 신설, `transform:sql_exec` 제거) + serializer/deserializer 일괄. **레거시 `transform: sql_exec` (ADR-0035 pre-load 패턴)은 코드 경로 유지** → 기존 파이프라인 무중단.

**Sweet-spot 결정 (#4 + #5 조합)**: ④로 source/sink 강제를 풀고 ⑤로 sql_exec을 진짜 standalone 으로 만들면, 두 변경이 자연스럽게 합쳐져 "single-node maintenance pipeline"(예: 야간 MERGE 1번 실행)이 가능해짐. 이전엔 placeholder source/sink를 끼워 넣어야 했음.

**호환성**:
- `transform: sql_exec` 경로(레거시) — 변경 없음, builder.py:194-208 그대로.
- `source: source` / `source: sink` 정상 그래프 — 변경 없음, validator는 이전과 동일하게 통과.
- `GraphNodeConfig`에 `statement` 옵셔널 필드 추가 — 기존 source/sink/transform/join/aggregate에 영향 없음(Pydantic이 무시).
- `serializeGraph`의 wireType 특별 케이스 — connectorType=="sql_exec"일 때만 발동, 나머지는 이전과 동일.

6 신규 코어 unit + 회귀 (코어 671 + 서버 410) green. mypy 코어 59 + 서버 99 OK.

---

## ADR-0043: SQL 컬럼 리니지 — `sqlglot.lineage` 기반 풀-AST 워커로 전면 교체

**Date**: 2026-05-28
**Status**: Accepted
**Context**: ADR-0041 Phase J에서 도입한 `_parse_select_columns`는 손으로 짠 FROM/JOIN 매처였다. `SELECT a, b FROM t` 같은 1:1 케이스만 풀고 JOIN/CTE/서브쿼리/UNION/윈도우/CASE는 전부 "sink opaque"로 떨궜다. 그 결과 카탈로그에 **컬럼 리니지가 가장 필요한 warehouse-to-warehouse 쿼리**(staging→marts 패턴)에서 정작 빈 그래프만 보였다. 사용자가 "어떤 쿼리든, 2천 줄짜리 monster여도, 정확하게 파싱"하라고 명시 요구.

**Decision**:
1. `etl_plugins/runtime/sql_lineage.py`(신규)에 `extract_sql_lineage(query, *, dialect=None, schema=None)` 도입.
   - 백엔드는 `sqlglot.lineage.lineage()` — sqlglot이 이미 CTE chain, 서브쿼리, JOIN(모든 종류), `UNION [ALL]`, `COALESCE`/`CASE`/산술, 윈도우 함수, `LATERAL`(Postgres)을 처리한다.
   - 각 출력 컬럼마다 lineage 트리를 펼친 뒤 leaf를 `(table, column)`으로 정규화. 한 컬럼이 여러 leaf를 갖는 것이 정상 케이스(`COALESCE(a.x, b.y)` → `[(a,x),(b,y)]`).
   - **Placeholder leaf 후처리**: sqlglot이 correlated/LATERAL 참조를 풀지 못해 `Placeholder`로 떨어뜨릴 때, 부모 AST에서 alias→table 맵을 구해 `o.amount` → `orders.amount`로 복구.
   - **Aggregate fallback**: `COUNT(*)` 같은 leaf는 `source=Select`로 끝난다(컬럼 참조 없음). 그 Select가 정확히 한 base table을 읽으면 그 테이블에 귀속(`_table_of_leaf` + `_expression_touches_table`). 순수 리터럴(`42 AS answer`)은 column/star가 expression에 없으므로 fallback이 꺼져 upstream 비어있음 유지.
   - **`SELECT *` 처리**: schema dict가 주어지면 `sqlglot.optimizer.qualify`로 먼저 확장해서 lineage 진행. schema 없으면 여전히 opaque(나중에 SchemaInspector 와이어업이 후속).
2. `etl_plugins/runtime/column_lineage.py`의 `_Mapping`을 `dict[str, tuple[ColumnRef, ...]]`로 widen.
   - 한 출력 컬럼에 여러 upstream을 그대로 보존. `ColumnEdge`는 원래부터 `upstreams: tuple[ColumnRef, ...]`였으니 데이터 모델은 변경 없음 — 런타임만 그 폭을 마침내 사용.
   - 트랜스폼 체인(`rename`/`select`/`drop`/`cast`/`add_constant`/`filter`/`dedupe`/`assert`)은 멀티-upstream을 손대지 않고 통과. `python`/`custom_python`/`sql_exec`/unknown은 종전대로 opaque.
3. 기존 테스트 2개(`test_join_marks_sink_opaque`, `test_complex_expression_keeps_column_drops_upstream`)가 새 동작과 모순 — 둘 다 옛 한계의 "주의 표시"였으므로 **새 정확한 결과**로 기대값 업데이트(JOIN은 정확한 per-table upstream, `UPPER(b)`는 `b`로 추적).
4. 새 테스트:
   - `tests/unit/runtime/test_sql_lineage.py`(25): 단순/별칭/함수/산술/3-way JOIN/COALESCE/체인 CTE/재귀 CTE/FROM 서브쿼리/correlated 서브쿼리/UNION/윈도우/CASE/LATERAL/`SELECT *` w·schema/`*` qualified/parse 에러/non-SELECT/empty + 모든 구성을 결합한 monster 쿼리.
   - `tests/unit/runtime/test_sql_lineage_stress.py`(4): 200줄 dbt-style 실전 쿼리(CTE 절단 회귀 가드 + 핵심 컬럼 raw table 매핑) + 100-layer chained CTE(2000줄+, <5s 수행) + 50-branch UNION ALL.
   - `tests/unit/runtime/test_column_lineage.py`(+3): JOIN 멀티-upstream / COALESCE 멀티-upstream / CTE chain.

**Consequences**:
- ✅ 카탈로그의 컬럼-레벨 lineage가 실전 SQL을 거의 모두 추적. JOIN/CTE/UNION/CASE/COALESCE/window가 정확히 표현됨.
- ✅ 데이터 모델(`tuple[ColumnRef, ...]`) 변경 없음 — DB 마이그레이션 없음. server의 `AssetRepository.persist_run_column_lineage`가 이미 `for up in edge.upstreams` 형태였기에 무중단.
- ✅ 100-layer CTE chain이 1.3s 이내 종료(Phase X 성능 회귀 가드).
- ⚠ `SELECT *`는 schema 없이는 여전히 opaque. SchemaInspector 연계는 후속 슬라이스(connectors의 `inspect_schema()` 결과를 lineage 호출에 inject).
- ⚠ AssetKey 추론은 source의 `connection`을 모든 leaf 테이블에 동일하게 적용. cross-connection JOIN(SQL이 federated query 등으로 표현)은 옵션 — 현재 정책은 "한 source.connection 내의 멀티 테이블"이라는 warehouse 일반 가정.
- ⚠ JOIN의 추가 upstream 테이블이 카탈로그에 자동 등록되어 있어야 edge가 만들어진다(`_asset_by_key`가 없으면 skip). 코어 `AssetLineage` emit 측에서 query의 모든 referenced 테이블을 inputs로 추가하는 별도 슬라이스가 다음 자연스러운 보완.

테스트 결과: 코어 unit 703 + server unit/it 429 + 25 신규 sql_lineage + 4 신규 stress + 3 신규 column_lineage 모두 green. mypy 코어 60 + 서버 100 OK. ruff clean.

---

## ADR-0044: Static lineage emit이 query의 모든 referenced 테이블을 input asset으로 등록

**Date**: 2026-05-28
**Status**: Accepted
**Context**: ADR-0043(Phase X)으로 컬럼 리니지는 JOIN의 양쪽 테이블을 multi-upstream으로 정확히 만들게 됐다. 하지만 asset 등록(`derive_lineage` → `AssetLineage.inputs`)은 여전히 `derive_asset_key`의 regex 파싱이 잡은 *첫 FROM 테이블 하나*만 input asset으로 등록했다. 그 결과 server의 `AssetRepository.persist_run_column_lineage`가 column edge를 만들 때 `_asset_by_key(workspace_id, up.asset)`이 두 번째 JOIN 테이블에 대해 `None`을 반환 → column edge가 그래프에 실제로 나타나지 않음. 컬럼-axis와 asset-axis가 어긋남.

**Decision**:
1. `etl_plugins/runtime/sql_lineage.py`에 `extract_referenced_tables(query, *, dialect=None) -> list[str]` 추가:
   - sqlglot AST 전체에서 `exp.Table` 노드를 모두 모음.
   - CTE 이름은 제외(CTE는 내부 alias이지 자산 아님).
   - 같은 테이블 dedupe하고 first-seen 순서 유지.
   - **fully-qualified 이름 반환**(`db.name` / `catalog.db.name`) — `derive_asset_key`의 regex가 dotted name을 그대로 가져오는 것과 일치시켜 `wh/public.orders`와 `wh/orders`가 두 row로 갈라지는 함정 회피.
2. `etl_plugins/runtime/lineage.py` `derive_lineage`가 source의 query가 있을 때 `extract_referenced_tables`로 추가 base 테이블을 모두 `inputs`에 등록 + 각 sink로 edge 생성:
   - linear path(`TaskConfig`): primary key + extras 모두 등록, 각 sink로 `edge(primary, sink)` + `edge(extra, sink)`.
   - graph path: `source` 타입 노드에 동일 로직. `src_keys` 리스트가 extras까지 포함하므로 기존의 "모든 source → 모든 sink" fan-out도 그대로 동작.
   - `derive_asset_key`의 primary key 추출은 변경 없음 — 기존 catalog row의 안정성 유지. 새 슬라이스는 **추가**만.
3. `sql_lineage.py`의 `_table_of_simple` / `_single_base_table` / `_build_alias_table_map`도 fq 이름을 사용하도록 통일 — column leaf와 asset key가 동일한 형태로 표현. 새 helper `_table_fq_name(tbl) → catalog.db.name | db.name | name`.
4. `_query_referenced_asset_keys(source_holder)` helper는 `TaskConfig | GraphNodeConfig`를 받아 source의 `connection`을 가지고 `AssetKey.of(connection, name)` 리스트를 만든다. 비-SQL source(Mongo collection name 등)는 sqlglot가 파싱하면 `extract_referenced_tables` 결과가 `[]` → 자동 fanout 없음.

**Consequences**:
- ✅ JOIN/CTE/UNION/correlated 서브쿼리의 모든 base 테이블이 카탈로그에 자동 등록. 새 e2e 테스트(`test_executor_persists_join_source_inputs_and_column_edges`)가 sqlite JOIN으로 검증.
- ✅ Phase X에서 만들어진 multi-upstream column edge가 실제 그래프 row를 만든다 — `_asset_by_key`가 두 번째 테이블에서도 hit. 컬럼-axis와 asset-axis 정합성 복원.
- ✅ Schema-qualified 이름(`public.orders`)이 두 row로 갈라지지 않음 — fq 이름을 column leaf와 asset extras 양쪽에서 동일하게 사용.
- ✅ DB 마이그레이션 없음. 기존 catalog row 변경 없음(추가만).
- ✅ 비-SQL source는 영향 없음(Mongo collection / Kafka topic / S3 key 등).
- ⚠ `derive_asset_key`의 primary key는 여전히 regex로 추출(behavioral 호환). 그래서 한 query에 *FROM 절이 없는* 비-SQL 문자열이면 primary key는 query 전체 문자열인데 extras는 `[]`라서 자연스럽게 단일 input — 의도된 동작.
- 🔜 다음 자연스러운 보완: `SELECT *`을 schema 없이 컬럼 enumerate 못하는 한계 — SchemaInspector(ADR-0033)를 lineage 호출에 inject하는 슬라이스로 마무리.

테스트 결과: 코어 unit 714(+11 신규: extract_referenced_tables 6 + derive_lineage 5) + server unit/it 430(+1 e2e JOIN 검증) green. mypy 코어 60 + 서버 100 OK. ruff clean.

---

## ADR-0045: 2000-쿼리 fuzz harness + SchemaInspector inject로 `SELECT *` 실행 시점 해결

**Date**: 2026-05-28
**Status**: Accepted
**Context**: ADR-0043/0044가 SQL 컬럼 리니지를 풀-AST로 끌어올렸지만, 사용자가 *"임의 생성 2000개 쿼리로 단계적 테스트하며 보완"* + *"SchemaInspector로 SELECT * 해결"* 요구. 두 작업이 자연스럽게 묶인다:
- Fuzz harness가 catch한 실제 미스를 보완하는 것이 신뢰성의 다음 단계.
- SELECT *는 결국 schema 없이 못 푸는 마지막 opaque 케이스 — `SchemaInspector`(ADR-0033)를 lineage 호출에 inject하면 완성.

**Decision**:

### Z1/Z2 — 2000-쿼리 fuzz harness
1. `tests/unit/runtime/sql_corpus_generator.py`(신규): 22 generator(L1 baseline → L2 predicate/grouping → L3 join → L4 cte → L5 subquery → L6 set op → L7 window → L8 conditional → L9 combined-real-world). 각 generator는 `GeneratedQuery(sql, expected, shape)`를 반환 — *합성 과정에서 ground-truth lineage를 동시에 build*. `Random(seed=42)` 고정으로 재현 가능.
2. `tests/unit/runtime/test_sql_lineage_fuzz.py`: 2002개 쿼리(22 × 91)를 round-robin으로 생성·실행, 22 shape별 pass/fail 통계 출력. exact equality vs containment-only는 shape별 분리(window/case/correlated 등은 sqlglot이 합당하게 추가하는 join key를 우리가 expected에 안 박으니까).
3. **첫 실행에서 발견된 회귀 (1820/2002 = 91%)**: `COUNT(*)`의 leaf 컬럼명이 projection alias(`cnt`)로 잘못 들어감. `_resolve_leaf`가 aggregate fallback에서 leaf의 outer name이 아닌 expression 내부 `exp.Column`/`exp.Star` 참조를 사용하도록 보완(`_column_from_expression(expr)`). 수정 후 **2002/2002 = 100%**, 모든 22 shape에서 100%.

### Z3 — SchemaInspector inject
1. **코어**: `derive_column_lineage(cfg, *, schemas=None)`에 옵셔널 `schemas: dict[connection_name, dict[table_name, dict[column_name, type]]]` 추가. `_initial_mapping` → `extract_sql_lineage(query, schema=schemas[connection])` 형태로 forward. 기본값은 종전대로 `None`(SELECT *는 opaque) — 모든 기존 호출 site 변경 없음.
2. **서버**: `RunExecutor._build_schemas_for_star_queries(session, run, version)` 신규 메소드:
   - PipelineConfig의 모든 source(linear/task-DAG/graph)를 훑어 `"*" in query`인 source의 connection + referenced 테이블을 수집(`extract_referenced_tables(query)` 활용).
   - `load_connections_by_name`로 connection row fetch, `ConnectionInspector.list_columns(connection, table)`로 schema fetch.
   - `InspectionUnsupportedError`(HTTP/Kafka 등)는 silent skip, per-table 실패는 log warning + 나머지 진행.
   - 결과 schemas dict을 `derive_column_lineage(cfg, schemas=...)`에 inject.
3. **best-effort**: schema lookup이 실패해도 column lineage derive는 정상 진행(opaque로 떨어질 뿐). run을 fail로 뒤집지 않음(기존 Phase J2 정책 유지).

**Consequences**:
- ✅ **2002/2002 fuzz pass rate** — 사용자 요구 "2천 줄 monster까지 어떤 유형이든 완벽 파싱"에 정량적 답변. 22 shape마다 100% 보장.
- ✅ **`SELECT *` no longer opaque**: SQL-introspectable connector(postgres/mysql/sqlite)의 source는 star projection도 실제 컬럼으로 확장. 카탈로그 column lineage 그래프의 마지막 빈틈 해소.
- ✅ **회귀 없음**: 기존 715 코어 단위 + 430 서버 it + 2002 fuzz + 새 26 column_lineage(+3) + 새 e2e(+1) 전부 green.
- ✅ schema fetch는 query에 `*`가 *실제로* 들어 있을 때만 발동 — 일반 쿼리는 overhead 0.
- ⚠ schema dict format은 sqlglot의 `qualify` 입력과 일치(`{table: {col: type}}`) — 다른 lineage 백엔드로 바꾸면 어댑터 필요.
- ⚠ SchemaInspector를 구현하지 않는 connector(HTTP/Kafka/S3)의 `*`는 여전히 opaque — 그쪽은 schema 개념 자체가 다르니 의도된 동작.

테스트 결과: 코어 unit 718(+3 신규: SELECT * w/schema 케이스) + server unit/it 431(+1 e2e SELECT * 검증) + 2002 fuzz green. mypy 코어 60 + 서버 100 OK. ruff clean.

---

## ADR-0046: Schema-passthrough fallback — 어떤 트랜스폼이든 카탈로그 column edge 자동 생성

**Date**: 2026-05-29
**Status**: Accepted
**Context**: 사용자 *"어떤 소스던 간에 결국 소스/타겟이 있잖아. 난 자동생성하고 싶은데 전부"*. 현황 점검:
- **Asset row** (source/sink 등록): 트랜스폼과 무관하게 이미 자동 (`derive_lineage`).
- **Asset edge** (source → sink): 트랜스폼과 무관하게 이미 자동.
- **Column lineage**: `python` / `custom_python` / `sql_exec` 트랜스폼이 끼면 sink가 `opaque_assets`로 떨어져 column 매핑 없음.

즉 카탈로그 자체는 잘 만들어지는데, *column 단위*가 트랜스폼 종류에 따라 비어 있다는 게 사용자가 본 문제. 사용자 코드의 정적 분석은 본질적으로 불가능하지만, 실전 python transform의 압도적 다수는 **컬럼 이름을 유지**하면서 값만 변형(필터·정규화·플래그 추가). 이 사실을 활용해 보수적 휴리스틱으로 column edge를 자동 생성.

**Decision**:
1. **`RunExecutor._augment_opaque_with_schema_passthrough`(신규)** — `_persist_column_lineage`에서 1차 derive 후 opaque sink가 남아 있으면 발동.
2. **알고리즘** (per opaque sink):
   - `derive_lineage(cfg)`로 source→sink edge 맵 확보(asset axis에서 이미 정확).
   - sink + 모든 upstream source의 schema fetch (`ConnectionInspector.list_columns` — Phase Z의 `_build_schemas_for_star_queries`가 이미 seed로 가져온 schemas는 재사용, 모자란 것만 추가 fetch).
   - **passthrough 규칙**: sink schema column name ∩ source schema column name의 각 컬럼에 대해 1:1 `ColumnEdge(downstream=sink_key.col, upstreams=(src_key.col,))` 생성.
   - 여러 source가 같은 이름 컬럼을 가지면(JOIN 등) 멀티-upstream으로 모두 포함.
3. **안전 가정**:
   - 컬럼명이 **동일**한 경우만 attribute. 이름이 다르면 mapping 없음 — 부정확한 추측 안 함.
   - sink가 SchemaInspector를 구현 안 하는 connector(Kafka/HTTP)면 schema fetch 실패 → 여전히 opaque(=정직).
   - 정확히 일치하지 않는 컬럼(python이 새 컬럼 추가)은 sink 측에 mapping 없는 row로 남음 — 기존 `add_constant` 케이스와 동일 의미(컬럼 존재, upstream unknown).
4. **best-effort**: passthrough 자체가 throw해도 run은 fail 안 되고 원래 opaque 결과를 유지. log warning.
5. **호출 위치**: `derive_column_lineage` 결과 받은 직후. opaque 없으면 no-op (overhead 0).

**Consequences**:
- ✅ **카탈로그 column lineage 자동 생성률 대폭 향상**: python/custom_python/sql_exec 트랜스폼을 거치는 파이프라인도, 컬럼명이 보존되는 한(실전 패턴 다수) 자동 column edge.
- ✅ **Source/sink schema 모두 사용 가능한 connector**(postgres/mysql/sqlite ↔ 같은 connector 부류) 사이에서 사실상 항상 column lineage 채움.
- ✅ **거짓 양성 없음**: 컬럼명 일치만 사용 → "python transform이 a를 b로 rename"같은 케이스는 자동 못 잡지만 false edge도 안 만듦.
- ✅ Asset axis는 종전대로 무조건 자동 — 트랜스폼 무관.
- ⚠ **본질적 opaque 잔존**: HTTP/Kafka sink(schema 없음), source/sink 컬럼명 완전 disjoint, schema fetch 실패. 이런 케이스는 여전히 column_lineage_opaque=True (정직한 상태 표시).
- ⚠ python이 의미적으로 컬럼 값을 크게 바꿔도(e.g. `email` → 해시) 우리는 같은 이름이면 passthrough로 간주. lineage는 "structural dependency" 의미라 보통 문제 없으나, semantic-aware lineage가 필요한 별도 use case가 있다면 사용자 명시 마커가 필요.
- ✅ DB 마이그레이션 없음. 기존 `column_lineage_opaque` 플래그 + ColumnEdge 모델 그대로.

**검증**: 코어 unit 718 (변경 없음 — 서버 측만 변경) + 서버 unit/it 431 → 432(+1 e2e: sqlite source + `custom_python` transform + sqlite sink → `column_lineage_opaque=False` + `id`/`name` 자동 1:1 edge). mypy 코어 60 + 서버 100 OK. ruff clean.

**Phase BB 보완 (2026-05-29 같은 날 dogfooding 발견)**: 4-stage 복잡 시나리오로 검증하다가 `_augment_opaque_with_schema_passthrough`가 **sink-only 컬럼**(python이 새로 추가한 컬럼, e.g. `tier`)에 대해 column row 자체를 안 만들던 사용성 미스 발견. 카탈로그에서 sink 테이블을 보면 sink schema에 있는 컬럼이 다 보이는 게 자연스러움. 보완: passthrough fallback이 sink schema의 *모든* 컬럼에 대해 column row 생성, intersection 컬럼은 upstream attribute + sink-only 컬럼은 빈 upstream 튜플(기존 `add_constant`와 동일 의미). 이로써 사용자가 카탈로그에서 sink 테이블 클릭하면 실제 물리 schema와 일치하는 컬럼 리스트를 본다.

---

## ADR-0047: 사용자-명시 `column_mapping` — 트랜스폼에 직접 의도 박기

**Date**: 2026-05-29
**Status**: Accepted
**Context**: ADR-0046(Phase AA) schema-passthrough fallback이 컬럼명 *보존* python 트랜스폼은 잘 잡지만, **컬럼명을 바꾸는** 패턴(`name` → `display_name`)이나 **a→b 데이터 이동** 같은 의도는 catch 못 한다. 정적 분석은 본질적 한계. 사용자가 정확한 카탈로그를 원할 때 빠져나갈 길이 필요.

**Decision**:
1. **트랜스폼 config의 `column_mapping` 옵셔널 필드**(`extra="allow"` 활용, 모델 변경 0):
   ```yaml
   transforms:
     - type: custom_python
       code: |
         def transform(record):
             d = dict(record.data)
             d['display_name'] = d.pop('name')
             return record.__class__(data=d, ...)
       column_mapping:
         id: [id]
         display_name: [name]   # 핵심: 명시적 a→b
   ```
   `{output_col: [source_col, ...]}` — source_col은 현재 `_Mapping`의 키. 빈 리스트 = 새 컬럼(no upstream, `add_constant`와 동일 의미).
2. **`column_lineage._apply_transform`이 type 분기 *전에* `column_mapping` 우선 처리**:
   - 모든 트랜스폼 타입에 적용 가능(rename/cast/python/custom_python/sql_exec 등). 정적 분석으로 정확한 declarative transforms에서도 사용자가 override하려면 박을 수 있음.
   - `_apply_explicit_column_mapping(mapping, declaration)`: dict이 아닌 malformed declaration → `None` 반환 시그널, caller가 type 기반 처리로 fallback.
3. **Replace 모드 (merge 아님)**: 명시한 출력 컬럼만 새 `_Mapping`에 들어감. 명시 안 한 컬럼은 흘러나가지 않음.
   - **Rationale**: merge 모드(명시 안 한 컬럼은 옛 mapping에서 흘러옴)는 사용자가 python 안에서 컬럼 추가/제거를 변경할 때 catalog가 stale해진다. Replace는 deterministic + 사용자가 의도를 정확히 기술.
   - **Trade-off**: verbose — 사용자가 모든 출력 컬럼을 나열해야 함. 다만 declaration은 정적 분석 불가능한 트랜스폼에만 박는 거니 보통 부담 크지 않음.
4. **Worker fallback과 상호작용**: `column_mapping`이 있으면 sink가 opaque가 되지 않음 → Phase AA의 schema-passthrough fallback 발동 안 함. 둘 다 한꺼번에 안 발동. 사용자가 책임 가져가는 모드.
5. **Sink-only 컬럼 누락**: 사용자가 명시 안 한 sink 컬럼은 catalog에 없음. 명시적이지만 사용자 부담 있음. 향후 옵션으로 `column_mapping_mode: replace | merge | augment_with_passthrough` 같은 토글 가능.

**Consequences**:
- ✅ **컬럼명 rename 같은 정적 분석 불가 패턴 100% 정확** — 사용자가 의도 박으면 카탈로그가 그대로 따라감.
- ✅ **Model 변경 0**: `TransformConfig.model_config = ConfigDict(extra="allow")` 덕분에 `column_mapping` 필드를 임의 트랜스폼에 박을 수 있음. DB 마이그레이션 0.
- ✅ **거짓 양성 0**: 사용자 의도만 따르므로 잘못된 매핑 자동 생성 안 함.
- ✅ **모든 트랜스폼 타입 적용**: declarative(`rename`/`cast`)도 override 가능 — 빌더에서 자동 추정한 매핑이 의도와 다르면 사용자가 명시.
- ⚠ **Replace 모드 verbose**: 사용자가 모든 출력 컬럼 나열 책임. 명시 안 한 컬럼은 catalog에서 누락. 빌더 UI에서 "기존 컬럼 자동 포함" 도우미 가능(향후).
- ⚠ **Type 분기 우선순위**: `column_mapping`이 declarative transforms(`rename` 등)에도 적용되니, 사용자가 의도치 않게 `rename` 트랜스폼에 `column_mapping`을 박으면 declarative 동작이 무효화됨. 빌더 UI에서 한 트랜스폼당 둘 중 하나만 노출하면 회피.

**검증**: 코어 unit 718 → 723(+5 신규): override python opaque / multi-source union / replace-mode 검증 / unknown source col → empty upstream / malformed declaration fall-through. 서버 it 433 → 434(+1 e2e: custom_python rename + column_mapping → catalog 정확). mypy 코어 60 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0048: Pipeline lint — dry-run에서 column_mapping 권장 등 advisory warning 노출

**Date**: 2026-05-29
**Status**: Accepted
**Context**: ADR-0047(Phase CC)이 사용자-명시 `column_mapping`을 도입했지만 *opt-in*. 사용자가 그 escape hatch를 *알아야* 채택률이 올라간다. python/custom_python/sql_exec 트랜스폼을 만들 때 "column_mapping을 박을 수 있다"는 사실을 자동으로 안내하면, 카탈로그 정확도가 자연스럽게 상승. Dry-run은 이미 사용자가 "이 파이프라인 실행 가능한가?" 확인하는 자연 통과 지점이라 lint 결과를 같이 노출하기 좋은 위치.

**Decision**:
1. **`etl_plugins/runtime/lint.py`(신규) `lint_pipeline(cfg) -> list[LintWarning]`** — pure function, 코어에 둠(라이브러리 단독 사용자도 활용 가능).
   - `LintWarning(code, message, location)` — `code`는 stable identifier(UI 필터/dismissal 키), `message`는 영어(i18n은 UI 책임), `location`은 `tasks.0.transforms.2` 같은 path(builder가 jump).
   - 모든 shape 순회: single-task / task-DAG / graph. `derive_column_lineage`의 walker와 동일 패턴.
2. **첫 규칙 `column_mapping_recommended`**:
   - `_OPAQUE_TRANSFORM_TYPES = {"python", "custom_python", "sql_exec"}` 중 하나면서 `column_mapping`이 없으면 발동.
   - Message: schema-passthrough fallback이 시도되지만 rename/이동은 못 잡으니 정확한 lineage를 원하면 `column_mapping` 추가하라는 권장.
3. **`DryRunResult.warnings: list[LintWarning]` 필드 추가** — 모든 dry-run return path에서 `lint_pipeline(pipeline_cfg)` 호출해 채움. **Advisory**: warnings만 있으면 `ok=True` 유지. 차단 안 함.
4. **`DryRunResponse` Pydantic schema에 `warnings: list[DryRunLintWarning]` 추가**, router가 dataclass → Pydantic 변환.
5. **하위 호환**: 기존 응답에 `warnings` 필드만 추가, 기존 필드 변경 0. 옛 클라이언트가 무시해도 동작.

**Consequences**:
- ✅ **column_mapping 채택률 향상 path**: 사용자가 python transform 만들 때 dry-run하면 즉시 "column_mapping 추가 권장" 안내. 자연스러운 nudge.
- ✅ **Lint extensibility**: 새 규칙은 `_lint_transform`에 helper 추가만으로 확장 가능. 예: `sink_table_missing_in_schema_inspector`, `python_imports_external_package` 등 향후 슬라이스.
- ✅ **Advisory 정책**: warnings이 운영을 막지 않음 — 사용자가 "지금 당장 실행"하고 싶으면 그대로 가능. 정확도는 trade-off.
- ✅ DB 마이그레이션 0. 응답 필드 추가만.
- ⚠ **Builder UI 통합은 별개 슬라이스**: 현재는 dry-run 응답만 노출. 빌더에서 properties panel에 warning 표시 + 인라인 fix 액션(column_mapping editor)은 후속.

**검증**: 코어 unit 723→732(+9 신규: rule fires/missing-mapping / mapping present 무 lint / 모든 트랜스폼 타입(python/custom_python/sql_exec) / declarative 무 lint / 멀티 opaque → 멀티 warning / task-DAG 순회 / graph 순회 / empty pipeline). 서버 it 434→436(+2 e2e: opaque transform → warning 노출 / column_mapping 박힌 동일 shape → 무 lint + happy-path에 `warnings==[]` 체크 추가). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0049: 확장 dogfood 시나리오 corpus — 실전 패턴 5종 sample-data e2e

**Date**: 2026-05-29
**Status**: Accepted
**Context**: 사용자 요청 *"테스트를 중점적으로 해서 항상 샘플 데이터를 생성해서 기능이 정상적인지 좀 깊게 테스트"*. Phase BB의 4-stage e-commerce는 headline shapes를 한 번 통과시켰지만, 실전 warehouse가 매일 쓰는 다양한 패턴(SCD/Fan-out/Aggregation chain/Self-join/Schema evolution)에서 모든 Phase X/Z/AA/CC 메커니즘이 정상 동작하는지 깊게 확인 필요.

**Decision**:
1. **`services/etlx-server/tests/db/test_extended_workflow_scenarios.py`(신규)** — 실전 warehouse 패턴 5종 e2e:
   - **Scenario A: SCD Type 2 dimension** — `customers_history`에 `effective_from`(alias→raw col) + `effective_to`/`is_current`(리터럴) 추가. literal 컬럼이 정확히 empty upstream으로 catalog에 표시되는지 검증.
   - **Scenario B: Fan-out by status** — `raw_orders` → `confirmed_orders` + `cancelled_orders` 두 sink로 `when` 술어 routing(ADR-0027). 양쪽 sink 모두 asset edge + per-column lineage 정확.
   - **Scenario C: Aggregation chain** — 3-stage `raw_sales` → `daily_sales` → `monthly_sales`. `SUM(amount)` GROUP BY가 정확히 base table column에 attribute. CTE 거치지 않고 직접 GROUP BY인 경우도 정확.
   - **Scenario D: Self-join (employee hierarchy)** — 같은 `employees` 테이블의 두 alias(`e`/`m`)가 모두 단일 base asset으로 매핑. `manager_name`도 `employees.name`으로 trace.
   - **Scenario E: Schema evolution** — sink 테이블에 `ALTER TABLE ADD COLUMN` 후 재실행 → passthrough fallback이 새 컬럼을 empty upstream으로 자동 등록(Phase BB sink-only column 보완 검증).
2. **각 시나리오의 검증 깊이** (3-layer):
   - **Record-level**: 실제 sink 테이블 SELECT으로 row 수 + 값 비교.
   - **Asset-level**: 모든 asset row 등록 + asset DAG edge 정확.
   - **Column-level**: per-column upstream attribution이 base table까지 정확.
3. **샘플 데이터 패턴**: 5-20 rows per 시나리오 — failure 시 reasonable counter-example을 print, 빠른 디버깅.
4. **단일 sqlite 파일로 src + dst connection 양쪽 운영** — 한 시나리오 내에서 cross-pipeline asset dedup도 자연 검증.

**Consequences**:
- ✅ **5종 실전 패턴이 정확히 동작 확인됨**: SCD Type 2 / Fan-out / Aggregation chain / Self-join / Schema evolution.
- ✅ **모든 Phase X/Z/AA/CC 메커니즘 깊은 검증**: literal column trace / 다중 sink edge / GROUP BY chain / alias resolver / sink-only column emission 등 각 메커니즘이 실전에서 동작.
- ✅ **Record-level 정확성 확인**: 단지 lineage만이 아니라 실제 데이터 transformation도 sample data로 검증 — "lineage가 맞다고 표시되는데 실제론 데이터가 빠짐" 같은 silent bug 방지.
- ✅ **회귀 가드**: 향후 column_lineage 코드 변경 시 5개 시나리오 중 어느 하나라도 깨지면 즉시 catch.
- ✅ DB 마이그레이션 0. 신규 테스트 파일만.
- ⚠ **Fan-out 시 sink schema에 routing-key column 보존 필요**: `when: data['status']==...`가 row를 보려면 sink schema에 status 포함. 실전에선 routing 후 `drop` transform 추가하는 패턴 흔함(시나리오는 lineage 검증에 집중하고자 단순화).
- ⚠ **Cross-connection asset row 분리**: `src/raw_sales`와 `src/daily_sales` 같은 read-side asset이 sink connection과 별도 row로 등록. 카탈로그가 의도된 동작이며 cross-pipeline lineage가 정확히 연결됨(Aggregation chain에서 검증).

**Dogfooding 발견 1개 (시나리오 B)**: 처음에 sink schema가 source projection의 일부만 가지면 sqlite append sink가 "no column named status" 실패. 실전 패턴 반영해서 sink schema에 routing key 포함하도록 시나리오 보강. 사용자 측 best practice 노트로 코멘트 남김.

**검증**: 코어 unit 732 (변경 없음 — 서버 측 신규 테스트만). 서버 it 436→441(+5 e2e 시나리오). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0050: 추가 lint 규칙 `column_mapping_unknown_source_column` + 실패/재시도 시나리오 검증

**Date**: 2026-05-29
**Status**: Accepted
**Context**: Phase EE에서 정상 경로의 5종 패턴을 검증했지만, 신뢰성의 또 다른 차원은 *(a) 사용자 실수 catch* + *(b) 비정상 경로(실패/재시도)에서도 카탈로그 일관성*. 둘 다 사용자 부담 줄이는 핵심:
- (a): `column_mapping`에 typo가 있으면 사용자가 본인 declaration이 잘못된 줄 모름. 카탈로그가 silent empty upstream → 디버깅 어려움.
- (b): 실패한 run이 카탈로그를 dirty하게 하면 운영자가 "정말 성공한 자산"과 "마지막 실패 자산"을 구분 못 함. 재시도가 row 중복하면 long-tail 통계 깨짐.

**Decision**:
1. **새 lint 규칙 `column_mapping_unknown_source_column`** (`etl_plugins/runtime/lint.py`):
   - `_lint_column_mapping_consistency(cfg)`: linear / task-DAG 경로에서 transform chain을 `derive_column_lineage`와 동일하게 walk. 각 transform의 `column_mapping`에서 declaration된 source col이 그 시점의 upstream `_Mapping` 키에 *없으면* warning.
   - 이전 transform의 rename도 정확히 반영(예: `a → id` rename 후 `column_mapping`이 `["a"]`를 참조하면 warning).
   - 새 컬럼 `output: []`은 source col 검증 대상 아님(empty list).
   - Graph shape는 후속 슬라이스(multi-source chain의 "upstream mapping" 의미가 별도 결정 필요).
2. **시나리오 F: Failed run leaves catalog untouched** — `custom_python`이 명시적 raise → run status=failed → worker의 except branch가 `_persist_lineage` 건너뜀 → sink/source asset row 생성 안 됨. 정상 동작 확인.
3. **시나리오 G: Rerun idempotency** — 같은 config 2회 실행 → asset row count 변화 없음, 같은 row(id) 유지, asset edge 중복 없음, column lineage edge 중복 없음. `AssetMaterialization`만 run마다 1행씩 증가(audit trail 의도).

**Consequences**:
- ✅ **사용자 실수 자동 catch**: column_mapping typo / stale 참조가 dry-run에서 즉시 안내. silent 부정확 mapping 회피.
- ✅ **Catalog cleanliness 정책 명문화**: 실패 run = 카탈로그 무변화 / 성공 run = idempotent upsert + 매번 materialization 추가. 사용자가 카탈로그를 신뢰 가능.
- ✅ **Phase EE 시나리오 corpus 7종으로 확장**: 정상 5 + 실패 + 재시도. 비정상 경로 회귀 가드.
- ✅ DB 마이그레이션 0. lint는 pure config 분석, 시나리오는 신규 테스트만.
- ⚠ **Graph shape lint 미지원**: multi-source chain의 "upstream mapping at a transform node" 의미가 별도 결정 필요. linear/task-DAG에서 80%+ 케이스 cover.
- ⚠ **재시도 정책 확장 시**: 만약 향후 partial success(일부 record만 sink로) 지원하면 catalog 정합성 정책 재검토 필요. 현재는 all-or-nothing batch.
- 🔧 **기존 e2e 수정 1건**: `test_dry_run_no_lint_warning_when_column_mapping_declared`가 `select 1` source에서 `id` 컬럼을 참조해 새 lint가 발동 → source query에 `select 1 as id` 추가하고 assertion을 narrow(특정 code 두 가지에 대해서만 단언). 후속 lint 규칙 추가가 깨지지 않도록.

**검증**: 코어 unit 732→737(+5: lint 규칙 typo 발동 / 정상 mapping 무 warning / rename 후 typo / empty list 무 warning / graph shape skip). 서버 it 441→443(+2: 실패 시 카탈로그 untouched / rerun idempotent). 기존 e2e 1개 보강(narrow assertion). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0051: Auto-materialize 시나리오 corpus — chain/fan-out/실패 차단 깊은 검증

**Date**: 2026-05-29
**Status**: Accepted
**Context**: ADR-0037의 D1 메커니즘(asset-driven auto-materialize)은 unit으로는 잘 cover되지만, *interplay*(catalog 정합성 + chain 동작 + 실패 차단)는 dogfooding이 더 catch한다. 사용자 운영 신뢰성에 직결되는 영역. 핵심 질문 3가지:
1. **체인이 정확히 동작?** Pipeline A 성공 → B 자동 trigger → B 성공 → catalog에 전체 chain 표시?
2. **다중 consumer fan-out?** A 출력이 B + C 둘 다의 input인 경우 양쪽 다 trigger?
3. **실패가 chain을 차단?** A 실패 → B 큐에 안 들어감 → catalog dirty 안 됨?

**Decision**:
1. **`services/etlx-server/tests/db/test_auto_materialize_scenarios.py`(신규)**: 3 시나리오 e2e:
   - **GG1 — Happy chain (A → B)**: A가 `staging` 작성, B(`auto_materialize: true`)가 `staging` 읽고 `mart` 작성. `_drain_pending_runs` helper로 큐를 끝까지 비움. 검증: A + B 각각 1 run, B의 `result_json.triggered_by_run = A.id`, catalog에 raw → staging → mart edge 전체 형성.
   - **GG2 — Fan-out (A → B + C)**: B/C 둘 다 같은 `dst/shared`를 source로 + 둘 다 `auto_materialize`. 검증: 1 A run + 2 자동 consumer run. 둘 다 trigger_by_run stamped, catalog의 `dst/shared` downstream에 mart_b + mart_c 모두 있음.
   - **GG3 — Failure blocks chain**: A의 `custom_python`이 raise → A fails. 검증: 1 A run(fails), B는 0 runs, sink/mart 비어 있음, catalog에 `dst/staging`/`dst/mart` 둘 다 없음(Phase FF의 시나리오 F와 일관).
2. **3-layer 검증 동일 적용**: record-level(sink 값) + asset-level(catalog edge) + run-level(`result_json` trigger lineage).
3. **`_drain_pending_runs` helper**: claim → execute 반복으로 자동 enqueue된 후속 run까지 한 step에 처리. 운영 시나리오와 같음(worker가 무한 loop).
4. **Asset key 디자인 dogfooding**: `dst/staging`이 A의 sink이자 B의 source. `derive_asset_key`가 `connection/table`로 키하기 때문에 cross-connection chain이 안 자연스러움. 시나리오 코드에 명시적 설명 코멘트 — 사용자 best practice 노트.

**Consequences**:
- ✅ **Auto-materialize chain 정확성 확인**: 3 패턴 모두 카탈로그가 정확하게 chain 표시, run lineage 정확.
- ✅ **Phase FF의 실패 정책과 일관**: 실패 chain은 카탈로그 dirty 안 함 + 다음 consumer trigger 안 함. 운영자가 "어디까지 성공"을 명확히 본다.
- ✅ **Asset key 의미 명문화**: `connection/table` 디자인 결정이 chain 시나리오에서 어떻게 표면화되는지 코멘트로 기록. 사용자가 chain pipeline 만들 때 connection을 어디 가리킬지 가이드.
- ✅ DB 마이그레이션 0. 신규 테스트만.
- ⚠ **Cycle 방지 / depth cap 시나리오 미포함**: `_trigger_asset_consumers`가 `trigger_chain` + `_MAX_TRIGGER_CHAIN`으로 처리하지만 unit test가 이미 cover. e2e는 옵션(향후 슬라이스).
- ⚠ **Multi-workspace 격리는 별도 슬라이스**: chain trigger가 workspace 안에서만 일어나는 건 `_trigger_asset_consumers` 쿼리에서 `workspace_id` 필터로 보장. 별도 시나리오로 e2e 검증 가능.

**검증**: 코어 unit 737 unchanged(서버 측 신규 시나리오만). 서버 it 443→446(+3 시나리오). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0052: Multi-workspace 격리 시나리오 — 멀티 테넌트 신뢰성 깊은 검증

**Date**: 2026-05-29
**Status**: Accepted
**Context**: 워커의 `_trigger_asset_consumers`와 `AssetRepository.list_for_workspace`는 둘 다 `workspace_id` 필터를 갖고 있지만, **현실 멀티 테넌트** 상황(매 고객마다 `"src"`/`"dst"` 같은 connection 이름 재사용 + `"orders"` 같은 흔한 테이블 이름)에서 *진짜로* 격리되는지 sample-data e2e로 확인 안 됨. 신뢰성 차원에서 핵심 질문 2가지:
1. **Auto-materialize trigger가 workspace 경계를 안 넘는가?** ws_a의 producer가 ws_b의 consumer에 영향 주지 않음?
2. **Catalog row가 workspace 격리되는가?** 동일 `asset_key` 문자열(예: `"dst/staging"`)이 양쪽 ws에 있어도 각각 다른 row?

**Decision**:
1. **`test_multi_workspace_isolation_scenarios.py`(신규)**: 2 시나리오 e2e:
   - **HH1 — Auto-materialize는 workspace-scoped**: 두 워크스페이스가 같은 connection name(`"src"`/`"dst"`) + 같은 table name(`raw`/`staging`/`mart`)을 쓰지만 각자 다른 sqlite 파일(per-tenant data). ws_a의 producer만 큐 → drain 후 정확히 ws_a producer + ws_a consumer 2 runs. ws_b는 0 runs. Data-plane(sqlite 파일)도 격리 확인.
   - **HH2 — Catalog row가 workspace-scoped**: 두 ws 양쪽에 같은 cfg로 pipeline run. `list_for_workspace(ws_a)` vs `list_for_workspace(ws_b)`이 같은 `asset_key` 문자열을 반환하지만 **row id가 다름**. `upstream` 조회가 자기 ws의 row만 반환 (disjoint id 집합 검증).
2. **3-layer 검증**: data(sqlite)/run(Run rows)/catalog(AssetRepository) 모두에서 격리.
3. **테스트 helper `_seed_two_ws_warehouse`**: 같은 schema의 두 sqlite 파일을 서로 다른 sample rows로 시드 → consumer가 *자기* data만 처리했는지 확인 가능.

**Consequences**:
- ✅ **멀티 테넌트 안전성 확인**: 같은 connection/table 이름이 두 ws에 공존해도 carrying-edge crossing 없음. SaaS 운영의 핵심 신뢰성.
- ✅ **Catalog의 워크스페이스 분리 보장**: `asset_key` 문자열이 동일해도 각 ws가 독립 row를 보유 — UI에서 워크스페이스 전환 시 다른 사용자의 catalog가 새지 않음.
- ✅ **Phase GG의 auto-materialize chain 시나리오와 일관**: chain은 workspace 안에서만 동작하는 게 정책상 보장 + e2e 검증.
- ✅ DB 마이그레이션 0. 신규 테스트만.
- ⚠ **DB-level row-level security 없음**: workspace_id 필터는 application layer(쿼리). DB role/RLS를 PostgreSQL에 따로 적용한다면 별도 슬라이스. 현재는 코드 신뢰 모델.
- ⚠ **Connection secret 공유 가정 없음**: 두 워크스페이스가 같은 connection name을 써도 각자의 `Connection.config_json`은 독립. 사용자가 한 ws의 secret을 다른 ws에서 읽을 수 없음(Connection table은 `workspace_id` PK + FK).

**검증**: 코어 unit 737 unchanged. 서버 it 446→448(+2 시나리오). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0053: DLQ 시나리오 검증 + 두 버그 보완(table forwarding / catalog 등록)

**Date**: 2026-05-29
**Status**: Accepted
**Context**: 사용자 요청 "sample 데이터로 깊게". DLQ는 record-level partial-success의 핵심 운영 패턴이지만 실전 시나리오 e2e 검증이 없었음. dogfood 시나리오 작성하다가 *두 개의 사용성 미스* 동시 발견:
1. **DLQ가 silent fail**: `Pipeline._dlq_route_batch`가 `sink.write([record], mode=...)`만 호출, `table` kwarg 전달 안 함. sqlite BatchSink는 `WriteError("requires 'table'")` 발생 → `contextlib.suppress(Exception)`이 silent 삼킴. *DLQ가 약속한 partial-success를 실제로 못 함*. 5개 record 중 3개 정상 → clean sink로, 2개 실패 → DLQ로 *기록 안 됨*(silent drop).
2. **DLQ가 catalog에 미등록**: `derive_lineage`가 DLQ destination을 outputs로 안 추가. 운영자가 "내 실패 record가 어디 갔지?" 묻고 카탈로그에서 DLQ 찾으면 없음. 카탈로그가 실제 데이터 flow와 다름.

**Decision**:
1. **코어 버그 fix 1 — `_dlq_route_batch`가 `table`/options 전달**:
   ```python
   write_kwargs = {"mode": self.dlq.mode}
   if self.dlq.table is not None:
       write_kwargs["table"] = self.dlq.table
   sink.write([record], **write_kwargs)
   ```
   `contextlib.suppress(Exception)`은 유지(best-effort). 단 진짜 dlq sink가 동작 가능한 상태일 땐 record가 실제로 도착함.
2. **코어 버그 fix 2 — `derive_lineage`가 DLQ를 outputs로 자동 등록**:
   - `cfg.dlq is not None`이면 `derive_asset_key(cfg.dlq.connection, cfg.dlq.model_dump())`로 키 얻고 `_add_out`. 모든 inputs → dlq edge도 추가(partial-success topology가 실제 data flow와 일치).
3. **e2e 시나리오 2종** (`test_dlq_scenarios.py`):
   - **II1 — Record-level partial success**: 5 raw record(3 positive amount + 2 negative). custom_python이 negative에 raise. 결과: clean_orders에 3 row, bad_orders에 2 row, run status=SUCCEEDED.
   - **II2 — DLQ catalog asset**: 동일 시나리오 + 카탈로그에 `src/raw_orders` + `dst/clean_orders` + **`dst/bad_orders`** 모두 등록 검증.
4. **코어 단위 1 신규** (`test_asset.py`): `derive_lineage`가 DLQ를 outputs에 추가 + edge 생성 직접 검증.

**Consequences**:
- ✅ **DLQ 실제 동작**: partial-success가 약속된 대로 record-level로 routing. silent drop 버그 해소.
- ✅ **카탈로그 완전성**: 실패 record가 어디 갔는지 카탈로그에서 답 가능. 운영자 디버깅 path 명확.
- ✅ **Phase EE 시나리오 B(fan-out by `when`)와 일관**: 다중 sink 라우팅 둘 다 카탈로그 등록.
- ✅ DB 마이그레이션 0. lineage emit 변경만(추가).
- ✅ 코어 723→738(+1 unit 신규 + 737 unchanged) + 서버 it 448→450(+2 e2e).
- ⚠ **`_dlq_route_batch`의 `contextlib.suppress(Exception)` 유지**: 실제 사용 시 sink가 down/credential 실패하면 여전히 silent drop. 향후 슬라이스에서 DLQ failure를 logger warn으로 emit하는 게 자연.
- ⚠ **Stream DLQ는 별개 경로**: `_dlq_route_stream`은 topic forwarding 다른 메커니즘 — 별도 시나리오 슬라이스 필요.

**Dogfooding 발견 2개 (코어 보완 동반)**: 둘 다 sample-data e2e가 아니면 catch 못 했을 미스. 단위 테스트로는 mock sink 쓰기 때문에 silent skip 못 잡음 + lineage emit 누락도 production 데이터 없으면 안 보임.

**검증**: 코어 unit 737→738(+1 DLQ 단위). 서버 it 448→450(+2 e2e). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0054: Cursor-based backfill 시나리오 — incremental sync semantics 깊은 검증

**Date**: 2026-05-29
**Status**: Accepted
**Context**: ADR-0039이 backfill을 도입했지만 sample-data e2e가 없어 cursor range semantics(exclusive lower / inclusive upper)가 실제로 어떻게 동작하는지 검증 부족. 운영 흔한 패턴: full sync → 누락분 backfill로 재처리. 카탈로그가 둘을 어떻게 구분/기록하는지도 확인 안 됨.

**Decision**:
1. **`test_backfill_scenarios.py`(신규)** — 2 시나리오 e2e:
   - **JJ1 — Backfill respects bounds**: 10 rows(day 1~10). cursor_from='2026-05-02', cursor_to='2026-05-05' → 정확히 3 rows(day 3, 4, 5) sink로. 검증: records_read=3 + records_written=3 + run.result_json에 backfill marker survive.
   - **JJ2 — Full run + backfill catalog**: full run(no cursor bounds, 10 rows) → backfill window(day 7-10, 4 rows). 결과: data plane에 14 rows(append), catalog에 same asset row + **2개의 materialization** entries(audit trail 의도 보존).
2. **`_seed_backfill_run` helper**: REST endpoint 없이 Run row에 `result_json.backfill.{cursor_from, cursor_to}` 직접 박음 — worker가 router와 동일하게 처리(코드 경로 일관).
3. **Sample data 구조**: 10일치 timestamp+value rows. 매일 1개 → cursor windowing 의미가 row count로 명확 표현.

**Consequences**:
- ✅ **cursor range semantics 명확화**: `(cursor_from, cursor_to]` (exclusive lower, inclusive upper) 동작이 e2e로 입증. ADR-0039 spec 따름.
- ✅ **Catalog materialization audit trail**: 같은 asset에 대해 full run + backfill을 둘 다 materialization entry로 보존(2개) — 운영자 "이 자산이 언제 새로 고쳐졌나"에 시간 순으로 답.
- ✅ **Run row source 구분**: `result_json.source = "backfill"` marker가 survive — 옛 backfill을 audit/replay 가능.
- ✅ DB 마이그레이션 0. 신규 e2e만.
- ⚠ **Incremental sync(자동 cursor watermarking) 별도 슬라이스**: 현재 backfill은 명시적 range만. 다음 run이 last_cursor 자동 sync는 worker에 미지원 — 후속 슬라이스에서 DbCursorState integration.
- ⚠ **400 error 케이스 e2e 미포함**: cursor 없는 파이프라인에서 backfill 시도 → router가 400. 이미 router 단위 테스트가 cover라 e2e 중복 불필요.

**검증**: 코어 unit 738 unchanged(서버 측 신규 시나리오만). 서버 it 450→452(+2 시나리오). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0055: Catalog REST API 시나리오 — UI 경로 e2e 검증

**Date**: 2026-05-29
**Status**: Accepted
**Context**: 카탈로그 동작은 worker 단의 e2e(EE/GG/HH/II/JJ)에서 검증됐지만 **UI가 실제로 호출하는 REST endpoint**를 통해서 동일한 데이터를 *조회*했을 때 정확한 응답이 오는지는 분리된 검증. 기존 `test_assets_router.py`는 `AssetRepository.persist_run_lineage`로 직접 seed → 응답 검증. **운영 시나리오와 다름**: worker가 쓴 catalog row를 UI가 읽음. serialization mismatch / shape drift가 worker↔API 사이에서 발생할 수 있음.

**Decision**:
1. **`test_catalog_api_scenarios.py`(신규)** — 3 시나리오, worker로 카탈로그 seed + REST로 조회:
   - **KK1 — List + Lineage REST**: 2-pipeline chain(raw→staging→mart) run → `GET /workspaces/{ws}/assets` 응답에 3 rows, `GET /assets/{staging_id}/lineage` 응답에 upstream=[raw], downstream=[mart].
   - **KK2 — Materializations + Column-lineage REST**: 같은 pipeline 2번 run → `/materializations` 응답에 2 entries, `/column-lineage` 응답에 `opaque=false` + columns의 upstream refs가 `src/raw`의 같은 이름 컬럼을 가리킴.
   - **KK3 — Cross-workspace 404 + non-member 403**: ws_a 유저(`u_a`)가 ws_b의 asset id를 ws_a URL로 요청 → 404. ws_a 비멤버(`u_b`)가 ws_a의 list 요청 → 403. 워크스페이스 context guard와 resource resolver 둘 다 정상 동작.
2. **App fixture pattern**: `_build_app(session)` 헬퍼가 `get_session` dependency를 outer transaction의 session으로 override. worker가 같은 session에서 write → REST handler가 같은 session에서 read → conftest의 outer-transaction이 양쪽 unified atomicity 제공.
3. **3-layer 검증**: catalog repo(직접 확인) + REST 응답(HTTP layer) + 권한 model(403/404)를 한 흐름에서 검증.

**Consequences**:
- ✅ **UI 경로의 정확성 입증**: 운영자가 UI에서 자산 클릭 → API → DB → 응답이 worker write와 일치. shape drift 회귀 가드.
- ✅ **ACL 동작 확인**: 워크스페이스 격리(Phase HH)가 REST layer까지 일관 — non-member는 403 빠른 차단, member의 cross-ws asset id는 404. UI 보안 경계 명확.
- ✅ **Materialization audit trail UI access**: 운영자가 "이 자산 언제 새로 고쳐졌나?" REST로 답 가능 — 2개 run이면 2개 entry 시간순.
- ✅ DB 마이그레이션 0. 신규 e2e만.
- ⚠ **HTTP 시나리오는 conftest의 outer-transaction에 의존**: production에서는 worker/REST가 다른 session pool. 다행히 둘 다 같은 PostgreSQL 인스턴스 가리키니까 logical isolation 동일.
- ⚠ **Cross-workspace 404의 정확한 차단 위치**: 현재 ws_a URL + ws_b asset id 조합은 404(`_resolve_or_404`). production 환경에서 ws_b URL에 ws_a asset id 조합은 다른 분기 — 모든 8가지 cross-axis 조합 e2e는 별도 슬라이스(현재는 가장 중요한 2가지만).

**검증**: 코어 unit 738 unchanged. 서버 it 452→455(+3). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0056: Auto-materialize cycle 차단 — `trigger_chain` 안전성 sample-data 검증

**Date**: 2026-05-29
**Status**: Accepted
**Context**: ADR-0037이 도입한 auto-materialize는 두 파이프라인이 서로의 출력을 읽으면 무한 loop를 만들 수 있는 잠재 위험. `_trigger_asset_consumers`가 `result_json.trigger_chain`(누적 pipeline id 목록)을 검사해서 이미 chain에 있는 pipeline은 재enqueue 안 함. 이게 핵심 안전 메커니즘이지만 unit test로 mock하는 것과 실제 worker drain loop에서 동작 확인은 다름. 운영 신뢰성 직결.

**Decision**:
1. **`test_auto_materialize_scenarios.py`에 LL1 추가** — sample-data e2e:
   - **Setup**: A(`auto_materialize: true`, source=staging, sink=mart), B(`auto_materialize: true`, source=mart, sink=staging). 두 sink/source가 서로 cross-wired.
   - **Trigger A only**: drain 후 정확히 A 1 run + B 1 run. 무한 loop 없음.
   - **trigger_chain 검증**: B의 result_json.trigger_chain == [A.id] — A를 chain에 stamp해서 다음 turn에 A 재enqueue를 막음.
2. **chain stamp의 의미 명문화**: `result_json.trigger_chain`은 누적 pipeline id 목록. worker가 새 consumer 발견할 때 `str(candidate_id) in trigger_chain` 체크 → skip.

**Consequences**:
- ✅ **운영 안전성 확인**: 잘못된 wiring(A↔B cyclic dep)이 무한 큐를 만들지 않음. drain 후 자연 종료.
- ✅ **Audit trail**: B의 result_json.trigger_chain이 정확한 lineage 기록. "B가 왜 enqueue됐나?" "A가 trigger했음" 명확.
- ✅ **Phase GG와 일관**: chain은 정상 path에서도 동일하게 stamp되지만 LL은 *cycle 차단* 측면에 집중.
- ✅ DB 마이그레이션 0. 신규 e2e만.
- ⚠ **Depth cap(`_MAX_TRIGGER_CHAIN`) e2e는 별도 슬라이스**: chain 길이가 cap 도달 시 stop + warning. unit이 cover하지만 e2e는 contrived(N 파이프라인 시드 필요). 정작 cycle break가 더 중요하고 흔한 안전 메커니즘.
- ⚠ **Cycle은 *그래프 cycle*만 catch**: A→A 같은 self-loop도 chain으로 막힘. 더 복잡한 fan-in cycle(A,B → C, C → A,B)는 chain만으론 부족할 수 있음 — 향후 슬라이스에서 graph-level cycle detection 추가 가능.

**검증**: 코어 unit 738 unchanged. 서버 it 455→456(+1 LL1). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0057: Workspace variables 시나리오 + 코어 silent bug 보완 (lineage emit이 unresolved 키 사용)

**Date**: 2026-05-29
**Status**: Accepted
**Context**: 사용자 *"sample 데이터 깊게"* 패턴 적용 — Phase MM은 workspace variables(`${var.name}`)의 multi-tenant 동작을 검증. 시나리오 작성 중 **silent correctness bug 발견**:
- pipeline은 정상 실행 (records_written=3 ok, sink table에 정상 데이터)
- 그러나 catalog asset_key가 **미해결 `${var.target_table}`** 그대로 저장
- 즉 사용자가 카탈로그를 보면 자산 이름이 "${var.target_table}" 같은 placeholder. 실제 데이터는 `marts_prod` 테이블에 있음
- 운영자가 카탈로그로 자산 찾을 수 없음

**Root cause**: `RunExecutor._lineage_for(version, log)`이 `version.config_json`을 *직접* `PipelineConfig.model_validate(...)`에 넘김. Worker의 pipeline 실행 path는 `_prepare`에서 `resolve_config_variables` 호출 후 build_pipeline — variable 해결됨. 하지만 lineage emit path는 별도라 raw config json 사용.

**Decision**:
1. **새 메소드 `_lineage_for_resolved(session, run, version, log)`**: `WorkspaceVariableRepository(session).as_dict(workspace_id)` → `resolve_config_variables(version.config_json, extra=global_vars)` → `PipelineConfig.model_validate` → `derive_lineage`. 반환: `(AssetLineage | None, PipelineConfig | None)` — resolved cfg를 caller가 재사용해서 column lineage도 동일하게 emit.
2. **`_persist_column_lineage(..., *, resolved_cfg=None)`** 옵셔널 인자 추가: success branch는 resolved cfg 전달, 기존 (호출 site가 없는) 경로는 옛 동작 유지(backward-compat).
3. **Success branch 변경**: `lineage, resolved_cfg = await self._lineage_for_resolved(...)` → 둘 다 not None일 때만 persist + column lineage + trigger.
4. **e2e 시나리오 1종** (`test_workspace_variables_scenarios.py` MM1): 두 워크스페이스(prod/dev)에 동일 pipeline config(`table: "${var.target_table}"`). 각자 `target_table` var 다른 값(marts_prod / marts_dev). 실행 후 catalog가 **각자 resolved key**(dst/marts_prod, dst/marts_dev) 보유 + 데이터도 각자 sink로.

**Consequences**:
- ✅ **Silent correctness bug 보완**: 카탈로그가 이제 실제 sink table 이름을 보여줌. 운영자 디버깅 path 동작.
- ✅ **Multi-tenant 환경별 운영 패턴 확인**: 같은 pipeline config text + 환경별 var → 환경별 catalog. SaaS의 핵심 use case.
- ✅ **Backward compat**: `_lineage_for(version, log)` 시그니처 변경 없음(opt-in). 기존 호출 site / 미래의 dry-run-like consumer 등도 동작.
- ✅ DB 마이그레이션 0. column_lineage 통합도 추가 인자 옵셔널.
- ⚠ **Phase BB의 4-stage 시나리오 등 옛 e2e는 variable 안 써서 영향 없음**: catalog 결과 동일.
- ⚠ **Variable 해결 실패 시 lineage 전체 skip**: variable이 없거나 typo면 `resolve_config_variables`가 `ConfigError` raise → log warning + lineage skip(opaque catalog). 이건 안전 정책(catalog가 잘못된 placeholder 키 저장 안 함).

**Dogfooding 가치 입증**: 단위 테스트는 mock cfg 쓰니까 catch 못 함. variable 정의된 ws에서 실행 → catalog 키 검증이 e2e가 catch. **이번 세션에서 4번째 silent bug** (Z의 COUNT(*) leaf + AA의 sink-only column + II의 DLQ silent drop + catalog 미등록 + 지금의 lineage variable resolution). 사용자 "sample 데이터로 깊게" 요청이 매번 가치 입증.

**검증**: 코어 unit 738 unchanged. 서버 it 456→457(+1 MM1). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0058: Sensors freshness 시나리오 — 시간 기반 auto-materialize sample-data e2e

**Date**: 2026-05-29
**Status**: Accepted
**Context**: ADR-0038의 시간 기반 auto-materialize(`freshness_sla_minutes`)는 unit으로는 cover되지만 실제 sample-data + 시간 시뮬레이션으로 *(a) stale → 자동 enqueue + (b) within SLA → no fire* 동작이 운영 시나리오에서 정확히 작동하는지 검증 부족. Phase GG/LL의 asset-driven trigger와 함께 운영 신뢰성 핵심.

**Decision**:
1. **`test_freshness_scenarios.py`(신규)** — 2 시나리오 e2e:
   - **NN1 — Stale asset triggers freshness**: `freshness_sla_minutes: 60` pipeline. 정상 run → catalog row 생성. `Asset.last_materialized_at`을 2시간 전으로 강제 update + 옛 Run.created_at도 같이 rewind(cooldown guard 회피). `Scheduler._tick_freshness(session, now)` 직접 호출 → fired=1 → PENDING run enqueue. `result_json.triggered_by == "freshness"` + `sla_minutes` stamp. Drain → asset refresh.
   - **NN2 — Within SLA no fire**: 같은 setup이지만 `last_materialized_at`이 기본값(now-ish). `_tick_freshness` → fired=0. PENDING 큐 empty. 건강한 pipeline이 큐 storm 안 함.
2. **시간 시뮬 전략**: `Asset.last_materialized_at` + `Run.created_at` 직접 UPDATE. freshness walker가 읽는 두 column이라 이 둘만 강제하면 일관된 과거 상태. 외부 시간 mock 불필요.
3. **Scheduler internal 호출**: `Scheduler(_SessionFactoryAdapter(session))` 후 `_tick_freshness(session, now)` 직접. caller commits 정책 따름. 실제 production code path 그대로(`tick_once`의 freshness leg 부분만).

**Consequences**:
- ✅ **시간 기반 auto-materialize 운영 정확성 확인**: stale 감지 + cooldown 둘 다 sample data로 입증.
- ✅ **Sample-data 시간 시뮬 패턴 확립**: 향후 다른 시간 기반 e2e(sensors / schedules / TTL)에서 재사용 가능.
- ✅ **Phase GG/LL과 보완**: asset-axis(GG) + cycle 차단(LL) + time-axis(NN)이 모두 sample-data로 검증된 auto-materialize family.
- ✅ DB 마이그레이션 0. 신규 e2e만.
- ⚠ **External wall-clock에 일부 의존**: `_tick_freshness`가 `now`를 받지만 `Asset.last_materialized_at` write는 worker가 자체 `datetime.now()` 사용. Drain 후 refresh 확인할 때 `refreshed.last_materialized_at >= now` 검증 — millisecond 정밀도 의존.
- ⚠ **Sensors 모듈(`etlx_server.sensors.builtins.asset_freshness`) 별도 검증 미포함**: 그것도 ADR-0038 family이지만 별도 메커니즘(K3 sensor framework). 향후 슬라이스에서 sensor e2e 추가 가능.

**검증**: 코어 unit 738 unchanged. 서버 it 457→459(+2 NN1/NN2). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0059: Connection 실패 시나리오 — catalog 깨끗 유지 정책 보장

**Date**: 2026-05-29
**Status**: Accepted
**Context**: 운영 흔한 실패 2종: (1) connection의 database path가 잘못됨(typo / dev env 미배포 / 권한) → connect() 단계 실패. (2) connection은 valid하지만 source query가 존재하지 않는 table 참조(drift / dropped / wrong env) → read() 단계 실패. 둘 다 사용자 운영 일상 발생. Phase EE Scenario F(transform raise), Phase II(DLQ failure)와 *같은 catalog clean 정책*이 적용되는지 sample-data로 검증.

**Decision**:
1. **`test_connection_failure_scenarios.py`(신규)** — 2 시나리오 e2e:
   - **OO1 — Invalid database path**: `/nope/does/not/exist/...db`로 src/dst 둘 다 가리킴. sqlite connect 실패 → worker except branch → run.status=FAILED + error_class/error_message 채워짐 + 카탈로그 empty.
   - **OO2 — Source table missing**: sqlite 파일은 valid하지만 `raw` table 없음. read 실패 → run.status=FAILED + error_message에 "raw" 또는 "no such table". 카탈로그 empty.
2. **검증 axis**:
   - **Run status**: FAILED + error_class/error_message 채워짐(운영자 디버깅 정보).
   - **Catalog state**: 양쪽 시나리오 모두 `list_for_workspace == set()` — *어떤 자산도 생성 안 됨*. 절반 만들어진 row 없음.
3. **정책 명문화**: Phase EE F + Phase II + Phase OO 모두 같은 "no half-written catalog rows on failure" 정책. 어느 단계에서 실패하든 catalog는 항상 일관.

**Consequences**:
- ✅ **운영 흔한 실패 catalog 안전성**: connect fail / read fail 둘 다 카탈로그 더럽히지 않음.
- ✅ **운영자 디버깅 path 명확**: error_class + error_message가 항상 채워짐 → Run 상세 페이지에서 무엇이 잘못됐는지 즉시 확인.
- ✅ **catalog clean 정책 통합 검증**: transform fail(F) / DLQ fail(II) / connect fail(OO1) / read fail(OO2) 4가지 stage 모두 같은 posture.
- ✅ DB 마이그레이션 0. 신규 e2e만.
- ⚠ **write fail은 별도 슬라이스**: sink connect/write 실패 시 일부 record가 sink에 들어간 partial-success는 본 시나리오에 미포함. sqlite append 모드에서 write 도중 실패 시뮬은 별도 복잡성.
- ⚠ **Network connection 실패(postgres / S3 / Kafka)는 미포함**: testcontainers 필요. sqlite로 catalog clean 정책의 일반성 입증 — 다른 connector도 같은 worker except path를 거치므로 동일 동작.

**검증**: 코어 unit 738 unchanged. 서버 it 459→461(+2 OO1/OO2). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0060: Pipeline evolution 시나리오 — config 변경 시 catalog 동작 명문화

**Date**: 2026-05-29
**Status**: Accepted
**Context**: 운영자가 pipeline config을 자주 편집(sink table 이름 변경, JOIN dimension 추가, source 쿼리 확장 등). 새 `PipelineVersion`이 생성되고 다음 run이 새 cfg로 실행. 카탈로그가 **어떻게 변화를 반영**하는지 명문화 부족:
- 옛 자산은 어떻게? 그대로 남나? stale 표시?
- 새 자산은 어떻게 등록되나?
- materialization 카운트는 어떻게 분리되나?
- source 쿼리 확장 시 새 input asset이 자동 등록되나?

운영 신뢰성과 카탈로그 추적성 직결.

**Decision**:
1. **`test_pipeline_evolution_scenarios.py`(신규)** — 2 시나리오 e2e:
   - **PP1 — Sink table rename**: v1 `sink: stage_v1` → v2 `sink: stage_v2`. 두 run 후 catalog가 `dst/stage_v1` + `dst/stage_v2` *둘 다* 보유. 각자 materialization 1개씩(no double counting). v1 row의 `last_materialized_at`은 v2 시점에 갱신 안 됨 → 자연스럽게 stale.
   - **PP2 — Source JOIN dimension 추가**: v1 source=raw → v2 source=raw JOIN dim_country. v2 run 후 catalog에 `src/dim_country` 자동 등록(Phase X 후속의 `extract_referenced_tables`) + edge `src/dim_country → dst/joined`. Sink row 같은 identity(동일 connection/table)라 materialization 1→2.
2. **catalog evolution 정책 명문화**:
   - **자산 row는 immutable / append-only**: 옛 자산은 절대 삭제 안 함. 옛 materialization 그대로 보존.
   - **새 자산 자동 등록**: 새 cfg가 참조하는 base table은 자동으로 input asset로 추가(Phase X 후속).
   - **Edge는 매 run마다 upsert**: 같은 (upstream, downstream) 조합이면 한 row, 중복 없음.
   - **Materialization은 run-per-row**: 같은 자산에 대해 각 run마다 1 entry.
3. **`_add_pipeline_version` helper**: `is_current=False` 옛 row + 새 row `is_current=True` 패턴 — API의 `PipelineRepository.ensure_version` 동작 mirror.

**Consequences**:
- ✅ **운영 마이그레이션 추적성**: 옛 자산이 catalog에 남아서 "이 자산 언제부터 쓰지 않았나?" 답 가능.
- ✅ **새 dimension 자동 catch**: source 쿼리 확장 시 운영자가 수동으로 dimension 등록할 필요 없음.
- ✅ **Materialization 정확성**: run-per-row이므로 시간순 audit trail이 정확.
- ✅ **PipelineVersion immutability와 일관**: 옛 version의 lineage는 보존, 새 version의 lineage 추가. 양쪽 모두 활용 가능.
- ✅ DB 마이그레이션 0. 신규 e2e만.
- ⚠ **옛 자산 stale 표시 자동화 부재**: 운영자가 직접 "이 자산 deprecated" 마킹해야 정리됨. 향후 슬라이스: PipelineVersion config에서 사라진 sink는 자동 archived flag 추가 가능.
- ⚠ **JOIN으로 새로 register된 input asset의 materialization은 없음**: dim_country는 input이지 sink가 아니라 lineage emit에서 last_materialized_at 갱신 안 됨. 정상 동작(읽기만 함).

**검증**: 코어 unit 738 unchanged. 서버 it 461→463(+2 PP1/PP2). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0061: Audit trail 통합 시나리오 — data-plane events 운영 시점 검증

**Date**: 2026-05-29
**Status**: Accepted
**Context**: Phase U/W(ADR-0041 W)가 도입한 data-plane audit events(`run.sql_read` / `run.sql_executed` / `run.python_executed`)는 unit으로 isolated 검증됨. 하지만 *operator workflow*는 "이 run의 audit_log를 보고 정확히 무엇이 일어났는지 안다"는 것. 운영 시점에 events 조합이 정확히 emit되는지 sample-data + real run으로 검증 부족.

**Decision**:
1. **`test_audit_trail_scenarios.py`(신규)** — 2 시나리오 e2e:
   - **QQ1 — Single run full data-plane audit**: sqlite source SELECT + custom_python transform + sqlite sink. 검증: `run.sql_read` 정확히 1 + `run.python_executed` 정확히 1 + `run.sql_executed` 0(sql_exec transform 없음). 각 row의 `after_json` 페이로드(connection_type / kind / query / code_hash) 정확.
   - **QQ2 — Multi-run isolation**: 같은 pipeline 2 run → 각 run의 resource_id로 필터하면 자기 audit row만. 시간순(`created_at`) 정확.
2. **검증 axis 3차원**: (a) event count(정확히 1번씩 emit, 누락/중복 없음), (b) payload(after_json의 field name/value 정확), (c) isolation(resource_id 필터 = run.id, cross-run leakage 없음).

**Consequences**:
- ✅ **Operator workflow 신뢰성**: audit_log scan만으로 "이 run에서 어떤 query 실행됐고 어떤 code 돌았나" 정확히 답 가능. GDPR/SOX compliance 충족.
- ✅ **Phase U/W의 unit + integration 양면 검증**: isolated unit + real run integration 둘 다.
- ✅ **Cross-run leakage 없음 입증**: 두 run의 audit 분리 명확. multi-tenant + multi-run 환경에서 forensic 안전.
- ✅ DB 마이그레이션 0. 신규 e2e만.
- ⚠ **Stream pipeline audit는 별도 슬라이스**: 본 시나리오는 batch만. Stream의 audit emit 패턴이 batch와 다르면 별도 검증 필요.
- ⚠ **Control-plane audit(workspace/connection/pipeline create/update)는 별도 e2e**: REST API layer 호출 필요(기존 router 테스트가 cover). 본 슬라이스는 data-plane에 집중.

**검증**: 코어 unit 738 unchanged. 서버 it 463→465(+2 QQ1/QQ2). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0062: K3 Sensor framework 통합 시나리오 — `asset_freshness` builtin 운영 검증

**Date**: 2026-05-29
**Status**: Accepted
**Context**: ADR-0041 K3 sensor framework는 builtin sensors(`asset_freshness` / `file_landed` / `lineage_arrival` / `dataset_row_count`)를 unit으로 검증됨. *Real catalog + real sensor row + SensorScheduler.tick_once* 운영 시점 통합은 검증 부족. asset-axis(Phase NN)와 sensor-axis(K3) 둘 다 freshness를 다루는데 어떻게 다른지 명확화 필요.

**Decision**:
1. **`test_sensor_framework_scenarios.py`(신규)** — 2 시나리오 e2e (`asset_freshness` builtin 중심):
   - **RR1 — Stale catalog triggers sensor**: pipeline 정상 run → catalog 등록. Asset.last_materialized_at 3시간 전 강제. SensorScheduler.tick_once → fired=1. Sensor row의 `last_check_at`/`last_triggered_at`/`last_result_json` 채워짐(`triggered: true`, message에 "stale"). PENDING run target_pipeline에 enqueue.
   - **RR2 — Fresh asset within budget**: 같은 setup, last_materialized_at 기본(최근). tick → fired=0. `last_triggered_at` 여전히 None. `last_result_json` "fresh" message. PENDING 큐 empty.
2. **asset-axis(NN) vs sensor-axis(K3) 구분 명문화**:
   - **Phase NN (pipeline-axis)**: `freshness_sla_minutes` on Schedule. Scheduler._tick_freshness가 *모든* current pipeline 자동 walk. 운영자가 producer pipeline에 설정.
   - **Phase RR (asset-axis sensor)**: `asset_freshness` sensor row + target_pipeline_id. SensorScheduler tick이 sensor table을 walk. 운영자가 consumer 입장에서 declare("이 asset이 stale하면 그때 ETL 다시 돌려라").
3. **시간 시뮬 패턴 재사용**: Phase NN에서 도입한 `Asset.last_materialized_at` 직접 UPDATE 패턴 그대로 적용. 외부 mock 불필요.

**Consequences**:
- ✅ **K3 sensor framework 운영 신뢰성 확인**: stale/fresh 양쪽 케이스 모두 정확히 동작.
- ✅ **Sensor row state machine 검증**: last_check_at + last_triggered_at + last_result_json 정확히 업데이트.
- ✅ **NN(pipeline-axis) vs K3(asset-axis) 둘 다 동작 확인**: 같은 freshness 개념이지만 다른 trigger path. 운영자가 둘 중 선택 가능.
- ✅ DB 마이그레이션 0. 신규 e2e만.
- ⚠ **다른 builtins(file_landed / lineage_arrival / dataset_row_count) e2e 미포함**: file_landed는 파일시스템 의존, lineage_arrival은 다른 시그널 패턴. 별도 슬라이스에서 cover 가능.
- ⚠ **Sensor poll_interval_seconds 동작 미검증**: 본 시나리오는 tick_once 직접 호출 → poll interval guard 우회. 정상 동작은 unit이 cover.

**검증**: 코어 unit 738 unchanged. 서버 it 465→467(+2 RR1/RR2). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0063: Cron schedule 시나리오 — 시간 기반 trigger family 3축 완성

**Date**: 2026-05-29
**Status**: Accepted
**Context**: Step 9.2 / ADR-0021의 cron scheduler가 도입됐지만 sample-data e2e 미검증. 시간 기반 trigger family 3축 중 마지막. Phase NN(pipeline-axis freshness) + Phase RR(asset-axis sensor) + 본 슬라이스(operator-axis cron)이 모두 sample-data로 검증되면 시간 기반 자동화의 핵심 메커니즘 e2e 통합 완성.

**Decision**:
1. **`test_schedule_cron_scenarios.py`(신규)** — 2 시나리오 e2e:
   - **SS1 — Due cron fires + drain to sink**: cron_expr='* * * * *'(매분), schedule.created_at 2분 전 강제. Scheduler.tick_once 호출 → `_compute_next_firing` 평가(croniter base = last_scheduled or created_at) → next_fire가 1분 전 → 즉시 due → fired=1. drain → records sink로 정상 write + run.schedule_id 채워짐.
   - **SS2 — Inactive schedule skipped**: 같은 setup, is_active=False → `_load_due_schedules`가 skip → fired=0, runs 비어 있음.
2. **시간 시뮬 패턴 확장**: Phase NN의 `last_materialized_at` 직접 UPDATE에 이어 본 슬라이스는 `schedules.created_at` 직접 UPDATE로 first-firing base를 과거로. 외부 mock 없이 시간 의존 로직 검증.
3. **시간 기반 trigger family 3축 명문화**:
   - **Pipeline-axis (NN/ADR-0038)**: Schedule.freshness_sla_minutes. *Producer* 입장. Scheduler._tick_freshness가 모든 current pipeline walk.
   - **Asset-axis (RR/K3)**: Sensor.config(asset_key, max_age_minutes). *Consumer* 입장. SensorScheduler가 sensor table walk.
   - **Operator-axis (SS/cron)**: Schedule.cron_expr. *Operator* 입장 (정시 실행). Scheduler.tick_once가 schedule table walk.

**Consequences**:
- ✅ **시간 기반 trigger 3축 모두 e2e 검증**: 운영 자동화 핵심이 sample-data로 통합 입증.
- ✅ **schedule.created_at rewind 패턴 확립**: cron `_compute_next_firing`의 base 계산을 시간 mock 없이 시뮬.
- ✅ **운영자 일시 중지 동작 확인**: is_active=False로 schedule pause 가능 + 안전.
- ✅ Run.schedule_id stamp 확인 — 어느 schedule이 enqueue했는지 audit lineage 유지.
- ✅ DB 마이그레이션 0. 신규 e2e만.
- ⚠ **No-catchup vs catchup-ish 정책 e2e 미포함**: 현재 tick당 1 missed cron만 enqueue (ADR-9.2의 trade-off). 별도 시나리오에서 검증 가능.
- ⚠ **multi-replica `FOR UPDATE SKIP LOCKED` 미검증**: 본 시나리오는 single-replica. 분산 환경의 동시 lock은 향후 슬라이스.

**검증**: 코어 unit 738 unchanged. 서버 it 467→469(+2 SS1/SS2). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0064: Operator onboarding journey — UX-first 전체 흐름 e2e 검증

**Date**: 2026-05-29
**Status**: Accepted
**Context**: 사용자 요청 *"UX를 고려해서 사용자 페르소나로 전체적인 점검을 항상 포함"*. 기존 시나리오들은 *기술적 isolated*(각 메커니즘별)였지만, 운영자 입장의 *first 15 minutes* 흐름(가입 → 첫 ETL 실행 → catalog 확인)이 끊김 없이 자연스러운지는 별도 검증 필요. 신규 운영자 입장에서 *어디서 막히나? 응답이 명확한가?* 페르소나 점검 자동화.

**Decision**:
1. **`test_onboarding_journey_scenario.py`(신규)** — 2 시나리오, 두 사용자 페르소나:
   - **TT1 — 운영자 신규 가입부터 첫 catalog까지 10-step REST journey**:
     1. POST /auth/login → access_token
     2. POST /workspaces (auto-Owner) → id/slug/name 확인
     3. POST /workspaces/{ws}/connections × 2 (src + dst, sqlite) → 201
     4. GET /workspaces/{ws}/connections → ids 회수
     5. POST /workspaces/{ws}/connections/{id}/test → ok=True
     6. POST /workspaces/{ws}/pipelines → 201 + id
     7. POST /pipelines/{id}/dry-run → ok=True + connectors 둘 다 ✓ + warnings 필드 존재
     8. POST /pipelines/{id}/trigger → 202 + run id
     9. (worker drain)
     10. GET /workspaces/{ws}/runs/{rid} → status=succeeded + records_written=3 + pipeline_version_id
     11. GET /workspaces/{ws}/assets → src/raw_orders + dst/clean_orders
     12. GET /audit?action=run.sql_read → 정확히 1 row + after_json.{query/connection/connection_type}
   - **TT2 — 데이터 엔지니어가 custom_python 첫 사용 → Phase DD 안내 동작**: 같은 흐름이지만 transforms에 `custom_python` 추가. dry-run 응답에 `column_mapping_recommended` warning + `location='transforms.0'` + message에 "column_mapping" 단어. run 자체는 ok 유지.
2. **검증 axis 3가지**:
   - **Status code**: 각 단계 예상 코드(201/200/202).
   - **응답 shape**: UI가 navigation에 쓰는 load-bearing field 모두 존재(id/slug/name/records_written/connectors[].ok/warnings/asset_key/after_json.query).
   - **흐름 일관성**: ID가 이전 단계 → 다음 단계로 자연 carry. 토큰 한 번 받고 끝까지 재사용.
3. **사용자 페르소나 정의**: TT1은 "운영자 day-1 sqlite 점검 흐름", TT2는 "데이터 엔지니어가 python 코드 박을 때 UX nudge가 적시에 뜨는지". 이후 시나리오들은 다른 페르소나(분석가 / 관리자) 별로 추가 가능.

**Consequences**:
- ✅ **UX 회귀 가드**: 응답 shape 변경 시(예: `records_written` 누락) onboarding e2e가 즉시 catch.
- ✅ **사용자 흐름의 끊김 입증**: 10-step REST path가 사용자가 멈출 곳 없이 자연 흐름. token 한 번 + 각 단계 응답에 다음 ID 포함.
- ✅ **Phase DD lint nudge가 적시에 dry-run 응답에 노출**: 데이터 엔지니어가 python 박는 즉시 "column_mapping 추가하세요" 응답 안내 → 의식적 결정 유도.
- ✅ **사용자 요구사항 응답**: "UX 고려, 사용자 페르소나로 전체 점검, dogfooding sample data" 모두 1 슬라이스에서 만족.
- ✅ DB 마이그레이션 0. 신규 e2e만.
- ⚠ **다른 페르소나 별도 슬라이스**: 분석가(low-code 빌더 UI 흐름), 관리자(membership/secret 관리), 컴플라이언스 담당자(audit query 깊은 사용). 각자 별 e2e로 cover 가능.
- ⚠ **에러 경로 별도 슬라이스**: dry-run 실패 / trigger 시 시크릿 없음 / 권한 부족 등은 별도 onboarding-error journey 시나리오로.

**검증**: 코어 unit 738 unchanged. 서버 it 469→471(+2 TT1/TT2). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0065: Analyst exploration journey + AssetSummary 누락 필드 보완

**Date**: 2026-05-29
**Status**: Accepted
**Context**: Phase TT(운영자 페르소나)에 이어 두 번째 사용자 페르소나 — **분석가**. 자산을 만들지 않지만 매일 catalog를 둘러보고 lineage를 추적하는 사용자. Viewer 권한으로 catalog 탐색 흐름이 자연스러운지 dogfood. *분석가가 어디서 막히나? 한눈에 보고 싶은 정보가 빠졌나?*

**Decision**:
1. **`test_analyst_exploration_journey_scenario.py`(신규)** — UU1: 분석가 catalog 탐색 5-step:
   - **Setup**: Owner가 2-stage chain(raw → staging → mart) 실행 → 자산 + materialization 채워짐.
   - **Step 1**: `GET /workspaces/{ws}/assets` — Viewer가 자산 목록 둘러봄. 응답에 `column_lineage_opaque` badge field 있어야 traceability를 한눈에 확인 가능.
   - **Step 2**: `GET /assets/{id}/lineage` — upstream/downstream으로 "한 클릭 위/아래" 탐색.
   - **Step 3**: `GET /assets/{id}/column-lineage` — `mart.amount`가 어디서 왔나 column drill-down.
   - **Step 4**: `GET /assets/{id}/materializations` — 마지막 새로고침 시각 + records_written.
   - **Step 5**: Viewer가 forbidden action(POST /pipelines) 시도 → 403 (UI가 "Locked" badge 결정).

2. **Dogfooding 발견 + 보완 — `AssetSummary`에 `column_lineage_opaque` 추가** (6번째 silent UX miss):
   - **Bug**: `GET /workspaces/{ws}/assets` 응답에 `column_lineage_opaque` 없음. 분석가가 목록에서 "이 자산 traceable?" 한눈에 못 봄. 매 자산마다 column-lineage 엔드포인트 호출 필요 → bad UX.
   - **Root cause**: ADR-0041 J2가 `Asset.column_lineage_opaque` 컬럼은 추가했지만 API summary schema에는 안 노출. 단위 테스트는 mock으로 동작 검증 → catch 못 함.
   - **보완**: `AssetSummary.column_lineage_opaque: bool = False` 필드 추가. `from_attributes=True` 덕분에 Asset row에서 자동 populate. Backward-compat(기본값 False).

3. **사용자 페르소나 확장**: TT(운영자) + UU(분석가) → 매 페르소나가 다른 미스를 catch. 향후 다른 페르소나(관리자, 컴플라이언스 담당자)는 별도 슬라이스.

**Consequences**:
- ✅ **분석가 UX 동작 확인**: catalog 탐색 + lineage drill-down + materialization audit + ACL 경계 모두 자연 흐름.
- ✅ **6번째 silent UX bug catch + 보완**: 응답에 필드 누락. UI가 자산 목록에서 "traceable" badge 표시 가능 → 분석가가 매번 drill-down 안 해도 됨.
- ✅ **사용자 페르소나 시리즈 확립**: TT + UU 패턴이 향후 페르소나 e2e 템플릿. 비슷한 미스 다른 영역에서도 catch 기대.
- ✅ **이번 세션 6번째 silent miss** (Z COUNT(*) / BB sink-only / II DLQ × 2 / MM lineage var / UU AssetSummary). dogfood + 사용자 페르소나 점검 가치 입증 누적.
- ✅ DB 마이그레이션 0. schema 필드 추가만, 기본값 backward-compat.
- ⚠ **다른 페르소나는 별도 슬라이스**: 관리자(membership/secret), 컴플라이언스(audit query 깊은 사용), 데이터 엔지니어(빌더+config 작성). 각자 별 e2e로 cover.
- ⚠ **Viewer ACL 경계 검증은 1개만**: 모든 Viewer-금지 action(POST/PATCH/DELETE/trigger 등)에 대한 매트릭스 검증은 별도 ACL 슬라이스로.

**검증**: 코어 unit 738 unchanged. 서버 it 471→472(+1 UU1). mypy 코어 61 + 서버 100 OK. ruff clean. DB 마이그레이션 0. backward-compat 유지.

---

## ADR-0066: Cross-DB replication — type mapping + 자동 sink table 생성

**Date**: 2026-05-29
**Status**: Accepted
**Context**: 사용자 요청 *"DB간의 마이그레이션 및 테이블 복제 추가. 데이터 타입을 DB별로 잘 맞춰주고"*. 실제 운영 흔한 패턴: postgres OLTP → sqlite/mysql analytics, mysql → postgres 등 cross-DB 복제 + 자동 destination DDL. 기존엔 dest 테이블 수동 생성 + 타입 직접 매핑 필요 → 엔지니어가 one-off 스크립트 양산.

**Decision**:
1. **`etl_plugins/core/type_mapping.py`(신규)** — DB-agnostic 타입 매핑 모듈:
   - **`CanonicalType` enum** — INTEGER/BIGINT/SMALLINT/REAL/DOUBLE/DECIMAL/TEXT/VARCHAR/BOOLEAN/TIMESTAMP/DATE/JSON/BLOB 13개. 작은 set + 새 dialect 추가는 dialect_ddl 한 entry로.
   - **`TypeSpec(canonical, length, precision, scale)`** — VARCHAR/DECIMAL의 attribute 보존.
   - **`normalize_db_type(raw)`** — 벤더 타입 문자열 → TypeSpec. 대소문자/공백/괄호 처리. unknown은 TEXT 안전 fallback.
   - **`render_canonical(spec, dialect)`** — TypeSpec → 벤더-specific DDL. length/precision은 base 타입이 받을 때만 emit(sqlite의 VARCHAR→TEXT는 length 드롭).
   - **dialect별 DDL 테이블**: sqlite / postgres / mysql 매핑. 향후 snowflake/duckdb는 한 entry 추가.
2. **`etl_plugins/core/inspect.py`에 `SchemaWriter` Protocol** — SchemaInspector의 dual. `ensure_table(table, columns, *, if_exists="skip" | "drop" | "error")`. optional capability, runtime_checkable.
3. **`SinkConfig.auto_create_table: bool = False`** — Pydantic 옵셔널 필드. True + sink가 SchemaWriter + source가 SchemaInspector → 자동 DDL.
4. **`SinkSpec.auto_create_table` + `Task.sink_auto_create_table`** — 코어 dataclass에 forward. builder가 single-sink/fan-out 양 path 채움.
5. **`Pipeline._auto_create_sink_tables(task, source, sinks)`** — `_run_pre_sql` 직전 호출. source.list_columns → 각 auto_create_table sink의 ensure_table. best-effort(예외는 silent skip — 다음 write가 더 명확한 에러).
6. **sqlite + postgres + mysql connector에 `ensure_table` 구현** — 각자 dialect로 render. identifier 화이트리스트(`_SAFE_IDENT`).
7. **`postgres.list_columns` 보강** — `character_maximum_length`/`numeric_precision`/`numeric_scale` 활용해서 VARCHAR(64) / NUMERIC(10,2) attribute 보존.

**Type 매핑 표 (핵심 예)**:

| Source vendor | Canonical | postgres | mysql | sqlite |
|---|---|---|---|---|
| `BIGINT` / `INT8` | BIGINT | `BIGINT` | `BIGINT` | `INTEGER` (affinity) |
| `INTEGER` / `INT4` | INTEGER | `INTEGER` | `INT` | `INTEGER` |
| `DOUBLE PRECISION` | DOUBLE | `DOUBLE PRECISION` | `DOUBLE` | `REAL` |
| `NUMERIC(10,2)` | DECIMAL | `NUMERIC(10,2)` | `DECIMAL(10,2)` | `NUMERIC(10,2)` |
| `VARCHAR(64)` | VARCHAR | `VARCHAR(64)` | `VARCHAR(64)` | `TEXT` (length 드롭) |
| `TIMESTAMPTZ` / `DATETIME` | TIMESTAMP | `TIMESTAMPTZ` | `DATETIME` | `TEXT` (ISO 8601) |
| `JSONB` / `JSON` | JSON | `JSONB` | `JSON` | `TEXT` |
| `BOOLEAN` | BOOLEAN | `BOOLEAN` | `TINYINT(1)` | `INTEGER` |

**Consequences**:
- ✅ **운영 흔한 cross-DB 복제 무료**: `auto_create_table: true` 하나만 박으면 dest DDL 자동. 엔지니어 one-off 스크립트 양산 해소.
- ✅ **타입 round-trip 정확**: postgres BIGINT → sqlite INTEGER → postgres BIGINT(다시 ensure_table) 모두 의도된 동작.
- ✅ **확장성**: 새 connector(snowflake/duckdb/clickhouse) 추가 시 dialect_ddl 한 entry + ensure_table 구현으로 끝.
- ✅ **Backward-compat**: SinkConfig.auto_create_table 기본 False → 기존 파이프라인 무영향. `_auto_create_sink_tables`는 best-effort라 capability 없는 connector(HTTP/Kafka)는 silent skip.
- ✅ **PostgresConnector.list_columns 정밀도 향상**: precision/scale + character_maximum_length 추가 emit → cross-DB뿐 아니라 SchemaInspector REST에서도 더 정확한 정보.
- ✅ 코어 unit 738 → 799 (+61 신규: type_mapping 모든 dialect/canonical 조합 50+ + sqlite ensure_table 6).
- ⚠ **테스트 시점 PostgreSQL connector의 raw type 문자열은 lower-case** — `bigint` vs `BIGINT`. normalize는 대소문자 무관이라 무영향이지만 통합 테스트가 lower-case 기대.
- ⚠ **JSON ↔ JSONB 변환은 connector write 책임**: PostgresConnector는 dict → JSONB 자동, SQLiteConnector는 dict → text 변환 필요(시나리오에서 명시적 json.dumps). 추후 별도 슬라이스에서 connector-level 자동 변환 가능.
- ⚠ **list_columns 정밀도 보강은 postgres만**: mysql/sqlite도 같은 패턴으로 향후 보강 가능. 현재 mysql은 `data_type`만 반환(VARCHAR 길이는 별도 column).

**검증**:
- 코어 unit **799** (+61: type_mapping 50+ + sqlite ensure_table 6 + 기타).
- 서버 it 472→473(+1 sqlite→sqlite e2e auto_create_table).
- 통합 it **2 신규**(postgres↔sqlite 양방향 cross-DB, testcontainers postgres).
- mypy 코어 62 + 서버 100 OK. ruff clean. DB 마이그레이션 0(metadata DB 무변경). backward-compat 완전 유지.

---

## ADR-0067: Engineer cross-DB onboarding journey — REST 전 path UX 검증

**Date**: 2026-05-29
**Status**: Accepted
**Context**: ADR-0066이 코어 메커니즘 + 통합 it cross-DB 양방향을 입증. 그러나 *사용자(엔지니어)* 입장에서 REST를 통해 cross-DB pipeline을 만들고 실행하는 전체 흐름이 자연스러운지는 별도 검증. Phase TT/UU 페르소나 패턴의 4번째 페르소나 = **데이터 엔지니어 cross-DB**.

**Decision**:
1. **`test_cross_db_engineer_journey_scenario.py`(신규)** — WW1: 엔지니어가 REST로 cross-DB pipeline 운영:
   - **Source warehouse**: sqlite + 의도적 mixed vendor 타입(`BIGINT/NUMERIC(10,2)/TIMESTAMPTZ/JSONB/VARCHAR(64)`).
   - **REST 흐름**: `POST /auth/login` → `POST /workspaces` → `POST /connections × 2` → `POST /pipelines`(sink에 `auto_create_table: True`) → `POST /dry-run`(ok=True + warnings 필드) → `POST /trigger` → drain → `GET /runs/{rid}`(SUCCEEDED + records_written=2) → sqlite 파일에서 `PRAGMA table_info` 직접 확인 → `GET /assets` 카탈로그 확인.
   - **타입 affinity 결과 검증**: `id INTEGER` + `amount NUMERIC(10,2)` + `created_at TEXT` + `payload TEXT` + `customer TEXT`(VARCHAR(64) length 드롭).
2. **검증 axis**: REST 응답 shape + sink 파일 실제 DDL + 카탈로그 asset_key 3-layer 일관성.

**Consequences**:
- ✅ **엔지니어 페르소나 cross-DB UX 검증**: ADR-0066 기능이 REST 표면에 자연 노출 + 사용자 흐름이 끊김 없음.
- ✅ **사용자 페르소나 시리즈 4번째**: 운영자(TT) + 분석가(UU) + 관리자(VV 폐기) + **엔지니어 cross-DB(WW)**. 핵심 페르소나 모두 sample-data e2e로 커버.
- ✅ **REST surface area 변경 없음**: SinkConfig.auto_create_table는 Pipeline config의 옵셔널 필드라 REST 응답 schema에는 영향 없음. UI도 추가 작업 없이 YAML/Builder에서 사용 가능.
- ✅ DB 마이그레이션 0. 신규 e2e만.
- ⚠ **빌더 UI에서 toggle 노출은 별도 슬라이스**: 현재는 raw config 텍스트로만 박을 수 있음. 빌더 properties panel에 체크박스 추가는 web 슬라이스로.

**검증**: 코어 unit 799 unchanged. 서버 it 473→474(+1 WW1). mypy 코어 62 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0068: Transform-aware `auto_create_table` — 7번째 silent miss 보완

**Date**: 2026-05-29
**Status**: Accepted
**Context**: 사용자 페르소나 *"엔지니어가 cross-DB pipeline에 SCD Type 2 transform 추가 시나리오"* (XX1)를 작성하다 발견된 7번째 silent miss: ADR-0066의 `auto_create_table`이 *source 원본* schema로 sink 테이블 생성. transform이 rename/drop/add column하면 sink에 post-transform 컬럼이 없어 write 실패 (`no column named region`).

**Decision**:
1. **`Task.transform_specs: list[dict]` 필드 추가** — compiled callable(`Task.transforms`) 옆에 raw `TransformConfig.model_dump()` 보관. introspection-only 용도.
2. **builder가 `task.transform_specs.append(tc.model_dump())`** — `build_transform` 호출과 함께. sql_exec(pre_sql로 빠짐)은 제외.
3. **새 helper `_project_columns_through_transforms(source_columns, task)`** — declarative chain 시뮬레이션:
   - `rename`: key swap + type 보존.
   - `add_constant`: 새 컬럼 + TEXT fallback type.
   - `cast`: target type으로 update.
   - `drop`: key 제거.
   - `select`: keep listed만.
   - `filter` / `dedupe` / `assert`: row-level, no shape change.
   - `python` / `custom_python` / `sql_exec` / unknown: return `[]` → caller가 source verbatim fallback (Phase VV 동작 보존).
4. **`Pipeline._auto_create_sink_tables`가 projected columns 사용** — empty list면 source columns로 fallback.

**Consequences**:
- ✅ **Cross-DB + transform 패턴 정상 동작**: SCD Type 2 / rename / add column 모두 sink 자동 DDL 정확.
- ✅ **Phase VV backward-compat 완전 유지**: opaque transform(python 등)은 옛 동작(source verbatim) 그대로.
- ✅ **새 declarative transform 타입은 helper에 case 추가하면 자동 처리** — 확장성.
- ✅ **사용자 페르소나 dogfooding이 또 catch**: 단순 cross-DB 복제 + transform 조합 운영 패턴이 단위 테스트로는 못 잡힌 silent miss를 시나리오로 catch.
- ✅ 이번 세션 **7번째 silent miss**: Z/BB/II×2/MM/UU/XX. dogfood 정량적 가치 누적.
- ✅ DB 마이그레이션 0. Task dataclass에 필드 추가만, builder 한 줄, runtime helper 1개.
- ⚠ **opaque transform이 컬럼을 추가하는 케이스는 여전히 cover 안 됨**: python transform이 add/rename하면 source verbatim fallback → write 실패. `column_mapping` declaration이 있을 때만 활용 가능(향후 슬라이스).
- ⚠ **`add_constant`의 type은 항상 TEXT**: literal value type 추론 안 함. 사용자가 `cast` 후속으로 type 지정.

**검증**: 코어 unit 799 unchanged. 서버 it 474→475(+1 XX1 — transform-aware auto-create 동작). mypy 코어 62 + 서버 100 OK. ruff clean. DB 마이그레이션 0.

---

## ADR-0069: 빌더 UI에 `auto_create_table` 토글 노출 + `boolean` FieldKind

**Date**: 2026-05-29
**Status**: Accepted
**Context**: ADR-0066/0068이 코어 기능 + 서버 e2e + REST 표면을 제공했지만 빌더 사용자(데이터 엔지니어 / 비개발 분석가)가 raw YAML로 박아야 함. UX 마지막 1마일.

**Decision**:
1. **`FieldDef`에 `kind: "boolean"` 추가** — `boolean` 토글 필드. `defaultValue` 옵셔널. wire shape는 `true` / `false` — serializer는 `false`를 dropper(`onChange(e.target.checked || undefined)` → `undefined` → n.data 안 들어감).
2. **Properties panel renderer가 boolean kind 처리** — `<input type="checkbox">` + label. ws의 accent color 사용.
3. **3개 RDBMS sink operators(postgres / mysql / sqlite)에 `auto_create_table` 필드 추가** — "Create table if missing" 라벨 + help text(cross-DB 타입 자동 변환 설명).
4. **wire shape**: `false`는 config에서 누락 → 기존 SinkConfig.auto_create_table 기본값 False와 정합. 옛 파이프라인 무변경.

**Consequences**:
- ✅ **빌더 사용자가 한 클릭으로 cross-DB migration 활성화**: postgres→sqlite, mysql→postgres 등.
- ✅ **`boolean` FieldKind는 향후 다른 토글에도 재사용 가능**: `cancel_on_failure` / `enable_dlq` / `retry` 등.
- ✅ **타입 안전**: ts strict + Pydantic + 코어 dataclass 3-layer 일관.
- ✅ Backward-compat: 새 필드 옵셔널 + 기본 false. 옛 파이프라인 무영향.
- ⚠ **Mongo / S3 / Kafka sink는 미포함**: SchemaWriter 미구현. 추후 schema registry / object schema 슬라이스에서 가능.

**검증**: web typecheck(tsc) clean. 코어 unit 799 unchanged. 서버 it 475 회귀 green. DB 마이그레이션 0. backward-compat 완전 유지.

---

## ADR-0070: Cross-DB fan-out — sink별 `auto_create_table` 독립 동작 검증

**Date**: 2026-05-29
**Status**: Accepted
**Context**: 운영 흔한 패턴: 같은 source를 두 destination(analytics sandbox + warehouse)으로 fan-out, 한 곳은 새 테이블 자동 생성, 다른 곳은 기존 테이블 유지. ADR-0066/0068의 `auto_create_table`이 sink별 독립 적용되는지 sample-data로 확인.

**Decision**:
1. **`test_cross_db_fanout_scenario.py`(신규)** — ZZ1: 동일 source → 2 sinks:
   - Sink #1 (`analytics`): fresh sqlite file, `orders_copy` 없음, `auto_create_table: true` → 자동 생성.
   - Sink #2 (`warehouse`): 이미 `orders_copy(id, amount, batch)` 존재, flag 없음 → untouched.
2. **검증**: 양 sink에 데이터 정상 write + analytics는 `(id, amount)`만 / warehouse는 기존 `(id, amount, batch)` 그대로.

**Consequences**:
- ✅ **Sink별 독립 동작 확인**: 운영자가 fan-out 시 일부 sink만 자동 생성 가능.
- ✅ **기존 테이블 보호**: flag 없는 sink는 절대 ALTER 안 함.
- ✅ DB 마이그레이션 0. 신규 e2e만.

**검증**: 서버 it 475→476(+1 ZZ1). DB 마이그레이션 0.

---

## ADR-0071: `auto_create_if_exists` — sink 자동 생성 충돌 정책

**Date**: 2026-05-29
**Status**: Accepted
**Context**: ADR-0066/0068의 `auto_create_table`은 "테이블 없으면 만든다(skip if exists)"만 처리. 운영 현실에서는 두 가지 다른 요구가 추가됨:
1. **Nightly snapshot replication** — source 스키마가 매일 진화. sink는 append가 아니라 *재구축* 되어야 함. 어제의 stale 컬럼이 남으면 안 됨.
2. **Strict 보호** — 같은 이름의 기존 테이블이 있으면 자동 생성을 *거부*하고 운영자가 수동 처리하도록 강제.

**Decision**: `SinkConfig.auto_create_if_exists`(str, default `"skip"`) 도입.
- `"skip"` (기본, BC) — 테이블 있으면 그대로 사용.
- `"drop"` — 테이블 있으면 `DROP TABLE` 후 새 스키마로 재생성.
- `"error"` — connector 차원에서 raise. 단, 런타임의 `_auto_create_sink_tables`는 `contextlib.suppress`로 감싸 best-effort이므로 *런타임에서는* 무력화 — 원본 테이블이 그대로 유지된다. raw script(`ensure_table` 직접 호출)에 대한 가드 의미만 가짐.
- 3 RDBMS connector(sqlite/postgres/mysql) `ensure_table`이 이미 if_exists 파라미터 지원(ADR-0066). 빌더 단방향 forwarding(single-sink/fan-out/graph 3 path)으로 wired.

**Consequences**:
- ✅ **Snapshot/scratch sink 재구축** — 일일 백필이나 explore용 sandbox에 적합한 동작.
- ✅ **BC 유지** — default `"skip"`이라 기존 YAML/UI 영향 없음.
- ⚠️ `"error"` 모드의 *런타임* 시맨틱은 best-effort 정책에 가려져 "원본 그대로"로 떨어진다 — 운영자가 sink 충돌을 확실히 막으려면 별도 pre-check transform("Assert table doesn't exist")이 필요. AAB+ 슬라이스에서 후속.
- ✅ **DB 마이그레이션 0**. 신규 e2e 2(AAA1=drop / AAA2=error 현재 시맨틱 문서화).

**검증**: 서버 it 476→478(+2 AAA1/AAA2). 코어 unit 799 green. mypy/ruff clean.

---

## ADR-0072: Cross-DB upsert — `ensure_table`가 `key_columns`를 PRIMARY KEY로 emit

**Date**: 2026-05-29
**Status**: Accepted
**Context**: Phase AAC dogfood가 발견한 8번째 silent miss. `auto_create_table=true` + `mode=upsert` + `key_columns=[id]` 조합으로 fresh sink 빌드 시:
- `ensure_table`이 PRIMARY KEY/UNIQUE 없이 plain CREATE TABLE 발행 → 첫 write가 `ON CONFLICT (id) DO UPDATE` / `ON DUPLICATE KEY UPDATE` 시 *"ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint"* 로 실패.
- 사용자가 "live customers cache" 패턴(매 run 최신 snapshot upsert)을 만들면 첫 run부터 망가짐. 운영 직관적인 사용 패턴.

**Decision**:
1. **`SchemaWriter.ensure_table`에 `primary_key: list[str] | None = None` 추가**. None이면 기존 동작(plain CREATE TABLE).
2. **`Pipeline._auto_create_sink_tables`가 `spec.mode == "upsert" and spec.key_columns`일 때 `primary_key=spec.key_columns` 자동 forwarding**. append/overwrite는 None 유지(불필요한 unique index 부담 회피).
3. **3 RDBMS connector(sqlite/postgres/mysql)**: PRIMARY KEY를 column fragments 끝에 table constraint로 emit(`PRIMARY KEY (id, ...)`). 각 dialect의 identifier quote 그대로(sqlite `"id"` / postgres `"id"` / mysql `` `id` ``). PK 컬럼이 columns에 없으면 WriteError, identifier 안전성 검증.

**Consequences**:
- ✅ **Cross-DB upsert 첫 run 자동 동작** — fresh sink + upsert + key_columns 조합이 just works.
- ✅ **BC 유지**: append/overwrite 모드는 PK 없이 그대로 생성 — 기존 동작.
- ✅ **Index overhead 명시적**: upsert일 때만 PK 인덱스 비용 발생(append-only는 영향 0).
- ⚠️ Composite PK도 자연 지원(`["region", "id"]`) — 운영자가 의식하지 않아도 됨.
- ✅ **DB 마이그레이션 0**. 코어 unit 799→802(+3 sqlite primary_key 검증) + 서버 it 479→480(+1 AAC1).
- ✅ 이번 세션 8번째 silent miss(Z/BB/II×2/MM/UU/XX/AAC) — dogfooding 가치 누적 증명.

---

## ADR-0073: Vertica + MSSQL 커넥터 — 마이그레이션 대상 dialect 확장

**Date**: 2026-05-29
**Status**: Accepted
**Context**: 사용자 *"연결 유형 좀 더 추가해줘. vertica 포함해주고. 그에 따른 테이블 복사 및 마이그레이션도 확장되어야해"*. 기존 RDBMS 커넥터는 postgres / mysql / sqlite 3종 — 엔터프라이즈에서 흔한 SQL Server (MSSQL) 와 분석용 column-store인 Vertica가 빠져 있어 cross-DB migration 활용성에 제약.

**Decision**:
1. **신규 커넥터 2종** (`etl_plugins/connectors/rdbms/`):
   - `vertica.py` — `vertica-python` 클라이언트, postgres-flavoured SQL. UPSERT는 `MERGE`로 emulate.
   - `mssql.py` — `pymssql`(FreeTDS) 클라이언트, square-bracket identifier. UPSERT는 native `MERGE`.
   - 둘 다 **lazy import** — 모듈은 driver 없어도 로드, `connect()`에서 명확한 `ConnectError`.
   - `BatchSource + BatchSink + SchemaInspector + SchemaWriter + SqlExecutor` 풀 구현 (다른 RDBMS와 contract 동일).
   - `auto_create_table` + `primary_key` (ADR-0072) 자동 지원.
2. **`core/type_mapping.py` dialect 추가**:
   - `vertica`: BIGINT/NUMERIC 그대로, TEXT/JSON → LONG VARCHAR(JSON 컬럼 없음), BOOLEAN/DATE 1급, BLOB → VARBINARY.
   - `mssql`: TEXT → NVARCHAR(MAX), BOOLEAN → BIT, TIMESTAMP → DATETIME2, BLOB → VARBINARY(MAX).
   - vendor 파싱 확장: `nvarchar`/`nchar`/`ntext`/`bit`/`datetime2`/`datetimeoffset`/`money`/`uniqueidentifier`/`long varchar` 등 → 적절한 canonical.
3. **`pyproject.toml`**: `[vertica]` (`vertica-python>=1.4`), `[mssql]` (`pymssql>=2.3`) extras + entry-points 등록.
4. **Web operators.ts**: `source:vertica`/`source:mssql`/`sink:vertica`/`sink:mssql` 4종 추가 — 기존 RDBMS sink 필드와 동일(connection/table/mode/key_columns/pre_sql/auto_create_table/auto_create_if_exists).
5. **`migration-config.ts` `MIGRATION_SUPPORTED_TYPES`**: vertica + mssql 포함 → 마이그레이션 폼이 두 신규 type의 connection을 source/sink로 노출.
6. **`connector-schemas.ts`**: 연결 생성 폼에 host/port/database/user/password (vertica는 ssl, mssql은 tds_version) 추가.
7. **server `_SQL_CONNECTION_TYPES`** (audit): vertica + mssql 추가 (run.sql_read/sql_executed audit 정확성).

**Consequences**:
- ✅ **마이그레이션 매트릭스 5×5 = 25 페어**(이전 3×3 = 9) — postgres ↔ vertica ↔ mssql ↔ mysql ↔ sqlite 모든 방향.
- ✅ **lazy import 패턴**: 사용자가 `[vertica]` extra 안 깔아도 import 안 깨짐 — 운영 friction 감소.
- ✅ 코어 unit 812→850(+38 type_mapping × 2 dialect). 서버 it 485 (worker test 기존 1건 검정 위닝 갱신).
- ✅ mypy/ruff clean. DB 마이그레이션 0. backward-compat 완전(기존 3 RDBMS dialect 영향 0).
- ⚠️ **testcontainers integration 미포함** — Vertica/MSSQL는 무거운 Docker 이미지(license 게이트) — testcontainers 추가는 별 슬라이스.
- ⚠️ **MERGE upsert는 한 행씩 실행** — 배치 MERGE는 후속 슬라이스 후보.

---

## ADR-0074: SQL 필드도 Monaco IDE — `CodeEditor` 일반화 + `kind: "sql"`

**Date**: 2026-06-04
**Status**: Accepted
**Context**: 사용자 *"SQL 쿼리도 Python 처럼 IDE 활용해줘"*. 빌더에서 Python(`custom_python`)은 Monaco 에디터(문법 하이라이팅·줄번호·테마 동기화, ADR-0041 I2)를 쓰는데, SQL 입력 surface 2곳은 plain `<textarea>`였음 — ① source `sourceQuery`의 raw-SQL 모드, ② `sql_exec`("Run SQL") 노드의 statement(`kind: "string"` + `multiline`). 비자명한 JOIN/CTE를 textarea로 작성·검토하기 불편.

**Decision**:
1. **`components/builder/code-editor.tsx` 신설** — `PythonCodeEditor`의 Monaco 셋업(lazy-load + SSR off, `data-theme` 동기화, **uncontrolled `defaultValue`** = 2026-05-26 cursor-jump fix 계승)을 `language` prop을 받는 범용 `CodeEditor`로 추출. `PythonCodeEditor`는 `<CodeEditor language="python">`로 위임(공개 API·starter 불변).
2. **`sourceQuery` SQL 모드** → `<CodeEditor language="sql">`. 모드 토글 시 에디터가 remount되어 visual에서 방금 만든 쿼리도 `defaultValue`로 들어옴.
3. **신규 `FieldDef` kind `"sql"`** — `sql_exec` statement 필드를 `string`+multiline → `kind: "sql"`로 교체. properties-panel에 `sql` 렌더러(`<CodeEditor language="sql">`) + `composite` 집합 포함(Monaco 클릭이 textarea로 새지 않게, pythonCode와 동일 이유). starter 없음(SQL statement는 boilerplate shape 없음).

**Consequences**:
- ✅ Python/SQL 입력이 동일 IDE 경험으로 통일. 코어/서버 변화 0(순수 web). web tsc clean + production build 통과.
- ⚠️ **Storybook 시각 baseline 변경** — `source-query-field.stories.tsx`의 JOIN 쿼리 스토리가 SQL 모드(Monaco lazy-load fallback)를 렌더 → 기존 textarea 스냅샷과 diff. Monaco 에디터는 SSR-disabled lazy-load라 의미 있는 스냅샷이 어려워 `PythonCodeEditor` 선례대로 전용 story는 두지 않음. ADR-0018(디자인 시스템) 맥락에서 이 변경을 기록.
- backward-compat: 저장 wire shape 불변(둘 다 plain string). 기존 파이프라인 영향 0.

**Follow-ups (2026-06-04)**:
- **ADY**: Monaco는 필드 placeholder를 렌더 안 함 → 사라진 SQL 예시를 "예: {example}" muted 힌트로 복원.
- **ADZ**: `CodeEditor` 전체화면 토글 — remount 없이 container className만 inline ↔ `fixed inset-0` 토글(MonacoEditor 트리 동일 위치 유지 → 버퍼·커서 보존), `automaticLayout` 리사이즈, Esc 종료.
- **AEA**: JSON 필드도 `CodeEditor language="json"` — Monaco 내장 JSON 언어 서비스로 실시간 squiggle 검증(SQL/Python엔 built-in validation 없음, JSON만 무료로 얻음).
- **AEB**: 5 RDBMS sink `pre_sql`도 `kind:"sql"` → 빌더 전 SQL 입력(source/Run SQL/pre_sql) 동일 IDE.
- 검증: dev 서버 동시 실행 중이라 `next build` 금지(메모리 규칙), `tsc` + 실행 중 dev에 curl(200/compile-error 확인)로 런타임 검증.

---

## ADR-0075: DLQ record preview — 동기 bounded 읽기 엔드포인트

**Date**: 2026-06-04
**Status**: Accepted
**Context**: 운영자 요청 *"DLQ UI — 실패 record를 실제로 본다"*. 기존엔 (a) 빌더에서 DLQ를 *설정*하고, (b) run 상세에서 DLQ로 라우팅된 *수*만(Phase AFB, metrics `etl_plugins.errors` `routed=dlq`) 봤다. 실패 record의 **내용**은 못 봤다. DLQ는 사용자가 지정한 sink(connection+table)일 뿐이라 그 안의 record를 보려면 그 테이블을 읽어야 한다. 서버엔 임의 sink/table을 읽는 엔드포인트가 없었다. `dry_run.py`는 "record sampling은 worker-side `run_pipeline_yaml(stop_after_records=N)` 경로로, 여기 재구현 금지"라고 명시적으로 scope-out 해뒀다 — 단, 그건 source 샘플링(transaction/stream/back-pressure)에 대한 주의였다.

**Decision**:
1. **`GET /workspaces/{ws}/pipelines/{pid}/dlq/records?limit=N`** (Viewer+, read-only, audit 없음). `limit`은 서버에서 `[1, 200]`로 클램프(기본 50).
2. **`etlx_server/pipelines/dlq_preview.py` `DlqPreviewService`** — dry-run의 connection 해석 헬퍼(`load_connections_by_name` / `resolve_placeholders` / `build_connector` + `WorkspaceVariableRepository.as_dict`)를 재사용해 현재 버전의 `dlq` config를 해석한다. DLQ sink가 `BatchSource`이면(모든 RDBMS 커넥터) 최대 N행을 읽어 raw record 리스트를 반환.
3. **동기 bounded 읽기**: `connect()` → `read(query=…, chunk_size=limit)`를 `asyncio.to_thread`에서 열고/닫는다. `dry_run.py`의 worker-path 주의는 **source 샘플링**(긴 트랜잭션/스트림)에 대한 것 — DLQ는 작은 단방향 테이블이고 `LIMIT`으로 bounded이므로 동기 읽기가 적절. 스트림/트랜잭션-heavy 경로가 **아니다**.
4. **dialect-aware preview query**: MSSQL은 `SELECT TOP n *`, 나머지(sqlite/postgres/mysql/vertica)는 `SELECT * … LIMIT n`. 이 dialect 지식은 **서버에 둔다**(코어를 서비스 편의로 바꾸지 않는다 — 코어 격리 규칙).
5. **방어**: 테이블명을 `^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$`로 제한(`unsafe_table` 거부). 값은 워크스페이스 자체 config에서 오지만 SELECT에 splice하므로 defense-in-depth.
6. **읽을 수 없는 경우 graceful**: `available=False` + 안정적 머신 `reason`(`no_dlq` / `stream_dlq`(topic) / `connection_missing` / `connection_build_failed` / `sink_not_readable`(Kafka/HTTP/write-only) / `unsafe_table` / `invalid_config` / `read_failed`)으로 UI가 안내 문구로 매핑.

**Consequences**:
- ✅ DLQ record 내용을 처음으로 조회 가능 — AFB(수) 다음 자연 단계. 서버 단독으로 동작, web은 후속 슬라이스(DLQ-2).
- ✅ **코어 변화 0** — 기존 `BatchSource.read()` 사용. 서버 신규 모듈 1 + 엔드포인트 1 + 스키마 1.
- ✅ 신규 서버 시나리오 4 it green(testcontainers): 실제 DLQ 라우팅 run(Phase II) 위에 preview를 얹어 라운드트립 검증(happy/limit/no_dlq/connection_missing). mypy + ruff clean.
- ⚠️ **RDBMS sink만 읽힘** — Kafka/HTTP/S3 DLQ는 `sink_not_readable`. 그 경우 UI는 "이 sink 유형은 미리보기 불가" 안내(사용자가 직접 조회). S3/object-storage 읽기는 후속 슬라이스 후보.
- ⚠️ **`SELECT *` 순서 비결정** — 어떤 N행인지는 connector/DB 순서에 의존. 미리보기 용도라 허용; 정렬/페이지네이션은 후속.

---

## ADR-0076: `dlq_recommended` lint 규칙 — per-record 코드 transform + DLQ 없음 경고

**Date**: 2026-06-04
**Status**: Accepted
**Context**: DLQ 뷰어(ADR-0075) 작업 중 dogfood로 드러난 footgun — `python`/`custom_python` transform은 record마다 임의 코드를 실행하므로 한 record에서 raise하면 `TransformError`로 **run 전체가 실패**한다. `dlq:`를 설정하면 그 record만 DLQ로 빠지고 나머지는 계속 처리(Phase II partial-success). 그런데 많은 파이프라인이 transform만 있고 DLQ가 없어, 운영자가 이 위험을 모른 채 운영하다 한 행 때문에 전체가 죽는다. 기존 lint(DD/FF/AAK)는 column lineage·auto_create만 다뤘다.

**Decision**:
1. **새 advisory 규칙 `dlq_recommended`** (`etl_plugins/runtime/lint.py`): `cfg.dlq is None` && 어떤 shape(linear/task-DAG/graph)든 `python`/`custom_python` transform이 있으면 파이프라인당 1회 경고. 메시지는 "transform이 raise하면 run 전체가 실패한다, dlq를 설정해 실패 record를 빼라". advisory(차단 안 함, `dry-run` ok 불변).
2. **`sql_exec` 제외** (`_PER_RECORD_CODE_TRANSFORM_TYPES = {python, custom_python}`) — sql_exec은 per-record가 아닌 one-shot statement라 record-level DLQ가 도움 안 됨.
3. **`location=None`** — 고칠 곳이 특정 노드가 아니라 pipeline settings의 `dlq` 블록이라 builder가 점프할 대상이 없음. AEN/AEO의 null-location 렌더(plain text) 경로 사용.
4. **web 변화 0** — dry-run `warnings`는 이미 빌더 DryRunPanel(AEN/AEO) + 마이그레이션 DryRunResultCard에 표시되고, 메시지는 서버 영어를 그대로 렌더(다른 lint 경고와 동일). 새 규칙이 자동 노출.

**Consequences**:
- ✅ 운영자가 dry-run에서 "DLQ 없이 transform 쓰면 한 행이 run을 죽인다"를 사전 인지 → DLQ 채택률↑, silent total-failure↓. AFB(DLQ 수)+ADR-0075(뷰어)+AFB partial 칩(DLQ-6)과 함께 DLQ 라이프사이클(권고→설정→실행→조회) 완성.
- ✅ 코어 unit 25 lint(19→25, +6 dlq 케이스). 기존 6개 정확-카운트 테스트는 `column_mapping_recommended`만 필터하도록 갱신(새 경고가 같은 fixture에서 동시 발화). 서버 dry-run/journey 21 it 회귀 0(membership 단언이라 안전).
- ✅ coarse 규칙(false-positive 비용 0 — DLQ가 늘 옳은 건 아니지만 advisory라 무해). mypy/ruff clean. DB 마이그레이션 0.
- ⚠️ graph shape도 커버(column_mapping consistency FF가 graph를 punt한 것과 달리, dlq는 단순 존재 검사라 graph 포함).

---

## ADR-0077: Snowflake 커넥터 추가 — 클라우드 DW 마이그레이션 대상 확장

**Date**: 2026-06-05
**Status**: Accepted (진행 중 — type_mapping 먼저, 커넥터/web/wiring 후속 슬라이스)
**Context**: 야간 DLQ/dogfood 백로그 소진 후 사용자가 "신규 DW 커넥터" 축 선택. Snowflake는 가장 보편적인 클라우드 DW. AAQ(vertica/mssql, ADR-0073) 패턴을 그대로 따라 5×5 → 6×6 마이그레이션 매트릭스로 확장.

**Decision**:
1. **`core/type_mapping.py`에 `snowflake` dialect 추가** (이 슬라이스): INTEGER/BIGINT/SMALLINT(=NUMBER(38,0) 별칭), FLOAT(REAL/DOUBLE 단일 64-bit), **NUMBER**(DECIMAL canonical), VARCHAR(TEXT+VARCHAR 통합, 무길이=16MB), BOOLEAN, **TIMESTAMP_TZ**, DATE, **VARIANT**(JSON semi-structured), BINARY(BLOB). vendor 파싱 확장: number/string/variant/object/array/timestamp_ntz|tz|ltz. `render_canonical`의 DECIMAL precision 부착 조건에 `NUMBER` 추가(NUMBER(p,s) 라운드트립).
2. **`snowflake.py` 커넥터** (후속): BatchSource/Sink + SchemaInspector(list_tables/list_columns) + ensure_table(auto_create + PRIMARY KEY) + execute_statement + read/write/merge upsert. driver(`snowflake-connector-python`) **lazy import**(extra 미설치 시 모듈 로드는 됨 — vertica/mssql 선례). `@ConnectorRegistry.register("snowflake")`.
3. **web** (후속): operators.ts source/sink 2종, connector-schemas.ts 폼(account/user/password/warehouse/database/schema/role), migration-config.ts MIGRATION_SUPPORTED_TYPES에 snowflake → 6×6.
4. **wiring** (후속): pyproject extras `[snowflake]` + entry-point, 서버 audit `_SQL_CONNECTION_TYPES`에 snowflake, ConnectorRegistry `_BUILTIN_MODULES` fallback(AAQ post-mortem), SPEC §6 지원 목록.

**Consequences**:
- ✅ type_mapping foundation 단독으로 완결·테스트 가능(드라이버 불요). 코어 unit +25 snowflake(911→936). 기존 fallback 테스트는 dialect="duckdb"로 교정(snowflake가 이제 실 dialect).
- ✅ 기존 5 dialect 영향 0(NUMBER substring은 NUMERIC/DECIMAL과 비충돌). mypy/ruff clean. DB 마이그레이션 0.
- ⚠️ **testcontainers 미포함** — Snowflake는 로컬 컨테이너가 없는 SaaS(계정 필요). vertica/mssql과 동일하게 커넥터 자체 contract test는 live 계정 게이트 → 단위는 type_mapping + (후속) 커넥터 순수 로직만.
- ⚠️ Snowflake 식별자 대문자 폴딩(unquoted → UPPERCASE) — 커넥터 슬라이스에서 quoting 정책 결정 필요.

---

## ADR-0078: BigQuery 커넥터 추가 — DBAPI 기반, 서버리스 DW

**Date**: 2026-06-05
**Status**: Accepted (진행 중 — type_mapping 먼저, 커넥터/web/wiring 후속)
**Context**: Snowflake(ADR-0077) 직후 같은 DW 축으로 BigQuery 추가. BigQuery는 전통 RDBMS와 달라 적응 필요: (a) host/port/user/password 대신 **service-account JSON 인증**, (b) 백틱 식별자 quoting, (c) `INFORMATION_SCHEMA`가 데이터셋-스코프, (d) DML INSERT 쿼터(행단위 INSERT 비권장), (e) GoogleSQL(INT64/FLOAT64/STRING/NUMERIC/JSON/BYTES).

**Decision**:
1. **`core/type_mapping.py` bigquery dialect** (이 슬라이스): INT64(INTEGER/BIGINT/SMALLINT 통합), FLOAT64(REAL/DOUBLE), NUMERIC(DECIMAL, precision 부착), STRING(TEXT+VARCHAR — STRING은 길이 무의미라 length 자동 drop), BOOL, TIMESTAMP, DATE, JSON(네이티브), BYTES. vendor 파싱: int64/byteint/float64/bignumeric/bytes(+ 기존 string/numeric/bool/json/datetime 재사용).
2. **커넥터 `bigquery.py`** (후속): `google-cloud-bigquery`의 **DB-API**(`google.cloud.bigquery.dbapi`)를 써서 vertica/snowflake와 동일 cursor 패턴(execute/executemany/fetchmany/description, paramstyle `%s`). 인증: `credentials_json`(서비스계정 dict/JSON, secret) 또는 ADC. 식별자는 **백틱**(`` `dataset.table` ``). `INFORMATION_SCHEMA`는 `<dataset>.INFORMATION_SCHEMA.{TABLES,COLUMNS}` 형태. lazy import. `@ConnectorRegistry.register("bigquery")`.
3. **web/배선** (후속): operators.ts source/sink, connector-schemas.ts 폼(project/dataset/credentials_json/location), migration-config → 7×7, pyproject `[bigquery]` extra+entry-point, registry builtin, 서버 audit + DLQ readable 타입.

**Consequences**:
- ✅ type_mapping 단독 완결·테스트(드라이버 불요). 코어 unit +26 bigquery. cross-DW(snowflake→bigquery: VARIANT→JSON, NUMBER→NUMERIC, TIMESTAMP_TZ→TIMESTAMP) 검증.
- ✅ STRING length-drop은 render_canonical의 기존 "VARCHAR in base" 가드로 자동(코드 변경 0).
- ⚠️ **DML INSERT 쿼터** — BigQuery는 행단위 DML INSERT가 비싸고 일일 쿼터 존재. 본 커넥터는 contract 일관성 위해 executemany INSERT 사용(vertica/mssql 선례). 대용량은 load job / Storage Write API가 정석 → **후속 최적화 슬라이스 후보**. ADR에 명시.
- ⚠️ **testcontainers 미포함** — BigQuery는 로컬 에뮬레이터가 제한적(쿼리 비호환). 커넥터는 fake-cursor 단위(생성 SQL) + live 프로젝트 게이트(snowflake/vertica/mssql 선례).

---

## ADR-0079: Redshift 커넥터 추가 — postgres-derived 클라우드 DW

**Date**: 2026-06-05
**Status**: Accepted (진행 중 — type_mapping 먼저)
**Context**: Snowflake(ADR-0077)/BigQuery(ADR-0078)에 이어 세 번째 클라우드 DW. Redshift는 postgres 와이어 프로토콜 파생이라 식별자 double-quote·대부분 타입이 postgres와 동일 — snowflake 커넥터가 가장 가까운 템플릿. 차이: SUPER(semi-structured), VARBYTE(binary), TEXT 없음(VARCHAR 최대 65535), official `redshift_connector` DBAPI.

**Decision**:
1. **type_mapping redshift dialect** (이 슬라이스): INTEGER/BIGINT/SMALLINT/REAL/DOUBLE PRECISION/DECIMAL(postgres와 동일), **TEXT→VARCHAR(65535)**(Redshift TEXT 미지원), VARCHAR(length), BOOLEAN, **TIMESTAMPTZ**, DATE, **SUPER**(JSON), **VARBYTE**(BLOB). vendor 파싱: super/varbyte(+ postgres int4/varchar/timestamptz 등 재사용).
2. **커넥터 `redshift.py`** (후속): `redshift_connector` DBAPI, host/port(5439)/database/user/password, double-quote quoting, information_schema 인스펙션, ensure_table(auto_create + PRIMARY KEY — Redshift는 미강제지만 DDL 유효), read/write, upsert는 MERGE(Redshift 2023+ GA). lazy import.
3. **web/배선** (후속): operators source/sink, connector-schemas 폼(host/port/database/user/password), migration → 8×8, pyproject [redshift] extra+entry-point, registry builtin, 서버 audit + DLQ readable 타입.

**Consequences**:
- ✅ type_mapping 단독 완결. 코어 unit +21 redshift. cross-DW(bigquery→redshift: JSON→SUPER, STRING→VARCHAR(65535), TIMESTAMP→TIMESTAMPTZ) 검증.
- ⚠️ **Redshift MERGE는 2023-04 GA** — 구 클러스터는 MERGE 미지원(그 경우 DELETE+INSERT 패턴이 정석). constant-SELECT-without-FROM USING 소스가 엔진에 따라 거부될 수 있음 → live 검증 게이트. ADR에 명시.
- ⚠️ **testcontainers 미포함** — Redshift는 로컬 컨테이너 없는 AWS 매니지드. fake-cursor 단위 + live 클러스터 게이트(snowflake/bigquery/vertica/mssql 선례).
- ⚠️ 대용량은 COPY(S3 경유)가 정석 — 본 커넥터는 행단위/배치 INSERT(contract 일관성). COPY 최적화는 후속.

---

## ADR-0080: ClickHouse 커넥터 추가 — 컬럼지향 OLAP, 부분 parity

**Date**: 2026-06-05
**Status**: Accepted (진행 중 — type_mapping 먼저)
**Context**: Snowflake/BigQuery/Redshift에 이어 네 번째이자 마지막 로드맵 DW. ClickHouse는 가장 이질적: (a) CREATE TABLE에 **ENGINE 절 필수**(MergeTree + ORDER BY), (b) 행단위 **UPSERT/MERGE 미지원**(append 최적화 — ReplacingMergeTree+머지 또는 비동기 mutation이 정석), (c) **Nullable(T)/LowCardinality(T) 래퍼**, (d) 폭-명명 정수(Int8/16/32/64, 대소문자 구분), (e) String이 text/binary/JSON 통합.

**Decision**:
1. **type_mapping clickhouse dialect** (이 슬라이스): Int32/Int64/Int16, Float32/Float64, Decimal(P,S), String(TEXT/VARCHAR/JSON/BLOB 통합), Bool, Date, DateTime64(3). 케이스 보존(Int64≠INT64). vendor 파싱: int16/int32/uint8|16|32|64/float32/fixedstring/datetime64(int8은 postgres BIGINT 유지 — ClickHouse Int8→BIGINT widening 안전).
2. **커넥터 `clickhouse.py`** (후속): `clickhouse_connect` DBAPI, 백틱 quoting, ensure_table는 **ENGINE = MergeTree() ORDER BY (pk|tuple())**(PK → 정렬키), list_columns에서 **Nullable()/LowCardinality() 언랩**, append(multi-row INSERT)/overwrite(TRUNCATE). **upsert는 명확한 WriteError**(ClickHouse는 ReplacingMergeTree 필요 — append 권장). lazy import.
3. **web/배선** (후속): operators source/sink(upsert 옵션은 노출하되 help에 제약 명시), connector-schemas 폼(host/port/database/user/password), migration → 9×9, pyproject [clickhouse] extra+entry-point, registry builtin, 서버 audit + DLQ readable.

**Consequences**:
- ✅ type_mapping 단독 완결. 코어 unit +25 clickhouse. cross-DW(redshift→clickhouse: SUPER→String, TIMESTAMPTZ→DateTime64(3)) 검증.
- ⚠️ **부분 parity** — ClickHouse sink는 snapshot(overwrite)/append만 완전 지원, **mirror(upsert)는 런타임 WriteError**(정직한 실패 + 안내). 마이그레이션 "mirror" 전략은 ClickHouse 대상에서 안 됨(문서화).
- ⚠️ ENGINE/ORDER BY 필수라 ensure_table이 MergeTree 가정(다른 엔진은 수동 pre_sql). Nullable 언랩은 커넥터에 한정(type_mapping은 generic 유지).
- ⚠️ **testcontainers 미포함** — fake-cursor 단위 + live 서버 게이트(타 DW 선례).

---

## ADR-0081: DynamoDB 커넥터 추가 — NoSQL 확장 + 첫 testcontainers 검증 커넥터(세션)

**Date**: 2026-06-05
**Status**: Accepted
**Context**: DW 로드맵(AGE~AGH) 완주 후 다음 축. object-storage DLQ를 먼저 검토했으나 per-record 라우팅 + fan-out 재읽기와 얽혀(S3 PUT 덮어쓰기로 인한 silent loss) 코어 핫패스 버퍼링/중복제거가 필요 — 잘 테스트된 DLQ 경로에 회귀 위험이 커 "깨끗한 슬라이스"가 아니라고 판단. 대신 DynamoDB 커넥터 선택: **localstack + boto3가 이미 dev-deps**라 DW 4종(fake-cursor only)과 달리 **실제 testcontainers 왕복 검증** 가능 + NoSQL 확장(현재 MongoDB뿐) + 신규 무거운 의존 0.

**Decision**:
1. **`dynamodb.py`** — `boto3` resource 기반 BatchSource/Sink(SchemaInspector/Writer 없음 — DynamoDB는 schemaless, 마이그레이션 대상 아님). read=`Table.scan`(페이지네이션), write=`Table.batch_writer`(25개 자동 배치+재시도). lazy import.
2. **타입 변환** — boto3 DynamoDB resource는 `float`을 거부하고 `Decimal`을 요구 → write 시 `float→Decimal`(str 경유로 0.1 정밀도 보존), read 시 `Decimal→int/float`. `_to_dynamo`/`_from_dynamo`가 dict/list 재귀.
3. **mode** — `put_item`은 PK로 교체(=upsert by key)라 append/upsert 둘 다 put. **overwrite는 거부**(DynamoDB는 cheap truncate 없음 — 명확한 WriteError).
4. **배선** — pyproject [dynamodb] extra(boto3 재사용) + entry-point, registry builtin. SQL 아니므로 서버 `_SQL_CONNECTION_TYPES`/DLQ readable에는 **미추가**(S3/Kafka/Mongo와 동일).
5. **web** — operators source/sink(append/upsert만), connector-schemas 폼(region/table/endpoint_url/credentials).

**Consequences**:
- ✅ **세션 첫 실제 testcontainers 통합 테스트 커넥터** — LocalStack DynamoDB로 write(batch_writer)→scan 왕복 + float↔Decimal 라운드트립 + put 교체(upsert) 4건 실증. 단위 9건(변환/검증/registry, 드라이버 불요).
- ✅ NoSQL 2종(MongoDB + DynamoDB). mypy/ruff clean. 코어 production 무영향(신규 모듈+목록).
- ⚠️ schemaless라 cross-DB 마이그레이션(SchemaWriter) 대상 아님 — 빌더 source/sink로만 노출(S3/Kafka 동일).
- ⚠️ scan은 전체 테이블 풀스캔(대용량은 비용/시간) — Query/필터 기반 incremental은 후속.

---

## ADR-0082: Cassandra 커넥터 추가 — 와이드칼럼(CQL), 마이그레이션 대상

**Date**: 2026-06-05
**Status**: Accepted (진행 중 — type_mapping 먼저)
**Context**: DynamoDB(AGJ) 후 NoSQL 추가 확장. Cassandra는 와이드칼럼이지만 **CQL이 tabular**(CREATE TABLE + PRIMARY KEY)라 DynamoDB와 달리 SchemaWriter/마이그레이션 대상이 됨. 차이: (a) **모든 INSERT가 upsert**(PK로 교체 — append/upsert 동일), (b) **decimal은 arbitrary-precision, (p,s) 미허용**, (c) JOIN/서브쿼리 없음, (d) lowercase CQL 타입(int/bigint/text/timestamp), (e) `cassandra-driver`(C-ext, 무거움).

**Decision**:
1. **type_mapping cassandra dialect** (이 슬라이스): int/bigint/smallint/float/double/decimal/text(TEXT+VARCHAR)/boolean/timestamp/date/blob. JSON→text(native 없음). vendor 파싱: varint/counter→BIGINT, ascii/uuid/timeuuid/inet→TEXT. **`_NO_PRECISION_DECIMAL_DIALECTS={"cassandra"}` 신설** — render_canonical이 cassandra decimal에 (p,s) 안 붙임(다른 dialect는 영향 0).
2. **커넥터 `cassandra.py`** (후속): `cassandra-driver`(Cluster/Session), double-quote quoting, system_schema.tables/columns 인스펙션, ensure_table(CREATE TABLE + PRIMARY KEY 필수), read=SELECT(paging), write=prepared INSERT(=upsert). overwrite=TRUNCATE+insert. lazy import. **fake-session 단위**(DW 패턴 — cassandra-driver 무거워 dev-dep 미포함, live 게이트).
3. **web/배선** (후속): operators source/sink, connector-schemas 폼(contact_points/port/keyspace/username/password), migration → 10×10, pyproject [cassandra] extra+entry-point, registry builtin, 서버 audit + DLQ readable(CQL은 SELECT/LIMIT 지원).

**Consequences**:
- ✅ type_mapping 단독 완결. 코어 unit +22 cassandra. cross-DB(clickhouse→cassandra) 검증. `_NO_PRECISION_DECIMAL_DIALECTS` 가드로 cassandra decimal 유효 CQL 보장.
- ✅ **SchemaWriter 대상** — DynamoDB와 달리 마이그레이션 매트릭스 합류(10×10). INSERT=upsert는 멱등 복제에 유리.
- ⚠️ **testcontainers 미포함** — cassandra-driver는 C-ext(빌드 부담) + Cassandra 컨테이너 기동 느림(~40s). DW 패턴(fake-session 단위 + live 게이트) 채택. DynamoDB의 실제 통합검증과 대비.
- ⚠️ CQL 제약(JOIN 없음, ALLOW FILTERING 비권장) — source는 단순 SELECT 위주, 복잡 쿼리는 사용자 책임.

---

## ADR-0083: Kinesis 커넥터 추가 — Streaming 확장(StreamSource/Sink) + LocalStack 검증

**Date**: 2026-06-05
**Status**: Accepted
**Context**: Streaming 카테고리는 Kafka뿐이었음. Kinesis 추가 — **boto3+localstack가 이미 dev-deps**라 DynamoDB처럼 **실제 testcontainers 왕복 검증** 가능 + Streaming 확장 + 신규 의존 0. Kafka(aiokafka, 네이티브 async)와 달리 boto3는 동기라 async 컨트랙트를 `asyncio.to_thread`로 래핑.

**Decision**:
1. **`kinesis.py`** — StreamSource/StreamSink(Kafka 패턴). `connect`이 boto3 client 생성, async 메서드가 blocking 호출을 `asyncio.to_thread`로 감쌈.
2. **subscribe** — describe_stream으로 샤드 열거 → 샤드별 shard iterator(기본 TRIM_HORIZON) → round-robin `get_records` 폴링, JSON 디코드 후 yield(unbounded — caller가 break). metadata에 shard_id/sequence_number/partition_key.
3. **publish** — `put_record`(JSON value + partition key). **commit/flush는 no-op**(raw Kinesis는 server-side checkpoint 없음 — KCL이 DynamoDB 사용, 범위 밖; put_record는 호출당 동기).
4. **배선** — pyproject [kinesis] extra(boto3 재사용) + entry-point + registry builtin. 스트림이라 SQL/마이그레이션/audit-SQL 목록 미추가(Kafka 동일). web operators stream source/sink(streaming:true, topic=stream, iterator_type) + connector-schemas 폼(region/endpoint_url/credentials).

**Consequences**:
- ✅ **LocalStack 실제 통합 it 2** — create_stream→publish(put_record)→subscribe(shard iterator get_records) 왕복 + metadata 검증. 단위 8(fake-client publish/subscribe 로직 + 인코딩 + driver-missing). DynamoDB와 함께 boto3-계열 커넥터는 실검증 라인.
- ✅ Streaming 2종(Kafka + Kinesis). mypy/ruff clean. 코어 production 무영향.
- ⚠️ subscribe는 단순 폴링(샤드 동적 분할/병합 미추적, enhanced fan-out 미사용) — 고처리량/리샤딩은 후속. checkpoint 없음(at-least-once는 caller 책임, Kafka commit과 대비).
- ⚠️ testcontainers 통합 it는 `-m it` 게이트(기본 unit run 미포함).

---

## ADR-0084: SQS 커넥터 추가 — 큐 기반 Streaming, 의미 있는 commit(ack)

**Date**: 2026-06-05
**Status**: Accepted
**Context**: Kinesis(AGL) 후 Streaming 추가. SQS도 boto3+localstack(dev-deps)라 실검증 가능 + 신규 의존 0. **Kinesis의 commit이 no-op이었던 것과 달리 SQS는 commit=메시지 delete(ack)** — StreamSource.commit 컨트랙트(after_sink_flush 시 호출)를 의미 있게 시연. 큐 기반 ETL(작업 적재/소비)은 흔한 패턴.

**Decision**:
1. **`sqs.py`** — StreamSource/Sink, boto3 동기를 `asyncio.to_thread`로 래핑(Kinesis 패턴). topic=큐 이름(get_queue_url로 URL 해석, 캐시) 또는 전체 URL.
2. **subscribe** — `receive_message` 롱폴(WaitTimeSeconds) 루프, JSON 디코드 yield. receipt handle을 `_pending`에 보관.
3. **commit** — `delete_message_batch`(10개씩)로 보관된 handle 삭제 = SQS ack. 삭제 안 하면 visibility timeout 후 재전달(at-least-once). **Kinesis no-op과 대비되는 진짜 checkpoint.**
4. **publish** — `send_message`(JSON body). flush no-op.
5. **배선** — pyproject [sqs] extra(boto3 재사용) + entry-point + registry. 스트림이라 SQL/migration 미추가. web stream source/sink + 연결 폼.

**Consequences**:
- ✅ **LocalStack 실제 통합 it 2** — publish→subscribe→commit 후 재수신 empty(삭제 ack 검증)까지. 단위 9(fake-client: URL 해석/캐시/passthrough, receive→yield→commit-delete, driver-missing).
- ✅ Streaming 3종(Kafka/Kinesis/SQS). boto3-계열 실검증 커넥터 3종(DynamoDB/Kinesis/SQS). mypy/ruff clean. 코어 production 무영향.
- ⚠️ FIFO 큐(MessageGroupId/Dedup) 미지원(표준 큐만) — 후속. visibility timeout/DLQ redrive는 SQS 측 설정에 위임.

---

## ADR-0085: Redis Streams 커넥터 추가 — StreamSource/Sink, XACK commit

**Date**: 2026-06-05
**Status**: Accepted
**Context**: AWS-localstack 계열(DynamoDB/Kinesis/SQS) 소진 후 Redis 추가. Redis는 localstack 미지원이라 실통합검증 불가 → DW 패턴(redis-py를 [redis] extra로만, fake-client 단위, live 게이트). **Redis Streams**(XADD/XREADGROUP/XACK)가 key-value보다 StreamSource/Sink에 자연스럽고 SQS처럼 **commit=XACK** ack 의미를 가짐.

**Decision**:
1. **`redis.py`** — StreamSource/Sink, redis-py 동기를 `asyncio.to_thread`로 래핑. nosql/ 디렉토리지만 스트림 컨트랙트(Redis Streams).
2. **subscribe** — consumer group 자동 생성(XGROUP CREATE + MKSTREAM, BUSYGROUP 관용), XREADGROUP(`>`)로 신규 entry 폴링, `data` 필드 JSON 디코드 yield. message id를 (stream,group)별 보관.
3. **commit** — XACK으로 보관 id ack(SQS delete와 동형 — 미ack 시 PEL 잔존/재처리). publish=XADD(`{"data": json}`). flush no-op.
4. **배선** — pyproject [redis] extra(redis>=5.0) + entry-point + registry + mypy override. 스트림이라 SQL/migration 미추가. web stream source/sink(group_id) + 연결 폼.

**Consequences**:
- ✅ 단위 8(fake-client: XADD/XREADGROUP→yield→XACK, BUSYGROUP 관용, driver-missing). Streaming 4종(Kafka/Kinesis/SQS/Redis), ack 의미 commit 3종(SQS/Redis/Kafka).
- ⚠️ **testcontainers 미포함** — localstack 비대상 + redis-py를 dev-dep에 안 넣음(DW 패턴). live Redis 게이트. (DynamoDB/Kinesis/SQS의 실검증과 대비.)
- ⚠️ Redis Streams 전용(pub/sub·key-value 미사용). consumer group PEL claim(XCLAIM)·trim(MAXLEN)은 후속.

---

## (이후 ADR 작성 시 위 양식을 복사해서 추가)
