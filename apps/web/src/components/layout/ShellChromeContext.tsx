"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

type ShellChromeContextValue = {
  /** True when any feature stack has pushed past its root (hide TabBar). */
  tabHidden: boolean;
  reportOverlay: (ownerId: string, deep: boolean) => void;
};

const ShellChromeContext = createContext<ShellChromeContextValue | null>(null);

export function ShellChromeProvider({ children }: { children: ReactNode }) {
  const [owners, setOwners] = useState<Record<string, boolean>>({});

  const reportOverlay = useCallback((ownerId: string, deep: boolean) => {
    setOwners((prev) => {
      const nextDeep = Boolean(deep);
      if (prev[ownerId] === nextDeep) return prev;
      if (!nextDeep) {
        if (!(ownerId in prev)) return prev;
        const rest = { ...prev };
        delete rest[ownerId];
        return rest;
      }
      return { ...prev, [ownerId]: true };
    });
  }, []);

  const tabHidden = useMemo(
    () => Object.values(owners).some(Boolean),
    [owners],
  );

  const value = useMemo(
    () => ({ tabHidden, reportOverlay }),
    [tabHidden, reportOverlay],
  );

  return (
    <ShellChromeContext.Provider value={value}>{children}</ShellChromeContext.Provider>
  );
}

export function useShellChrome() {
  const ctx = useContext(ShellChromeContext);
  if (!ctx) {
    return {
      tabHidden: false,
      reportOverlay: () => {},
    };
  }
  return ctx;
}
