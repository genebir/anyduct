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

## (이후 ADR 작성 시 위 양식을 복사해서 추가)
