"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useShellChrome } from "@/components/layout/ShellChromeContext";
import { haptics } from "@/lib/haptics";

type UseShellStackOptions<T extends string> = {
  root: T;
  /** Sync browser back with stack pop (default true). */
  history?: boolean;
};

/**
 * In-tab push stack: root stays mounted, overlays hide TabBar via ShellChrome.
 * History: pushState on push; UI/edge back → history.back() → popstate pops stack.
 */
export function useShellStack<T extends string>(opts: UseShellStackOptions<T>) {
  const { root, history: useHistory = true } = opts;
  const ownerId = useId();
  const { reportOverlay } = useShellChrome();
  const [stack, setStack] = useState<T[]>([root]);
  const stackRef = useRef(stack);
  stackRef.current = stack;
  const depth = stack.length;
  const page = stack[stack.length - 1] ?? root;
  const overlayOpen = depth > 1;

  useEffect(() => {
    reportOverlay(ownerId, overlayOpen);
    return () => reportOverlay(ownerId, false);
  }, [ownerId, overlayOpen, reportOverlay]);

  const push = useCallback(
    (next: T) => {
      setStack((s) => {
        if (s[s.length - 1] === next) return s;
        if (useHistory && typeof window !== "undefined") {
          try {
            window.history.pushState(
              { anzaiShell: true, ownerId, depth: s.length + 1 },
              "",
            );
          } catch {
            /* ignore */
          }
        }
        return [...s, next];
      });
    },
    [ownerId, useHistory],
  );

  /** Prefer history.back so popstate keeps stack/history aligned. */
  const pop = useCallback(() => {
    if (stackRef.current.length <= 1) return;
    haptics.selection();
    if (useHistory && typeof window !== "undefined") {
      window.history.back();
      return;
    }
    setStack((s) => (s.length > 1 ? s.slice(0, -1) : s));
  }, [useHistory]);

  const popSoft = pop;

  const reset = useCallback(() => {
    setStack([root]);
  }, [root]);

  useEffect(() => {
    if (!useHistory) return;
    const onPop = () => {
      if (stackRef.current.length <= 1) return;
      setStack((s) => (s.length > 1 ? s.slice(0, -1) : s));
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [useHistory]);

  return {
    stack,
    page,
    depth,
    overlayOpen,
    push,
    pop,
    popSoft,
    reset,
    ownerId,
  };
}
