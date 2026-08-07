"use client";

import { useCallback, useEffect, useState } from "react";
import { isStandalone } from "@/lib/standalone";
import { haptics } from "@/lib/haptics";

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

const DISMISS_KEY = "anzai-install-dismissed";

/** Expose deferred install prompt for settings «安装到主屏幕». */
let deferredPrompt: BeforeInstallPromptEvent | null = null;
const listeners = new Set<() => void>();

function notifyInstallListeners() {
  listeners.forEach((fn) => fn());
}

export function getDeferredInstallPrompt() {
  return deferredPrompt;
}

export function subscribeInstallPrompt(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function canNativeInstall(): boolean {
  return Boolean(deferredPrompt);
}

export async function promptNativeInstall(): Promise<"accepted" | "dismissed" | "unavailable"> {
  if (!deferredPrompt) return "unavailable";
  const ev = deferredPrompt;
  deferredPrompt = null;
  notifyInstallListeners();
  await ev.prompt();
  const { outcome } = await ev.userChoice;
  return outcome;
}

function isIosSafariTab(): boolean {
  const standaloneProp = (navigator as Navigator & { standalone?: boolean }).standalone;
  return standaloneProp === false;
}

/** Banner: iOS share-sheet hint + Android/Chrome beforeinstallprompt. */
export function InstallPrompt() {
  const [show, setShow] = useState(false);
  const [mode, setMode] = useState<"ios" | "android" | null>(null);

  useEffect(() => {
    if (typeof navigator === "undefined") return;
    if (isStandalone()) return;

    const dismissed = sessionStorage.getItem(DISMISS_KEY);
    const onBip = (e: Event) => {
      e.preventDefault();
      deferredPrompt = e as BeforeInstallPromptEvent;
      notifyInstallListeners();
      if (!dismissed) {
        setMode("android");
        setShow(true);
      }
    };
    window.addEventListener("beforeinstallprompt", onBip);

    if (isIosSafariTab() && !dismissed) {
      setMode("ios");
      setShow(true);
    }

    return () => window.removeEventListener("beforeinstallprompt", onBip);
  }, []);

  const dismiss = useCallback(() => {
    sessionStorage.setItem(DISMISS_KEY, "1");
    setShow(false);
  }, []);

  const onAndroidInstall = useCallback(async () => {
    haptics.tap();
    const outcome = await promptNativeInstall();
    if (outcome !== "unavailable") dismiss();
  }, [dismiss]);

  if (!show || !mode) return null;

  return (
    <div className="install-banner" role="status">
      <div className="install-banner-text">
        <strong>添加到主屏幕</strong>
        <span>
          {mode === "ios"
            ? "点底部分享 →「添加到主屏幕」后全屏使用"
            : "安装后从主屏幕打开，更像原生 App"}
        </span>
      </div>
      {mode === "android" ? (
        <button type="button" className="install-banner-close" onClick={() => void onAndroidInstall()}>
          安装
        </button>
      ) : null}
      <button type="button" className="install-banner-close" onClick={dismiss}>
        {mode === "android" ? "稍后" : "知道了"}
      </button>
    </div>
  );
}
