"use client";

import { useTabNav } from "@/components/layout/TabNavContext";
import { useEffect, useState } from "react";

function pathMatches(pathname: string, tabPath: string): boolean {
  return tabPath === "/" ? pathname === "/" : pathname.startsWith(tabPath);
}

/**
 * True when this main tab is the visible route and the document is foregrounded.
 * Uses optimistic TabNav path so polls follow finger, not router lag.
 */
export function useTabActive(tabPath: string): boolean {
  const { path } = useTabNav();
  const [visible, setVisible] = useState(
    () => typeof document === "undefined" || document.visibilityState === "visible",
  );

  useEffect(() => {
    const onVis = () => setVisible(document.visibilityState === "visible");
    onVis();
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  return pathMatches(path, tabPath) && visible;
}
