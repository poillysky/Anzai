"use client";

import {
  ChartColumn,
  ICON_SIZE_TAB,
  ICON_STROKE,
  Newspaper,
  PieChart,
  Warehouse,
  type LucideIcon,
} from "@/components/ui/icons";
import { useTabNav } from "@/components/layout/TabNavContext";
import { haptics } from "@/lib/haptics";
import { warmTabDataFor, warmTabRoutes } from "@/lib/prefetch";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, type PointerEvent } from "react";

type Tab =
  | { href: string; label: string; Icon: LucideIcon; avatar?: undefined }
  | { href: string; label: string; Icon?: undefined; avatar: string };

/** 五 Tab：仓库 / 股票 / 新闻 / 分析 / 安崽 — docs/界面设计.md §3 */
const tabs: Tab[] = [
  { href: "/", label: "仓库", Icon: Warehouse },
  { href: "/market", label: "行情", Icon: ChartColumn },
  { href: "/news", label: "新闻", Icon: Newspaper },
  { href: "/analysis", label: "分析", Icon: PieChart },
  { href: "/agent", label: "安崽", avatar: "/avatars/anzai.png" },
];

function isActive(path: string, href: string): boolean {
  return href === "/" ? path === "/" : path.startsWith(href);
}

function deferWarm(href: string) {
  const run = () => void warmTabDataFor(href);
  if (typeof window.requestIdleCallback === "function") {
    window.requestIdleCallback(run, { timeout: 400 });
  } else {
    window.setTimeout(run, 0);
  }
}

export function TabBar() {
  const router = useRouter();
  const { path, commit } = useTabNav();

  useEffect(() => {
    warmTabRoutes((href) => router.prefetch(href));
  }, [router]);

  function onPress(href: string, e: PointerEvent<HTMLAnchorElement>) {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    if (isActive(path, href)) return;
    e.preventDefault();
    haptics.selection();
    commit(href);
    deferWarm(href);
  }

  return (
    <nav className="tabbar" aria-label="主导航">
      {tabs.map((tab) => {
        const active = isActive(path, tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            replace
            scroll={false}
            className={["tab-item", active ? "tab-item-active" : ""].filter(Boolean).join(" ")}
            aria-current={active ? "page" : undefined}
            onPointerDown={(e) => onPress(tab.href, e)}
            onClick={(e) => {
              // Navigation already handled on pointerdown; block default / double nav
              e.preventDefault();
              if (!isActive(path, tab.href)) {
                commit(tab.href);
                deferWarm(tab.href);
              }
            }}
          >
            <span className="tab-icon" aria-hidden>
              {tab.avatar ? (
                <img
                  className="tab-avatar"
                  src={tab.avatar}
                  alt=""
                  width={ICON_SIZE_TAB}
                  height={ICON_SIZE_TAB}
                  draggable={false}
                />
              ) : tab.Icon ? (
                <tab.Icon size={ICON_SIZE_TAB} strokeWidth={ICON_STROKE} absoluteStrokeWidth />
              ) : null}
            </span>
            <span className="tab-label">{tab.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
