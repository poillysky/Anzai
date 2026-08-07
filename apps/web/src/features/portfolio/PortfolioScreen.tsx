"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Lock,
  Minus,
  Plus,
  Search,
  X,
} from "@/components/ui/icons";
import { ActionSheet } from "@/components/overlay/ActionSheet";
import { CenterModal } from "@/components/overlay/CenterModal";
import { useOverlay } from "@/components/overlay/OverlayContext";
import { EmptyState } from "@/components/ui/EmptyState";
import { api } from "@/lib/api";
import {
  formatMoney,
  formatPct,
  formatSignedMoney,
  pnlArrow,
  pnlClass,
  pnlTone,
} from "@/lib/format";
import { haptics } from "@/lib/haptics";
import { cacheFetch, cachePeek, cacheSet, PrefetchKeys, PrefetchTtl } from "@/lib/prefetch";
import type {
  DepthFlow,
  Holding,
  PortfolioReturnsDim,
  PortfolioReturnsSummary,
  PortfolioSummary,
  ShortBias,
} from "@/lib/types";

/** A 股六位代码粗分市场：5/6/9→沪，0/1/2/3→深 */
function inferCnMarket(code: string): "SH" | "SZ" | null {
  const s = code.trim();
  if (!/^\d{6}$/.test(s)) return null;
  const head = s[0];
  if (head === "5" || head === "6" || head === "9") return "SH";
  if (head === "0" || head === "1" || head === "2" || head === "3") return "SZ";
  return null;
}
import { SwipeRevealRow } from "@/features/portfolio/SwipeRevealRow";
import {
  biasChipClass,
  biasChipText,
  biasChipTitle,
  isGoldBiasKey,
} from "@/lib/shortBiasChip";

const POLL_MS = 15000;

type SortKind = "weight" | "pnl" | "day";
type PnlMode = "day" | "total";
type TradeMode = "add" | "reduce" | "edit";

/** 补仓：加权平均成本 */
function applyBuy(shares: number, cost: number, qty: number, price: number) {
  const nextShares = shares + qty;
  const nextCost = nextShares > 0 ? (shares * cost + qty * price) / nextShares : cost;
  return { shares: nextShares, cost: roundCost(nextCost) };
}

/** 减仓：剩余份额成本价不变（A 股记账常见口径） */
function applySell(shares: number, cost: number, qty: number) {
  const nextShares = Math.max(0, shares - qty);
  return { shares: nextShares, cost };
}

function roundCost(n: number) {
  return Math.round(n * 1000) / 1000;
}

const SORT_TABS: { kind: SortKind; label: string }[] = [
  { kind: "day", label: "今日" },
  { kind: "pnl", label: "累计" },
  { kind: "weight", label: "占比" },
];

