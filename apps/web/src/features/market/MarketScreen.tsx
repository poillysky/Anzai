"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Activity,
  CandlestickChart,
  CircleDollarSign,
  Landmark,
  Layers,
  RefreshCw,
  Search,
  TrendingDown,
  TrendingUp,
  Wallet,
  X,
  type LucideIcon,
} from "@/components/ui/icons";
import { CenterModal } from "@/components/overlay/CenterModal";
import { useOverlay } from "@/components/overlay/OverlayContext";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  getIntradayLevelBubbles,
  IndexSparkline,
} from "@/features/market/IndexSparkline";
import { MarketRow } from "@/features/market/MarketRow";
import { api } from "@/lib/api";
import "./market.css";
import { OfflineBanner } from "@/components/layout/OfflineBanner";
import { usePullToRefresh } from "@/hooks/usePullToRefresh";
import {
  formatAmount,
  formatChange,
  formatMoney,
  formatPct,
  pnlArrowTone,
  pnlClass,
  pnlTone,
} from "@/lib/format";
import { haptics } from "@/lib/haptics";
import { goldGramsFromAmount, otcSharesFromAmount } from "@/lib/otcFund";
import { useForegroundEpoch, useTabActive } from "@/hooks/useTabActive";
import {
  cacheForceFetch,
  cachePeek,
  cacheSet,
  cacheSWR,
  PrefetchKeys,
  PrefetchTtl,
  fundNavToSeries,
  goldSectionBiasKeys,
  scheduleWarmMarketScope,
  shortBiasMap,
  warmFundHero,
  warmGoldHero,
} from "@/lib/prefetch";
import type {
  FundBoard,
  FundBoardItem,
  FundNavHistory,
  FundSearchHit,
  GoldBoard,
  GoldBoardItem,
  IndexQuote,
  IntradaySeries,
  LeaderStock,
  LeadersBoard,
  MarketSession,
  SearchHit,
  ShortBias,
  ShortBiasBatch,
} from "@/lib/types";
import { BiasMid } from "@/features/market/BiasMid";
import {
  goldBiasKey,
} from "@/lib/shortBiasChip";

const INDEX_POLL_MS = 15000;
const INTRADAY_POLL_MS = 30000;
const LEADERS_POLL_MS = 20000;
const SESSION_POLL_MS = 60000;
const SEARCH_DEBOUNCE_MS = 280;
const DEFAULT_INDEX = "sh-composite";

type MarketScope = "stock" | "fund" | "gold";
type GoldSectionId = "domestic" | "international" | "shop";
type FundSectionId = "broad" | "sector" | "theme" | "otc";
type FundChartMode = "intraday" | "daily";
type BoardKind = "up" | "down" | "amount" | "turnover" | "etf";

const FUND_SECTION_TABS: { id: FundSectionId; short: string }[] = [
  { id: "broad", short: "宽基" },
  { id: "sector", short: "行业" },
  { id: "theme", short: "跨境" },
  { id: "otc", short: "场外" },
];

const INDEX_META: Record<string, { short: string; tone: string }> = {
  "sh-composite": { short: "上证", tone: "sh" },
  "sz-component": { short: "深成", tone: "sz" },
  chinext: { short: "创业", tone: "cy" },
  "hk-hsi": { short: "港股", tone: "hk" },
  "us-nasdaq": { short: "美股", tone: "us" },
};

const INDEX_ORDER = ["sh-composite", "sz-component", "chinext", "hk-hsi", "us-nasdaq"];

const BOARD_TABS: { kind: BoardKind; label: string; Icon: LucideIcon }[] = [
  { kind: "up", label: "涨幅", Icon: TrendingUp },
  { kind: "down", label: "跌幅", Icon: TrendingDown },
  { kind: "amount", label: "成交", Icon: Wallet },
  { kind: "turnover", label: "换手", Icon: Activity },
  { kind: "etf", label: "ETF", Icon: Layers },
];

const KIND_LABEL: Record<string, string> = {
  stock: "股票",
  etf: "ETF",
  index: "指数",
  hk: "港股",
  us: "美股",
  ipo: "新股",
};

function canAddHolding(market: string, kind?: string | null, note?: string | null): boolean {
  if (kind === "ipo" || (note || "").includes("待上市") || (note || "").includes("新股")) {
    return false;
  }
  return market === "SH" || market === "SZ" || market === "JD" || market === "OF";
}

