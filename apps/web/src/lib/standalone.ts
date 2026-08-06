/** Detect iOS / PWA standalone (home screen) mode. */
export function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  const mq = window.matchMedia("(display-mode: standalone)").matches;
  const ios = Boolean(
    (navigator as Navigator & { standalone?: boolean }).standalone,
  );
  return mq || ios;
}

export function isNarrowPhone(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(max-width: 480px)").matches;
}

/** True when we should hide desktop phone chrome (real phone or installed PWA). */
export function shouldUseNativeShell(): boolean {
  return isStandalone() || isNarrowPhone();
}
