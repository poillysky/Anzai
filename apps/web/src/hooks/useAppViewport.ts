"use client";

import { useEffect, useLayoutEffect, useState } from "react";
import { pinDocumentScroll, pinUnderlyingScrollers } from "@/lib/iosKeyboard";
import { isStandalone, shouldUseNativeShell } from "@/lib/standalone";
import { useIOSFocusGuard } from "@/hooks/useIOSFocusGuard";

const KEYBOARD_THRESHOLD = 80;
const STABILITY_MS = 80;

/**
 * App viewport shell — layered keyboard model (docs/iOS全屏设计.md §3.6).
 *
 * L2 --app-height  layout height (stable; never shrink for keyboard)
 * L3 --vv-height    visualViewport.height (modals / overlays)
 * L4 data-keyboard  chrome flag (TabBar fade, keep flex space)
 * L5 scroll lock    safety net while keyboard open
 * L6 heal           standalone stuck-shrink remeasure
 *
 * Do NOT shrink --app-height to vv.height on keyboard — that fights Safari
 * and causes the slide-down / bounce-up on mid-page search focus.
 */
export function useAppViewport() {
  const [nativeShell, setNativeShell] = useState(false);
  const [standalone, setStandalone] = useState(false);

  useIOSFocusGuard(true);

  useLayoutEffect(() => {
    const native = shouldUseNativeShell();
    const alone = isStandalone();
    setNativeShell(native);
    setStandalone(alone);
    document.documentElement.dataset.shell = native ? "native" : "preview";
    document.documentElement.dataset.standalone = alone ? "1" : "0";
    document.body.classList.toggle("is-native-shell", native);
    document.body.classList.toggle("is-standalone", alone);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    let layoutH = screen.height || window.innerHeight;
    let stabilityTimer: number | null = null;
    let pendingInset = 0;

    const measureGap = () => {
      const vv = window.visualViewport;
      if (!vv) return 0;
      return Math.max(0, layoutH - vv.height, window.innerHeight - vv.height);
    };

    const commitKeyboard = (open: boolean, inset: number) => {
      root.dataset.keyboard = open ? "1" : "0";
      root.style.setProperty("--keyboard-inset", `${Math.round(open ? inset : 0)}px`);
    };

    const applyLayoutHeight = () => {
      const alone = isStandalone();
      const native = shouldUseNativeShell();
      let h: number;

      if (alone) {
        h = Math.max(screen.height || 0, window.innerHeight, layoutH);
        layoutH = h;
      } else if (native) {
        h = Math.max(window.innerHeight, layoutH);
        layoutH = Math.max(layoutH, window.innerHeight);
      } else {
        h = window.visualViewport?.height || window.innerHeight;
      }

      root.style.setProperty("--app-height", `${Math.round(h)}px`);
    };

    const applyVisualHeight = () => {
      const vv = window.visualViewport;
      const vh = vv?.height || window.innerHeight;
      root.style.setProperty("--vv-height", `${Math.round(vh)}px`);
      if (vv) {
        root.style.setProperty("--vv-offset-top", `${Math.round(vv.offsetTop)}px`);
      }
    };

    const onViewportSignal = () => {
      applyVisualHeight();
      const gap = measureGap();

      if (gap <= KEYBOARD_THRESHOLD) {
        if (stabilityTimer) {
          window.clearTimeout(stabilityTimer);
          stabilityTimer = null;
        }
        // gap≈0: no real keyboard (desktop preview / closed) — never keep TabBar hidden
        commitKeyboard(false, 0);
        return;
      }

      // Track keyboard gap for modal-lift only (shell stays frozen).
      // Soft cap avoids rare VV glitches; half of this is applied in CSS.
      pendingInset = Math.min(gap, Math.round(layoutH * 0.55));
      root.style.setProperty("--keyboard-inset", `${pendingInset}px`);
      if (stabilityTimer) window.clearTimeout(stabilityTimer);
      stabilityTimer = window.setTimeout(() => {
        stabilityTimer = null;
        commitKeyboard(true, pendingInset);
        pinDocumentScroll();
        pinUnderlyingScrollers();
      }, STABILITY_MS);
    };

    const healViewport = () => {
      if (!isStandalone()) return;
      const el = document.querySelector(".app-shell") as HTMLElement | null;
      if (!el) return;
      const expected = screen.height || window.innerHeight;
      if (window.innerHeight >= expected - 8) return;
      const prev = el.style.display;
      el.style.display = "none";
      void el.offsetHeight;
      el.style.display = prev || "";
      applyLayoutHeight();
      applyVisualHeight();
    };

    const applyMode = () => {
      const native = shouldUseNativeShell();
      const alone = isStandalone();
      setNativeShell(native);
      setStandalone(alone);
      root.dataset.shell = native ? "native" : "preview";
      root.dataset.standalone = alone ? "1" : "0";
      document.body.classList.toggle("is-native-shell", native);
      document.body.classList.toggle("is-standalone", alone);
    };

    const onFocusIn = () => {
      if (!shouldUseNativeShell() && !isStandalone()) return;
      if (!isEditableFocused()) return;
      pinDocumentScroll();
      // Only hide TabBar when visualViewport proves the keyboard is up.
      // Proactive data-keyboard on focus made TabBar vanish on desktop preview
      // and mid-page search expand (portfolio) before any keyboard appears.
      const gap = measureGap();
      if (gap > KEYBOARD_THRESHOLD) {
        commitKeyboard(true, gap);
      }
    };

    const onFocusOut = () => {
      window.setTimeout(() => {
        if (isEditableFocused()) return;
        commitKeyboard(false, 0);
        pinDocumentScroll();
        applyLayoutHeight();
        applyVisualHeight();
        healViewport();
      }, 120);
      window.setTimeout(healViewport, 400);
    };

    const onScroll = () => {
      if (root.dataset.keyboard === "1" || root.dataset.modal === "1") {
        pinDocumentScroll();
        pinUnderlyingScrollers();
      }
    };

    const onWinResize = () => {
      applyLayoutHeight();
      onViewportSignal();
    };

    applyMode();
    applyLayoutHeight();
    applyVisualHeight();
    commitKeyboard(false, 0);

    const boot = [100, 500, 1000].map((ms) =>
      window.setTimeout(() => {
        applyLayoutHeight();
        applyVisualHeight();
      }, ms),
    );

    window.addEventListener("resize", onWinResize);
    window.visualViewport?.addEventListener("resize", onViewportSignal);
    window.visualViewport?.addEventListener("scroll", () => {
      applyVisualHeight();
      if (root.dataset.keyboard === "1" || root.dataset.modal === "1") {
        pinDocumentScroll();
        pinUnderlyingScrollers();
      }
    });
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("orientationchange", () => {
      if (stabilityTimer) window.clearTimeout(stabilityTimer);
      commitKeyboard(false, 0);
      window.setTimeout(() => {
        layoutH = screen.height || window.innerHeight;
        applyLayoutHeight();
        applyVisualHeight();
      }, 200);
    });
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("focusout", onFocusOut);

    const mq = window.matchMedia("(display-mode: standalone)");
    const onMq = () => {
      applyMode();
      applyLayoutHeight();
      applyVisualHeight();
    };
    mq.addEventListener?.("change", onMq);

    return () => {
      boot.forEach((id) => window.clearTimeout(id));
      if (stabilityTimer) window.clearTimeout(stabilityTimer);
      window.removeEventListener("resize", onWinResize);
      window.removeEventListener("scroll", onScroll);
      window.visualViewport?.removeEventListener("resize", onViewportSignal);
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("focusout", onFocusOut);
      mq.removeEventListener?.("change", onMq);
    };
  }, []);

  return { nativeShell, standalone };
}

function isEditableFocused(): boolean {
  const a = document.activeElement;
  if (!(a instanceof HTMLElement)) return false;
  const tag = a.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || a.isContentEditable;
}