/** Shanghai calendar date YYYY-MM-DD (A-share session day). */
function shanghaiTodayIso(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

/** 北京时间时钟（与会话状态对齐，不跟本机时区跑偏） */
function formatClock(d: Date): string {
  return d.toLocaleTimeString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** 统一 kicker：状态文案 · 更新时间 */
function formatKickerLine(label: string, updatedAt: Date | null): string {
  const text = (label || "").trim() || "—";
  if (!updatedAt) return text;
  return `${text} · ${formatClock(updatedAt)}`;
}

function hitToLeader(hit: SearchHit): LeaderStock {
  return {
    symbol: hit.symbol,
    name: hit.name,
    market: hit.market,
    price: hit.price ?? 0,
    change_pct: hit.change_pct,
    kind: hit.kind,
    note: hit.note,
  };
}

function goldBoardToLeader(row: GoldBoardItem): LeaderStock | null {
  if (!row.holdable || !row.symbol || !row.market) return null;
  return {
    symbol: row.symbol,
    name: row.name,
    market: row.market,
    price: row.price ?? 0,
    change_pct: row.change_pct,
  };
}

function fundBoardToLeader(row: FundBoardItem): LeaderStock | null {
  if (!row.symbol || !row.market) return null;
  if (row.holdable === false) return null;
  return {
    symbol: row.symbol,
    name: row.name,
    market: row.market,
    price: row.price ?? 0,
    change_pct: row.change_pct,
  };
}

function fundSearchHitToItem(hit: FundSearchHit): FundBoardItem {
  const isOtc = hit.kind === "otc" || hit.market === "OF";
  const market = isOtc ? "OF" : hit.market || "SH";
  const price = hit.price ?? undefined;
  const change_pct = hit.change_pct ?? undefined;
  let prev: number | undefined;
  if (price != null && price > 0 && change_pct != null && change_pct > -100) {
    const denom = 1 + change_pct / 100;
    if (denom > 0) prev = price / denom;
  }
  return {
    id: isOtc ? `OF-${hit.symbol}` : `${market}-${hit.symbol}`,
    name: hit.name,
    section: isOtc ? "otc" : "broad",
    price,
    change_pct,
    prev,
    unit: isOtc ? "净值" : "元",
    freshness: hit.as_of || "",
    note: isOtc
      ? hit.as_of
        ? `日净值 · ${hit.as_of}`
        : "日净值 · 非实时"
      : `${market}${hit.symbol}`,
    holdable: true,
    symbol: hit.symbol,
    market,
    kind: isOtc ? "otc" : "etf",
  };
}

/** Minutes since 00:00 in Asia/Shanghai. */
function shanghaiMinutesNow(d = new Date()): number {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(d);
  const h = Number(parts.find((p) => p.type === "hour")?.value ?? "0");
  const m = Number(parts.find((p) => p.type === "minute")?.value ?? "0");
  if (!Number.isFinite(h) || !Number.isFinite(m)) return 0;
  return Math.min(Math.max(h * 60 + m, 0), 24 * 60 - 1);
}

function minsToHhmm(mins: number): string {
  const clamped = Math.min(Math.max(Math.round(mins), 0), 24 * 60 - 1);
  const h = Math.floor(clamped / 60);
  const m = clamped % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function chartToPoints(
  chart: number[] | undefined,
  session: "cn" | "us" | "hk" | "day24" | "comex" = "cn",
  _chartSlots?: number,
  chartTimes?: string[] | null,
): { time: string; price: number }[] {
  if (!chart?.length) return [];
  const n = chart.length;
  if (chartTimes && chartTimes.length === n) {
    return chart.map((price, i) => ({
      time: chartTimes[i] || String(i),
      price,
    }));
  }
  if (session === "day24") {
    // JD line has no timestamps. Axis is 00:00–24:00, but the series only
    // runs from session open → *now* (Shanghai). Never stretch to 24:00 early.
    const endMins = Math.max(shanghaiMinutesNow(), 1);
    return chart.map((price, i) => ({
      time: minsToHhmm((i / Math.max(n - 1, 1)) * endMins),
      price,
    }));
  }
  if (session === "comex") {
    // 06:00 → …；无时间戳时按已出点数估时刻（右端仍由 spark 轴固定到 05:00）
    const open = 6 * 60;
    return chart.map((price, i) => {
      const mins = open + i;
      return { time: minsToHhmm(mins % (24 * 60)), price };
    });
  }
  if (session === "us") {
    // Map onto US overnight window up to "now" within that window when possible
    const window = 6.5 * 60;
    return chart.map((price, i) => {
      const off = Math.round((i / Math.max(n - 1, 1)) * (window - 1));
      const total = 21 * 60 + 30 + off;
      return { time: minsToHhmm(total % (24 * 60)), price };
    });
  }
  return chart.map((price, i) => ({
    time: String(i),
    price,
  }));
}

/** Sparkline session axis for gold board items. */
function goldSparkSession(item: GoldBoardItem | null): "cn" | "us" | "hk" | "day24" | "comex" {
  if (!item) return "cn";
  const fromApi = (item.chart_session || "").trim();
  if (
    fromApi === "cn" ||
    fromApi === "us" ||
    fromApi === "hk" ||
    fromApi === "day24" ||
    fromApi === "comex"
  ) {
    return fromApi;
  }
  if (item.holdable) return "cn";
  if (item.section === "international") return "comex";
  if (item.section === "shop") return "day24";
  // 浙商 / 民生
  return "day24";
}

function formatGoldPrice(price: number | null | undefined, unit?: string): string {
  if (price == null || !Number.isFinite(price)) return "—";
  const digits = (unit || "").includes("美元") ? 2 : price >= 100 ? 2 : 3;
  return price.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function heroKicker(
  session: MarketSession | null,
  updatedAt: Date | null,
  pollFailed: boolean,
): { live: boolean; text: string } {
  if (pollFailed) return { live: false, text: "更新失败" };
  const state = session?.state;
  if (state === "closed" || state === "weekend" || state === "holiday") {
    return {
      live: false,
      text: formatKickerLine(session?.label ?? "已收盘", updatedAt),
    };
  }
  if (state === "lunch") {
    return {
      live: false,
      text: formatKickerLine(session?.label ?? "午间休市", updatedAt),
    };
  }
  if (state === "auction") {
    return {
      live: true,
      text: formatKickerLine(session?.label ?? "集合竞价", updatedAt),
    };
  }
  if (state === "pre") {
    return {
      live: false,
      text: formatKickerLine(session?.label ?? "未开盘", updatedAt),
    };
  }
  if (state === "trading") {
    return {
      live: true,
      text: formatKickerLine(session?.label ?? "交易中", updatedAt),
    };
  }
  return { live: false, text: formatKickerLine("—", updatedAt) };
}

/** Shanghai weekday short: Mon…Sun (en-US). */
function shanghaiWeekdayShort(): string {
  return new Date().toLocaleDateString("en-US", {
    timeZone: "Asia/Shanghai",
    weekday: "short",
  });
}

/** Rough SGE AU9999 session (BJ): night ~20:00–02:30, day ~09:00–15:30. */
function au9999SessionLabel(): { live: boolean; label: string } {
  const day = shanghaiWeekdayShort();
  if (day === "Sat" || day === "Sun") {
    return { live: false, label: "上金所休市" };
  }
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Shanghai",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date());
  const hh = Number(parts.find((p) => p.type === "hour")?.value ?? "0");
  const mm = Number(parts.find((p) => p.type === "minute")?.value ?? "0");
  const mins = hh * 60 + mm;
  const inNight = mins >= 20 * 60 || mins <= 2 * 60 + 30;
  const inDay = mins >= 9 * 60 && mins <= 15 * 60 + 30;
  if (inNight || inDay) return { live: true, label: "上金所" };
  if (mins > 15 * 60 + 30 && mins < 20 * 60) {
    return { live: false, label: "日盘收盘" };
  }
  return { live: false, label: "上金所休市" };
}

function isGoldEtfItem(item: GoldBoardItem): boolean {
  const f = (item.freshness || "").trim();
  return (Boolean(item.holdable) && item.market !== "JD") || f.includes("场内");
}

/**
 * Gold hero kicker: ETFs follow today's A-share session (周四收盘 → 金ETF收盘);
 * AU9999 uses SGE day/night; JD stays live.
 */
function goldHeroKicker(
  item: GoldBoardItem | null,
  updatedAt: Date | null,
  pollFailed: boolean,
  aShareSession: MarketSession | null,
): { live: boolean; text: string } {
  if (pollFailed) return { live: false, text: "更新失败" };
  if (!item) {
    return { live: false, text: formatKickerLine("金价参考", updatedAt) };
  }

  const live = isGoldItemLive(item, aShareSession);
  const status = goldKickerStatus(item, aShareSession, live);
  return { live, text: formatKickerLine(status, updatedAt) };
}

function goldKickerStatus(
  item: GoldBoardItem,
  aShare: MarketSession | null,
  live: boolean,
): string {
  const f = (item.freshness || "").trim();
  if (item.id === "au9999" || f.includes("上金所")) {
    return live ? "上金所" : au9999SessionLabel().label;
  }
  if (f.includes("实时") || item.market === "JD") return "实时报价";
  if (isGoldEtfItem(item)) {
    if (live) return "场内ETF";
    const state = aShare?.state;
    if (state === "closed" || state === "weekend" || state === "holiday") {
      return state === "holiday" ? "金ETF休市" : "金ETF收盘";
    }
    if (state === "lunch") return "午休";
    if (state === "auction") return aShare?.label ?? "集合竞价";
    if (state === "pre") return "未开盘";
    return aShare?.label ?? "场内ETF";
  }
  if (f.startsWith("门店") || item.section === "shop") return "门店参考";
  if (item.section === "international") return "国际行情";
  if (f.includes("暂无")) return "暂无报价";
  if (f && f.length <= 14) return f;
  return "参考价";
}

function isGoldItemLive(item: GoldBoardItem, aShare: MarketSession | null): boolean {
  if (item.price == null) return false;
  // 上金所 AU9999：按日盘/夜盘，勿被 freshness「上金所实时」里的「实时」误判
  if (item.id === "au9999" || (item.freshness || "").includes("上金所")) {
    return au9999SessionLabel().live;
  }
  // 积存金近全天报价，不跟 A 股时段
  if (item.market === "JD" || (item.freshness || "").includes("实时")) return true;
  if (isGoldEtfItem(item)) {
    return aShare?.state === "trading" || aShare?.state === "auction";
  }
  if (item.section === "international") {
    const day = shanghaiWeekdayShort();
    return day !== "Sat" && day !== "Sun";
  }
  return false;
}

export default function MarketScreen() {
  const router = useRouter();
  const { toast } = useOverlay();
  const tabActive = useTabActive("/market");
  const fgEpoch = useForegroundEpoch();
  const [indices, setIndices] = useState<IndexQuote[]>(
    () => cachePeek<IndexQuote[]>(PrefetchKeys.indices) ?? [],
  );
  const [selectedKey, setSelectedKey] = useState("sh-composite");
  const [boardKind, setBoardKind] = useState<BoardKind>("up");
  const selectedKeyRef = useRef(selectedKey);
  const boardKindRef = useRef(boardKind);
  selectedKeyRef.current = selectedKey;
  boardKindRef.current = boardKind;
  const [intraday, setIntraday] = useState<IntradaySeries | null>(
    () => cachePeek<IntradaySeries>(PrefetchKeys.intraday(DEFAULT_INDEX)),
  );
  const [leaders, setLeaders] = useState<LeadersBoard | null>(
    () => cachePeek<LeadersBoard>(PrefetchKeys.leaders(DEFAULT_INDEX, "up")),
  );
  const [session, setSession] = useState<MarketSession | null>(
    () => cachePeek<MarketSession>(PrefetchKeys.session(DEFAULT_INDEX)),
  );
  const [error, setError] = useState<string | null>(null);
  const [pollFailed, setPollFailed] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const leadersBodyRef = useRef<HTMLDivElement>(null);
  const ptrBarRef = useRef<HTMLDivElement>(null);

  const [searchQuery, setSearchQuery] = useState("");
  const [searchHits, setSearchHits] = useState<SearchHit[] | null>(null);
  const [fundSearchHits, setFundSearchHits] = useState<FundSearchHit[] | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const searchSeq = useRef(0);
  const [fundNavSeries, setFundNavSeries] = useState<IntradaySeries | null>(() => {
    const board = cachePeek<FundBoard>(PrefetchKeys.fundBoard);
    const sec = board?.sections.find((s) => s.id === "broad") ?? board?.sections[0];
    const item = sec?.items?.[0];
    if (!item?.symbol) return null;
    const market = item.market || "OF";
    const hist = cachePeek<FundNavHistory>(PrefetchKeys.fundNav(market, item.symbol));
    return hist ? fundNavToSeries(hist, item) : null;
  });

  const [detail, setDetail] = useState<LeaderStock | null>(null);
  const [shares, setShares] = useState("1000");
  const [cost, setCost] = useState("");
  const [boughtAt, setBoughtAt] = useState(() => shanghaiTodayIso());
  const [saving, setSaving] = useState(false);
  const [marketScope, setMarketScope] = useState<MarketScope>("stock");
  const [goldBoard, setGoldBoard] = useState<GoldBoard | null>(
    () => cachePeek<GoldBoard>(PrefetchKeys.goldBoard),
  );
  const [fundBoard, setFundBoard] = useState<FundBoard | null>(
    () => cachePeek<FundBoard>(PrefetchKeys.fundBoard),
  );
  const [goldSection, setGoldSection] = useState<GoldSectionId>("domestic");
  const [fundSection, setFundSection] = useState<FundSectionId>("broad");
  /** 场内 ETF：分时 / 日K；场外固定日净值 */
  const [fundChartMode, setFundChartMode] = useState<FundChartMode>("daily");
  const [fundIntraday, setFundIntraday] = useState<IntradaySeries | null>(() => {
    const board = cachePeek<FundBoard>(PrefetchKeys.fundBoard);
    const sec = board?.sections.find((s) => s.id === "broad") ?? board?.sections[0];
    const item = sec?.items?.[0];
    if (!item?.symbol || !item.market || item.kind === "otc") return null;
    return cachePeek<IntradaySeries>(PrefetchKeys.symbolIntraday(item.market, item.symbol));
  });
  /** 行业二级板块：煤炭 / 贵金属 / 电子… */
  const [fundIndustryId, setFundIndustryId] = useState<string>("");
  const [selectedGoldId, setSelectedGoldId] = useState<string>("");
  const [selectedFundId, setSelectedFundId] = useState<string>("");
  /** 搜索选中：不切分类，英雄区仍展示该基金 */
  const [fundSearchPick, setFundSearchPick] = useState<FundBoardItem | null>(null);
  const [selectedLeaderKey, setSelectedLeaderKey] = useState<string>("");
  /** 榜单 / 搜索选中的个股（存对象，清空搜索后仍可看图） */
  const [selectedLeader, setSelectedLeader] = useState<LeaderStock | null>(null);
  const [leaderIntraday, setLeaderIntraday] = useState<IntradaySeries | null>(null);
  const [goldIntraday, setGoldIntraday] = useState<IntradaySeries | null>(() => {
    const board = cachePeek<GoldBoard>(PrefetchKeys.goldBoard);
    const sec = board?.sections.find((s) => s.id === "domestic") ?? board?.sections[0];
    const item = sec?.items?.[0];
    if (!item?.holdable || !item.symbol || !item.market) return null;
    return cachePeek<IntradaySeries>(PrefetchKeys.symbolIntraday(item.market, item.symbol));
  });
  const [goldBiasByKey, setGoldBiasByKey] = useState<Record<string, ShortBias>>(() => {
    const board = cachePeek<GoldBoard>(PrefetchKeys.goldBoard);
    const keys = goldSectionBiasKeys(board, "domestic");
    if (!keys.length) return {};
    const batch = cachePeek<ShortBiasBatch>(PrefetchKeys.shortBias(keys));
    return batch ? shortBiasMap(batch) : {};
  });

  const searching = searchQuery.trim().length > 0;
  const selected = indices.find((i) => i.key === selectedKey) ?? null;
  const goldSectionData =
    goldBoard?.sections.find((s) => s.id === goldSection) ?? goldBoard?.sections[0] ?? null;
  const fundSectionData =
    fundBoard?.sections.find((s) => s.id === fundSection) ?? fundBoard?.sections[0] ?? null;
  const fundIndustryGroups = fundSection === "sector" ? fundSectionData?.groups ?? [] : [];
  const fundIndustryGroup =
    fundIndustryGroups.find((g) => g.id === fundIndustryId) ?? fundIndustryGroups[0] ?? null;
  const fundListItems =
    fundSection === "sector" && fundIndustryGroup
      ? fundIndustryGroup.items
      : (fundSectionData?.items ?? []);
  const selectedGoldItem =
    goldSectionData?.items.find((i) => i.id === selectedGoldId) ??
    goldSectionData?.items[0] ??
    null;
  const selectedFundItem =
    fundSearchPick && fundSearchPick.id === selectedFundId
      ? fundSearchPick
      : fundListItems.find((i) => i.id === selectedFundId) ?? fundListItems[0] ?? null;
  /** Keep 5 slots always — filtering empties collapsed the grid and shoved hero down on load */
  const grid = INDEX_ORDER.map((key) => ({
    key,
    quote: indices.find((i) => i.key === key) ?? null,
  }));
  const goldSparkSess = goldSparkSession(selectedGoldItem);
  const goldChartPoints = chartToPoints(
    selectedGoldItem?.chart,
    goldSparkSess,
    selectedGoldItem?.chart_slots,
    selectedGoldItem?.chart_times,
  );
  const isOtcFund = marketScope === "fund" && selectedFundItem?.kind === "otc";
  const fundSparkMode: FundChartMode = isOtcFund ? "daily" : fundChartMode;
  const useEtfChart =
    marketScope === "gold" &&
    Boolean(selectedGoldItem?.holdable && selectedGoldItem.symbol) &&
    goldChartPoints.length < 2;
  const activeLeader = (() => {
    if (marketScope !== "stock" || !selectedLeaderKey) return null;
    const fromBoard = leaders?.items.find(
      (r) => `${r.market}-${r.symbol}` === selectedLeaderKey,
    );
    if (fromBoard) return fromBoard;
    if (
      selectedLeader &&
      `${selectedLeader.market}-${selectedLeader.symbol}` === selectedLeaderKey
    ) {
      return selectedLeader;
    }
    const hit = searchHits?.find((h) => `${h.market}-${h.symbol}` === selectedLeaderKey);
    return hit ? hitToLeader(hit) : null;
  })();
  const leaderPrevClose =
    activeLeader && activeLeader.change_pct != null && activeLeader.price > 0
      ? activeLeader.price / (1 + activeLeader.change_pct / 100)
      : undefined;
  const fundPrevClose = (() => {
    if (!selectedFundItem) return undefined;
    if (selectedFundItem.prev != null && selectedFundItem.prev > 0) {
      return selectedFundItem.prev;
    }
    const px = selectedFundItem.price;
    const chg = selectedFundItem.change_pct;
    if (px != null && px > 0 && chg != null && chg > -100) {
      const denom = 1 + chg / 100;
      if (denom > 0) return px / denom;
    }
    // 日K 已加载时用倒数第二点作昨净值
    const pts = fundNavSeries?.points;
    if (pts && pts.length >= 2) return pts[pts.length - 2]?.price;
    return undefined;
  })();
  const heroQuote =
    marketScope === "fund"
      ? {
          name: selectedFundItem?.name ?? "基金",
          price: selectedFundItem?.price ?? undefined,
          change_pct: selectedFundItem?.change_pct,
          prev_close: fundPrevClose,
          market: selectedFundItem?.market || "SH",
          unit: isOtcFund ? "净值" : (undefined as string | undefined),
        }
      : marketScope === "gold"
        ? {
            name: selectedGoldItem?.name ?? "黄金",
            price: selectedGoldItem?.price ?? undefined,
            change_pct: selectedGoldItem?.change_pct,
            prev_close: selectedGoldItem?.prev ?? undefined,
            market: selectedGoldItem?.market || "SH",
            unit: selectedGoldItem?.unit,
          }
        : activeLeader
          ? {
              name: activeLeader.name,
              price: activeLeader.price,
              change_pct: activeLeader.change_pct,
              prev_close: leaderPrevClose,
              market: activeLeader.market,
              unit: undefined as string | undefined,
            }
          : {
              name: selected?.name ?? INDEX_META[selectedKey]?.short ?? "上证指数",
              price: selected?.price,
              change_pct: selected?.change_pct,
              prev_close: selected?.prev_close,
              market: selected?.market ?? "SH",
              unit: undefined as string | undefined,
            };
  const activeIntraday =
    marketScope === "fund"
      ? fundSparkMode === "daily"
        ? fundNavSeries
        : fundIntraday
      : marketScope === "gold"
        ? useEtfChart
          ? goldIntraday
          : goldChartPoints.length
            ? ({
                key: selectedGoldItem?.id || "gold",
                symbol: selectedGoldItem?.id || "gold",
                name: selectedGoldItem?.name || "黄金",
                market: selectedGoldItem?.market || "SH",
                prev_close: selectedGoldItem?.prev ?? goldChartPoints[0]?.price,
                session: goldSparkSess,
                points: goldChartPoints,
              } satisfies IntradaySeries)
            : null
        : activeLeader
          ? leaderIntraday
          : intraday;
  const toneClass = pnlTone(heroQuote.change_pct, heroQuote.price, heroQuote.prev_close);
  const heroTone =
    toneClass === "text-up" ? "up" : toneClass === "text-down" ? "down" : "flat";
  const kicker =
    marketScope === "gold"
      ? goldHeroKicker(selectedGoldItem, updatedAt, pollFailed, session)
      : marketScope === "fund"
        ? isOtcFund
          ? {
              text: selectedFundItem?.freshness
                ? `日净值 · ${selectedFundItem.freshness}`
                : formatKickerLine("日净值 · 非实时", updatedAt),
              live: false,
            }
          : pollFailed
            ? { text: "更新失败", live: false }
            : {
                text: formatKickerLine("场内 ETF", updatedAt),
                live: Boolean(selectedFundItem?.price),
              }
        : heroKicker(session, updatedAt, pollFailed);
  const levelBubbles = useMemo(
    () =>
      getIntradayLevelBubbles(
        activeIntraday?.points ?? [],
        activeIntraday?.prev_close ?? heroQuote.prev_close,
      ),
    [activeIntraday?.points, activeIntraday?.prev_close, heroQuote.prev_close],
  );

  const applyGoldBoard = useCallback(
    (board: GoldBoard) => {
      cacheSet(PrefetchKeys.goldBoard, board);
      setGoldBoard(board);
      setSelectedGoldId((prev) => {
        const sec = board.sections.find((s) => s.id === goldSection) ?? board.sections[0];
        if (prev && sec?.items.some((i) => i.id === prev)) return prev;
        return sec?.items[0]?.id || "";
      });
      void warmGoldHero(board).catch(() => {});
    },
    [goldSection],
  );

  const loadIndices = useCallback(async (force = false) => {
    if (force) {
      setIndices(await cacheForceFetch(PrefetchKeys.indices, () => api.getIndices()));
      return;
    }
    await cacheSWR(
      PrefetchKeys.indices,
      () => api.getIndices(),
      PrefetchTtl.indices,
      setIndices,
    );
  }, []);

  const loadIntraday = useCallback(async (key: string, force = false) => {
    const apply = (data: IntradaySeries) => {
      cacheSet(PrefetchKeys.intraday(key), data);
      // Drop stale responses after user switched index
      if (selectedKeyRef.current !== key) return;
      setIntraday(data);
    };
    if (force) {
      apply(
        await cacheForceFetch(PrefetchKeys.intraday(key), () => api.getIntraday(key)),
      );
      return;
    }
    await cacheSWR(
      PrefetchKeys.intraday(key),
      () => api.getIntraday(key),
      PrefetchTtl.intraday,
      apply,
    );
  }, []);

  const loadLeaders = useCallback(async (key: string, kind: BoardKind, force = false) => {
    const apply = (data: LeadersBoard) => {
      cacheSet(PrefetchKeys.leaders(key, kind), data);
      if (selectedKeyRef.current !== key || boardKindRef.current !== kind) return;
      setLeaders(data);
    };
    if (force) {
      apply(
        await cacheForceFetch(PrefetchKeys.leaders(key, kind), () =>
          api.getLeaders(key, kind),
        ),
      );
      return;
    }
    await cacheSWR(
      PrefetchKeys.leaders(key, kind),
      () => api.getLeaders(key, kind),
      PrefetchTtl.leaders,
      apply,
    );
  }, []);

  const loadSession = useCallback(async (key: string, force = false) => {
    const apply = (data: MarketSession) => {
      cacheSet(PrefetchKeys.session(key), data);
      if (selectedKeyRef.current !== key) return;
      setSession(data);
    };
    if (force) {
      apply(
        await cacheForceFetch(PrefetchKeys.session(key), () => api.getSession(key)),
      );
      return;
    }
    await cacheSWR(
      PrefetchKeys.session(key),
      () => api.getSession(key),
      PrefetchTtl.session,
      apply,
    );
  }, []);

  const loadGold = useCallback(
    async (force = false) => {
      if (force) {
        applyGoldBoard(
          await cacheForceFetch(PrefetchKeys.goldBoard, () => api.getGoldBoard()),
        );
        return;
      }
      await cacheSWR(
        PrefetchKeys.goldBoard,
        () => api.getGoldBoard(),
        PrefetchTtl.gold,
        applyGoldBoard,
      );
    },
    [applyGoldBoard],
  );

  const applyFundBoard = useCallback(
    (board: FundBoard) => {
      cacheSet(PrefetchKeys.fundBoard, board);
      setFundBoard(board);
      setFundIndustryId((prevIndustry) => {
        const sec = board.sections.find((s) => s.id === fundSection) ?? board.sections[0];
        const groups = sec?.id === "sector" ? sec.groups ?? [] : [];
        const nextIndustry =
          groups.length === 0
            ? ""
            : prevIndustry && groups.some((g) => g.id === prevIndustry)
              ? prevIndustry
              : groups[0]?.id || "";
        setSelectedFundId((prev) => {
          const list =
            sec?.id === "sector"
              ? (groups.find((g) => g.id === nextIndustry) ?? groups[0])?.items ?? []
              : (sec?.items ?? []);
          if (prev && list.some((i) => i.id === prev)) return prev;
          return list[0]?.id || "";
        });
        return nextIndustry;
      });
      void warmFundHero(board).catch(() => {});
    },
    [fundSection],
  );

  const loadFund = useCallback(
    async (force = false) => {
      if (force) {
        applyFundBoard(
          await cacheForceFetch(PrefetchKeys.fundBoard, () => api.getFundBoard()),
        );
        return;
      }
      await cacheSWR(
        PrefetchKeys.fundBoard,
        () => api.getFundBoard(),
        PrefetchTtl.fund,
        applyFundBoard,
      );
    },
    [applyFundBoard],
  );

  const loadGoldIntraday = useCallback(
    async (symbol: string, market: string, force = false) => {
      const key = PrefetchKeys.symbolIntraday(market, symbol);
      const apply = (data: IntradaySeries) => {
        cacheSet(key, data);
        setGoldIntraday(data);
      };
      if (force) {
        apply(await cacheForceFetch(key, () => api.getSymbolIntraday(symbol, market)));
        return;
      }
      await cacheSWR(
        key,
        () => api.getSymbolIntraday(symbol, market),
        PrefetchTtl.symbolIntraday,
        apply,
      );
    },
    [],
  );

  const refreshMarket = useCallback(
    async (key = selectedKey, kind = boardKind, force = false) => {
      try {
        setError(null);
        await Promise.all([
          loadIndices(force),
          loadIntraday(key, force),
          loadLeaders(key, kind, force),
          loadSession(key, force),
          loadGold(force).catch(() => {}),
          loadFund(force).catch(() => {}),
        ]);
        setUpdatedAt(new Date());
        setPollFailed(false);
      } catch {
        setPollFailed(true);
      }
    },
    [
      loadIndices,
      loadIntraday,
      loadLeaders,
      loadSession,
      loadGold,
      loadFund,
      selectedKey,
      boardKind,
    ],
  );

  const pullRefresh = useCallback(async () => {
    await refreshMarket(selectedKey, boardKind, true);
    const extra: Promise<unknown>[] = [];
    if (marketScope === "stock" && activeLeader?.symbol && activeLeader.market) {
      const { symbol, market } = activeLeader;
      extra.push(
        api
          .getSymbolIntraday(symbol, market)
          .then((series) => setLeaderIntraday(series.points?.length ? series : null))
          .catch(() => setLeaderIntraday(null)),
      );
    }
    if (
      marketScope === "gold" &&
      useEtfChart &&
      selectedGoldItem?.symbol &&
      selectedGoldItem.market
    ) {
      extra.push(
        loadGoldIntraday(selectedGoldItem.symbol, selectedGoldItem.market, true).catch(
          () => setGoldIntraday(null),
        ),
      );
    }
    if (marketScope === "fund" && selectedFundItem?.symbol) {
      const code = selectedFundItem.symbol;
      const mkt = selectedFundItem.market || "OF";
      if (!isOtcFund && selectedFundItem.market) {
        const key = PrefetchKeys.symbolIntraday(selectedFundItem.market, code);
        extra.push(
          cacheForceFetch(key, () => api.getSymbolIntraday(code, selectedFundItem.market!))
            .then((series) => setFundIntraday(series.points?.length ? series : null))
            .catch(() => setFundIntraday(null)),
        );
      }
      extra.push(
        cacheForceFetch(PrefetchKeys.fundNav(mkt, code), () =>
          api.getFundNavHistory(code, 30, mkt),
        )
          .then((hist) => setFundNavSeries(fundNavToSeries(hist, selectedFundItem)))
          .catch(() => {}),
      );
    }
    if (extra.length) await Promise.all(extra);
  }, [
    refreshMarket,
    selectedKey,
    boardKind,
    marketScope,
    activeLeader,
    useEtfChart,
    selectedGoldItem,
    selectedFundItem,
    isOtcFund,
    loadGoldIntraday,
  ]);

  const {
    refreshing: ptrRefreshing,
    ready: ptrReady,
  } = usePullToRefresh(leadersBodyRef, ptrBarRef, {
    onRefresh: pullRefresh,
    disabled: detail != null,
    onArmed: () => haptics.selection(),
  });

  useEffect(() => {
    if (!tabActive) return;
    // force: resume / remount must not serve TTL-fresh stale cache after iOS freeze
    void refreshMarket(selectedKey, boardKind, true);
    const iTimer = setInterval(
      () =>
        void loadIndices(true)
          .then(() => {
            setUpdatedAt(new Date());
            setPollFailed(false);
          })
          .catch(() => setPollFailed(true)),
      INDEX_POLL_MS,
    );
    const dTimer = setInterval(
      () => void loadIntraday(selectedKey, true).catch(() => {}),
      INTRADAY_POLL_MS,
    );
    const lTimer = setInterval(
      () =>
        void loadLeaders(selectedKey, boardKind, true)
          .then(() => {
            setUpdatedAt(new Date());
            setPollFailed(false);
          })
          .catch(() => setPollFailed(true)),
      LEADERS_POLL_MS,
    );
    const sTimer = setInterval(
      () => void loadSession(selectedKey, true).catch(() => {}),
      SESSION_POLL_MS,
    );
    const gTimer = setInterval(() => void loadGold(true).catch(() => {}), INTRADAY_POLL_MS);
    const fTimer = setInterval(() => void loadFund(true).catch(() => {}), INTRADAY_POLL_MS);
    return () => {
      clearInterval(iTimer);
      clearInterval(dTimer);
      clearInterval(lTimer);
      clearInterval(sTimer);
      clearInterval(gTimer);
      clearInterval(fTimer);
    };
  }, [
    tabActive,
    fgEpoch,
    refreshMarket,
    loadIndices,
    loadIntraday,
    loadLeaders,
    loadSession,
    loadGold,
    loadFund,
    selectedKey,
    boardKind,
  ]);

  // Gold ETF hero — intraday
  useEffect(() => {
    if (!tabActive) return;
    if (marketScope === "fund") {
      return;
    }
    const item = marketScope === "gold" && useEtfChart ? selectedGoldItem : null;
    if (!item?.symbol || !item.market) {
      if (marketScope === "gold" && useEtfChart) setGoldIntraday(null);
      return;
    }
    const symbol = item.symbol;
    const market = item.market;
    void loadGoldIntraday(symbol, market, true).catch(() => setGoldIntraday(null));
    const timer = setInterval(() => {
      void loadGoldIntraday(symbol, market, true).catch(() => {});
    }, INTRADAY_POLL_MS);
    return () => clearInterval(timer);
  }, [
    tabActive,
    fgEpoch,
    marketScope,
    useEtfChart,
    selectedGoldItem?.symbol,
    selectedGoldItem?.market,
    loadGoldIntraday,
  ]);

  // 场内基金分时（场外无实时）
  useEffect(() => {
    if (!tabActive || marketScope !== "fund" || isOtcFund) {
      if (marketScope !== "fund") setFundIntraday(null);
      return;
    }
    const symbol = selectedFundItem?.symbol;
    const market = selectedFundItem?.market;
    if (!symbol || !market) {
      setFundIntraday(null);
      return;
    }
    const key = PrefetchKeys.symbolIntraday(market, symbol);
    const cached = cachePeek<IntradaySeries>(key);
    if (cached?.points?.length) setFundIntraday(cached);
    else setFundIntraday(null);

    let cancelled = false;
    const load = (force = false) => {
      const apply = (series: IntradaySeries) => {
        if (cancelled) return;
        cacheSet(key, series);
        setFundIntraday(series.points?.length ? series : null);
      };
      if (force) {
        return cacheForceFetch(key, () => api.getSymbolIntraday(symbol, market))
          .then(apply)
          .catch(() => {
            if (!cancelled) setFundIntraday(null);
          });
      }
      return cacheSWR(
        key,
        () => api.getSymbolIntraday(symbol, market),
        PrefetchTtl.symbolIntraday,
        apply,
      ).catch(() => {
        if (!cancelled) setFundIntraday(null);
      });
    };
    void load(true);
    if (fundSparkMode !== "intraday") {
      return () => {
        cancelled = true;
      };
    }
    const timer = setInterval(() => void load(true), INTRADAY_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [
    tabActive,
    fgEpoch,
    marketScope,
    isOtcFund,
    fundSparkMode,
    selectedFundItem?.symbol,
    selectedFundItem?.market,
  ]);

  // 基金日走势：场外日净值；场内日 K（切换日K 时用）
  useEffect(() => {
    if (!tabActive || marketScope !== "fund") {
      if (marketScope !== "fund") setFundNavSeries(null);
      return;
    }
    const code = selectedFundItem?.symbol;
    const market = selectedFundItem?.market || "OF";
    if (!code) {
      setFundNavSeries(null);
      return;
    }
    const cacheKey = PrefetchKeys.fundNav(market, code);
    const cached = cachePeek<FundNavHistory>(cacheKey);
    if (cached) {
      setFundNavSeries(fundNavToSeries(cached, selectedFundItem!));
    } else {
      setFundNavSeries(null);
    }

    let cancelled = false;
    const applyHist = (hist: FundNavHistory) => {
      if (cancelled) return;
      cacheSet(cacheKey, hist);
      setFundNavSeries(
        fundNavToSeries(hist, {
          symbol: code,
          name: selectedFundItem?.name || code,
          market,
        }),
      );
    };
    void cacheForceFetch(cacheKey, () => api.getFundNavHistory(code, 30, market))
      .then(applyHist)
      .catch(() => {
        if (!cancelled && !cached) setFundNavSeries(null);
      });
    return () => {
      cancelled = true;
    };
  }, [
    tabActive,
    fgEpoch,
    marketScope,
    selectedFundItem?.kind,
    selectedFundItem?.symbol,
    selectedFundItem?.market,
    selectedFundItem?.name,
  ]);

  useEffect(() => {
    const q = searchQuery.trim();
    if (!q) {
      searchSeq.current += 1;
      setSearchHits(null);
      setFundSearchHits(null);
      setSearchLoading(false);
      return;
    }
    const seq = ++searchSeq.current;
    setSearchLoading(true);
    const timer = setTimeout(() => {
      if (marketScope === "fund") {
        void api
          .searchFunds(q, 20)
          .then((res) => {
            if (seq !== searchSeq.current) return;
            setFundSearchHits(res.items);
            setSearchHits(null);
          })
          .catch(() => {
            if (seq !== searchSeq.current) return;
            setFundSearchHits([]);
          })
          .finally(() => {
            if (seq !== searchSeq.current) return;
            setSearchLoading(false);
          });
        return;
      }
      void api
        .searchSymbols(q)
        .then((res) => {
          if (seq !== searchSeq.current) return;
          setSearchHits(res.items);
          setFundSearchHits(null);
        })
        .catch(() => {
          if (seq !== searchSeq.current) return;
          setSearchHits([]);
        })
        .finally(() => {
          if (seq !== searchSeq.current) return;
          setSearchLoading(false);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchQuery, marketScope]);

  // 股票榜 / 搜索选中个股 → 顶部英雄区切分时
  useEffect(() => {
    if (!tabActive || marketScope !== "stock" || !activeLeader) {
      if (marketScope !== "stock") setLeaderIntraday(null);
      return;
    }
    const { symbol, market } = activeLeader;
    let cancelled = false;
    const load = () =>
      api
        .getSymbolIntraday(symbol, market)
        .then((series) => {
          if (cancelled) return;
          setLeaderIntraday(series.points?.length ? series : null);
        })
        .catch(() => {
          if (!cancelled) setLeaderIntraday(null);
        });
    void load();
    const timer = setInterval(() => void load(), INTRADAY_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [
    tabActive,
    fgEpoch,
    marketScope,
    activeLeader?.symbol,
    activeLeader?.market,
    selectedLeaderKey,
  ]);

  useEffect(() => {
    if (!detail) {
      return;
    }
    setCost(String(detail.price || ""));
    // 场外/积存金按金额；股票·ETF 按数量（股/份）；默认金额 1000 元 / 数量 100（1 手）
    setShares(
      detail.market === "JD" || detail.market === "OF"
        ? "1000"
        : "100",
    );
    setBoughtAt(shanghaiTodayIso());
  }, [detail]);

  function clearStockSelection() {
    setSelectedLeaderKey("");
    setSelectedLeader(null);
    setLeaderIntraday(null);
  }

  function selectIndex(key: string) {
    if (key === selectedKey) {
      if (selectedLeaderKey) {
        haptics.tap();
        clearStockSelection();
      }
      return;
    }
    haptics.tap();
    setSelectedKey(key);
    clearStockSelection();
    // Keep board tab sticky — only refresh quote/chart/list for the new index.
    setIntraday(cachePeek<IntradaySeries>(PrefetchKeys.intraday(key)));
    setLeaders(cachePeek<LeadersBoard>(PrefetchKeys.leaders(key, boardKind)));
    setSession(cachePeek<MarketSession>(PrefetchKeys.session(key)));
    void Promise.all([
      loadIntraday(key),
      loadLeaders(key, boardKind),
      loadSession(key),
    ]).catch(() => {});
  }

  function selectScope(scope: MarketScope) {
    if (scope === marketScope) return;
    haptics.tap();
    setMarketScope(scope);
    clearStockSelection();
    setFundSearchPick(null);
    setSearchQuery("");
    setSearchHits(null);
    setFundSearchHits(null);
    if (scope === "fund" || scope === "gold") {
      scheduleWarmMarketScope(scope);
    }
  }

  function selectGoldItem(id: string) {
    if (!id || id === selectedGoldId) return;
    haptics.tap();
    setSelectedGoldId(id);
  }

  function selectGoldSection(id: GoldSectionId) {
    if (id === goldSection) return;
    haptics.tap();
    setGoldSection(id);
    const sec = goldBoard?.sections.find((s) => s.id === id);
    setSelectedGoldId(sec?.items[0]?.id || "");
    if (goldBoard) void warmGoldHero(goldBoard, id).catch(() => {});
  }

  function selectFundSection(id: FundSectionId) {
    if (id === fundSection) return;
    haptics.tap();
    setFundSearchPick(null);
    setFundSection(id);
    const sec = fundBoard?.sections.find((s) => s.id === id);
    if (id === "sector" && sec?.groups?.length) {
      const g = sec.groups[0];
      setFundIndustryId(g.id);
      setSelectedFundId(g.items[0]?.id || "");
      return;
    }
    setFundIndustryId("");
    setSelectedFundId(sec?.items[0]?.id || "");
  }

  function selectFundIndustry(id: string) {
    if (!id || id === fundIndustryId) return;
    haptics.tap();
    setFundSearchPick(null);
    setFundIndustryId(id);
    const g = fundSectionData?.groups?.find((x) => x.id === id);
    setSelectedFundId(g?.items[0]?.id || "");
  }

  function selectFundItem(id: string) {
    if (!id || id === selectedFundId) return;
    haptics.tap();
    setFundSearchPick(null);
    setSelectedFundId(id);
  }

  useEffect(() => {
    if (!tabActive || marketScope !== "gold" || !goldBoard) return;
    const keys = goldSectionBiasKeys(goldBoard, goldSection);
    if (keys.length === 0) {
      setGoldBiasByKey({});
      return;
    }
    const cacheKey = PrefetchKeys.shortBias(keys);
    const cached = cachePeek<ShortBiasBatch>(cacheKey);
    if (cached) setGoldBiasByKey(shortBiasMap(cached));

    let cancelled = false;
    void cacheForceFetch(cacheKey, () => api.getShortBias(keys))
      .then((batch) => {
        if (cancelled) return;
        setGoldBiasByKey(shortBiasMap(batch));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [tabActive, fgEpoch, marketScope, goldSection, goldBoard]);

  function selectBoard(kind: BoardKind) {
    if (kind === boardKind) return;
    haptics.tap();
    setBoardKind(kind);
    clearStockSelection();
  }

  function selectLeaderRow(row: LeaderStock) {
    const key = `${row.market}-${row.symbol}`;
    if (key === selectedLeaderKey) {
      openDetail(row);
      return;
    }
    haptics.tap();
    setSelectedLeaderKey(key);
    setSelectedLeader(row);
    setLeaderIntraday(null);
  }

  function clearSearch() {
    haptics.tap();
    setSearchQuery("");
    setSearchHits(null);
    setFundSearchHits(null);
  }

  function openDetail(row: LeaderStock) {
    haptics.tap();
    setDetail(row);
  }

  function openSearchHit(hit: SearchHit) {
    selectLeaderRow(hitToLeader(hit));
  }

  function pickFundSearchHit(hit: FundSearchHit) {
    const item = fundSearchHitToItem(hit);
    if (item.id === selectedFundId && fundSearchPick?.id === item.id) {
      const leader = fundBoardToLeader(item);
      if (leader) openDetail(leader);
      return;
    }
    haptics.tap();
    setSelectedFundId(item.id);
    setFundSearchPick(item);
  }

  async function onAddHolding(e: FormEvent) {
    e.preventDefault();
    if (!detail) return;
    if (!canAddHolding(detail.market, detail.kind, detail.note)) {
      toast(
        detail.kind === "ipo" || (detail.note || "").includes("待上市")
          ? "新股尚未上市，不能加入仓库"
          : "持仓仅支持沪深 A 股 / ETF / 场外基金 / 积存金",
        "warning",
      );
      return;
    }
    const navOrCost = Number(cost);
    let qty = Number(shares);
    let unitCost = navOrCost;
    if (detail.market === "OF") {
      const converted = otcSharesFromAmount(Number(shares), navOrCost);
      if (converted == null) {
        toast("请输入有效金额和确认净值", "warning");
        return;
      }
      qty = converted;
      unitCost = navOrCost;
    } else if (detail.market === "JD") {
      const converted = goldGramsFromAmount(Number(shares), navOrCost);
      if (converted == null) {
        toast("请输入有效金额和买入金价", "warning");
        return;
      }
      qty = converted;
      unitCost = navOrCost;
    } else if (!Number.isFinite(qty) || qty <= 0 || !Number.isFinite(unitCost) || unitCost <= 0) {
      toast("请输入有效数量和成交价", "warning");
      return;
    }
    setSaving(true);
    try {
      await api.createHolding({
        symbol: detail.symbol,
        name: detail.name,
        market: detail.market as "SH" | "SZ" | "JD" | "OF",
        shares: qty,
        cost: unitCost,
        bought_at: boughtAt.trim() || shanghaiTodayIso(),
      });
      toast("已加入仓库", "success");
      setDetail(null);
      router.push("/");
    } catch {
      toast("加入失败", "warning");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="market-page">
      <OfflineBanner />
      <div className="market-page-pin">
        <div className="market-scope-tabs" role="tablist" aria-label="市场范围">
          {(
            [
              { id: "stock" as const, label: "股票", Icon: CandlestickChart },
              { id: "fund" as const, label: "基金", Icon: Landmark },
              { id: "gold" as const, label: "黄金", Icon: CircleDollarSign },
            ] as const
          ).map((tab) => {
            const Icon = tab.Icon;
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={marketScope === tab.id}
                className="market-scope-tab"
                data-active={marketScope === tab.id ? "1" : "0"}
                data-scope={tab.id}
                onPointerDown={() => {
                  if (tab.id === "fund" || tab.id === "gold") {
                    scheduleWarmMarketScope(tab.id);
                  }
                }}
                onClick={() => selectScope(tab.id)}
              >
                <Icon
                  className="market-scope-tab-icon"
                  size={14}
                  strokeWidth={2.25}
                  absoluteStrokeWidth
                  aria-hidden
                />
                <span className="market-scope-tab-label">{tab.label}</span>
              </button>
            );
          })}
        </div>

        <section
          className="market-index-hero"
          data-tone={heroTone}
          data-live={kicker.live ? "1" : "0"}
          aria-label={
            marketScope === "gold"
              ? "黄金走势"
              : marketScope === "fund"
                ? "基金走势"
                : activeLeader
                  ? `${activeLeader.name}走势`
                  : "市场指数"
          }
        >
          <div className="market-index-head">
            <div className="market-index-head-main">
              <div className="market-index-head-row">
                <div className="market-index-head-name">{heroQuote.name}</div>
                <div className="market-index-head-kicker" data-live={kicker.live ? "1" : "0"}>
                  <span className="market-live-dot" aria-hidden />
                  {kicker.text}
                </div>
              </div>
              <div className={`market-index-head-quote ${toneClass}`}>
                <span className="market-index-head-price">
                  {marketScope === "gold"
                    ? formatGoldPrice(heroQuote.price, heroQuote.unit)
                    : formatMoney(heroQuote.price)}
                </span>
                <div className="market-index-head-deltas">
                  <span className="market-index-delta">
                    <span className="pnl-arrow" aria-hidden>
                      {pnlArrowTone(heroQuote.change_pct, heroQuote.price, heroQuote.prev_close)}
                    </span>
                    {formatChange(heroQuote.price, heroQuote.prev_close)}
                    {marketScope === "gold" && heroQuote.unit ? (
                      <span className="market-gold-unit"> {heroQuote.unit}</span>
                    ) : null}
                  </span>
                  <span className="market-index-delta market-index-delta-pct">
                    {formatPct(heroQuote.change_pct)}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="market-spark-wrap">
            {marketScope === "fund" ? (
              <div className="market-fund-chart-seg" role="tablist" aria-label="走势周期">
                {(
                  [
                    { id: "intraday" as const, label: "分时" },
                    { id: "daily" as const, label: "日K" },
                  ] as const
                ).map((tab) => {
                  const lockedDaily = isOtcFund;
                  const active = (lockedDaily ? "daily" : fundChartMode) === tab.id;
                  const disabled = lockedDaily && tab.id === "intraday";
                  return (
                    <button
                      key={tab.id}
                      type="button"
                      role="tab"
                      aria-selected={active}
                      aria-disabled={disabled ? "true" : undefined}
                      disabled={disabled}
                      title={disabled ? "场外基金仅日净值" : undefined}
                      className="market-fund-chart-seg-tab"
                      data-active={active ? "1" : "0"}
                      onClick={() => {
                        if (disabled || fundChartMode === tab.id) return;
                        haptics.tap();
                        setFundChartMode(tab.id);
                      }}
                    >
                      {tab.label}
                    </button>
                  );
                })}
              </div>
            ) : null}
            <IndexSparkline
              points={activeIntraday?.points ?? []}
              prevClose={activeIntraday?.prev_close ?? heroQuote.prev_close}
              changePct={heroQuote.change_pct}
              fillWidth={marketScope === "fund"}
              session={
                marketScope === "fund"
                  ? fundSparkMode === "daily"
                    ? "daily"
                    : (activeIntraday?.session ?? "cn")
                  : marketScope === "gold"
                    ? useEtfChart
                      ? (activeIntraday?.session ?? "cn")
                      : goldSparkSess
                    : activeIntraday?.session ??
                      (heroQuote.market === "US" ? "us" : heroQuote.market === "HK" ? "hk" : "cn")
              }
              label={
                marketScope === "fund"
                  ? fundSparkMode === "daily"
                    ? isOtcFund
                      ? `${heroQuote.name}日净值走势`
                      : `${heroQuote.name}近30日走势`
                    : `${heroQuote.name}分时走势`
                  : `${heroQuote.name}分时走势`
              }
              interactive
            />
          </div>

          <div
            className="market-level-bubbles"
            aria-label="关键价位"
            aria-hidden={levelBubbles.length === 0}
            data-empty={levelBubbles.length === 0 ? "1" : "0"}
          >
            {levelBubbles.map((b) => (
              <span key={b.key} className="market-level-bubble">
                <span className="market-level-bubble-label">{b.label}</span>
                <span className="market-level-bubble-val">{formatMoney(b.value)}</span>
              </span>
            ))}
          </div>

          {marketScope === "stock" ? (
            <div className="market-index-grid" role="tablist" aria-label="切换指数">
              {grid.map(({ key, quote }) => {
                const meta = INDEX_META[key] ?? {
                  short: quote?.name ?? key,
                  tone: "sh",
                };
                const active = key === selectedKey;
                const pctTone = pnlTone(quote?.change_pct, quote?.price, quote?.prev_close);
                return (
                  <button
                    key={key}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    className="market-index-tile"
                    data-active={active ? "1" : "0"}
                    data-tone={meta.tone}
                    disabled={!quote}
                    onClick={() => selectIndex(key)}
                  >
                    <span className="market-index-tile-name">{meta.short}</span>
                    <span className={`market-index-tile-pct ${pctTone}`}>
                      {quote ? formatPct(quote.change_pct) : "—"}
                    </span>
                  </button>
                );
              })}
            </div>
          ) : marketScope === "fund" ? (
            <div
              className="market-index-grid market-gold-grid market-fund-grid"
              role="tablist"
              aria-label="基金分类"
            >
              {FUND_SECTION_TABS.map((tab) => {
                const active = fundSection === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    className="market-index-tile market-index-tile--label"
                    data-active={active ? "1" : "0"}
                    data-tone="fund"
                    onClick={() => selectFundSection(tab.id)}
                  >
                    <span className="market-index-tile-name">{tab.short}</span>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="market-index-grid market-gold-grid" role="tablist" aria-label="金价分类">
              {(
                [
                  { id: "domestic" as const, short: "国内" },
                  { id: "international" as const, short: "国际" },
                  { id: "shop" as const, short: "门店" },
                ] as const
              ).map((tab) => {
                const sec = goldBoard?.sections.find((s) => s.id === tab.id);
                const top = sec?.items[0];
                const active = goldSection === tab.id;
                const pctTone = pnlTone(
                  top?.change_pct,
                  top?.price ?? undefined,
                  top?.prev ?? undefined,
                );
                return (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    className="market-index-tile"
                    data-active={active ? "1" : "0"}
                    data-tone="gold"
                    onClick={() => selectGoldSection(tab.id)}
                  >
                    <span className="market-index-tile-name">{tab.short}</span>
                    <span className={`market-index-tile-pct ${pctTone}`}>
                      {top?.price != null ? formatPct(top.change_pct) : "—"}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        {(marketScope === "stock" || marketScope === "fund") && (
          <label className="market-search" data-active={searching ? "1" : "0"}>
            <Search size={15} strokeWidth={2.25} absoluteStrokeWidth aria-hidden />
            <input
              className="market-search-input"
              type="search"
              inputMode="search"
              enterKeyHint="search"
              autoComplete="off"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              placeholder={
                marketScope === "fund"
                  ? "基金代码或名称，如 510300 / 白酒"
                  : "代码或名称，如 510300 / 茅台"
              }
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              aria-label={marketScope === "fund" ? "搜索基金" : "搜索股票或 ETF"}
            />
            {searching && (
              <button
                type="button"
                className="market-search-clear"
                aria-label="清除搜索"
                onClick={(e) => {
                  e.preventDefault();
                  clearSearch();
                }}
              >
                <X size={11} strokeWidth={2.5} absoluteStrokeWidth />
              </button>
            )}
          </label>
        )}

        {marketScope === "stock" && !searching && (
          <div className="market-board-tabs market-board-tabs-5" role="tablist" aria-label="榜单类型">
            {BOARD_TABS.map((tab) => {
              const Icon = tab.Icon;
              return (
                <button
                  key={tab.kind}
                  type="button"
                  role="tab"
                  aria-selected={boardKind === tab.kind}
                  className="market-board-tab"
                  data-active={boardKind === tab.kind ? "1" : "0"}
                  data-kind={tab.kind}
                  onClick={() => selectBoard(tab.kind)}
                >
                  <Icon size={12} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                  {tab.label}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <section
        className="inset-group market-leaders"
        aria-label={
          searching
            ? "搜索结果"
            : marketScope === "gold"
              ? goldSectionData?.title || "黄金"
              : marketScope === "fund"
                ? fundIndustryGroup?.title || fundSectionData?.title || "基金"
                : (leaders?.title ?? "榜单")
        }
      >
        <div className="inset-group-header market-leaders-head">
          <span>
            {searching
              ? "搜索结果"
              : marketScope === "gold"
                ? goldSectionData?.title || "黄金"
                : marketScope === "fund"
                  ? fundIndustryGroup?.title || fundSectionData?.title || "基金"
                  : (leaders?.title ?? BOARD_TABS.find((t) => t.kind === boardKind)?.label)}
          </span>
          <span>
            {searching
              ? `${
                  marketScope === "fund"
                    ? (fundSearchHits?.length ?? 0)
                    : (searchHits?.length ?? 0)
                } 条`
              : marketScope === "gold"
                ? `${goldSectionData?.items.length ?? 0} 只`
                : marketScope === "fund"
                  ? `${fundListItems.length} 只`
                  : `${leaders?.items.length ?? 0} 只`}
          </span>
        </div>
        {marketScope === "fund" &&
          fundSection === "sector" &&
          !searching &&
          fundIndustryGroups.length > 0 && (
            <div className="market-fund-boards-wrap">
              <div className="news-boards" role="tablist" aria-label="行业板块">
                {fundIndustryGroups.map((g) => (
                  <button
                    key={g.id}
                    type="button"
                    role="tab"
                    aria-selected={
                      (fundIndustryGroup?.id || fundIndustryGroups[0]?.id) === g.id
                    }
                    className="news-board-chip"
                    data-active={
                      (fundIndustryGroup?.id || fundIndustryGroups[0]?.id) === g.id
                        ? "1"
                        : "0"
                    }
                    onClick={() => selectFundIndustry(g.id)}
                  >
                    {g.title}
                  </button>
                ))}
              </div>
            </div>
          )}
        <div
          className="market-leaders-body"
          ref={leadersBodyRef}
          data-ptr={ptrRefreshing ? "1" : "0"}
        >
          <div
            ref={ptrBarRef}
            className="news-ptr"
            data-ready={ptrReady ? "1" : "0"}
            data-refreshing={ptrRefreshing ? "1" : "0"}
            aria-hidden
          >
            <div className="news-ptr-inner">
              <RefreshCw
                className="news-ptr-icon"
                size={14}
                strokeWidth={2.2}
                absoluteStrokeWidth
              />
              <span className="news-ptr-label">
                {ptrRefreshing ? "刷新中" : ptrReady ? "松开刷新" : "下拉刷新"}
              </span>
            </div>
          </div>
          {error && !searching && marketScope === "stock" && (
            <p className="text-up" style={{ fontSize: 13, padding: "12px 16px" }}>
              {error}
            </p>
          )}
          {searching ? (
            marketScope === "fund" ? (
              searchLoading && fundSearchHits == null ? (
                <div className="market-leaders-skel" aria-hidden>
                  {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="market-skel-row" />
                  ))}
                </div>
              ) : !fundSearchHits || fundSearchHits.length === 0 ? (
                <div style={{ padding: "8px 4px 16px" }}>
                  <EmptyState title="未找到基金" hint="试试代码或简称，如 510300 / 白酒 / 沪深300" />
                </div>
              ) : (
                fundSearchHits.map((hit) => (
                  <MarketRow
                    key={`${hit.market}-${hit.symbol}`}
                    active={
                      !!(
                        fundSearchPick &&
                        fundSearchPick.symbol === hit.symbol &&
                        fundSearchPick.market === (hit.kind === "otc" ? "OF" : hit.market)
                      )
                    }
                    onClick={() => pickFundSearchHit(hit)}
                    leading={
                      <span className="market-search-kind">
                        {hit.kind === "etf" ? "场内" : "场外"}
                      </span>
                    }
                    name={hit.name}
                    meta={
                      (hit.kind === "etf"
                        ? `${hit.market}${hit.symbol}`
                        : hit.as_of
                          ? `${hit.symbol} · ${hit.as_of.slice(5)}`
                          : hit.symbol) + (hit.fund_type ? ` · ${hit.fund_type}` : "")
                    }
                    price={formatMoney(hit.price)}
                    badge={
                      <span className={`market-row-badge ${pnlClass(hit.change_pct)}`}>
                        {formatPct(hit.change_pct)}
                      </span>
                    }
                  />
                ))
              )
            ) : searchLoading && searchHits == null ? (
              <div className="market-leaders-skel" aria-hidden>
                {Array.from({ length: 6 }).map((_, i) => (
                  <div key={i} className="market-skel-row" />
                ))}
              </div>
            ) : !searchHits || searchHits.length === 0 ? (
              <div style={{ padding: "8px 4px 16px" }}>
                <EmptyState title="未找到" hint="试试代码或简称，如 510300 / 茅台" />
              </div>
            ) : (
              searchHits.map((hit) => {
                const viewOnly = !canAddHolding(hit.market, hit.kind, hit.note);
                const rowKey = `${hit.market}-${hit.symbol}`;
                return (
                  <MarketRow
                    key={rowKey}
                    active={rowKey === selectedLeaderKey}
                    onClick={() => openSearchHit(hit)}
                    leading={
                      <span className="market-search-kind">
                        {KIND_LABEL[hit.kind] ?? "标的"}
                      </span>
                    }
                    name={hit.name}
                    meta={`${hit.market}${hit.symbol}${hit.note ? ` · ${hit.note}` : ""}${viewOnly ? " · 仅查阅" : ""}`}
                    price={hit.note && (hit.price == null || hit.change_pct == null) ? (hit.price != null ? formatMoney(hit.price) : "—") : formatMoney(hit.price)}
                    badge={
                      <span className={`market-row-badge ${hit.note ? "text-mute" : pnlClass(hit.change_pct)}`}>
                        {hit.note ? "待上市" : formatPct(hit.change_pct)}
                      </span>
                    }
                  />
                );
              })
            )
          ) : marketScope === "fund" ? (
            !fundListItems.length ? (
              fundBoard ? (
                <div style={{ padding: "8px 4px 16px" }}>
                  <EmptyState title="暂无标的" hint="换个板块看看" />
                </div>
              ) : (
                <div className="market-leaders-skel" aria-label="加载中">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className="market-skel-row" />
                  ))}
                </div>
              )
            ) : (
              fundListItems.map((row, i) => {
                const leader = goldBoardToLeader(row);
                return (
                  <MarketRow
                    key={row.id}
                    active={row.id === selectedFundId || row.id === selectedFundItem?.id}
                    onClick={() => {
                      if (row.id === selectedFundId) {
                        if (leader) openDetail(leader);
                        return;
                      }
                      selectFundItem(row.id);
                    }}
                    leading={
                      <span
                        className="market-leader-rank"
                        data-top={i < 3 ? String(i + 1) : "0"}
                        aria-label={`第 ${i + 1} 名`}
                      >
                        {i + 1}
                      </span>
                    }
                    name={row.name}
                    meta={
                      row.kind === "otc"
                        ? row.freshness
                          ? `${row.symbol} · ${String(row.freshness).slice(5)}`
                          : row.symbol
                        : row.note || `${row.market || ""}${row.symbol || ""}`
                    }
                    price={formatMoney(row.price)}
                    badge={
                      <span
                        className={`market-row-badge ${
                          row.change_pct != null ? pnlClass(row.change_pct) : "text-mute"
                        }`}
                      >
                        {row.change_pct != null ? formatPct(row.change_pct) : "—"}
                      </span>
                    }
                  />
                );
              })
            )
          ) : marketScope === "gold" ? (
            !(goldSectionData?.items.length) ? (
              <div className="market-leaders-skel" aria-label="加载中">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="market-skel-row" />
                ))}
              </div>
            ) : (
              goldSectionData.items.map((row, i) => {
                const leader = goldBoardToLeader(row);
                const m = (row.market || "").toUpperCase();
                const key = goldBiasKey(row);
                const bias = key ? goldBiasByKey[key] : null;
                const showBias =
                  m !== "SH" &&
                  m !== "SZ" &&
                  bias &&
                  !(bias.bias === "na" && !bias.label.includes("陈旧"));
                return (
                  <MarketRow
                    key={row.id}
                    active={row.id === selectedGoldId || row.id === selectedGoldItem?.id}
                    onClick={() => {
                      if (row.id === selectedGoldId) {
                        if (leader) openDetail(leader);
                        return;
                      }
                      selectGoldItem(row.id);
                    }}
                    leading={
                      <span
                        className="market-leader-rank"
                        data-top={i < 3 ? String(i + 1) : "0"}
                        aria-label={`第 ${i + 1} 名`}
                      >
                        {i + 1}
                      </span>
                    }
                    name={row.name}
                    meta={
                      row.note && goldSection === "shop"
                        ? `${row.note}${row.freshness ? ` · ${row.freshness}` : ""}`
                        : row.freshness || row.unit || ""
                    }
                    mid={
                      showBias && bias ? (
                        <BiasMid
                          className="market-row-bias"
                          bias={bias}
                          market={bias.market}
                          symbol={bias.symbol}
                        />
                      ) : null
                    }
                    price={formatGoldPrice(row.price, row.unit)}
                    badge={
                      <span
                        className={`market-row-badge ${
                          row.change_pct != null ? pnlClass(row.change_pct) : "text-mute"
                        }`}
                      >
                        {row.change_pct != null
                          ? formatPct(row.change_pct)
                          : goldSection === "shop"
                            ? "零售"
                            : "—"}
                      </span>
                    }
                  />
                );
              })
            )
          ) : !leaders ? (
            <div className="market-leaders-skel" aria-label="加载中">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="market-skel-row" />
              ))}
            </div>
          ) : leaders.items.length === 0 ? (
            <div style={{ padding: "8px 4px 16px" }}>
              <EmptyState title="暂无数据" hint="稍后刷新或切换榜单" />
            </div>
          ) : (
            leaders.items.map((row, i) => {
              const rowKey = `${row.market}-${row.symbol}`;
              const pctBadge = (
                <span className={`market-row-badge ${pnlClass(row.change_pct)}`}>
                  {formatPct(row.change_pct)}
                </span>
              );
              return (
                <MarketRow
                  key={rowKey}
                  active={rowKey === selectedLeaderKey}
                  onClick={() => selectLeaderRow(row)}
                  leading={
                    <span
                      className="market-leader-rank"
                      data-top={i < 3 ? String(i + 1) : "0"}
                      aria-label={`第 ${i + 1} 名`}
                    >
                      {i + 1}
                    </span>
                  }
                  name={row.name}
                  meta={`${row.market}${row.symbol}`}
                  price={formatMoney(row.price)}
                  badge={
                    boardKind === "amount" ? (
                      <div className="market-leader-metrics">
                        <span className="market-leader-metric-mute">
                          {formatAmount(row.amount)}
                        </span>
                        {pctBadge}
                      </div>
                    ) : boardKind === "turnover" ? (
                      <div className="market-leader-metrics">
                        <span className="market-leader-metric-mute">
                          {row.turnover != null ? `${row.turnover.toFixed(2)}%` : "--"}
                        </span>
                        {pctBadge}
                      </div>
                    ) : (
                      pctBadge
                    )
                  }
                />
              );
            })
          )}
        </div>
      </section>

      <CenterModal
        open={detail != null}
        title={detail?.name ?? "详情"}
        onClose={() => setDetail(null)}
        footer={
          detail && canAddHolding(detail.market, detail.kind, detail.note) ? (
            <button
              className="btn btn-block btn-modal-primary"
              type="submit"
              form="market-add-holding"
              disabled={saving}
            >
              {saving ? "加入中…" : "加入仓库持仓"}
            </button>
          ) : (
            <button className="btn btn-block btn-modal-muted" type="button" disabled>
              {detail?.kind === "ipo" || (detail?.note || "").includes("待上市")
                ? "新股待上市 · 不可入仓"
                : "仅查阅 · 不可入仓"}
            </button>
          )
        }
      >
        {detail && (
          <div className="market-detail">
            <div className="market-detail-quote">
              <div className="market-detail-code">
                <span className="market-detail-mkt">{detail.market}</span>
                <span className="market-detail-sym">{detail.symbol}</span>
              </div>
              <div className={`market-detail-price ${pnlTone(detail.change_pct, detail.price, detail.prev_close)}`}>
                {formatMoney(detail.price)}
              </div>
              <div className={`market-detail-chg ${pnlTone(detail.change_pct, detail.price, detail.prev_close)}`}>
                <span className="pnl-arrow">
                  {pnlArrowTone(detail.change_pct, detail.price, detail.prev_close)}
                </span>
                {formatPct(detail.change_pct)}
              </div>
            </div>

            {canAddHolding(detail.market, detail.kind, detail.note) && (
              <form id="market-add-holding" className="market-detail-form" onSubmit={onAddHolding}>
                <p className="market-detail-form-title">加入仓库</p>
                <label className="market-detail-field">
                  <span className="market-detail-label">
                    {detail.market === "OF" || detail.market === "JD"
                      ? "投入金额（元）"
                      : "数量（股/份）"}
                  </span>
                  <input
                    value={shares}
                    onChange={(e) => setShares(e.target.value)}
                    inputMode="decimal"
                    placeholder={
                      detail.market === "OF" || detail.market === "JD"
                        ? "例如 1000"
                        : "例如 100"
                    }
                    required
                  />
                </label>
                <label className="market-detail-field">
                  <span className="market-detail-label">
                    {detail.market === "JD"
                      ? "买入金价（元/克）"
                      : detail.market === "OF"
                        ? "确认净值"
                        : "成交价"}
                  </span>
                  <input
                    value={cost}
                    onChange={(e) => setCost(e.target.value)}
                    inputMode="decimal"
                    placeholder={
                      detail.market === "OF"
                        ? "确认日净值"
                        : detail.market === "JD"
                          ? "买入金价"
                          : "成交价"
                    }
                    required
                  />
                </label>
                {(detail.market === "OF" || detail.market === "JD") &&
                  (() => {
                    const preview =
                      detail.market === "OF"
                        ? otcSharesFromAmount(Number(shares), Number(cost))
                        : goldGramsFromAmount(Number(shares), Number(cost));
                    return preview != null ? (
                      <p className="market-detail-otc-preview">
                        约 {preview}
                        {detail.market === "OF" ? " 份" : " 克"}
                      </p>
                    ) : null;
                  })()}
                <label className="market-detail-field">
                  <span className="market-detail-label">
                    {detail.market === "OF" ? "确认日" : "买入日"}
                  </span>
                  <input
                    type="date"
                    value={boughtAt}
                    onChange={(e) => setBoughtAt(e.target.value || shanghaiTodayIso())}
                    required
                  />
                </label>
              </form>
            )}
          </div>
        )}
      </CenterModal>
    </div>
  );
}
