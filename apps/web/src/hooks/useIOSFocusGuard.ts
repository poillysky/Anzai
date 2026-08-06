"use client";

import { useEffect } from "react";
import { focusWithoutScroll, isEditableElement, pinDocumentScroll } from "@/lib/iosKeyboard";
import { shouldUseNativeShell } from "@/lib/standalone";

/**
 * L1 Focus Guard — intercept focus on iOS/native shell before Safari's
 * pre-focus visibility check scrolls the layout viewport.
 *
 * Event order on iOS: pointerdown → mousedown → (focus) → …
 * We handle mousedown (and touchend as fallback) with preventDefault +
 * focus({ preventScroll: true }).
 *
 * Refs: ios-pwa-keyboard-fix ARCHITECTURE, viewport-lock.
 */
export function useIOSFocusGuard(enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    if (typeof window === "undefined") return;

    const shouldGuard = (el: HTMLElement) => {
      // Always guard modal/sheet/login/chat composer — background scroll is the bug
      if (el.closest(".modal-card, .sheet-panel, .login-root, .agent-composer-shell")) {
        return true;
      }
      return shouldUseNativeShell();
    };

    const takeOver = (el: HTMLElement, e: Event) => {
      if (document.activeElement === el) return;
      e.preventDefault();
      focusWithoutScroll(el);
    };

    const onMouseDown = (e: MouseEvent) => {
      const t = e.target;
      if (!isEditableElement(t)) return;
      if (e.button !== 0) return;
      if (!shouldGuard(t)) return;
      takeOver(t, e);
    };

    const onTouchEnd = (e: TouchEvent) => {
      const t = e.target;
      if (!isEditableElement(t)) return;
      if (!shouldGuard(t)) return;
      if (document.activeElement === t) {
        pinDocumentScroll();
        return;
      }
      takeOver(t, e);
    };

    document.addEventListener("mousedown", onMouseDown, true);
    document.addEventListener("touchend", onTouchEnd, { capture: true, passive: false });

    return () => {
      document.removeEventListener("mousedown", onMouseDown, true);
      document.removeEventListener("touchend", onTouchEnd, true);
    };
  }, [enabled]);
}
