"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

type Options = {
  onRefresh: () => void | Promise<void>;
  disabled?: boolean;
  threshold?: number;
  maxPull?: number;
  /** Kept visible at least this long so spinner doesn't flash */
  minSpinMs?: number;
  onArmed?: () => void;
};

/**
 * iOS-friendly pull-to-refresh. Pull distance is applied via DOM (no React
 * re-render per touchmove) so motion stays smooth.
 */
export function usePullToRefresh(
  scrollerRef: RefObject<HTMLElement | null>,
  indicatorRef: RefObject<HTMLElement | null>,
  {
    onRefresh,
    disabled = false,
    threshold = 68,
    maxPull = 108,
    minSpinMs = 480,
    onArmed,
  }: Options,
) {
  const [refreshing, setRefreshing] = useState(false);
  const [ready, setReady] = useState(false);
  const pullRef = useRef(0);
  const startY = useRef<number | null>(null);
  const tracking = useRef(false);
  const armedFired = useRef(false);
  const refreshingRef = useRef(false);
  const onRefreshRef = useRef(onRefresh);
  const onArmedRef = useRef(onArmed);
  onRefreshRef.current = onRefresh;
  onArmedRef.current = onArmed;

  useEffect(() => {
    refreshingRef.current = refreshing;
  }, [refreshing]);

  useEffect(() => {
    const el = scrollerRef.current;
    const bar = indicatorRef.current;
    if (!el || !bar || disabled) return;

    const setBarHeight = (h: number, animate: boolean) => {
      bar.style.transition = animate ? "height 0.28s cubic-bezier(0.22, 1, 0.36, 1)" : "none";
      bar.style.height = `${Math.max(0, h)}px`;
    };

    const resetPull = (animate: boolean) => {
      pullRef.current = 0;
      armedFired.current = false;
      setReady(false);
      setBarHeight(0, animate);
    };

    const onStart = (e: TouchEvent) => {
      if (refreshingRef.current) return;
      if (el.scrollTop > 2) {
        tracking.current = false;
        startY.current = null;
        return;
      }
      tracking.current = true;
      armedFired.current = false;
      startY.current = e.touches[0]?.clientY ?? null;
      setBarHeight(pullRef.current, false);
    };

    const onMove = (e: TouchEvent) => {
      if (!tracking.current || startY.current == null || refreshingRef.current) return;
      if (el.scrollTop > 2) {
        tracking.current = false;
        startY.current = null;
        resetPull(false);
        return;
      }
      const y = e.touches[0]?.clientY ?? startY.current;
      const dy = y - startY.current;
      if (dy <= 0) {
        if (pullRef.current > 0) resetPull(false);
        return;
      }
      const d = Math.min(maxPull, dy * 0.42);
      pullRef.current = d;
      setBarHeight(d, false);

      const isReady = d >= threshold;
      if (isReady && !armedFired.current) {
        armedFired.current = true;
        setReady(true);
        onArmedRef.current?.();
      } else if (!isReady && armedFired.current) {
        armedFired.current = false;
        setReady(false);
      }

      if (d > 8) e.preventDefault();
    };

    const onEnd = () => {
      if (!tracking.current) return;
      tracking.current = false;
      startY.current = null;
      const d = pullRef.current;
      if (d >= threshold && !refreshingRef.current) {
        setRefreshing(true);
        refreshingRef.current = true;
        setReady(true);
        setBarHeight(48, true);
        const started = Date.now();
        void Promise.resolve(onRefreshRef.current())
          .catch(() => {
            /* caller handles errors */
          })
          .finally(() => {
            const wait = Math.max(0, minSpinMs - (Date.now() - started));
            window.setTimeout(() => {
              refreshingRef.current = false;
              setRefreshing(false);
              resetPull(true);
            }, wait);
          });
      } else {
        resetPull(true);
      }
    };

    el.addEventListener("touchstart", onStart, { passive: true });
    el.addEventListener("touchmove", onMove, { passive: false });
    el.addEventListener("touchend", onEnd);
    el.addEventListener("touchcancel", onEnd);
    return () => {
      el.removeEventListener("touchstart", onStart);
      el.removeEventListener("touchmove", onMove);
      el.removeEventListener("touchend", onEnd);
      el.removeEventListener("touchcancel", onEnd);
    };
  }, [scrollerRef, indicatorRef, disabled, threshold, maxPull, minSpinMs]);

  return { refreshing, ready };
}
