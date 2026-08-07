"use client";

import { useEffect } from "react";

/** Register shell service worker (production / HTTPS / localhost). */
export function ServiceWorkerRegister() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("serviceWorker" in navigator)) return;
    const host = window.location.hostname;
    const ok =
      window.location.protocol === "https:" ||
      host === "localhost" ||
      host === "127.0.0.1";
    if (!ok) return;

    let cancelled = false;
    let onVis: (() => void) | null = null;
    let onResume: (() => void) | null = null;

    void navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .then((reg) => {
        if (cancelled) return;
        // iOS may keep visibility "visible" while frozen — also check on pageshow/focus
        onResume = () => {
          void reg.update();
        };
        onVis = () => {
          if (document.visibilityState === "visible") void reg.update();
        };
        document.addEventListener("visibilitychange", onVis);
        window.addEventListener("pageshow", onResume);
        window.addEventListener("focus", onResume);
      })
      .catch(() => {
        /* SW optional in dev */
      });

    return () => {
      cancelled = true;
      if (onVis) document.removeEventListener("visibilitychange", onVis);
      if (onResume) {
        window.removeEventListener("pageshow", onResume);
        window.removeEventListener("focus", onResume);
      }
    };
  }, []);

  return null;
}
