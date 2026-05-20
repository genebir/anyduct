// Flat, namespaced translation dictionaries. No i18n library — a typed
// dictionary + a tiny `t()` (see locale-provider.tsx) is enough for a
// two-language UI and keeps the bundle lean.
//
// Conventions:
//   * Keys are dot-namespaced by area: nav.*, header.*, login.*, common.*
//   * `en` is the source of truth for the key set; `ko` MUST mirror it.
//     `Messages` is derived from `en`, so a missing/extra `ko` key is a
//     TypeScript error at build time.
//   * Interpolation uses {name} placeholders, filled by t(key, { name }).

export const en = {
  // generic actions / labels reused across screens
  "common.save": "Save",
  "common.cancel": "Cancel",
  "common.delete": "Delete",
  "common.edit": "Edit",
  "common.create": "Create",
  "common.close": "Close",
  "common.confirm": "Confirm",
  "common.loading": "Loading…",
  "common.search": "Search",
  "common.test": "Test",
  "common.add": "Add",
  "common.back": "Back",
  "common.retry": "Retry",
  "common.refresh": "Refresh",

  // sidebar navigation
  "nav.overview": "Overview",
  "nav.connections": "Connections",
  "nav.pipelines": "Pipelines",
  "nav.schedules": "Schedules",
  "nav.runs": "Runs",
  "nav.members": "Members",
  "nav.audit": "Audit log",
  "nav.settings": "Settings",
  "nav.selectWorkspace": "Select workspace",
  "nav.noWorkspaces": "No workspaces yet.",

  // header
  "header.signOut": "Sign out",
  "header.toLight": "Switch to light theme",
  "header.toDark": "Switch to dark theme",
  "header.language": "Language",

  // login
  "login.title": "Welcome back",
  "login.subtitle": "Sign in to manage your pipelines.",
  "login.email": "Email",
  "login.password": "Password", // pragma: allowlist secret
  "login.submit": "Sign in",
  "login.submitting": "Signing in…",
  "login.error": "Invalid email or password.",
  "login.success": "Signed in",
  "login.ssoHintPrefix": "Trouble signing in? Ask your workspace owner or visit",
  "login.ssoHintSuffix": "for SSO options.",

  // workspaces switcher
  "workspaces.title": "Workspaces",
  "workspaces.create": "New workspace",
  "workspaces.empty": "You don't belong to any workspace yet.",
} as const;

export type Messages = Record<keyof typeof en, string>;

export const ko: Messages = {
  "common.save": "저장",
  "common.cancel": "취소",
  "common.delete": "삭제",
  "common.edit": "편집",
  "common.create": "생성",
  "common.close": "닫기",
  "common.confirm": "확인",
  "common.loading": "불러오는 중…",
  "common.search": "검색",
  "common.test": "테스트",
  "common.add": "추가",
  "common.back": "뒤로",
  "common.retry": "재시도",
  "common.refresh": "새로고침",

  "nav.overview": "개요",
  "nav.connections": "연결",
  "nav.pipelines": "파이프라인",
  "nav.schedules": "스케줄",
  "nav.runs": "실행 기록",
  "nav.members": "멤버",
  "nav.audit": "감사 로그",
  "nav.settings": "설정",
  "nav.selectWorkspace": "워크스페이스 선택",
  "nav.noWorkspaces": "아직 워크스페이스가 없습니다.",

  "header.signOut": "로그아웃",
  "header.toLight": "라이트 테마로 전환",
  "header.toDark": "다크 테마로 전환",
  "header.language": "언어",

  "login.title": "다시 오신 걸 환영합니다",
  "login.subtitle": "로그인하여 파이프라인을 관리하세요.",
  "login.email": "이메일",
  "login.password": "비밀번호",
  "login.submit": "로그인",
  "login.submitting": "로그인 중…",
  "login.error": "이메일 또는 비밀번호가 올바르지 않습니다.",
  "login.success": "로그인되었습니다",
  "login.ssoHintPrefix": "로그인에 문제가 있나요? 워크스페이스 소유자에게 문의하거나",
  "login.ssoHintSuffix": "에서 SSO 옵션을 확인하세요.",

  "workspaces.title": "워크스페이스",
  "workspaces.create": "새 워크스페이스",
  "workspaces.empty": "아직 속한 워크스페이스가 없습니다.",
};

export const dictionaries = { en, ko } as const;
export type Locale = keyof typeof dictionaries;
export const LOCALES: Locale[] = ["ko", "en"];
export const LOCALE_LABELS: Record<Locale, string> = {
  ko: "한국어",
  en: "English",
};
