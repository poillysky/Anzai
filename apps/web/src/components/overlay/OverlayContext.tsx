"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ToastView, type ToastItem, type ToastKind } from "@/components/overlay/Toast";
import { haptics } from "@/lib/haptics";

export const OVERLAY_ROOT_ID = "anzai-overlay-root";

type OverlayContextValue = {
  toast: (message: string, kind?: ToastKind) => void;
};

const OverlayContext = createContext<OverlayContextValue | null>(null);

const TOAST_MS = 2400;
const MAX_TOASTS = 2;

export function OverlayProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const toast = useCallback((message: string, kind: ToastKind = "info") => {
    if (kind === "success") haptics.success();
    else if (kind === "warning") haptics.warning();
    else haptics.tap();

    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, message, kind }].slice(-MAX_TOASTS));
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, TOAST_MS);
  }, []);

  const value = useMemo(() => ({ toast }), [toast]);

  return (
    <OverlayContext.Provider value={value}>
      {children}
      <div id={OVERLAY_ROOT_ID} className="overlay-root" />
      <div className="toast-stack" aria-live="polite" aria-relevant="additions">
        {toasts.map((t) => (
          <ToastView key={t.id} item={t} />
        ))}
      </div>
    </OverlayContext.Provider>
  );
}

export function useOverlay() {
  const ctx = useContext(OverlayContext);
  if (!ctx) {
    throw new Error("useOverlay must be used within OverlayProvider");
  }
  return ctx;
}

export function getOverlayRoot(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.getElementById(OVERLAY_ROOT_ID);
}
