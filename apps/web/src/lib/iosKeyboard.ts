/** Shared iOS keyboard / focus helpers for the app shell. */

const SHELL_LOCK = "data-anzai-scroll-lock";

const SCROLLER_SEL =
  ".app-shell .app-main, .app-shell .portfolio-holdings-body, .app-shell .market-leaders-body";

let lockCount = 0;
let touchMoveBlock: ((e: TouchEvent) => void) | null = null;
let vvPinTimer: number | null = null;

export function isEditableElement(el: EventTarget | null): el is HTMLElement {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
    const input = el as HTMLInputElement;
    if (tag === "INPUT" && input.type === "button") return false;
    if (tag === "INPUT" && input.type === "submit") return false;
    if (tag === "INPUT" && input.type === "checkbox") return false;
    if (tag === "INPUT" && input.type === "radio") return false;
    if (tag === "INPUT" && input.readOnly) return false;
    if (tag === "INPUT" && input.disabled) return false;
    return true;
  }
  return el.isContentEditable;
}

export function pinDocumentScroll() {
  if (typeof window === "undefined") return;
  if (window.scrollY !== 0 || window.scrollX !== 0) {
    window.scrollTo(0, 0);
  }
  if (document.documentElement.scrollTop) document.documentElement.scrollTop = 0;
  if (document.body.scrollTop) document.body.scrollTop = 0;
}

/** Re-apply frozen scrollTops on shell scrollers (iOS may nudge them on focus). */
export function pinUnderlyingScrollers() {
  if (typeof document === "undefined") return;
  document.querySelectorAll<HTMLElement>(".app-shell [data-anzai-saved-scroll]").forEach((el) => {
    const y = Number(el.dataset.anzaiSavedScroll || 0);
    if (el.scrollTop !== y) el.scrollTop = y;
  });
}

/**
 * Freeze app-shell scroll containers while an overlay is open.
 * Stops the "background page slides under the modal" feel on iOS focus/keyboard.
 */
export function lockUnderlyingScroll() {
  if (typeof document === "undefined") return;
  const shell = document.querySelector(".app-shell");
  if (!shell) return;

  lockCount += 1;
  if (lockCount > 1) {
    pinDocumentScroll();
    pinUnderlyingScrollers();
    return;
  }

  pinDocumentScroll();
  shell.setAttribute(SHELL_LOCK, "1");

  document.querySelectorAll<HTMLElement>(SCROLLER_SEL).forEach((el) => {
    if (el.dataset.anzaiSavedScroll != null) return;
    el.dataset.anzaiSavedScroll = String(el.scrollTop);
    el.dataset.anzaiSavedOverflow = el.style.overflow;
    el.style.overflow = "hidden";
  });

  touchMoveBlock = (e: TouchEvent) => {
    const t = e.target;
    if (
      t instanceof Element &&
      t.closest(
        ".modal-card, .sheet-panel, .modal-body, .toast-stack, .login-form, .login-gate, .login-scroll, .login-back",
      )
    ) {
      // Allow vertical drag only inside long form bodies; login form is short — still block page pull
      if (t.closest(".login-form") && !t.closest(".modal-body") && !t.closest(".login-scroll")) {
        e.preventDefault();
      }
      return;
    }
    e.preventDefault();
  };
  document.addEventListener("touchmove", touchMoveBlock, { passive: false, capture: true });

  // iOS often nudges scroll / VV for a few frames after focus
  if (vvPinTimer) window.clearInterval(vvPinTimer);
  let ticks = 0;
  vvPinTimer = window.setInterval(() => {
    pinDocumentScroll();
    pinUnderlyingScrollers();
    ticks += 1;
    if (ticks >= 16 || lockCount === 0) {
      if (vvPinTimer) window.clearInterval(vvPinTimer);
      vvPinTimer = null;
    }
  }, 32);
}

export function unlockUnderlyingScroll() {
  if (typeof document === "undefined") return;
  lockCount = Math.max(0, lockCount - 1);
  if (lockCount > 0) return;
  clearScrollLockDom();
}

/** Drop all scroll locks (HMR / orphaned focus locks). */
export function resetUnderlyingScrollLock() {
  if (typeof document === "undefined") return;
  lockCount = 0;
  clearScrollLockDom();
}

function clearScrollLockDom() {
  if (vvPinTimer) {
    window.clearInterval(vvPinTimer);
    vvPinTimer = null;
  }

  if (touchMoveBlock) {
    document.removeEventListener("touchmove", touchMoveBlock, true);
    touchMoveBlock = null;
  }

  document.querySelector(".app-shell")?.removeAttribute(SHELL_LOCK);

  document.querySelectorAll<HTMLElement>(".app-shell [data-anzai-saved-scroll]").forEach((el) => {
    const y = Number(el.dataset.anzaiSavedScroll || 0);
    el.style.overflow = el.dataset.anzaiSavedOverflow || "";
    delete el.dataset.anzaiSavedScroll;
    delete el.dataset.anzaiSavedOverflow;
    el.scrollTop = y;
  });

  pinDocumentScroll();
}

/**
 * Take over focus before Safari's pre-focus visibility scroll.
 * Must run from mousedown/touchend with preventDefault.
 * @see https://github.com/Crscristi28/ios-pwa-keyboard-fix/blob/main/docs/ARCHITECTURE.md
 */
export function focusWithoutScroll(el: HTMLElement) {
  el.focus({ preventScroll: true });
  pinDocumentScroll();
  pinUnderlyingScrollers();
}
