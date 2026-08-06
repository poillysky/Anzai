"use client";

import { usePathname } from "next/navigation";
import { useRef, type ReactNode } from "react";

const TAB_PATHS = ["/", "/market", "/news", "/analysis", "/agent"] as const;

function isTabPath(path: string): boolean {
  return (TAB_PATHS as readonly string[]).includes(path);
}

/**
 * Keep visited main tabs mounted (display toggle) to preserve scroll/state.
 */
export function TabCache({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const cache = useRef<Map<string, ReactNode>>(new Map());

  if (isTabPath(pathname)) {
    cache.current.set(pathname, children);
  }

  if (!isTabPath(pathname)) {
    return <>{children}</>;
  }

  return (
    <>
      {Array.from(cache.current.entries()).map(([path, node]) => {
        const active = path === pathname;
        return (
          <div
            key={path}
            className="tab-pane"
            data-active={active ? "1" : "0"}
            hidden={!active}
            aria-hidden={!active}
          >
            {node}
          </div>
        );
      })}
    </>
  );
}
