"use client";

import { useTabNav } from "@/components/layout/TabNavContext";
import { useEffect, useState, useSyncExternalStore } from "react";

function pathMatches(pathname: string, tabPath: string): boolean {
  return tabPath === "/" ? pathname === "/" : pathname.startsWith(tabPath);
}

function docVisible(): boolean {
  if (typeof document === "undefined") return true;
  return document.visibilityState === "visible";
}

/** Shared across all tabs — iOS may freeze timers without flipping visibility. */
let foregroundEpoch = 0;
let resumeInstalled = false;
let lastBumpAt = 0;
const resumeListeners = new Set<() => void>();

function bumpForegroundEpoch() {
  if (typeof document !== "undefined" && document.visibilityState === "hidden") {
    return;
  }
  const now = Date.now();
  if (now - lastBumpAt < 600) return;
  lastBumpAt = now;
  foregroundEpoch += 1;
  resumeListeners.forEach((l) => l());
}

function ensureResumeListeners() {
  if (typeof window === "undefined" || resumeInstalled) return;
  resumeInstalled = true;
  let wasHidden = !docVisible();

  const onVis = () => {
    if (document.visibilityState === "hidden") {
      wasHidden = true;
      return;
    }
    if (wasHidden) {
      wasHidden = false;
      bumpForegroundEpoch();
    }
  };

  document.addEventListener("visibilitychange", onVis);
  window.addEventListener("pageshow", bumpForegroundEpoch);
  window.addEventListener("focus", bumpForegroundEpoch);
}

function subscribeForegroundEpoch(onStoreChange: () => void): () => void {
  ensureResumeListeners();
  resumeListeners.add(onStoreChange);
  return () => {
    resumeListeners.delete(onStoreChange);
  };
}

function getForegroundEpoch(): number {
  ensureResumeListeners();
  return foregroundEpoch;
}

/**
 * Bumps when the app returns to foreground.
 * Put in poll-effect deps and force-refresh so frozen intervals remount after iOS background.
 */
export function useForegroundEpoch(): number {
  return useSyncExternalStore(
    subscribeForegroundEpoch,
    getForegroundEpoch,
    () => 0,
  );
}

/**
 * True when this main tab is the visible route and the document is foregrounded.
 * Uses optimistic TabNav path so polls follow finger, not router lag.
 */
export function useTabActive(tabPath: string): boolean {
  const { path } = useTabNav();
  // Re-render on resume even if visibilityState stayed "visible"
  useForegroundEpoch();
  const [visible, setVisible] = useState(docVisible);

  useEffect(() => {
    const sync = () => setVisible(docVisible());
    sync();
    document.addEventListener("visibilitychange", sync);
    window.addEventListener("pageshow", sync);
    window.addEventListener("focus", sync);
    return () => {
      document.removeEventListener("visibilitychange", sync);
      window.removeEventListener("pageshow", sync);
      window.removeEventListener("focus", sync);
    };
  }, []);

  return pathMatches(path, tabPath) && visible;
}
