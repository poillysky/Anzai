"use client";

import { useEffect, useState } from "react";

/** Lightweight online/offline strip for list tabs. */
export function OfflineBanner({ className = "" }: { className?: string }) {
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    const sync = () => setOffline(typeof navigator !== "undefined" && !navigator.onLine);
    sync();
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);
    return () => {
      window.removeEventListener("online", sync);
      window.removeEventListener("offline", sync);
    };
  }, []);

  if (!offline) return null;

  return (
    <div className={`offline-banner ${className}`.trim()} role="status">
      离线 · 展示缓存内容，联网后下拉刷新
    </div>
  );
}