function formatClockShort(d: Date): string {
  return d.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** 元 → 亿/万，带符号 */
function formatFlowYi(amount: number): string {
  const yi = amount / 1e8;
  if (Math.abs(yi) >= 0.01) {
    return `${yi >= 0 ? "+" : ""}${yi.toFixed(2)}亿`;
  }
  const wan = amount / 1e4;
  return `${wan >= 0 ? "+" : ""}${wan.toFixed(0)}万`;
}

/** Local calendar YYYY-MM-DD (default 买入日). */
function todayIsoDate(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Compact signed amount for calendar cells (Alipay-style, no thousands sep). */
function formatCellPnl(n: number): string {
  const abs = Math.abs(n).toFixed(2);
  if (n > 0) return `+${abs}`;
  if (n < 0) return `-${abs}`;
  return abs;
}

function formatCellPct(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"] as const;

type CalCell = {
  key: string;
  dayLabel: string;
  pnl: number | null;
  pnl_pct: number | null;
  isToday: boolean;
  empty?: boolean;
};

function todayIsoLocal(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Build Sun-first month grid for day-dim calendar. */
function buildDayCalendar(
  ref: string,
  buckets: PortfolioReturnsSummary["buckets"],
): CalCell[] {
  const base = new Date(`${ref.slice(0, 10)}T12:00:00`);
  const y = base.getFullYear();
  const m = base.getMonth();
  const firstDow = new Date(y, m, 1).getDay();
  const daysInMonth = new Date(y, m + 1, 0).getDate();
  const map = new Map(buckets.map((b) => [b.key, b]));
  const today = todayIsoLocal();
  const cells: CalCell[] = [];
  for (let i = 0; i < firstDow; i++) {
    cells.push({ key: `pad-${i}`, dayLabel: "", pnl: null, pnl_pct: null, isToday: false, empty: true });
  }
  for (let day = 1; day <= daysInMonth; day++) {
    const key = `${y}-${String(m + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const b = map.get(key);
    cells.push({
      key,
      dayLabel: String(day),
      pnl: b ? b.pnl : null,
      pnl_pct: b ? b.pnl_pct : null,
      isToday: key === today,
    });
  }
  return cells;
}

/** 3×4 month grid for month/year dim. */
function buildMonthGrid(
  year: number,
  buckets: PortfolioReturnsSummary["buckets"],
): CalCell[] {
  const map = new Map(buckets.map((b) => [b.key, b]));
  const now = new Date();
  const cells: CalCell[] = [];
  for (let month = 1; month <= 12; month++) {
    const key = `${year}-${String(month).padStart(2, "0")}`;
    const b = map.get(key);
    cells.push({
      key,
      dayLabel: `${month}月`,
      pnl: b ? b.pnl : null,
      pnl_pct: b ? b.pnl_pct : null,
      isToday: now.getFullYear() === year && now.getMonth() + 1 === month,
    });
  }
  return cells;
}

type HoldingGroup = {
  key: string;
  symbol: string;
  name: string;
  market: "SH" | "SZ" | "JD";
  lots: Holding[];
  shares: number;
  cost: number;
  market_value: number;
  weight: number;
  day_pnl: number;
  day_pnl_pct: number | null;
  pnl: number;
  last_price: number | null;
  change_pct: number | null;
  pnl_pct: number | null;
};

/** Client-side recompute after delete — mirrors backend cashflow % when possible. */
function summarizeHoldings(holdings: Holding[]): PortfolioSummary {
  let totalCost = 0;
  let totalMv = 0;
  let totalDay = 0;
  let totalDayBase = 0;
  for (const h of holdings) {
    const price = h.last_price ?? h.cost;
    const mv = h.market_value ?? h.shares * price;
    totalCost += h.shares * h.cost;
    totalMv += mv;
    const dp = h.day_pnl ?? 0;
    totalDay += dp;
    const dpp = h.day_pnl_pct;
    if (dpp != null && Number.isFinite(dpp) && Math.abs(dpp) > 1e-9) {
      // recover baseline: day_pnl / (pct/100)
      totalDayBase += (dp / dpp) * 100;
    } else if (dpp === 0 || dpp == null) {
      if (h.prev_close != null && h.prev_close > 0) {
        totalDayBase += h.shares * h.prev_close;
      } else if (Math.abs(dp) > 1e-9) {
        totalDayBase += Math.max(mv - dp, 0);
      }
    }
  }
  const totalPnl = totalMv - totalCost;
  const next = holdings.map((h) => {
    const price = h.last_price ?? h.cost;
    const mv = h.market_value ?? h.shares * price;
    return {
      ...h,
      weight: totalMv > 0 ? Math.round((mv / totalMv) * 10000) / 100 : 0,
    };
  });
  return {
    total_cost: Math.round(totalCost * 100) / 100,
    total_market_value: Math.round(totalMv * 100) / 100,
    total_pnl: Math.round(totalPnl * 100) / 100,
    total_pnl_pct:
      totalCost > 0 ? Math.round((totalPnl / totalCost) * 10000) / 100 : 0,
    day_pnl: Math.round(totalDay * 100) / 100,
    day_pnl_pct:
      totalDayBase > 1e-9 ? Math.round((totalDay / totalDayBase) * 10000) / 100 : 0,
    holdings: next,
  };
}

/** Merge same symbol into one card; lots kept for batch cost lines. */
function groupHoldings(items: Holding[]): HoldingGroup[] {
  const map = new Map<string, Holding[]>();
  for (const h of items) {
    const k = `${h.market}:${h.symbol}`;
    const arr = map.get(k) ?? [];
    arr.push(h);
    map.set(k, arr);
  }
  return [...map.entries()].map(([key, lots]) => {
    const ordered = [...lots].sort((a, b) => a.cost - b.cost);
    const shares = ordered.reduce((s, h) => s + h.shares, 0);
    const costBasis = ordered.reduce((s, h) => s + h.shares * h.cost, 0);
    const cost = shares > 0 ? costBasis / shares : 0;
    const market_value = ordered.reduce((s, h) => s + (h.market_value ?? 0), 0);
    const weight = ordered.reduce((s, h) => s + (h.weight ?? 0), 0);
    const day_pnl = ordered.reduce((s, h) => s + (h.day_pnl ?? 0), 0);
    const pnl = ordered.reduce((s, h) => s + (h.pnl ?? 0), 0);
    const primary = ordered.reduce((a, b) =>
      (b.market_value ?? 0) > (a.market_value ?? 0) ? b : a,
    );
    const day_pnl_pct =
      ordered.length === 1
        ? (primary.day_pnl_pct ?? null)
        : (() => {
            let dayBase = 0;
            for (const h of ordered) {
              const dp = h.day_pnl ?? 0;
              const dpp = h.day_pnl_pct;
              if (dpp != null && Number.isFinite(dpp) && Math.abs(dpp) > 1e-9) {
                dayBase += (dp / dpp) * 100;
              } else if (h.prev_close != null && h.prev_close > 0) {
                dayBase += h.shares * h.prev_close;
              }
            }
            return dayBase > 1e-9
              ? Math.round((day_pnl / dayBase) * 10000) / 100
              : primary.day_pnl_pct ?? null;
          })();
    return {
      key,
      symbol: primary.symbol,
      name: primary.name || primary.symbol,
      market: primary.market,
      lots: ordered,
      shares,
      cost,
      market_value,
      weight,
      day_pnl,
      day_pnl_pct,
      pnl,
      last_price: primary.last_price ?? null,
      change_pct: primary.change_pct ?? null,
      pnl_pct: costBasis > 0 ? (pnl / costBasis) * 100 : null,
    };
  });
}

function sortGroups(groups: HoldingGroup[], kind: SortKind): HoldingGroup[] {
  const list = [...groups];
  if (kind === "day") list.sort((a, b) => b.day_pnl - a.day_pnl);
  else if (kind === "pnl") list.sort((a, b) => (b.pnl_pct ?? 0) - (a.pnl_pct ?? 0));
  else list.sort((a, b) => b.weight - a.weight);
  return list;
}

/** @deprecated Removed — server consolidate_same_symbol on GET. */

/** Hero calendar entry — opens 收益日历. */
function ReturnsCalEntry({
  dayPnl,
  busy,
  onClick,
}: {
  dayPnl: number;
  busy?: boolean;
  onClick: () => void;
}) {
  const tone = dayPnl > 0 ? "up" : dayPnl < 0 ? "down" : "flat";
  const now = new Date();
  const dayNum = String(now.getDate());
  const monthLabel = `${now.getMonth() + 1}月`;
  return (
    <button
      type="button"
      className="portfolio-hero-cal"
      data-tone={tone}
      data-busy={busy ? "1" : "0"}
      aria-label="打开收益日历"
      aria-busy={busy || undefined}
      disabled={busy}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <span className="portfolio-hero-cal-face" aria-hidden>
        <span className="portfolio-hero-cal-band">{monthLabel}</span>
        <span className="portfolio-hero-cal-num">{dayNum}</span>
      </span>
      <span className="portfolio-hero-cal-caption">收益日历</span>
    </button>
  );
}

export default function PortfolioScreen() {
  const { toast } = useOverlay();
  const [data, setData] = useState<PortfolioSummary | null>(
    () => cachePeek<PortfolioSummary>(PrefetchKeys.portfolio),
  );
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(
    () => !cachePeek<PortfolioSummary>(PrefetchKeys.portfolio),
  );
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [pollFailed, setPollFailed] = useState(false);
  const [sortKind, setSortKind] = useState<SortKind>("day");
  const [pnlMode, setPnlMode] = useState<PnlMode>("day");
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [detail, setDetail] = useState<Holding | null>(null);
  const [detailPage, setDetailPage] = useState<"overview" | "trade">("overview");
  const [tradeMode, setTradeMode] = useState<TradeMode>("add");
  const [deleteId, setDeleteId] = useState<number | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{
    ids: number[];
    label: string;
  } | null>(null);
  const [swipeOpenKey, setSwipeOpenKey] = useState<string | null>(null);
  const [shortBiasByKey, setShortBiasByKey] = useState<Record<string, ShortBias>>(
    {},
  );
  const [depthFlow, setDepthFlow] = useState<DepthFlow | null>(null);
  const [depthFlowLoading, setDepthFlowLoading] = useState(false);
  const [depthExpanded, setDepthExpanded] = useState(false);
  const [tradeQty, setTradeQty] = useState("");
  const [tradePrice, setTradePrice] = useState("");
  const [tradeDate, setTradeDate] = useState(todayIsoDate);
  const [returnsOpen, setReturnsOpen] = useState(false);
  const [returnsOpening, setReturnsOpening] = useState(false);
  const [returnsDim, setReturnsDim] = useState<PortfolioReturnsDim>("day");
  const [returnsRef, setReturnsRef] = useState<string | undefined>();
  const [returnsData, setReturnsData] = useState<PortfolioReturnsSummary | null>(() =>
    cachePeek<PortfolioReturnsSummary>(PrefetchKeys.portfolioReturns("day")),
  );
  const [returnsLoading, setReturnsLoading] = useState(false);
  const [returnsError, setReturnsError] = useState<string | null>(null);
  const [returnsUnit, setReturnsUnit] = useState<"cny" | "pct">("cny");
  const [returnsSelected, setReturnsSelected] = useState<string | null>(null);
  const returnsOpenGen = useRef(0);

  const [symbol, setSymbol] = useState("");
  const [market, setMarket] = useState<"SH" | "SZ">("SH");
  const [shares, setShares] = useState("1000");
  const [cost, setCost] = useState("4.20");
  const [boughtAt, setBoughtAt] = useState(todayIsoDate);
  const [resolveName, setResolveName] = useState("");
  const [resolveStatus, setResolveStatus] = useState<"idle" | "loading" | "ok" | "miss">(
    "idle",
  );
  const resolveGen = useRef(0);

  const warmReturnsCache = useCallback(() => {
    for (const dim of ["day", "month", "year"] as const) {
      void cacheFetch(
        PrefetchKeys.portfolioReturns(dim),
        () => api.getPortfolioReturns(dim),
        PrefetchTtl.portfolioReturns,
      ).then((res) => {
        if (dim === "day") setReturnsData((prev) => prev ?? res);
      });
    }
  }, []);

  const load = useCallback(async () => {
    try {
      setError(null);
      let portfolio = await api.getPortfolio();
      cacheSet(PrefetchKeys.portfolio, portfolio);
      setData(portfolio);
      setUpdatedAt(new Date());
      setPollFailed(false);
      setDetail((prev) => {
        if (!prev) return prev;
        return portfolio.holdings.find((h) => h.id === prev.id) ?? null;
      });
      warmReturnsCache();
      const keys = [
        ...new Set(
          portfolio.holdings.map((h) => `${h.market}:${h.symbol}`),
        ),
      ];
      if (keys.length > 0) {
        try {
          const batch = await api.getShortBias(keys);
          const next: Record<string, ShortBias> = {};
          for (const item of batch.items) {
            next[`${item.market}:${item.symbol}`] = item;
          }
          setShortBiasByKey(next);
        } catch {
          // bias is additive — keep last good map on transient failure
        }
      } else {
        setShortBiasByKey({});
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "加载失败";
      setError(msg);
      setPollFailed(true);
    } finally {
      setLoading(false);
    }
  }, [warmReturnsCache]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    const onVis = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [load]);

  /** Fetch returns; when openAfter, only open modal after payload ready (news-reader pattern). */
  const fetchReturns = useCallback(
    async (
      dim: PortfolioReturnsDim,
      ref?: string,
      opts?: { openAfter?: boolean; silent?: boolean },
    ) => {
      const gen = ++returnsOpenGen.current;
      const key = PrefetchKeys.portfolioReturns(dim, ref);
      if (!opts?.silent) setReturnsLoading(true);
      setReturnsError(null);
      try {
        const res = await cacheFetch(
          key,
          () => api.getPortfolioReturns(dim, ref),
          PrefetchTtl.portfolioReturns,
        );
        if (gen !== returnsOpenGen.current) return null;
        setReturnsDim(dim);
        setReturnsRef(ref);
        setReturnsData(res);
        if (opts?.openAfter) setReturnsOpen(true);
        return res;
      } catch (e) {
        if (gen !== returnsOpenGen.current) return null;
        setReturnsError(e instanceof Error ? e.message : "加载失败");
        if (opts?.openAfter) setReturnsOpen(true);
        return null;
      } finally {
        if (gen === returnsOpenGen.current) {
          setReturnsLoading(false);
          setReturnsOpening(false);
        }
      }
    },
    [],
  );

  /** Prefetch body before open so card paints final content (no mid-open grow). */
  const openReturns = useCallback(
    async (dim: PortfolioReturnsDim = "day") => {
      haptics.tap();
      setReturnsSelected(null);
      setReturnsUnit("cny");
      const cached = cachePeek<PortfolioReturnsSummary>(PrefetchKeys.portfolioReturns(dim));
      if (cached) {
        setReturnsDim(dim);
        setReturnsRef(undefined);
        setReturnsData(cached);
        setReturnsError(null);
        setReturnsOpen(true);
        void fetchReturns(dim, undefined, { silent: true });
        return;
      }
      setReturnsOpening(true);
      await fetchReturns(dim, undefined, { openAfter: true });
    },
    [fetchReturns],
  );

  /** Switch dim / period inside modal — paint cache first, then refresh. */
  const switchReturns = useCallback(
    (dim: PortfolioReturnsDim, ref?: string) => {
      haptics.tap();
      setReturnsSelected(null);
      const cached = cachePeek<PortfolioReturnsSummary>(
        PrefetchKeys.portfolioReturns(dim, ref),
      );
      if (cached) {
        setReturnsDim(dim);
        setReturnsRef(ref);
        setReturnsData(cached);
        setReturnsError(null);
        void fetchReturns(dim, ref, { silent: true });
        return;
      }
      void fetchReturns(dim, ref);
    },
    [fetchReturns],
  );

  const returnsCells = useMemo(() => {
    if (!returnsData) return [];
    if (returnsDim === "day") {
      return buildDayCalendar(returnsData.ref, returnsData.buckets);
    }
    const y = Number(returnsData.ref.slice(0, 4)) || new Date().getFullYear();
    return buildMonthGrid(y, returnsData.buckets);
  }, [returnsData, returnsDim]);

  function closeSearch() {
    setSearchOpen(false);
    const active = document.activeElement;
    if (active instanceof HTMLElement) active.blur();
  }

  function clearSearch() {
    setQuery("");
    closeSearch();
  }

  const filtered = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    if (!q) return data.holdings;
    return data.holdings.filter(
      (h) =>
        h.symbol.toLowerCase().includes(q) ||
        (h.name || "").toLowerCase().includes(q),
    );
  }, [data, query]);

  const sorted = useMemo(
    () => sortGroups(groupHoldings(filtered), sortKind),
    [filtered, sortKind],
  );

  const symbolCount = sorted.length;

  const heroTone =
    data == null
      ? "flat"
      : (data.day_pnl ?? 0) > 0
        ? "up"
        : (data.day_pnl ?? 0) < 0
          ? "down"
          : data.total_pnl > 0
            ? "up"
            : data.total_pnl < 0
              ? "down"
              : "flat";

  const kicker = pollFailed
    ? { live: false, text: "更新失败" }
    : updatedAt
      ? { live: true, text: `已更新 ${formatClockShort(updatedAt)}` }
      : { live: false, text: "仓管" };

  async function onAdd(e: FormEvent) {
    e.preventDefault();
    const qty = Number(shares);
    const price = Number(cost);
    if (!Number.isFinite(qty) || qty <= 0 || !Number.isFinite(price) || price <= 0) {
      toast("请输入有效份额和成本", "warning");
      return;
    }
    setSaving(true);
    try {
      const sym = symbol.trim().toUpperCase();
      // Same code already in warehouse → merge as 补仓 (never create another lot)
      const same = (data?.holdings ?? []).filter(
        (h) => h.symbol.toUpperCase() === sym && h.market === market,
      );
      if (same.length > 0) {
        const primary = same.reduce((a, b) =>
          (b.market_value ?? 0) >= (a.market_value ?? 0) ? b : a,
        );
        let s = primary.shares;
        let c = primary.cost;
        for (const h of same) {
          if (h.id === primary.id) continue;
          const merged = applyBuy(s, c, h.shares, h.cost);
          s = merged.shares;
          c = merged.cost;
        }
        const next = applyBuy(s, c, qty, price);
        const buyDate = boughtAt.trim() || todayIsoDate();
        const earlier =
          primary.bought_at && primary.bought_at < buyDate ? primary.bought_at : buyDate;
        await api.updateHolding(primary.id, {
          shares: next.shares,
          cost: next.cost,
          bought_at: earlier,
          trade_price: price,
          trade_date: buyDate,
        });
        for (const h of same) {
          if (h.id !== primary.id) await api.deleteHolding(h.id);
        }
        setAddOpen(false);
        toast(`已并入 ${primary.name || sym}`, "success");
      } else {
        await api.createHolding({
          symbol: sym,
          market,
          name: resolveName.trim() || undefined,
          shares: qty,
          cost: price,
          bought_at: boughtAt.trim() || todayIsoDate(),
        });
        setAddOpen(false);
        toast("已加入仓库", "success");
      }
      await load();
    } catch {
      toast("添加失败", "warning");
    } finally {
      setSaving(false);
    }
  }

  async function onSaveDetail(e: FormEvent) {
    e.preventDefault();
    if (!detail) return;
    setSaving(true);
    try {
      await api.updateHolding(detail.id, {
        shares: Number(shares),
        cost: Number(cost),
        bought_at: boughtAt.trim() || todayIsoDate(),
        ...(Number(shares) !== detail.shares
          ? {
              trade_price:
                detail.last_price != null && detail.last_price > 0
                  ? detail.last_price
                  : Number(cost) || detail.cost,
            }
          : {}),
      });
      toast("已更新", "success");
      closeDetail();
      await load();
    } catch {
      toast("更新失败", "warning");
    } finally {
      setSaving(false);
    }
  }

  async function onTrade(e: FormEvent) {
    e.preventDefault();
    if (!detail) return;
    const qty = Number(tradeQty);
    const price = Number(tradePrice);
    if (!Number.isFinite(qty) || qty <= 0) {
      toast("请输入有效份额", "warning");
      return;
    }

    if (tradeMode === "add") {
      if (!Number.isFinite(price) || price <= 0) {
        toast("请输入买入价", "warning");
        return;
      }
      const next = applyBuy(detail.shares, detail.cost, qty, price);
      setSaving(true);
      try {
        await api.updateHolding(detail.id, {
          shares: next.shares,
          cost: next.cost,
          trade_price: price,
          trade_date: tradeDate.trim() || todayIsoDate(),
        });
        toast(`已补仓 ${qty} 份`, "success");
        closeDetail();
        await load();
      } catch {
        toast("补仓失败", "warning");
      } finally {
        setSaving(false);
      }
      return;
    }

    if (tradeMode === "reduce") {
      if (!Number.isFinite(price) || price <= 0) {
        toast("请输入卖出价", "warning");
        return;
      }
      if (qty > detail.shares) {
        toast("减仓份额不能超过持仓", "warning");
        return;
      }
      if (qty >= detail.shares) {
        setDeleteId(detail.id);
        return;
      }
      const next = applySell(detail.shares, detail.cost, qty);
      setSaving(true);
      try {
        await api.updateHolding(detail.id, {
          shares: next.shares,
          trade_price: price,
        });
        toast(`已减仓 ${qty} 份`, "success");
        closeDetail();
        await load();
      } catch {
        toast("减仓失败", "warning");
      } finally {
        setSaving(false);
      }
    }
  }

  async function confirmDelete() {
    const fromSwipe = pendingDelete != null;
    const ids =
      pendingDelete?.ids ?? (deleteId != null ? [deleteId] : []);
    if (ids.length === 0) return;
    const idSet = new Set(ids);

    // Close sheet / swipe first, then sync totals immediately
    setDeleteId(null);
    setPendingDelete(null);
    setSwipeOpenKey(null);
    closeDetail();
    setData((prev) => {
      if (!prev) return prev;
      return summarizeHoldings(prev.holdings.filter((h) => !idSet.has(h.id)));
    });
    setUpdatedAt(new Date());

    try {
      for (const id of ids) {
        await api.deleteHolding(id);
      }
      toast(fromSwipe ? "已删除" : "已空仓", "success");
      void load();
    } catch {
      toast(fromSwipe ? "删除失败" : "空仓失败", "warning");
      await load();
    }
  }

  function requestDeleteGroup(g: HoldingGroup) {
    setSwipeOpenKey(null);
    setPendingDelete({
      ids: g.lots.map((h) => h.id),
      label: g.name || g.symbol,
    });
  }

  function openAdd() {
    haptics.tap();
    setSymbol("");
    setMarket("SH");
    setShares("1000");
    setCost("4.20");
    setBoughtAt(todayIsoDate());
    setResolveName("");
    setResolveStatus("idle");
    setAddOpen(true);
  }

  /** 输入代码 → 搜名称，并尽量对齐市场 */
  useEffect(() => {
    if (!addOpen) return;
    const q = symbol.trim();
    if (!q) {
      setResolveName("");
      setResolveStatus("idle");
      return;
    }
    const inferred = inferCnMarket(q);
    if (inferred) setMarket(inferred);

    const gen = ++resolveGen.current;
    setResolveStatus("loading");
    const timer = window.setTimeout(() => {
      void api
        .searchSymbols(q, 8)
        .then((res) => {
          if (gen !== resolveGen.current) return;
          const items = res.items || [];
          const exact = items.find(
            (h) => h.symbol.replace(/^0+/, "") === q.replace(/^0+/, "") || h.symbol === q,
          );
          const hit =
            exact ||
            items.find((h) => h.market === "SH" || h.market === "SZ") ||
            items[0] ||
            null;
          if (!hit || (hit.market !== "SH" && hit.market !== "SZ")) {
            setResolveName("");
            setResolveStatus(q.length >= 6 ? "miss" : "idle");
            return;
          }
          setResolveName(hit.name || "");
          setMarket(hit.market);
          setResolveStatus(hit.name ? "ok" : "miss");
        })
        .catch(() => {
          if (gen !== resolveGen.current) return;
          setResolveName("");
          setResolveStatus("miss");
        });
    }, 280);
    return () => window.clearTimeout(timer);
  }, [addOpen, symbol]);

  function openDetail(h: Holding) {
    haptics.tap();
    setDetail(h);
    setDetailPage("overview");
    setDepthFlow(null);
    setDepthExpanded(false);
    setTradeMode("reduce");
    setShares(String(h.shares));
    setCost(String(h.cost));
    setBoughtAt(h.bought_at?.trim() || todayIsoDate());
    setTradeQty("");
    setTradePrice(
      h.last_price != null && h.last_price > 0 ? String(h.last_price) : String(h.cost),
    );
  }

  function closeDetail() {
    setDetail(null);
    setDetailPage("overview");
  }

  function openTradePage(mode: TradeMode) {
    haptics.tap();
    setTradeMode(mode);
    if (mode === "add" && detail) {
      setTradeDate(todayIsoDate());
      setTradePrice(
        detail.last_price != null && detail.last_price > 0
          ? String(detail.last_price)
          : String(detail.cost),
      );
      setTradeQty("");
    } else if (mode === "reduce") {
      setTradeQty("");
    } else if (mode === "edit" && detail) {
      setShares(String(detail.shares));
      setCost(String(detail.cost));
      setBoughtAt(detail.bought_at?.trim() || todayIsoDate());
    }
    setDetailPage("trade");
  }

  function backDetailOverview() {
    haptics.tap();
    setDetailPage("overview");
  }

  useEffect(() => {
    if (!detail) {
      setDepthFlow(null);
      setDepthFlowLoading(false);
      return;
    }
    let cancelled = false;
    setDepthFlowLoading(true);
    void api
      .getDepthFlow(detail.symbol, detail.market, 5)
      .then((snap) => {
        if (!cancelled) setDepthFlow(snap);
      })
      .catch(() => {
        if (!cancelled) setDepthFlow(null);
      })
      .finally(() => {
        if (!cancelled) setDepthFlowLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [detail?.id, detail?.symbol, detail?.market]);

  /** Open from list row immediately; soft-refresh so modal stays in sync after consolidate. */
  function openGroupDetail(g: HoldingGroup) {
    const live =
      data?.holdings.find((h) => h.symbol === g.symbol && h.market === g.market) ??
      g.lots[0];
    if (!live) {
      toast("持仓不存在", "warning");
      return;
    }
    openDetail(live);
    void api
      .getPortfolio()
      .then((portfolio) => {
        setData(portfolio);
        setUpdatedAt(new Date());
        const row = portfolio.holdings.find(
          (h) => h.symbol === g.symbol && h.market === g.market,
        );
        if (row) {
          setDetail((prev) => (prev && prev.symbol === row.symbol ? row : prev));
        }
      })
      .catch(() => {
        /* keep optimistic open */
      });
  }

  const tradePreview = useMemo(() => {
    if (!detail) return null;
    const qty = Number(tradeQty);
    const price = Number(tradePrice);
    if (!Number.isFinite(qty) || qty <= 0) return null;
    if (tradeMode === "add") {
      if (!Number.isFinite(price) || price <= 0) return null;
      return applyBuy(detail.shares, detail.cost, qty, price);
    }
    if (tradeMode === "reduce") {
      if (qty > detail.shares) return null;
      return applySell(detail.shares, detail.cost, qty);
    }
    return null;
  }, [detail, tradeMode, tradeQty, tradePrice]);

  return (
    <div className="portfolio-page">
      <div className="portfolio-page-pin">
        <section
          className="portfolio-hero"
          data-tone={heroTone}
          data-live={kicker.live ? "1" : "0"}
          aria-label="组合总览"
        >
          <div className="portfolio-hero-top">
            <div className="portfolio-hero-main">
              <div className="portfolio-hero-label-row">
                <span className="portfolio-hero-label">安崽老婆本</span>
                <span className="portfolio-hero-kicker" data-live={kicker.live ? "1" : "0"}>
                  {kicker.live && <span className="market-live-dot" aria-hidden />}
                  {kicker.text}
                </span>
              </div>
              {loading && !data ? (
                <div className="portfolio-hero-skel" aria-label="加载中" />
              ) : (
                <div className="portfolio-hero-price">{formatMoney(data?.total_market_value)}</div>
              )}
              {!loading && data ? (
                <button
                  type="button"
                  className={`portfolio-hero-dayline ${pnlClass(data.day_pnl)}`}
                  onClick={() => void openReturns("day")}
                  disabled={returnsOpening}
                  aria-busy={returnsOpening || undefined}
                  aria-label="打开收益日历"
                >
                  <span className="portfolio-hero-dayline-label">今日</span>
                  <span className="portfolio-hero-deltas">
                    <span className="portfolio-delta">
                      {(data.day_pnl ?? 0) > 0 ? (
                        <ArrowUpRight size={11} strokeWidth={2.6} absoluteStrokeWidth aria-hidden />
                      ) : (data.day_pnl ?? 0) < 0 ? (
                        <ArrowDownRight size={11} strokeWidth={2.6} absoluteStrokeWidth aria-hidden />
                      ) : null}
                      {formatSignedMoney(data.day_pnl)}
                    </span>
                    <span className="portfolio-delta portfolio-delta-pct">
                      {formatPct(data.day_pnl_pct)}
                    </span>
                  </span>
                </button>
              ) : null}
            </div>
            {!loading && data ? (
              <ReturnsCalEntry
                dayPnl={data.day_pnl ?? 0}
                busy={returnsOpening}
                onClick={() => void openReturns("day")}
              />
            ) : null}
          </div>

          <div className="portfolio-kpi" aria-label="盈亏指标">
            <button
              type="button"
              className="portfolio-kpi-cell"
              data-active={pnlMode === "day" ? "1" : "0"}
              onClick={() => {
                haptics.tap();
                setPnlMode("day");
                setSortKind("day");
              }}
            >
              <div className="portfolio-meta-label">今日盈亏</div>
              <div className={`portfolio-kpi-value ${pnlClass(data?.day_pnl)}`}>
                <span className="pnl-arrow">{pnlArrow(data?.day_pnl)}</span>
                {formatSignedMoney(data?.day_pnl)}
              </div>
              <div className={`portfolio-kpi-sub ${pnlClass(data?.day_pnl_pct)}`}>
                {formatPct(data?.day_pnl_pct)}
              </div>
            </button>
            <button
              type="button"
              className="portfolio-kpi-cell"
              data-active={pnlMode === "total" ? "1" : "0"}
              onClick={() => {
                haptics.tap();
                setPnlMode("total");
                setSortKind("pnl");
              }}
            >
              <div className="portfolio-meta-label">
                <CalendarDays size={11} strokeWidth={2.2} absoluteStrokeWidth aria-hidden />
                累计盈亏
              </div>
              <div className={`portfolio-kpi-value ${pnlClass(data?.total_pnl)}`}>
                <span className="pnl-arrow">{pnlArrow(data?.total_pnl)}</span>
                {formatSignedMoney(data?.total_pnl)}
              </div>
              <div className={`portfolio-kpi-sub ${pnlClass(data?.total_pnl_pct)}`}>
                {formatPct(data?.total_pnl_pct)}
              </div>
            </button>
            <div className="portfolio-kpi-cell portfolio-kpi-static">
              <div className="portfolio-meta-label">
                <Lock size={11} strokeWidth={2.2} absoluteStrokeWidth aria-hidden />
                持仓成本
              </div>
              <div className="portfolio-kpi-value">{formatMoney(data?.total_cost)}</div>
              <div className="portfolio-kpi-sub text-mute">
                {symbolCount > 0 ? `${symbolCount} 只` : "--"}
              </div>
            </div>
          </div>
        </section>

        <div className="portfolio-sort-tabs" role="tablist" aria-label="排序">
          {SORT_TABS.map(({ kind, label }) => (
            <button
              key={kind}
              type="button"
              role="tab"
              className="portfolio-sort-tab"
              data-active={sortKind === kind ? "1" : "0"}
              aria-selected={sortKind === kind}
              onClick={() => {
                haptics.tap();
                setSortKind(kind);
                // Keep hero KPI in sync when sorting by PnL modes
                if (kind === "day") setPnlMode("day");
                if (kind === "pnl") setPnlMode("total");
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <section className="portfolio-holdings" aria-label="持仓明细">
        <div className="portfolio-holdings-head">
          <span>持仓</span>
          <div className="portfolio-holdings-head-right">
            {data && symbolCount > 0 ? (
              <span className="portfolio-holdings-count">{symbolCount} 只</span>
            ) : null}
            <button
              type="button"
              className="portfolio-icon-btn"
              aria-label="搜索持仓"
              data-active={query ? "1" : "0"}
              onClick={() => {
                haptics.tap();
                setSearchOpen(true);
              }}
            >
              <Search size={15} strokeWidth={2.25} absoluteStrokeWidth />
            </button>
            <button
              type="button"
              className="portfolio-add-btn"
              aria-label="添加持仓"
              onClick={openAdd}
            >
              <Plus size={15} strokeWidth={2.25} absoluteStrokeWidth />
            </button>
          </div>
        </div>

        <div className="portfolio-holdings-body">
          {loading && !data ? (
            <div className="market-leaders-skel" aria-label="加载中">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="market-skel-row" />
              ))}
            </div>
          ) : !data || data.holdings.length === 0 ? (
            <div style={{ padding: "12px 4px 20px" }}>
              <EmptyState
                title={error && !data ? "加载失败" : "还没有持仓"}
                hint={error && !data ? error : "点右上角 + 录入一只沪深 ETF / 股票"}
              />
            </div>
          ) : sorted.length === 0 ? (
            <div style={{ padding: "12px 4px 20px" }}>
              <EmptyState title="无匹配持仓" hint="换个代码或简称试试" />
            </div>
          ) : (
            sorted.map((g) => {
              const pct =
                sortKind === "pnl"
                  ? g.pnl_pct
                  : sortKind === "weight"
                    ? g.weight
                    : sortKind === "day"
                      ? g.day_pnl_pct
                      : g.change_pct;
              const pctLabel =
                sortKind === "weight"
                  ? `${g.weight.toFixed(1)}%`
                  : formatPct(pct);
              const mainAmt =
                sortKind === "day"
                  ? formatSignedMoney(g.day_pnl)
                  : sortKind === "pnl"
                    ? formatSignedMoney(g.pnl)
                    : formatMoney(g.market_value);
              const mainClass =
                sortKind === "day"
                  ? pnlClass(g.day_pnl)
                  : sortKind === "pnl"
                    ? pnlClass(g.pnl)
                    : "";
              const cardTone =
                sortKind === "pnl"
                  ? g.pnl > 0
                    ? "up"
                    : g.pnl < 0
                      ? "down"
                      : "flat"
                  : g.day_pnl > 0
                    ? "up"
                    : g.day_pnl < 0
                      ? "down"
                      : "flat";
              return (
                <article key={g.key} className="portfolio-card" data-tone={cardTone}>
                  <SwipeRevealRow
                    open={swipeOpenKey === g.key}
                    onOpenChange={(open) =>
                      setSwipeOpenKey(open ? g.key : null)
                    }
                    onDelete={() => requestDeleteGroup(g)}
                  >
                    <button
                      type="button"
                      className="portfolio-card-main"
                      onClick={() => {
                        if (swipeOpenKey === g.key) {
                          setSwipeOpenKey(null);
                          return;
                        }
                        void openGroupDetail(g);
                      }}
                    >
                      <div className="portfolio-card-left">
                        <span className="portfolio-row-name">
                          <span className="portfolio-row-name-text">{g.name}</span>
                          {(() => {
                            const bias = shortBiasByKey[g.key];
                            if (!bias) return null;
                            if (
                              bias.bias === "na" &&
                              !bias.label.includes("陈旧")
                            ) {
                              return null;
                            }
                            return (
                              <span
                                className={biasChipClass(bias, g.market, g.symbol)}
                                title={biasChipTitle(bias, g.market, g.symbol)}
                              >
                                {biasChipText(bias, g.market, g.symbol)}
                              </span>
                            );
                          })()}
                        </span>
                        <span className="portfolio-row-meta">
                          {g.symbol}
                          <span className="portfolio-row-dot" aria-hidden>
                            ·
                          </span>
                          {formatMoney(g.last_price)}
                        </span>
                      </div>
                      <div className="portfolio-card-right">
                        <span
                          className={`portfolio-row-chg ${pnlClass(sortKind === "weight" ? null : pct)}`}
                        >
                          {pctLabel}
                        </span>
                        <span className={`portfolio-row-pnl ${mainClass}`}>{mainAmt}</span>
                      </div>
                    </button>
                    <div className="portfolio-card-bar" data-tone={cardTone} aria-hidden>
                      <i style={{ width: `${Math.min(100, Math.max(0, g.weight))}%` }} />
                    </div>
                  </SwipeRevealRow>
                </article>
              );
            })
          )}
        </div>
      </section>

      <CenterModal
        open={searchOpen}
        title="搜索持仓"
        onClose={closeSearch}
        footer={
          <div className="portfolio-detail-footer">
            <button type="button" className="btn btn-ghost" onClick={clearSearch}>
              清除
            </button>
            <button type="button" className="btn btn-block" onClick={closeSearch}>
              完成
            </button>
          </div>
        }
      >
        <div className="portfolio-search-modal">
          <label className="portfolio-filter">
            <Search size={14} strokeWidth={2} absoluteStrokeWidth aria-hidden />
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="代码或名称"
              enterKeyHint="search"
              autoComplete="off"
              autoCorrect="off"
              spellCheck={false}
              data-autofocus="true"
            />
            {query ? (
              <button
                type="button"
                className="portfolio-filter-clear"
                aria-label="清空"
                onClick={() => setQuery("")}
              >
                <X size={14} strokeWidth={2} absoluteStrokeWidth />
              </button>
            ) : null}
          </label>
          <p className="portfolio-search-hint">输入后点完成，下方持仓列表会按关键词筛选</p>
        </div>
      </CenterModal>

      <CenterModal
        open={addOpen}
        title="添加持仓"
        onClose={() => setAddOpen(false)}
        footer={
          <button
            className="btn btn-block"
            type="submit"
            form="add-holding-form"
            disabled={saving}
          >
            {saving ? "保存中…" : "确认加入"}
          </button>
        }
      >
        <form id="add-holding-form" className="form-grid" onSubmit={onAdd}>
          <input
            className="full"
            value={symbol}
            onChange={(e) => {
              const next = e.target.value.replace(/\s/g, "");
              setSymbol(next);
              setResolveName("");
              setResolveStatus(next ? "loading" : "idle");
            }}
            placeholder="代码，如 518880 / 510300"
            inputMode="numeric"
            autoComplete="off"
            required
          />
          <p
            className="portfolio-resolve-hint full"
            data-status={resolveStatus}
            aria-live="polite"
          >
            {resolveStatus === "loading"
              ? "识别名称中…"
              : resolveStatus === "ok" && resolveName
                ? `${resolveName} · ${market}`
                : resolveStatus === "miss"
                  ? "未找到该代码，仍可手动加入（保存时再取行情名）"
                  : "输入六位代码后自动识别中文名称"}
          </p>
          <select
            className="full"
            value={market}
            onChange={(e) => setMarket(e.target.value as "SH" | "SZ")}
          >
            <option value="SH">上交所 SH</option>
            <option value="SZ">深交所 SZ</option>
          </select>
          <input
            value={shares}
            onChange={(e) => setShares(e.target.value)}
            type="number"
            min="0"
            step="1"
            placeholder="份额"
            required
          />
          <input
            value={cost}
            onChange={(e) => setCost(e.target.value)}
            type="number"
            min="0"
            step="0.001"
            placeholder="买入价（成交价）"
            required
          />
          <label className="full portfolio-date-field">
            <span>买入日</span>
            <input
              type="date"
              value={boughtAt}
              onChange={(e) => setBoughtAt(e.target.value || todayIsoDate())}
              required
            />
          </label>
          <p className="portfolio-trade-hint full">
            昨买今录：买入日选昨天、买入价填昨成交价。今日盈亏按昨收算；累计盈亏按买入价算。
          </p>
        </form>
      </CenterModal>

      <CenterModal
        open={detail != null}
        title={
          detailPage === "trade"
            ? tradeMode === "add"
              ? "补仓"
              : tradeMode === "reduce"
                ? "减仓"
                : "改成本"
            : detail?.name || detail?.symbol || "持仓详情"
        }
        onClose={closeDetail}
        onBack={detailPage === "trade" ? backDetailOverview : undefined}
        footer={
          detailPage === "overview" ? (
            <button
              type="button"
              className="btn btn-ghost btn-block"
              onClick={() => {
                if (detail) setDeleteId(detail.id);
              }}
            >
              空仓
            </button>
          ) : tradeMode === "edit" ? (
            <button
              className="btn btn-block"
              type="submit"
              form="edit-holding-form"
              disabled={saving}
            >
              {saving ? "保存中…" : "保存修改"}
            </button>
          ) : (
            <button
              className="btn btn-block"
              type="submit"
              form="trade-holding-form"
              disabled={saving}
            >
              {saving
                ? "提交中…"
                : tradeMode === "add"
                  ? "确认补仓"
                  : "确认减仓"}
            </button>
          )
        }
      >
        {detail && detailPage === "overview" ? (
          <div className="portfolio-detail">
            {(() => {
              const bias = shortBiasByKey[`${detail.market}:${detail.symbol}`];
              const lastFlow = depthFlow?.flow_days?.length
                ? depthFlow.flow_days[depthFlow.flow_days.length - 1]
                : null;
              const bid1 = depthFlow?.book?.bids?.[0];
              const ask1 = depthFlow?.book?.asks?.[0];
              const quoteTone = pnlTone(
                detail.change_pct,
                detail.last_price,
                detail.prev_close,
              );
              const dayTone =
                (detail.day_pnl ?? 0) > 0
                  ? "up"
                  : (detail.day_pnl ?? 0) < 0
                    ? "down"
                    : "flat";
              const totalTone =
                (detail.pnl ?? 0) > 0 ? "up" : (detail.pnl ?? 0) < 0 ? "down" : "flat";
              return (
                <>
                  <div className="portfolio-detail-quote">
                    <div className="portfolio-detail-quote-main">
                      <span className="portfolio-detail-code">
                        {detail.market} · {detail.symbol}
                      </span>
                      <span className={`portfolio-detail-last ${quoteTone}`}>
                        {formatMoney(detail.last_price)}
                      </span>
                    </div>
                    <div className="portfolio-detail-quote-side">
                      <span className={`portfolio-detail-quote-badge ${quoteTone}`}>
                        <span className="portfolio-detail-quote-tag">行情</span>
                        {formatPct(detail.change_pct)}
                      </span>
                      {bias &&
                      !(bias.bias === "na" && !bias.label.includes("陈旧")) ? (
                        <span
                          className={biasChipClass(bias, detail.market, detail.symbol)}
                          title={biasChipTitle(bias, detail.market, detail.symbol)}
                        >
                          {biasChipText(bias, detail.market, detail.symbol)}
                        </span>
                      ) : null}
                    </div>
                  </div>

                  <div className="portfolio-detail-pnl" role="group" aria-label="盈亏">
                    <div className="portfolio-detail-pnl-cell" data-tone={dayTone}>
                      <div className="portfolio-detail-pnl-label">今日</div>
                      <div className={`portfolio-detail-pnl-amt ${pnlClass(detail.day_pnl)}`}>
                        {formatSignedMoney(detail.day_pnl)}
                      </div>
                      <div className={`portfolio-detail-pnl-pct ${pnlClass(detail.day_pnl_pct)}`}>
                        {formatPct(detail.day_pnl_pct)}
                      </div>
                    </div>
                    <div className="portfolio-detail-pnl-cell" data-tone={totalTone}>
                      <div className="portfolio-detail-pnl-label">累计</div>
                      <div className={`portfolio-detail-pnl-amt ${pnlClass(detail.pnl)}`}>
                        {formatSignedMoney(detail.pnl)}
                      </div>
                      <div className={`portfolio-detail-pnl-pct ${pnlClass(detail.pnl_pct)}`}>
                        {formatPct(detail.pnl_pct)}
                      </div>
                    </div>
                  </div>

                  <div className="portfolio-detail-facts" aria-label="持仓概况">
                    <div>
                      <span>{detail.market === "JD" ? "克数" : "份额"}</span>
                      <b>{detail.shares}</b>
                    </div>
                    <div>
                      <span>成本</span>
                      <b>{formatMoney(detail.cost)}</b>
                    </div>
                    <div>
                      <span>市值</span>
                      <b>{formatMoney(detail.market_value)}</b>
                    </div>
                    <div>
                      <span>仓位</span>
                      <b>{(detail.weight ?? 0).toFixed(1)}%</b>
                    </div>
                    <div className="portfolio-detail-facts-wide">
                      <span>买入日</span>
                      <b>{detail.bought_at?.trim() || boughtAt || "—"}</b>
                    </div>
                  </div>

                  <section className="portfolio-depth-flow portfolio-depth-flow--sheet" aria-label="盘口与资金">
                    <button
                      type="button"
                      className="portfolio-depth-toggle"
                      onClick={() => {
                        haptics.tap();
                        setDepthExpanded((v) => !v);
                      }}
                    >
                      <span className="portfolio-depth-toggle-main">
                        {(() => {
                          if (depthFlowLoading && !depthFlow) return "资金加载中…";
                          if (detail.market === "JD") {
                            return depthFlow?.flow_label || "积存金无场内资金";
                          }
                          const flowBit = lastFlow
                            ? `${depthFlow?.flow_label || "资金"} ${formatFlowYi(lastFlow.main_net)}`
                            : "资金暂无";
                          if (depthFlow?.book_live && bid1 && ask1) {
                            return `${flowBit} · 买${bid1.price.toFixed(2)}/卖${ask1.price.toFixed(2)}`;
                          }
                          if (depthFlow && !depthFlow.book_live) {
                            const bookHint = isGoldBiasKey(detail.market, detail.symbol)
                              ? " · 场内已收无盘口"
                              : " · 已收盘无盘口";
                            return `${flowBit}${bookHint}`;
                          }
                          return flowBit;
                        })()}
                      </span>
                      <span className="portfolio-depth-toggle-chevron" data-open={depthExpanded ? "1" : "0"}>
                        {depthExpanded
                          ? "收起"
                          : detail.market === "JD"
                            ? "说明"
                            : depthFlow?.book_live
                              ? "盘口"
                              : "明细"}
                      </span>
                    </button>
                    {depthExpanded ? (
                      <div className="portfolio-depth-panel">
                        {detail.market === "JD" ? (
                          <p className="text-mute portfolio-depth-flow-empty">
                            {depthFlow?.note ||
                              "积存金为场外金价，无交易所五档与主力资金；请看实时金价与仓位盈亏。"}
                          </p>
                        ) : (
                          <>
                            {lastFlow ? (
                              <p className="text-mute portfolio-flow-mini">
                                超大 {formatFlowYi(lastFlow.super_net)} · 大{" "}
                                {formatFlowYi(lastFlow.large_net)} · 中{" "}
                                {formatFlowYi(lastFlow.mid_net)} · 小{" "}
                                {formatFlowYi(lastFlow.small_net)}
                              </p>
                            ) : null}
                            {depthFlow?.book_live &&
                            depthFlow.book &&
                            (depthFlow.book.bids.length > 0 || depthFlow.book.asks.length > 0) ? (
                              <div className="portfolio-book-grid" aria-label="买卖五档">
                                <div className="portfolio-book-col is-ask">
                                  <span className="portfolio-book-col-label">卖</span>
                                  {[...depthFlow.book.asks].reverse().map((lv, i) => (
                                    <div key={`a-${i}`} className="portfolio-book-row">
                                      <span>{lv.price > 0 ? lv.price.toFixed(2) : "—"}</span>
                                      <span>{lv.volume > 0 ? Math.round(lv.volume) : "—"}</span>
                                    </div>
                                  ))}
                                </div>
                                <div className="portfolio-book-col is-bid">
                                  <span className="portfolio-book-col-label">买</span>
                                  {depthFlow.book.bids.map((lv, i) => (
                                    <div key={`b-${i}`} className="portfolio-book-row">
                                      <span>{lv.price > 0 ? lv.price.toFixed(2) : "—"}</span>
                                      <span>{lv.volume > 0 ? Math.round(lv.volume) : "—"}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            ) : (
                              <p className="text-mute portfolio-depth-flow-empty">
                                {depthFlow?.session_state === "trading"
                                  ? "五档暂无挂单"
                                  : isGoldBiasKey(detail.market, detail.symbol)
                                    ? "黄金ETF场内已收盘，无实时五档；上方资金为日频统计"
                                    : "非交易时段，无实时买卖五档"}
                              </p>
                            )}
                          </>
                        )}
                      </div>
                    ) : null}
                  </section>
                </>
              );
            })()}

            <div className="portfolio-trade-tabs" role="navigation" aria-label="仓位操作">
              <button type="button" onClick={() => openTradePage("add")}>
                <Plus size={14} strokeWidth={2.25} absoluteStrokeWidth />
                补仓
              </button>
              <button type="button" onClick={() => openTradePage("reduce")}>
                <Minus size={14} strokeWidth={2.25} absoluteStrokeWidth />
                减仓
              </button>
              <button type="button" onClick={() => openTradePage("edit")}>
                改成本
              </button>
            </div>
          </div>
        ) : null}

        {detail && detailPage === "trade" ? (
          <div className="portfolio-detail portfolio-detail--trade">
            <p className="portfolio-trade-context">
              {detail.name || detail.symbol} · {detail.shares}
              {detail.market === "JD" ? "克" : "份"} · 成本 {formatMoney(detail.cost)} · 现价{" "}
              {formatMoney(detail.last_price)}
            </p>
            {tradeMode === "edit" ? (
              <form id="edit-holding-form" className="form-grid portfolio-detail-form" onSubmit={onSaveDetail}>
                <label className="portfolio-field">
                  <span>份额</span>
                  <input
                    value={shares}
                    onChange={(e) => setShares(e.target.value)}
                    type="number"
                    min="0"
                    step="1"
                    placeholder="份额"
                    required
                    data-autofocus
                  />
                </label>
                <label className="portfolio-field">
                  <span>成本价</span>
                  <input
                    value={cost}
                    onChange={(e) => setCost(e.target.value)}
                    type="number"
                    min="0"
                    step="0.001"
                    placeholder="成本价"
                    required
                  />
                </label>
                <label className="full portfolio-date-field">
                  <span>买入日</span>
                  <input
                    type="date"
                    value={boughtAt}
                    onChange={(e) => setBoughtAt(e.target.value || todayIsoDate())}
                    required
                  />
                </label>
              </form>
            ) : (
              <form id="trade-holding-form" className="form-grid portfolio-detail-form" onSubmit={onTrade}>
                <label className="portfolio-field">
                  <span className="portfolio-field-head">
                    <span>{tradeMode === "add" ? "补仓份额" : "减仓份额"}</span>
                    {tradeMode === "reduce" && (
                      <span className="portfolio-qty-presets" role="group" aria-label="快捷份额">
                        <button
                          type="button"
                          className="portfolio-qty-chip"
                          onClick={() => {
                            haptics.tap();
                            setTradeQty(String(Math.floor(detail.shares / 2) || 1));
                          }}
                        >
                          ½
                        </button>
                        <button
                          type="button"
                          className="portfolio-qty-chip"
                          onClick={() => {
                            haptics.tap();
                            setTradeQty(String(Math.floor(detail.shares) || 1));
                          }}
                        >
                          全部
                        </button>
                      </span>
                    )}
                  </span>
                  <input
                    value={tradeQty}
                    onChange={(e) => setTradeQty(e.target.value)}
                    type="number"
                    min="1"
                    step="1"
                    placeholder={tradeMode === "add" ? "补仓份额" : "减仓份额"}
                    required
                    data-autofocus
                  />
                </label>
                {tradeMode === "add" ? (
                  <>
                    <label className="portfolio-field">
                      <span>买入价</span>
                      <input
                        value={tradePrice}
                        onChange={(e) => setTradePrice(e.target.value)}
                        type="number"
                        min="0"
                        step="0.001"
                        placeholder="买入价"
                        required
                      />
                    </label>
                    <label className="full portfolio-date-field">
                      <span>买入日</span>
                      <input
                        type="date"
                        value={tradeDate}
                        onChange={(e) => setTradeDate(e.target.value || todayIsoDate())}
                        required
                      />
                    </label>
                  </>
                ) : (
                  <label className="portfolio-field">
                    <span>卖出价</span>
                    <input
                      value={tradePrice}
                      onChange={(e) => setTradePrice(e.target.value)}
                      type="number"
                      min="0"
                      step="0.001"
                      placeholder="卖出价"
                      required
                    />
                  </label>
                )}
                {tradePreview && (
                  <p className="portfolio-trade-preview full">
                    操作后：{tradePreview.shares} 份 · 成本 {formatMoney(tradePreview.cost)}
                    {tradeMode === "reduce" && tradePreview.shares === 0
                      ? "（将空仓）"
                      : ""}
                  </p>
                )}
              </form>
            )}
          </div>
        ) : null}
      </CenterModal>

      <CenterModal
        open={returnsOpen}
        title="收益日历"
        onClose={() => setReturnsOpen(false)}
        footer={
          <button type="button" className="btn btn-block" onClick={() => setReturnsOpen(false)}>
            关闭
          </button>
        }
      >
        <div className="portfolio-returns">
          <div className="portfolio-returns-chrome">
            <div className="portfolio-returns-toolbar">
              <div className="portfolio-returns-dims" role="tablist" aria-label="时间维度">
                {(
                  [
                    ["day", "日"],
                    ["month", "月"],
                    ["year", "年"],
                  ] as const
                ).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    role="tab"
                    aria-selected={returnsDim === key}
                    className="portfolio-returns-dim"
                    data-active={returnsDim === key ? "1" : "0"}
                    onClick={() => switchReturns(key)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="portfolio-returns-unit" role="group" aria-label="显示单位">
                <button
                  type="button"
                  className="portfolio-returns-unit-btn"
                  data-active={returnsUnit === "cny" ? "1" : "0"}
                  onClick={() => {
                    haptics.tap();
                    setReturnsUnit("cny");
                  }}
                >
                  ¥
                </button>
                <button
                  type="button"
                  className="portfolio-returns-unit-btn"
                  data-active={returnsUnit === "pct" ? "1" : "0"}
                  onClick={() => {
                    haptics.tap();
                    setReturnsUnit("pct");
                  }}
                >
                  %
                </button>
              </div>
            </div>

            <div className="portfolio-returns-nav">
              <button
                type="button"
                className="portfolio-returns-nav-btn"
                aria-label="上一段"
                disabled={returnsLoading}
                onClick={() => {
                  if (!returnsData?.prev_ref) return;
                  switchReturns(returnsDim, returnsData.prev_ref);
                }}
              >
                <ChevronLeft size={18} strokeWidth={2.2} absoluteStrokeWidth aria-hidden />
              </button>
              <div className="portfolio-returns-period">
                <CalendarDays size={14} strokeWidth={2.2} absoluteStrokeWidth aria-hidden />
                <span>{returnsData?.label ?? "—"}</span>
              </div>
              <button
                type="button"
                className="portfolio-returns-nav-btn"
                aria-label="下一段"
                disabled={!returnsData?.next_ref || returnsLoading}
                onClick={() => {
                  if (!returnsData?.next_ref) return;
                  switchReturns(returnsDim, returnsData.next_ref);
                }}
              >
                <ChevronRight size={18} strokeWidth={2.2} absoluteStrokeWidth aria-hidden />
              </button>
            </div>
          </div>

          {returnsLoading && !returnsData ? (
            <div className="portfolio-returns-loading">加载中…</div>
          ) : returnsError ? (
            <div className="portfolio-returns-error">{returnsError}</div>
          ) : returnsData ? (
            <>
              <div
                className={`portfolio-returns-summary ${pnlClass(returnsData.pnl)}`}
                data-tone={
                  returnsData.pnl > 0 ? "up" : returnsData.pnl < 0 ? "down" : "flat"
                }
              >
                <div className="portfolio-returns-summary-main">
                  <span className="portfolio-returns-summary-label">区间盈亏</span>
                  <span className="portfolio-returns-summary-value">
                    {formatSignedMoney(returnsData.pnl)}
                  </span>
                </div>
                <span className={`portfolio-returns-summary-chip ${pnlClass(returnsData.pnl_pct)}`}>
                  {formatPct(returnsData.pnl_pct)}
                </span>
              </div>

              <div
                className="portfolio-returns-cal"
                data-dim={returnsDim}
                aria-label={returnsDim === "day" ? "收益日历" : "收益月历"}
              >
                {returnsDim === "day" ? (
                  <div className="portfolio-returns-cal-head" aria-hidden>
                    {WEEKDAYS.map((w) => (
                      <span key={w}>{w}</span>
                    ))}
                  </div>
                ) : null}
                <div
                  className={
                    returnsDim === "day"
                      ? "portfolio-returns-cal-grid"
                      : "portfolio-returns-cal-grid portfolio-returns-cal-grid--months"
                  }
                >
                  {returnsCells.map((cell) => {
                    if (cell.empty) {
                      return <div key={cell.key} className="portfolio-returns-cal-pad" />;
                    }
                    const tone =
                      cell.pnl == null
                        ? "none"
                        : cell.pnl > 0
                          ? "up"
                          : cell.pnl < 0
                            ? "down"
                            : "flat";
                    const selected = returnsSelected === cell.key;
                    const value =
                      cell.pnl == null
                        ? ""
                        : returnsUnit === "pct"
                          ? formatCellPct(cell.pnl_pct ?? 0)
                          : formatCellPnl(cell.pnl);
                    return (
                      <button
                        key={cell.key}
                        type="button"
                        className="portfolio-returns-cal-cell"
                        data-tone={tone}
                        data-selected={selected ? "1" : "0"}
                        data-today={cell.isToday ? "1" : "0"}
                        onClick={() => {
                          haptics.tap();
                          setReturnsSelected(cell.key);
                        }}
                      >
                        <span className="portfolio-returns-cal-day">
                          {cell.isToday && returnsDim === "day" ? "今" : cell.dayLabel}
                        </span>
                        {value ? (
                          <span className="portfolio-returns-cal-val">{value}</span>
                        ) : (
                          <span className="portfolio-returns-cal-val portfolio-returns-cal-val--empty">
                            ·
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>

              {returnsData.note ? (
                <p className="portfolio-returns-note">{returnsData.note}</p>
              ) : null}
            </>
          ) : null}
        </div>
      </CenterModal>

      <ActionSheet
        open={deleteId != null || pendingDelete != null}
        title={
          pendingDelete
            ? `从仓库删除「${pendingDelete.label}」？不可恢复`
            : "确认空仓？清空后不可恢复"
        }
        onClose={() => {
          setDeleteId(null);
          setPendingDelete(null);
        }}
        actions={[
          {
            label: pendingDelete ? "确认删除" : "确认空仓",
            destructive: true,
            onClick: () => confirmDelete(),
          },
        ]}
      />

    </div>
  );
}
