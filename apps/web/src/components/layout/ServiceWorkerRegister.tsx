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
    void navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .then((reg) => {
        if (cancelled) return;
        // Check for updates when app becomes visible
        const onVis = () => {
          if (document.visibilityState === "visible") void reg.update();
        };
        document.addEventListener("visibilitychange", onVis);
      })
      .catch(() => {
        /* SW optional in dev */
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return null;
}
