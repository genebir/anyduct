import { useState, useCallback, type ReactNode } from "react";
import {
  AuthContext,
  type AuthContextValue,
  type AuthState,
} from "@/components/providers/auth-provider";
import {
  ThemeContext,
  type ThemeContextValue,
  type Theme,
} from "@/components/providers/theme-provider";
import {
  WorkspaceContext,
  type WorkspaceContextValue,
} from "@/components/providers/workspace-provider";
import type { CurrentUser, WorkspaceSummary } from "@/lib/api";

/**
 * Fake providers for Storybook canvases.
 *
 * They mount the same Context objects the production providers do, so any
 * component that reads via ``useAuth()`` / ``useTheme()`` / ``useWorkspaces()``
 * works without hitting the API or localStorage. Each takes an explicit
 * value so stories can dial in the exact state they want to show.
 */

export const DEFAULT_USER: CurrentUser = {
  id: "u-1",
  email: "demo@etlx.example",
  name: "Demo User",
  is_superadmin: false,
};

export const DEFAULT_WORKSPACES: WorkspaceSummary[] = [
  {
    id: "ws-1",
    slug: "acme-data",
    name: "Acme Data",
    color_hex: "#FF3D8B",
    role: "owner",
  },
  {
    id: "ws-2",
    slug: "growth",
    name: "Growth",
    color_hex: "#60A5FA",
    role: "editor",
  },
];

export function MockAuthProvider({
  children,
  state = { kind: "signed-in", user: DEFAULT_USER },
}: {
  children: ReactNode;
  state?: AuthState;
}) {
  const value: AuthContextValue = {
    state,
    signIn: async () => undefined,
    signOut: async () => undefined,
    refreshUser: async () => undefined,
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function MockThemeProvider({
  children,
  initial = "dark",
}: {
  children: ReactNode;
  initial?: Theme;
}) {
  const [theme, setTheme] = useState<Theme>(initial);
  const toggleTheme = useCallback(
    () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    [],
  );
  const value: ThemeContextValue = { theme, setTheme, toggleTheme };
  return (
    <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
  );
}

export function MockWorkspaceProvider({
  children,
  workspaces = DEFAULT_WORKSPACES,
  currentIndex = 0,
}: {
  children: ReactNode;
  workspaces?: WorkspaceSummary[];
  currentIndex?: number;
}) {
  const [current, setCurrent] = useState<WorkspaceSummary | null>(
    workspaces[currentIndex] ?? null,
  );
  const value: WorkspaceContextValue = {
    workspaces,
    current,
    setCurrent: (id) => setCurrent(workspaces.find((w) => w.id === id) ?? null),
    refresh: async () => undefined,
    loading: false,
  };
  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}

/** Convenience: all three providers in their happy-path defaults. */
export function MockAppShell({
  children,
  authState,
  workspaces,
  currentWorkspaceIndex,
  initialTheme,
}: {
  children: ReactNode;
  authState?: AuthState;
  workspaces?: WorkspaceSummary[];
  currentWorkspaceIndex?: number;
  initialTheme?: Theme;
}) {
  return (
    <MockThemeProvider initial={initialTheme}>
      <MockAuthProvider state={authState}>
        <MockWorkspaceProvider
          workspaces={workspaces}
          currentIndex={currentWorkspaceIndex}
        >
          {children}
        </MockWorkspaceProvider>
      </MockAuthProvider>
    </MockThemeProvider>
  );
}
