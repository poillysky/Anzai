import type { Metadata, Viewport } from "next";

/** Login segment: cream shell continuous with PWA gate (BrewStory-style same-color boot). */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
  themeColor: "#faf9f6",
  interactiveWidget: "overlays-content",
};

export const metadata: Metadata = {
  appleWebApp: {
    capable: true,
    // Draw under notch — hero/cream fills 刘海; chrome uses safe-area padding
    statusBarStyle: "black-translucent",
    title: "安崽ETF",
  },
};

export default function LoginLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      {/* Preload gate art — continuous with cream auth-boot */}
      <link rel="preload" as="image" href="/brand/anzai-login-hero.png" />
      {children}
    </>
  );
}
