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
import { workspacesApi, type WorkspaceSummary } from "@/lib/api";
import { useAuth } from "./auth-provider";

const STORAGE_KEY = "etlx.workspace";

interface WorkspaceContextValue {
  workspaces: WorkspaceSummary[];
  current: WorkspaceSummary | null;
  setCurrent: (id: string) => void;
  refresh: () => Promise<void>;
  loading: boolean;
}

/** Exported so Storybook can build a fake Provider — see auth-provider.tsx. */
export const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);
export type { WorkspaceContextValue };

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { state: authState } = useAuth();
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const list = await workspacesApi.list();
      setWorkspaces(list);
      const persisted =
        typeof window === "undefined"
          ? null
          : window.localStorage.getItem(STORAGE_KEY);
      const pick =
        list.find((w) => w.id === persisted)?.id ?? list[0]?.id ?? null;
      setCurrentId(pick);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (authState.kind === "signed-in") {
      void refresh();
    } else if (authState.kind === "anonymous") {
      setWorkspaces([]);
      setCurrentId(null);
    }
  }, [authState.kind, refresh]);

  const setCurrent = useCallback((id: string) => {
    setCurrentId(id);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, id);
    }
  }, []);

  const current = useMemo(
    () => workspaces.find((w) => w.id === currentId) ?? null,
    [workspaces, currentId],
  );

  const value = useMemo<WorkspaceContextValue>(
    () => ({ workspaces, current, setCurrent, refresh, loading }),
    [workspaces, current, setCurrent, refresh, loading],
  );

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspaces(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx)
    throw new Error("useWorkspaces must be used within <WorkspaceProvider>");
  return ctx;
}
