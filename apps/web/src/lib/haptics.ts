/** Unified light haptics for iOS/Android Web. No-op when unsupported. */

export type HapticKind = "tap" | "success" | "warning" | "selection";

const PATTERNS: Record<HapticKind, number | number[]> = {
  tap: 10,
  selection: 8,
  success: [10, 30, 12],
  warning: [16, 40, 16],
};

export function haptic(kind: HapticKind = "tap"): void {
  if (typeof navigator === "undefined" || typeof navigator.vibrate !== "function") {
    return;
  }
  try {
    navigator.vibrate(PATTERNS[kind]);
  } catch {
    /* ignore */
  }
}

export const haptics = {
  tap: () => haptic("tap"),
  selection: () => haptic("selection"),
  success: () => haptic("success"),
  warning: () => haptic("warning"),
};
