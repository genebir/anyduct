# DESIGN.md — etlx-web 디자인 시스템

> `services/etlx-web` (Step 10)의 디자인 마스터 문서. 토큰·컴포넌트·인터랙션의 단일 진실(SSOT). 변경 시 ADR-0018을 갱신.
>
> 이 문서가 정한 토큰을 어기는 색·간격·폰트는 PR에서 reject. 모든 새 컴포넌트는 §11 토큰만 사용해서 작성한다.

---

## 1. 디자인 철학

ETL은 **데이터 신뢰성**이 생명이다. 도구가 "장난감처럼" 보이면 운영팀이 안 쓴다. 반대로 Airflow·Dagster·NiFi처럼 "기능 위주 + 디자인 후순위"로 가면 비개발자가 무서워한다. 우리는 그 사이를 친다.

세 가지 원칙으로 모든 결정을 거른다.

| 원칙 | 의미 | 실천 |
|---|---|---|
| **Trust** | 데이터 파이프라인은 잘못되면 큰일 난다. UI가 "조용히, 정확하게" 작동해 보여야 한다. | 깊은 네이비 베이스, 과한 그라데이션·이모지 배제, 상태 표시는 항상 명시적·일관적 |
| **Clarity** | 정보 밀도가 높아도 한눈에 잡혀야 한다. | 8pt grid, 일관된 위계, 색은 의미가 있을 때만 사용 |
| **Delight** | 매일 쓰는 도구라 매번 만족스러워야 한다. | 팝한 핑크 강조, 부드러운 200ms 모션, 키보드 우선, command palette |

> 한 줄 요약: **"감독실의 콘솔" 같은 느낌. 진지하지만 차갑지 않다.**

---

## 2. 디자인 언어 — Arc Browser 차용 요소

Arc의 어떤 점을 가져오고, 어떤 점은 의도적으로 버리는지 명시한다.

### 가져오는 것

1. **좌측 사이드바 중심 레이아웃** — 상단 메뉴바 최소화. 사이드바에 워크스페이스 / 파이프라인 / 연결 / 실행이력 / 설정. 접을 수 있고(240→64px), 펼치면 부드럽게 슬라이드.
2. **Spaces = Workspace** — Arc의 Space처럼, 워크스페이스마다 고유 색상 인디케이터(좌측 4px 바). 컨텍스트 전환이 시각적으로 즉시 인지.
3. **Command Palette (Cmd+K / Ctrl+K)** — 모든 액션(파이프라인 만들기, 연결 추가, run 검색, 설정 점프)의 진입점. 마우스보다 키보드.
4. **부드러운 translucent surface** — 모달·시트·드롭다운에 `backdrop-blur` + 반투명. 단, 본 캔버스는 불투명.
5. **미세한 border + 배경 색조로 위계** — drop shadow는 거의 안 씀. 카드 깊이는 surface 톤 차이로.
6. **마이크로 인터랙션** — 200ms ease-out 기본. hover시 살짝 lift, 활성시 좌측에 핑크 2px 인디케이터.
7. **큰 corner radius** — 카드 14px, 모달 20px, 버튼 10px. 부드럽고 모던.
8. **빈 상태도 차분하게** — 큰 일러스트·이모지 대신 짧은 문구 + 명확한 CTA 1개.

### 버리는 것

1. **Arc의 자유로운 Boost(사용자 색 커스터마이즈)** — ETL 도구에는 과함. 워크스페이스 컬러 인디케이터만 제공.
2. **Arc의 squircle 강조** — squircle은 brand가 강한데, 우리는 일반 rounded rectangle로 충분.
3. **숨겨진 UI** — Arc는 의도적으로 일부 UI를 숨긴다(URL bar 등). 우리는 데이터 도구라서 **항상 보이는** 사이드바·헤더가 안전.
4. **장난스러운 톤** — Arc의 마이크로카피는 캐주얼하지만, 우리는 한 톤 더 진지하게.

---

## 3. 컬러 시스템

### 3.1 톤 결정

