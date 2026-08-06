"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  CircleOff,
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
} from "@/lib/format";
import { haptics } from "@/lib/haptics";
import { cacheFetch, cachePeek, cacheSet, PrefetchKeys, PrefetchTtl } from "@/lib/prefetch";
import type {
  Holding,
  PortfolioReturnsDim,
  PortfolioReturnsSummary,
  PortfolioSummary,
} from "@/lib/types";
import { SwipeRevealRow } from "@/features/portfolio/SwipeRevealRow";

const POLL_MS = 15000;

type SortKind = "weight" | "pnl" | "value" | "day";
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
  { kind: "value", label: "市值" },
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
  market: "SH" | "SZ";
  lots: Holding[];
  shares: number;
  cost: number;
  market_value: number;
  weight: number;
  day_pnl: number;
  pnl: number;
  last_price: number | null;
  change_pct: number | null;
  pnl_pct: number | null;
};

/** Client-side recompute after delete — mirrors backend portfolio.py formulas. */
function summarizeHoldings(holdings: Holding[]): PortfolioSummary {
  let totalCost = 0;
  let totalMv = 0;
  let totalDay = 0;
  let totalPrev = 0;
  for (const h of holdings) {
    const price = h.last_price ?? h.cost;
    const mv = h.market_value ?? h.shares * price;
    totalCost += h.shares * h.cost;
    totalMv += mv;
    if (h.day_pnl != null) totalDay += h.day_pnl;
    if (h.prev_close != null && h.prev_close > 0) {
      totalPrev += h.shares * h.prev_close;
    } else if (h.day_pnl != null) {
      totalPrev += mv - h.day_pnl;
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
      totalPrev > 0 ? Math.round((totalDay / totalPrev) * 10000) / 100 : 0,
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
  else if (kind === "value") list.sort((a, b) => b.market_value - a.market_value);
  else list.sort((a, b) => b.weight - a.weight);
  return list;
}

/** Merge same-symbol lots into one row — UI only shows current state. */
async function consolidateDuplicateLots(holdings: Holding[]): Promise<boolean> {
  const groups = groupHoldings(holdings).filter((g) => g.lots.length > 1);
  if (groups.length === 0) return false;
  for (const g of groups) {
    const primary = g.lots.reduce((a, b) =>
      (b.market_value ?? 0) >= (a.market_value ?? 0) ? b : a,
    );
    let s = primary.shares;
    let c = primary.cost;
    for (const h of g.lots) {
      if (h.id === primary.id) continue;
      const merged = applyBuy(s, c, h.shares, h.cost);
      s = merged.shares;
      c = merged.cost;
    }
    await api.updateHolding(primary.id, { shares: s, cost: c });
    for (const h of g.lots) {
      if (h.id !== primary.id) await api.deleteHolding(h.id);
    }
  }
  return true;
}

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
  const [sortKind, setSortKind] = useState<SortKind>("value");
  const [pnlMode, setPnlMode] = useState<PnlMode>("day");
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [detail, setDetail] = useState<Holding | null>(null);
  const [tradeMode, setTradeMode] = useState<TradeMode>("add");
  const [deleteId, setDeleteId] = useState<number | null>(null);
  const [pendingDelete, setPendingDelete] = useState<{
    ids: number[];
    label: string;
  } | null>(null);
  const [swipeOpenKey, setSwipeOpenKey] = useState<string | null>(null);
  const [tradeQty, setTradeQty] = useState("");
  const [tradePrice, setTradePrice] = useState("");
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

  const [symbol, setSymbol] = useState("510300");
  const [market, setMarket] = useState<"SH" | "SZ">("SH");
  const [shares, setShares] = useState("1000");
  const [cost, setCost] = useState("4.20");
  const [boughtAt, setBoughtAt] = useState(todayIsoDate);

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
      if (await consolidateDuplicateLots(portfolio.holdings)) {
        portfolio = await api.getPortfolio();
      }
      cacheSet(PrefetchKeys.portfolio, portfolio);
      setData(portfolio);
      setUpdatedAt(new Date());
      setPollFailed(false);
      setDetail((prev) => {
        if (!prev) return prev;
        return portfolio.holdings.find((h) => h.id === prev.id) ?? null;
      });
      warmReturnsCache();
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
      });
      toast("已更新", "success");
      setDetail(null);
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
        await api.updateHolding(detail.id, { shares: next.shares, cost: next.cost });
        toast(`已补仓 ${qty} 份`, "success");
        setDetail(null);
        await load();
      } catch {
        toast("补仓失败", "warning");
      } finally {
        setSaving(false);
      }
      return;
    }

    if (tradeMode === "reduce") {
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
        await api.updateHolding(detail.id, { shares: next.shares });
        toast(`已减仓 ${qty} 份`, "success");
        setDetail(null);
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
    setDetail(null);
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
    setSymbol("510300");
    setMarket("SH");
    setShares("1000");
    setCost("4.20");
    setBoughtAt(todayIsoDate());
    setAddOpen(true);
  }

  function openDetail(h: Holding) {
    haptics.tap();
    setDetail(h);
    setTradeMode("reduce");
    setShares(String(h.shares));
    setCost(String(h.cost));
    setBoughtAt(h.bought_at?.trim() || todayIsoDate());
    setTradeQty("");
    setTradePrice(
      h.last_price != null && h.last_price > 0 ? String(h.last_price) : String(h.cost),
    );
  }

  /** Open card using live row — shares on modal must match list (after server consolidate). */
  async function openGroupDetail(g: HoldingGroup) {
    try {
      const portfolio = await api.getPortfolio();
      setData(portfolio);
      setUpdatedAt(new Date());
      const row = portfolio.holdings.find(
        (h) => h.symbol === g.symbol && h.market === g.market,
      );
      if (!row) {
        toast("持仓不存在", "warning");
        return;
      }
      openDetail(row);
    } catch {
      toast("打开失败", "warning");
    }
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
                    : sortKind === "weight"
                      ? formatMoney(g.market_value)
                      : formatMoney(g.market_value);
              const mainClass =
                sortKind === "day"
                  ? pnlClass(g.day_pnl)
                  : sortKind === "pnl"
                    ? pnlClass(g.pnl)
                    : "";
              const live =
                data?.holdings.find((h) => h.symbol === g.symbol && h.market === g.market) ??
                g.lots[0];
              const cardTone =
                g.day_pnl > 0 ? "up" : g.day_pnl < 0 ? "down" : "flat";
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
                        <span className="portfolio-row-name">{g.name}</span>
                        <span className="portfolio-row-meta">
                          {g.symbol}
                          <span className="portfolio-row-dot" aria-hidden>
                            ·
                          </span>
                          {formatMoney(g.last_price)}
                          <span className="portfolio-row-dot" aria-hidden>
                            ·
                          </span>
                          {live.shares}份
                        </span>
                        <span className="portfolio-card-mv text-mute">
                          市值 {formatMoney(live.market_value ?? g.market_value)} · 成本{" "}
                          {formatMoney(live.cost)}
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
            onChange={(e) => setSymbol(e.target.value)}
            placeholder="代码，如 510300"
            inputMode="numeric"
            required
          />
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
            placeholder="成本价"
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
        </form>
      </CenterModal>

      <CenterModal
        open={detail != null}
        title={detail?.name || detail?.symbol || "持仓详情"}
        onClose={() => setDetail(null)}
        footer={
          tradeMode === "edit" ? (
            <div className="portfolio-detail-footer">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => {
                  if (detail) setDeleteId(detail.id);
                }}
              >
                空仓
              </button>
              <button
                className="btn btn-block"
                type="submit"
                form="edit-holding-form"
                disabled={saving}
              >
                {saving ? "保存中…" : "保存修改"}
              </button>
            </div>
          ) : (
            <div className="portfolio-detail-footer">
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => {
                  if (detail) setDeleteId(detail.id);
                }}
              >
                空仓
              </button>
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
            </div>
          )
        }
      >
        {detail && (
          <div className="portfolio-detail">
            <div className="market-detail-quote">
              <div className="market-detail-code">
                <span className="market-detail-mkt">{detail.market}</span>
                <span className="market-detail-sym">{detail.symbol}</span>
              </div>
              <div className={`market-detail-price ${pnlClass(detail.change_pct)}`}>
                {formatMoney(detail.last_price)}
              </div>
              <div className={`market-detail-chg ${pnlClass(detail.change_pct)}`}>
                <span className="pnl-arrow">{pnlArrow(detail.change_pct)}</span>
                {formatPct(detail.change_pct)}
              </div>
            </div>

            <div className="portfolio-detail-stats portfolio-detail-stats-4">
              <div>
                <div className="portfolio-meta-label">今日盈亏</div>
                <div className={`portfolio-meta-value ${pnlClass(detail.day_pnl)}`}>
                  {formatSignedMoney(detail.day_pnl)}
                </div>
              </div>
              <div>
                <div className="portfolio-meta-label">累计盈亏</div>
                <div className={`portfolio-meta-value ${pnlClass(detail.pnl)}`}>
                  {formatSignedMoney(detail.pnl)}
                </div>
              </div>
              <div>
                <div className="portfolio-meta-label">持仓 / 成本</div>
                <div className="portfolio-meta-value">
                  {detail.shares} · {formatMoney(detail.cost)}
                </div>
              </div>
              <div>
                <div className="portfolio-meta-label">市值 / 占比</div>
                <div className="portfolio-meta-value">
                  {formatMoney(detail.market_value)} · {(detail.weight ?? 0).toFixed(1)}%
                </div>
              </div>
            </div>
            <p className="portfolio-bought-meta">
              买入日 {detail.bought_at?.trim() || boughtAt || "—"}
            </p>

            <div className="portfolio-trade-tabs" role="tablist" aria-label="仓位操作">
              <button
                type="button"
                role="tab"
                data-active={tradeMode === "add" ? "1" : "0"}
                onClick={() => {
                  haptics.tap();
                  setTradeMode("add");
                  setTradePrice(
                    detail.last_price != null && detail.last_price > 0
                      ? String(detail.last_price)
                      : String(detail.cost),
                  );
                }}
              >
                <Plus size={14} strokeWidth={2.25} absoluteStrokeWidth />
                补仓
              </button>
              <button
                type="button"
                role="tab"
                data-active={tradeMode === "reduce" ? "1" : "0"}
                onClick={() => {
                  haptics.tap();
                  setTradeMode("reduce");
                }}
              >
                <Minus size={14} strokeWidth={2.25} absoluteStrokeWidth />
                减仓
              </button>
              <button
                type="button"
                role="tab"
                data-active={tradeMode === "edit" ? "1" : "0"}
                onClick={() => {
                  haptics.tap();
                  setTradeMode("edit");
                  setShares(String(detail.shares));
                  setCost(String(detail.cost));
                  setBoughtAt(detail.bought_at?.trim() || todayIsoDate());
                }}
              >
                改成本
              </button>
              <button
                type="button"
                className="portfolio-trade-flat"
                onClick={() => {
                  haptics.tap();
                  setDeleteId(detail.id);
                }}
              >
                <CircleOff size={14} strokeWidth={2} absoluteStrokeWidth />
                空仓
              </button>
            </div>

            {tradeMode === "edit" ? (
              <form id="edit-holding-form" className="form-grid" onSubmit={onSaveDetail}>
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
                  placeholder="成本价"
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
              </form>
            ) : (
              <form id="trade-holding-form" className="form-grid" onSubmit={onTrade}>
                <input
                  value={tradeQty}
                  onChange={(e) => setTradeQty(e.target.value)}
                  type="number"
                  min="1"
                  step="1"
                  placeholder={tradeMode === "add" ? "补仓份额" : "减仓份额"}
                  required
                />
                {tradeMode === "add" ? (
                  <input
                    value={tradePrice}
                    onChange={(e) => setTradePrice(e.target.value)}
                    type="number"
                    min="0"
                    step="0.001"
                    placeholder="买入价"
                    required
                  />
                ) : (
                  <button
                    type="button"
                    className="btn btn-ghost portfolio-trade-half"
                    onClick={() => {
                      haptics.tap();
                      setTradeQty(String(Math.floor(detail.shares / 2) || 1));
                    }}
                  >
                    减半
                  </button>
                )}
                {tradePreview && (
                  <p className="portfolio-trade-preview full">
                    操作后：{tradePreview.shares} 份 · 成本 {formatMoney(tradePreview.cost)}
                    {tradeMode === "reduce" && tradePreview.shares === 0
                      ? "（将空仓）"
                      : ""}
                  </p>
                )}
                <p className="portfolio-trade-hint full">
                  {tradeMode === "add"
                    ? "补仓按买入价加权平均成本"
                    : "减仓不改剩余成本价；减完全部即空仓"}
                </p>
              </form>
            )}
          </div>
        )}
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
