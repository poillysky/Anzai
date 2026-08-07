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
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useTransition, type MouseEvent } from "react";
import { warmTabRoutes } from "@/lib/prefetch";

type Tab =
  | { href: string; label: string; Icon: LucideIcon; avatar?: undefined }
  | { href: string; label: string; Icon?: undefined; avatar: string };

/** 五 Tab：仓库 / 股票 / 新闻 / 分析 / 安崽 — docs/界面设计.md §3 */
const tabs: Tab[] = [
  { href: "/", label: "仓库", Icon: Warehouse },
  { href: "/market", label: "股票", Icon: ChartColumn },
  { href: "/news", label: "新闻", Icon: Newspaper },
  { href: "/analysis", label: "分析", Icon: PieChart },
  { href: "/agent", label: "安崽", avatar: "/avatars/anzai.png" },
];

function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

export function TabBar() {
  const pathname = usePathname();
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  useEffect(() => {
    warmTabRoutes((href) => router.prefetch(href));
  }, [router]);

  function go(href: string) {
    if (isActive(pathname, href) && !pending) return;
    startTransition(() => {
      router.replace(href, { scroll: false });
    });
  }

  return (
    <nav className="tabbar" aria-label="主导航">
      {tabs.map((tab) => {
        const active = isActive(pathname, tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            replace
            scroll={false}
            className={["tab-item", active ? "tab-item-active" : ""].filter(Boolean).join(" ")}
            aria-current={active ? "page" : undefined}
            onClick={(e: MouseEvent<HTMLAnchorElement>) => {
              e.preventDefault();
              go(tab.href);
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