- **베이스 = 깊은 네이비**. Arc 다크모드보다 약간 더 푸르다(Arc는 거의 검정에 가까운 회색).
- **강조 = 팝한 핫핑크**. 단일 색, 그라데이션은 아주 제한적으로(주요 CTA·로딩 정도).
- **다크모드가 기본**. 라이트모드도 지원하지만 데이터 도구는 다크가 자연스럽고 장시간 사용에 눈이 편하다.

### 3.2 다크 모드 토큰

| 토큰 | HEX | RGB | 용도 |
|---|---|---|---|
| `--bg-base` | `#0A1228` | 10 18 40 | 페이지 최하층 배경 |
| `--bg-surface` | `#0F1730` | 15 23 48 | 사이드바, 본 캔버스 |
| `--bg-elevated` | `#15203F` | 21 32 63 | 카드, 입력 필드 |
| `--bg-overlay` | `#1A2A4F` | 26 42 79 | hover 상태, 활성 row |
| `--bg-translucent` | `rgba(15,23,48,0.72)` | — | 모달·드롭다운 (backdrop-blur 16px와 함께) |
| `--border-subtle` | `#1F2D54` | 31 45 84 | 카드 외곽, 디바이더 |
| `--border-default` | `#2A3A6B` | 42 58 107 | 입력 필드, 강조 외곽 |
| `--border-strong` | `#3B4F8A` | 59 79 138 | focus ring 보조 |
| `--text-primary` | `#F4F6FB` | 244 246 251 | 본문 (95% 대비) |
| `--text-secondary` | `#A8B3D1` | 168 179 209 | 라벨, 메타 |
| `--text-muted` | `#6E7AA0` | 110 122 160 | placeholder, 비활성 |
| `--text-disabled` | `#4A557A` | 74 85 122 | disabled |
| **`--accent`** | **`#FF3D8B`** | 255 61 139 | **주요 CTA, 활성 인디케이터, 핵심 강조** |
| `--accent-hover` | `#FF5599` | 255 85 153 | hover |
| `--accent-active` | `#FF2D7C` | 255 45 124 | active/pressed |
| `--accent-muted` | `rgba(255,61,139,0.12)` | — | 핑크 tint 배경 (Badge, 선택 row) |
| `--accent-ring` | `rgba(255,61,139,0.40)` | — | focus ring |
| `--accent-gradient` | `linear-gradient(135deg,#FF3D8B 0%,#FF7AB3 100%)` | — | 메인 CTA, 로딩 스피너만 |

### 3.3 의미 컬러 (Semantic)

네이비/핑크와 충돌하지 않도록 차분한 톤으로 선정.

| 토큰 | HEX | 용도 |
|---|---|---|
| `--success` | `#4ADE80` | run success, healthy |
| `--success-muted` | `rgba(74,222,128,0.12)` | badge bg |
| `--warning` | `#FBBF24` | retry, slow |
| `--warning-muted` | `rgba(251,191,36,0.12)` | |
| `--error` | `#F87171` | failure, DLQ |
| `--error-muted` | `rgba(248,113,113,0.12)` | |
| `--info` | `#60A5FA` | scheduled, queued |
| `--info-muted` | `rgba(96,165,250,0.12)` | |

