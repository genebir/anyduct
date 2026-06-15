"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useRouter, usePathname } from "next/navigation";
import {
  ApiError,
  clearTokens,
  getToken,
  login as apiLogin,
  logout as apiLogout,
  me,
  type CurrentUser,
} from "@/lib/api";

type AuthState =
  | { kind: "loading" }
  | { kind: "anonymous" }
  | { kind: "signed-in"; user: CurrentUser };

interface AuthContextValue {
  state: AuthState;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

/**
 * Exported so Storybook can build a fake Provider that satisfies
 * ``useAuth()`` without hitting the API. Production code should keep using
 * ``AuthProvider`` + ``useAuth()``.
 */
export const AuthContext = createContext<AuthContextValue | null>(null);
export type { AuthContextValue, AuthState };

const PUBLIC_ROUTES = new Set(["/login"]);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ kind: "loading" });
  const router = useRouter();
  const pathname = usePathname();

  const refreshUser = useCallback(async () => {
    if (!getToken()) {
      setState({ kind: "anonymous" });
      return;
    }
    try {
      const user = await me();
      setState({ kind: "signed-in", user });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearTokens();
      }
      setState({ kind: "anonymous" });
    }
  }, []);

  useEffect(() => {
    void refreshUser();
    const handleUnauthorized = () => setState({ kind: "anonymous" });
    window.addEventListener("anyduct:unauthorized", handleUnauthorized);
    return () =>
      window.removeEventListener("anyduct:unauthorized", handleUnauthorized);
  }, [refreshUser]);

  useEffect(() => {
    if (state.kind === "anonymous" && !PUBLIC_ROUTES.has(pathname)) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
    if (state.kind === "signed-in" && pathname === "/login") {
      router.replace("/workspaces");
    }
  }, [state, pathname, router]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      await apiLogin(email, password);
      await refreshUser();
    },
    [refreshUser],
  );

  const signOut = useCallback(async () => {
    await apiLogout();
    setState({ kind: "anonymous" });
    router.replace("/login");
  }, [router]);

  const value = useMemo<AuthContextValue>(
    () => ({ state, signIn, signOut, refreshUser }),
    [state, signIn, signOut, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}

export function useCurrentUser(): CurrentUser | null {
  const { state } = useAuth();
  return state.kind === "signed-in" ? state.user : null;
}
