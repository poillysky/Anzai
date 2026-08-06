"use client";

import { AppShell } from "@/components/layout/AppShell";

/**
 * Root providers — shell owns overlay.
 * React Query：等做 Agent / 分析屏再挂 QueryClient（见 docs/架构.md）。
 */
export function AppProviders({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
