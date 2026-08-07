import type { Metadata, Viewport } from "next";
import { AppleSplashLinks } from "@/components/layout/AppleSplashLinks";
import { AppProviders } from "@/providers/AppProviders";
import "./globals.css";

export const metadata: Metadata = {
  title: "安崽",
  description: "个人 A 股 / ETF 分析助手",
  applicationName: "安崽",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "安崽",
  },
  formatDetection: {
    telephone: false,
  },
  manifest: "/manifest.webmanifest",
  icons: {
    apple: [{ url: "/icons/apple-touch-icon-180.png?v=2", sizes: "180x180", type: "image/png" }],
    icon: [
      { url: "/icons/icon-32.png?v=2", sizes: "32x32", type: "image/png" },
      { url: "/icons/icon-192.png?v=2", sizes: "192x192", type: "image/png" },
      { url: "/icons/icon-512.png?v=2", sizes: "512x512", type: "image/png" },
    ],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
  viewportFit: "cover",
  themeColor: "#070708",
  // Prefer overlay keyboard — reduces layout resize jump (iOS may still pan; JS pins scroll)
  interactiveWidget: "overlays-content",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="mobile-web-app-capable" content="yes" />
        <meta name="apple-touch-fullscreen" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
        <meta name="apple-mobile-web-app-title" content="安崽" />
        <AppleSplashLinks />
        {/* Runs before paint; html class differs from SSR — suppressHydrationWarning above */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{if(!localStorage.getItem("anzai_access_token")){document.documentElement.classList.add("auth-pending-login");}}catch(e){}})();`,
          }}
        />
      </head>
      <body className="antialiased" suppressHydrationWarning>
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