> 핑크(#FF3D8B)와 에러 코랄(#F87171)을 같은 화면에서 동시에 쓰면 시각 충돌. **핑크 = "이게 가장 중요한 액션"**, **에러 = "이게 잘못됐다"** 로 의미를 분리. 두 색이 가까이 보이는 곳은 디자인 리뷰 필수.

### 3.4 라이트 모드 토큰 (보조)

| 토큰 | HEX |
|---|---|
| `--bg-base` | `#F8F9FD` |
| `--bg-surface` | `#FFFFFF` |
| `--bg-elevated` | `#F4F6FB` |
| `--bg-overlay` | `#E8ECF5` |
| `--border-subtle` | `#E2E8F0` |
| `--border-default` | `#CBD5E1` |
| `--text-primary` | `#0A1228` |
| `--text-secondary` | `#475569` |
| `--text-muted` | `#94A3B8` |
| `--accent` | `#FF3D8B` *(동일, 충분히 대비)* |
| `--accent-muted` | `rgba(255,61,139,0.10)` |

### 3.5 워크스페이스 컬러 (Arc Spaces 차용)

워크스페이스마다 한 가지 색만 부여 — 사이드바 좌측 4px 인디케이터 + 워크스페이스 메뉴 색. 8가지 프리셋:

```
#FF3D8B (default pink), #6366F1 (indigo), #10B981 (emerald),
#F59E0B (amber), #EC4899 (magenta), #06B6D4 (cyan),
#8B5CF6 (violet), #14B8A6 (teal)
```

### 3.6 대비 검증

WCAG AA(4.5:1 본문, 3:1 큰 텍스트) 기준으로 다음이 통과해야 함:
- `text-primary` on `bg-base` → ~15:1 ✅
- `text-secondary` on `bg-base` → ~7:1 ✅
- `accent` on `bg-base` → ~5:8:1 (큰 텍스트·아이콘 OK, 작은 본문에 핑크 색 글씨는 금지)
- 모든 의미 컬러 muted bg + 해당 컬러 텍스트 → 검증 필수 (CI에 axe-core 추가)

> **금지**: 작은 본문 텍스트(13px 이하)를 핑크로 쓰지 않는다. 핑크는 항상 **버튼·인디케이터·아이콘·큰 헤딩** 같은 시각 요소.

---

## 4. 타이포그래피

### 4.1 폰트

- **Sans**: `Pretendard Variable` (primary) → `Inter` 폴백 → system stack. 가변 폰트, 가중치 45~920.
  Pretendard는 Apple SD Gothic Neo의 느낌을 웹에서 재현하는 한국어 우선 가변 폰트로, 한·영 혼용 화면에서 굵기·자간이 일관돼 Arc Browser 같은 차분한 느낌을 준다. 별도의 Latin 전용 폰트를 두지 않고 Pretendard 하나로 한·영을 모두 처리한다 (ADR-0025).
- **Mono**: `JetBrains Mono` — SQL/YAML/로그 표시용.
- **Display(선택)**: 큰 헤딩은 Pretendard Bold로 충분. 별도 디스플레이 폰트 없음 (Arc도 그러함).

폰트 파일은 `services/etlx-web/public/fonts/`에 self-host (`PretendardVariable.woff2`, woff2-variations). CDN 의존 금지 — 프로덕션 컨테이너가 런타임 외부 의존 없이 동작해야 함.

### 4.2 스케일

1.25 modular scale 기반, px 단위로 고정.

| 토큰 | size/line | weight | 용도 |
|---|---|---|---|
| `text-display` | 40 / 48 | 700 | 랜딩·온보딩 외에 거의 안 씀 |
| `text-h1` | 28 / 36 | 600 | 페이지 제목 |
| `text-h2` | 22 / 30 | 600 | 섹션 제목 |
| `text-h3` | 18 / 26 | 600 | 카드 제목 |
| `text-h4` | 15 / 22 | 600 | 작은 라벨 그룹 |
| `text-body` | 14 / 22 | 400 | **기본 본문** |
| `text-body-emphasis` | 14 / 22 | 500 | 강조 본문 |
| `text-small` | 13 / 20 | 400 | 메타 정보 |
| `text-label` | 12 / 16 | 600 | 폼 라벨, 칩 (uppercase 사용 가능, letter-spacing 0.04em) |
| `text-caption` | 11 / 14 | 500 | 헬프 텍스트, 푸터 |
| `text-mono` | 13 / 20 | 400 | 코드, SQL, 로그 |

### 4.3 한국어 처리

- 한글은 영문보다 시각적으로 두꺼워 보이므로 본문에 weight 400 사용 시 자연스러움 (Pretendard 400).
- 한글 line-height는 영문보다 1~2px 더 필요할 수 있으나, 위 스케일이 이미 여유 있음.
- 영문·숫자가 섞인 라벨: 시스템에 맡기고 별도 처리 X.

---

## 5. 간격 · 레이아웃 · 반경

### 5.1 간격 토큰 (8pt grid)

```
space-0   = 0
space-px  = 1
space-0.5 = 2
space-1   = 4
space-2   = 8
space-3   = 12
space-4   = 16
space-5   = 20
space-6   = 24
space-8   = 32
space-10  = 40
space-12  = 48
space-16  = 64
space-20  = 80
space-24  = 96
```

규칙: **컴포넌트 내부는 4/8/12/16, 컴포넌트 간 분리는 16/24/32, 섹션은 48/64.**

### 5.2 반경

| 토큰 | px | 용도 |
|---|---|---|
| `radius-sm` | 6 | 작은 칩, badge |
| `radius-md` | 10 | 버튼, 입력 필드 |
| `radius-lg` | 14 | 카드, 사이드바 항목 |
| `radius-xl` | 20 | 모달, 시트, 큰 패널 |
| `radius-2xl` | 28 | hero/empty 일러스트 컨테이너 |
| `radius-full` | 9999 | avatar, 라운드 칩 |

### 5.3 그림자

> 다크모드에서는 그림자가 거의 안 보인다. 깊이는 surface 톤으로 표현. 그림자는 모달에만 약하게.

```
shadow-sm:  0 1px 2px rgba(0,0,0,0.4)
shadow-md:  0 4px 12px rgba(0,0,0,0.5)
shadow-lg:  0 16px 40px rgba(0,0,0,0.6)  /* 모달 */
shadow-accent: 0 0 0 4px var(--accent-ring)  /* focus */
```

### 5.4 레이아웃

| 구성 | 값 |
|---|---|
| 사이드바 width | 240px (펼침) / 64px (접힘) |
| 헤더 height | 56px |
| 본 컨텐츠 max-width | 1280px (반응형 우선, breakpoint 768/1024/1280) |
| Gutter | 24px (mobile) / 32px (desktop) |
| 카드 padding | 20px (default) / 16px (compact) |

### 5.5 z-index

```
z-base:    0
z-sticky:  20
z-fixed:   30
z-dropdown:40
z-overlay: 50
z-modal:   60
z-toast:   70
z-tooltip: 80
z-cmdk:    90  /* command palette는 최상위 */
```

---

## 6. 모션 · 인터랙션

### 6.1 타이밍

| 토큰 | duration | easing | 용도 |
|---|---|---|---|
| `motion-fast` | 120ms | `ease-out` | 토글, 체크 |
| `motion-default` | 200ms | `cubic-bezier(0.4,0,0.2,1)` | 대부분의 hover/transition |
| `motion-slow` | 320ms | `cubic-bezier(0.4,0,0.2,1)` | 모달/시트 진입 |
| `motion-spring` | spring (framer) | `{stiffness: 300, damping: 30}` | 드래그, 노드 이동 |

### 6.2 표준 인터랙션 패턴

- **버튼 hover** — `brightness(1.1) + translateY(-0.5px)`, 200ms.
- **카드 hover** — `border-color → border-strong`, `bg → bg-overlay`, 200ms.
- **활성 항목** — 좌측에 핑크 2px bar 슬라이드 인 (200ms).
- **사이드바 항목 hover** — 배경만 살짝(8% opacity 핑크), 텍스트는 그대로.
- **focus ring** — 4px `accent-ring` (`rgba(255,61,139,0.4)`), `outline-offset: 2px`.
- **Loading** — 스피너는 핑크 그라데이션 호 회전. Skeleton은 `bg-elevated`에서 `bg-overlay`로 1.5초 shimmer.
- **페이지 전환** — opacity 0→1 + translateY(8px → 0), 200ms.
- **모달 진입** — backdrop fade 200ms, 카드는 scale(0.96→1) + opacity 320ms.
- **Toast** — 우상단(데스크탑) / 하단(모바일). 슬라이드 + fade, 4초 후 자동 사라짐.

### 6.3 금지 사항

- 무한 스피너로 막막함 주기 → 1초 넘어가면 progress 표시 또는 안내 텍스트.
- 5초 이상 작업은 toast → background job 패턴으로.
- 모든 마이크로 인터랙션을 한꺼번에 적용하지 말 것. 1 화면에 2~3개 정도가 적정.
- `prefers-reduced-motion: reduce`에서는 모든 transition을 `motion-fast`로 강등, transform은 제거.

---

## 7. 컴포넌트

shadcn/ui를 베이스로 깔고 토큰만 우리 것으로 갈아 끼운다. 새 컴포넌트 작성 시 §11 토큰 외 하드코딩 금지.

### 7.1 Button

| Variant | 배경 | 텍스트 | hover |
|---|---|---|---|
| `primary` | `accent-gradient` | white | brightness 1.1 |
| `secondary` | `bg-elevated` | `text-primary` | `bg-overlay` |
| `ghost` | transparent | `text-secondary` | `bg-overlay` |
| `destructive` | `error` | white | brightness 1.1 |
| `outline` | transparent + `border-default` | `text-primary` | `bg-overlay` |

크기: `sm` 32 / `md` 40 / `lg` 48 (height). radius 10. icon은 좌측, gap 8.

### 7.2 Input / Textarea / Select

- 배경 `bg-elevated`, border `border-subtle`, radius 10, height 40.
- focus: border `accent`, ring `accent-ring`.
- placeholder: `text-muted`.
- 에러: border `error`, 아래 helper text에 `error` 색.

### 7.3 Card

- 배경 `bg-elevated`, border `border-subtle`, radius 14, padding 20.
- 헤더(있으면): `h3` + 우측 액션 슬롯, 하단 border-subtle.
- hover시 interactive면 §6.2.

### 7.4 Sidebar (Arc-style)

```
┌─────────┬────────────────────────┐
│ ▌ ⌘K   │  헤더 (페이지명 + 액션) │
│  검색   ├────────────────────────┤
│         │                        │
│ Spaces  │   본 컨텐츠            │
│ ● 🏢 워 │                        │
│ ● 📊 마 │                        │
│         │                        │
│ ─────   │                        │
│ Nav     │                        │
│ ▌ 🔗 연 │                        │
│   📐 파 │                        │
│   📅 스 │                        │
│   📈 실 │                        │
│   ⚙  설 │                        │
└─────────┴────────────────────────┘
```

- 좌측 4px 컬러 bar로 현재 워크스페이스 표시. 워크스페이스 전환 = bar 색 슬라이드.
- 활성 항목: bg `accent-muted` + 텍스트 `accent` + 좌측 핑크 2px.
- 접힘 모드(64px): 아이콘만, hover 시 tooltip으로 라벨.
- 상단에 검색·command palette 진입(⌘K).

### 7.5 Command Palette

- 화면 중앙 위쪽 1/3 지점, max-width 640, radius 20, backdrop-blur 24px.
- 입력창 큼(56px), 아래에 그룹화된 결과 (최근/액션/네비/파이프라인/연결).
- 키보드: ↑↓ 이동, ⏎ 실행, Esc 닫기. 마우스 클릭도 OK.
- 그룹 헤더: `text-label` + uppercase + `text-muted`.

### 7.6 Pipeline Node (React Flow)

```
┌──────────────────────────────┐
│ ● postgres                 ⋯ │  ← header: 아이콘(컨넥터 컬러) + 타입명 + 메뉴
├──────────────────────────────┤
│ pg_prod / orders            │  ← 핵심 정보 1줄
│ ↻ every 1h · 5 mins ago     │  ← 메타 1줄
└──────────────────────────────┘
   ●                         ●     ← React Flow handle
```

- 너비 240, radius 14, bg `bg-elevated`, border `border-subtle`.
- selected: border `accent`, 작은 그림자.
- 타입별 아이콘 색: source = info, transform = warning, sink = success, dlq = error (어디까지나 인디케이터).
- handle: 핑크 점, 호버시 ring.

### 7.7 Status Badge

| Status | bg | text | dot |
|---|---|---|---|
| success | `success-muted` | `success` | `success` |
| running | `info-muted` | `info` | `info` (pulse) |
| warning | `warning-muted` | `warning` | `warning` |
| error | `error-muted` | `error` | `error` |
| queued | `bg-overlay` | `text-secondary` | `text-muted` |
| skipped | `bg-overlay` | `text-muted` | `text-disabled` |

크기: height 22, padding 4/10, radius 6, text-label.

### 7.8 Data Table

- 헤더 row: `bg-surface`, sticky, `text-label` uppercase, `text-secondary`.
- 본 row: `bg-base`, hover `bg-overlay`, selected `accent-muted` + 좌측 핑크 bar.
- divider: `border-subtle` 1px.
- 빈 상태: 표 안에 차분한 안내 + CTA.
- pagination: 우하단, 페이지 수보다 "rows N of M" 우선.

### 7.9 Dialog / Sheet

- backdrop: `rgba(10,18,40,0.6)` + `backdrop-blur(16px)`.
- dialog: 중앙, max-width 560, radius 20, padding 24, shadow-lg.
- sheet: 우측 슬라이드, width 520(데스크탑) / 100%(모바일), radius 좌측만 20.

### 7.10 Toast (Sonner 기반)

- 우상단(데스크탑) / 하단(모바일).
- 카드 스타일(bg-elevated + border-subtle + radius 14 + shadow-md).
- 아이콘: 상태 색.
- 액션 1개 노출 가능("취소" 같은).

### 7.11 Empty State

```
        [중성 아이콘 48px, text-muted]
        제목 (h3)
        설명 한두 줄 (text-secondary)

              [Primary Button]
              [Secondary link]
```

큰 일러스트나 mascot 금지. 메시지는 짧고 액션이 명확해야 한다.

---

## 8. 아이콘

- **세트**: Lucide Icons (line, stroke 1.5px). 별도 set 추가 금지.
- **크기**: 14 / 16 / 20 / 24. 본문 내부는 16, 버튼은 16, 사이드바 20, 헤더 액션 20.
- **색**: 텍스트와 동일 색 따라감 (currentColor). 강조 아이콘만 `accent`.
- **커넥터별 인디케이터 색** (작은 점):
  - postgres `#336791`, mysql `#4479A1`, sqlite `#003B57`,
  - s3 `#FF9900`, kafka `#231F20`(다크에서는 텍스트 색),
  - 미정 = `text-muted`

브랜드 로고는 SVG로 별도 보관 (`services/etlx-web/public/logos/`).

---

## 9. 다크 / 라이트 모드

- **기본은 다크.** OS 설정 따르기 + 사용자 수동 토글 (헤더 우측 메뉴).
- 토큰을 CSS variables로 분리. `data-theme="light"` 또는 `data-theme="dark"`로 스위치.
- 페이지 로드 시 깜빡임 방지: blocking inline script로 root에 클래스 먼저 적용.

---

## 10. 접근성 (a11y)

- WCAG **AA** 의무. 대시보드 핵심 페이지는 AAA 권장.
- 색만으로 의미 전달 금지 — 모든 status는 색 + 아이콘 + 텍스트 라벨 셋트.
- 키보드 내비게이션 완전 지원. 모든 인터랙티브 요소 `:focus-visible` 표시(§6.2 focus ring).
- 스크린리더: aria-label, aria-live(toast), aria-busy(loading).
- `prefers-reduced-motion` 존중.
- 폼은 label-input 1:1, 에러는 inline + aria-describedby.
- 자동 검증: CI에 `axe-core` + `pa11y` 한 페이지당 1회.

---

## 11. 구현 — 토큰 → 코드

### 11.1 CSS variables 정의 (`globals.css`)

```css
@layer base {
  :root[data-theme="dark"], :root {
    --bg-base: 10 18 40;
    --bg-surface: 15 23 48;
    --bg-elevated: 21 32 63;
    --bg-overlay: 26 42 79;

    --border-subtle: 31 45 84;
    --border-default: 42 58 107;
    --border-strong: 59 79 138;

    --text-primary: 244 246 251;
    --text-secondary: 168 179 209;
    --text-muted: 110 122 160;
    --text-disabled: 74 85 122;

    --accent: 255 61 139;
    --accent-hover: 255 85 153;
    --accent-active: 255 45 124;
    --accent-ring: 255 61 139;       /* alpha는 사용처에서 / .4 */

    --success: 74 222 128;
    --warning: 251 191 36;
    --error: 248 113 113;
    --info: 96 165 250;
  }

  :root[data-theme="light"] {
    --bg-base: 248 249 253;
    --bg-surface: 255 255 255;
    --bg-elevated: 244 246 251;
    --bg-overlay: 232 236 245;
    --border-subtle: 226 232 240;
    --border-default: 203 213 225;
    --border-strong: 148 163 184;
    --text-primary: 10 18 40;
    --text-secondary: 71 85 105;
    --text-muted: 148 163 184;
    --text-disabled: 203 213 225;
    /* accent/semantic은 동일 */
  }

  body {
    background-color: rgb(var(--bg-base));
    color: rgb(var(--text-primary));
    font-family: 'Inter Variable', 'Pretendard Variable', system-ui, sans-serif;
    font-feature-settings: 'cv11', 'ss01', 'ss03';  /* Inter 권장 */
  }
}
```

### 11.2 Tailwind config (Tailwind v4 가정)

```ts
// services/etlx-web/tailwind.config.ts
import type { Config } from 'tailwindcss'

export default {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg:        'rgb(var(--bg-base) / <alpha-value>)',
        surface:   'rgb(var(--bg-surface) / <alpha-value>)',
        elevated:  'rgb(var(--bg-elevated) / <alpha-value>)',
        overlay:   'rgb(var(--bg-overlay) / <alpha-value>)',

        border: {
          subtle:  'rgb(var(--border-subtle) / <alpha-value>)',
          DEFAULT: 'rgb(var(--border-default) / <alpha-value>)',
          strong:  'rgb(var(--border-strong) / <alpha-value>)',
        },

        text: {
          DEFAULT:  'rgb(var(--text-primary) / <alpha-value>)',
          secondary:'rgb(var(--text-secondary) / <alpha-value>)',
          muted:    'rgb(var(--text-muted) / <alpha-value>)',
          disabled: 'rgb(var(--text-disabled) / <alpha-value>)',
        },

        accent: {
          DEFAULT: 'rgb(var(--accent) / <alpha-value>)',
          hover:   'rgb(var(--accent-hover) / <alpha-value>)',
          active:  'rgb(var(--accent-active) / <alpha-value>)',
        },

        success: 'rgb(var(--success) / <alpha-value>)',
        warning: 'rgb(var(--warning) / <alpha-value>)',
        error:   'rgb(var(--error) / <alpha-value>)',
        info:    'rgb(var(--info) / <alpha-value>)',
      },

      borderRadius: {
        sm: '6px', md: '10px', lg: '14px', xl: '20px', '2xl': '28px',
      },

      fontFamily: {
        sans: ['Inter Variable', 'Pretendard Variable', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'monospace'],
      },

      fontSize: {
        'display':         ['40px', { lineHeight: '48px', fontWeight: '700' }],
        'h1':              ['28px', { lineHeight: '36px', fontWeight: '600' }],
        'h2':              ['22px', { lineHeight: '30px', fontWeight: '600' }],
        'h3':              ['18px', { lineHeight: '26px', fontWeight: '600' }],
        'h4':              ['15px', { lineHeight: '22px', fontWeight: '600' }],
        'body':            ['14px', { lineHeight: '22px', fontWeight: '400' }],
        'body-emphasis':   ['14px', { lineHeight: '22px', fontWeight: '500' }],
        'small':           ['13px', { lineHeight: '20px', fontWeight: '400' }],
        'label':           ['12px', { lineHeight: '16px', fontWeight: '600', letterSpacing: '0.04em' }],
        'caption':         ['11px', { lineHeight: '14px', fontWeight: '500' }],
      },

      transitionTimingFunction: {
        DEFAULT: 'cubic-bezier(0.4,0,0.2,1)',
      },

      transitionDuration: {
        fast: '120ms', DEFAULT: '200ms', slow: '320ms',
      },

      backgroundImage: {
        'accent-gradient': 'linear-gradient(135deg, rgb(var(--accent)) 0%, #FF7AB3 100%)',
      },

      boxShadow: {
        sm: '0 1px 2px rgba(0,0,0,0.4)',
        md: '0 4px 12px rgba(0,0,0,0.5)',
        lg: '0 16px 40px rgba(0,0,0,0.6)',
        accent: '0 0 0 4px rgba(255,61,139,0.4)',
      },
    },
  },
} satisfies Config
```

### 11.3 shadcn/ui 통합

```bash
pnpm dlx shadcn@latest init     # baseColor: slate, cssVariables: 사용 ON
pnpm dlx shadcn@latest add button card input dialog sheet command tooltip toast badge tabs select dropdown-menu separator
```

생성된 컴포넌트의 색 토큰을 **모두 우리 토큰으로 치환**. 예: `bg-card` → `bg-elevated`, `border` → `border-subtle`, `text-primary-foreground` → 그대로 두되 라이트/다크 분기 검증.

### 11.4 React Flow 테마

```ts
// services/etlx-web/components/pipeline-builder/theme.ts
export const flowTheme = {
  bg: 'rgb(var(--bg-base))',
  nodeBg: 'rgb(var(--bg-elevated))',
  nodeBorder: 'rgb(var(--border-subtle))',
  nodeBorderActive: 'rgb(var(--accent))',
  edge: 'rgb(var(--border-default))',
  edgeActive: 'rgb(var(--accent))',
  handle: 'rgb(var(--accent))',
  grid: 'rgb(var(--border-subtle) / 0.5)',
}
```

### 11.5 모션은 Framer Motion

```ts
// services/etlx-web/lib/motion.ts
export const motionPresets = {
  fadeUp: {
    initial: { opacity: 0, y: 8 },
    animate: { opacity: 1, y: 0 },
    transition: { duration: 0.2, ease: [0.4, 0, 0.2, 1] },
  },
  modalIn: {
    initial: { opacity: 0, scale: 0.96 },
    animate: { opacity: 1, scale: 1 },
    transition: { duration: 0.32, ease: [0.4, 0, 0.2, 1] },
  },
}
```

---

## 12. 디자인 ↔ 개발 협업 규약

1. **Figma 파일은 토큰을 import해 사용.** Figma Variables → Tailwind config가 단일 진실. 디자이너가 시안에서 임의 색을 쓰면 PR reject.
2. **새 컴포넌트는 먼저 Figma에 → 토큰 검증 → Storybook → 본 코드.** Storybook은 `services/etlx-web/.storybook/`.
3. **Visual regression**: Chromatic 또는 Playwright 스크린샷. 핵심 페이지 5개 (dashboard / pipeline builder / connection form / run detail / settings).
4. **A11y 검사**: PR마다 axe-core 자동 검사. AA 위반 시 머지 금지.
5. **변경 추적**: 디자인 토큰 변경은 ADR. 단순 컴포넌트 신규는 ADR 불필요, PR description에 스크린샷.
6. **금지 사항**:
   - Tailwind arbitrary value(`bg-[#123456]`) 사용 금지. 토큰만.
   - 인라인 style 금지(예외: 동적 차트 색).
   - 컴포넌트 안에서 hex 색 하드코딩 금지.
   - 이모지 UI 금지. (텍스트 안 데이터에는 OK)

---

## 13. 적용 우선순위 (Step 10 진행 순서)

Step 10에 들어갈 때 다음 순서로:

1. **10.0 토큰 구현** — `globals.css` + `tailwind.config.ts` + Storybook 초기화. 디자인 시스템이 코드로 살아있는지 visual sanity check.
2. **10.1 기초 컴포넌트** — Button / Input / Card / Badge / Sidebar / Header / Command Palette. 이 단계가 가장 중요. 이후 모든 페이지가 이걸 조립만 한다.
3. **10.2 레이아웃 셸** — Sidebar + Header + Content slot, 워크스페이스 전환, theme toggle.
4. **10.3 Connection 관리** — 가장 단순한 CRUD. 디자인 시스템 검증 페이지.
5. **10.4 Pipeline Builder** — React Flow + 커스텀 노드. 디자인 난이도 ↑.
6. **10.5 Schedule + Run 모니터링** — Data Table 컴포넌트 첫 등장.
7. **10.6 관리자(워크스페이스 / RBAC / Audit) 화면**.
8. **10.7 빈 상태 / 에러 / 로딩 패스 점검** — 전 페이지 빈 상태 디자인 적용 확인.
9. **10.8 a11y 통과 / visual regression baseline**.

각 단계마다 디자이너 리뷰 → 토큰 위반 0건 확인 → 머지.

---

## 14. 참고

- Arc Browser 디자인 분석: 사이드바, Spaces, Command Bar, Squircle (사용 안 함), Translucent surfaces, micro motions.
- Linear / Vercel Dashboard / Raycast / Cron — 데이터·도구 UI 톤 참고.
- Refactoring UI (Adam Wathan) — 위계·색·여백 원칙.
- Inclusive Components (Heydon Pickering) — a11y 패턴.
