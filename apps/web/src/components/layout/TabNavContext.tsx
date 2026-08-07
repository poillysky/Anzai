"use client";

import { usePathname, useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

type TabNavValue = {
  /** Optimistic path — updates on press before Next router settles */
  path: string;
  commit: (href: string) => void;
};

const TabNavContext = createContext<TabNavValue | null>(null);

const TAB_HREFS = new Set(["/", "/market", "/news", "/analysis", "/agent"]);

export function TabNavProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [path, setPath] = useState(pathname);

  useEffect(() => {
    setPath(pathname);
  }, [pathname]);

  const commit = useCallback(
    (href: string) => {
      if (!TAB_HREFS.has(href)) return;
      setPath((prev) => (prev === href ? prev : href));
      if (pathname !== href) {
        router.replace(href, { scroll: false });
      }
    },
    [pathname, router],
  );

  const value = useMemo(() => ({ path, commit }), [path, commit]);

  return <TabNavContext.Provider value={value}>{children}</TabNavContext.Provider>;
}

export function useTabNav(): TabNavValue {
  const ctx = useContext(TabNavContext);
  const pathname = usePathname();
  return ctx ?? { path: pathname, commit: () => undefined };
}
