"use client";

import { useTabNav } from "@/components/layout/TabNavContext";
import AgentScreen from "@/features/agent/AgentScreen";
import AnalysisScreen from "@/features/analysis/AnalysisScreen";
import MarketScreen from "@/features/market/MarketScreen";
import NewsScreen from "@/features/news/NewsScreen";
import PortfolioScreen from "@/features/portfolio/PortfolioScreen";
import { useEffect, useRef, useState, type ComponentType, type ReactNode } from "react";

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
  const { path } = useTabNav();
  const visited = useRef<Set<TabPath>>(new Set());
  const [, bump] = useState(0);

  if (isTabPath(path)) {
    visited.current.add(path);
  }

  /** Idle-mount remaining tabs so later switches are pure show/hide. */
  useEffect(() => {
    const mountRest = () => {
      let added = false;
      for (const p of TAB_PATHS) {
        if (!visited.current.has(p)) {
          visited.current.add(p);
          added = true;
        }
      }
      if (added) bump((n) => n + 1);
    };
    if (typeof window.requestIdleCallback === "function") {
      const id = window.requestIdleCallback(mountRest, { timeout: 1800 });
      return () => window.cancelIdleCallback(id);
    }
    const t = window.setTimeout(mountRest, 500);
    return () => window.clearTimeout(t);
  }, []);

  if (!isTabPath(path)) {
    return <>{children}</>;
  }

  return (
    <>
      {TAB_PATHS.filter((p) => visited.current.has(p)).map((p) => {
        const Screen = SCREENS[p];
        const active = p === path;
        return (
          <div
            key={p}
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
