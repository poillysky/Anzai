"use client";

import { useEffect, useLayoutEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAppViewport } from "@/hooks/useAppViewport";
import { OverlayProvider } from "@/components/overlay/OverlayContext";
import { getAccessToken } from "@/lib/auth";
import { scheduleTabWarm } from "@/lib/prefetch";
import { InstallPrompt } from "./InstallPrompt";
import { StatusBar } from "./StatusBar";
import { TabBar } from "./TabBar";
import { TabCache } from "./TabCache";
import "@/features/auth/login.css";

const TAB_PATHS = new Set(["/", "/market", "/news", "/analysis", "/agent"]);
/** Pin header + scroll body — match CSS; set on shell so layout mode is known before :has */
const PIN_MAIN_PATHS = new Set(["/", "/market", "/news"]);

/**
 * Auth gate before paint:
 * - No token on app routes → cream boot + replace /login (never reveal TabBar/home)
 * - Token on /login → dark boot + replace /
 * - Only set ready when the URL already matches the session
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { nativeShell } = useAppViewport();
  const pathname = usePathname();
  const router = useRouter();
  const isLogin = pathname === "/login";
  const showTab = TAB_PATHS.has(pathname);

  const [ready, setReady] = useState(false);
  /** Synced in layout effect before paint — initial value must match SSR (pathname only) */
  const [bootLogin, setBootLogin] = useState(isLogin);

  useLayoutEffect(() => {
    const token = getAccessToken();
    const goingLogin = !token && !isLogin;
    const goingApp = Boolean(token && isLogin);

    setBootLogin(!token);
    document.body.classList.toggle("is-login-route", !token);
    document.documentElement.classList.toggle("auth-pending-login", !token);

    if (goingLogin) {
      // Keep boot visible — revealing children here was the home↔login flash
      setReady(false);
      router.replace("/login");
      return;
    }
    if (goingApp) {
      setReady(false);
      router.replace("/");
      return;
    }
    setReady(true);
  }, [pathname, isLogin, router]);

  useLayoutEffect(() => {
    return () => {
      document.body.classList.remove("is-login-route");
    };
  }, []);

  /** After auth ready: prefetch tab routes, JS chunks, and first-screen API */
  useEffect(() => {
    if (!ready || isLogin || !getAccessToken()) return;
    return scheduleTabWarm((href) => router.prefetch(href));
  }, [ready, isLogin, router]);

  if (!ready) {
    return (
      <div className={`device-stage ${nativeShell ? "device-stage-native" : ""}`}>
        <div
          className={`device-frame ${nativeShell ? "device-frame-native" : ""}`}
          aria-label={nativeShell ? "安崽ETF" : "iOS 预览"}
        >
          <div className="device-bezel">
            <div
              className={`app-shell app-shell-no-tab ${bootLogin ? "app-shell-login" : ""}`}
            >
              <main className="app-main">
                <div
                  className={`auth-boot ${bootLogin ? "auth-boot-login" : ""}`}
                  aria-hidden
                />
              </main>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className={`device-stage ${nativeShell ? "device-stage-native" : ""}`}>
      <div
        className={`device-frame ${nativeShell ? "device-frame-native" : ""}`}
        aria-label={nativeShell ? "安崽ETF" : "iOS 预览"}
      >
        <div className="device-bezel">
          <OverlayProvider>
            {!nativeShell && !isLogin && (
              <>
                <div className="ios-notch" aria-hidden>
                  <span className="ios-notch-speaker" />
                  <span className="ios-notch-camera" />
                </div>
                <StatusBar />
              </>
            )}

            <div
              className={`app-shell ${showTab ? "" : "app-shell-no-tab"} ${isLogin ? "app-shell-login" : ""}`}
            >
              <main className={`app-main${PIN_MAIN_PATHS.has(pathname) ? " app-main--pin" : ""}`}>
                {isLogin ? children : <TabCache>{children}</TabCache>}
              </main>
              {showTab ? <TabBar /> : null}
            </div>

            {!nativeShell && !isLogin && <div className="device-home-indicator" aria-hidden />}
            {!isLogin && <InstallPrompt />}
          </OverlayProvider>
        </div>
      </div>
    </div>
  );
}
