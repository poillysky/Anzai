"use client";

import { usePathname } from "next/navigation";
import { useRef, type ComponentType, type ReactNode } from "react";
import AgentScreen from "@/features/agent/AgentScreen";
import AnalysisScreen from "@/features/analysis/AnalysisScreen";
import MarketScreen from "@/features/market/MarketScreen";
import NewsScreen from "@/features/news/NewsScreen";
import PortfolioScreen from "@/features/portfolio/PortfolioScreen";

const TAB_PATHS = ["/", "/market", "/news", "/analysis", "/agent"] as const;
type TabPath = (typeof TAB_PATHS)[number];

/** Own screen instances — do NOT cache Next.js `children` (RSC slot swaps remount and abort SSE). */
const SCREENS: Record<TabPath, ComponentType> = {
  "/": PortfolioScreen,
  "/market": MarketScreen,
  "/news": NewsScreen,
  "/analysis": AnalysisScreen,
  "/agent": AgentScreen,
};

function isTabPath(path: string): path is TabPath {
  return (TAB_PATHS as readonly string[]).includes(path);
}

/**
 * Keep visited main tabs mounted (display toggle) to preserve scroll/state
 * and in-flight Agent SSE across tab switches.
 */
export function TabCache({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const visited = useRef<Set<TabPath>>(new Set());

  if (isTabPath(pathname)) {
    visited.current.add(pathname);
  }

  if (!isTabPath(pathname)) {
    return <>{children}</>;
  }

  return (
    <>
      {TAB_PATHS.filter((path) => visited.current.has(path)).map((path) => {
        const Screen = SCREENS[path];
        const active = path === pathname;
        return (
          <div
            key={path}
            className="tab-pane"
            data-active={active ? "1" : "0"}
            hidden={!active}
            aria-hidden={!active}
          >
            <Screen />
          </div>
        );
      })}
    </>
  );
}
