"use client";

import { useEffect, useState } from "react";
import { isStandalone } from "@/lib/standalone";

/** web.dev: use navigator.standalone to detect iOS browser vs installed PWA. */
export function InstallPrompt() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    if (typeof navigator === "undefined") return;
    if (isStandalone()) return;

    const standaloneProp = (navigator as Navigator & { standalone?: boolean }).standalone;
    // undefined => not iOS WebKit; false => iOS Safari tab; true => already installed
    if (standaloneProp !== false) return;

    const dismissed = sessionStorage.getItem("anzai-install-dismissed");
    if (!dismissed) setShow(true);
  }, []);

  if (!show) return null;

  return (
    <div className="install-banner" role="status">
      <div className="install-banner-text">
        <strong>添加到主屏幕</strong>
        <span>点底部分享 →「添加到主屏幕」后全屏使用</span>
      </div>
      <button
        type="button"
        className="install-banner-close"
        onClick={() => {
          sessionStorage.setItem("anzai-install-dismissed", "1");
          setShow(false);
        }}
      >
        知道了
      </button>
    </div>
  );
}
