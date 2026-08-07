"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Activity,
  CandlestickChart,
  CircleDollarSign,
  Layers,
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
import { api } from "@/lib/api";
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
import { cachePeek, cacheSet, PrefetchKeys } from "@/lib/prefetch";
import type {
  GoldBoard,
  GoldBoardItem,
  IndexQuote,
  IntradaySeries,
  LeaderStock,
  LeadersBoard,
  MarketSession,
  SearchHit,
  ShortBias,
} from "@/lib/types";
import {
  biasChipClass,
  biasChipText,
  biasChipTitle,
  goldBiasKey,
} from "@/lib/shortBiasChip";

const INDEX_POLL_MS = 15000;
const INTRADAY_POLL_MS = 30000;
const LEADERS_POLL_MS = 20000;
const SESSION_POLL_MS = 60000;
const SEARCH_DEBOUNCE_MS = 280;
const DEFAULT_INDEX = "sh-composite";

type MarketScope = "stock" | "gold";
type GoldSectionId = "domestic" | "international" | "shop";
type BoardKind = "up" | "down" | "amount" | "turnover" | "etf";
type DetailChartStatus = "idle" | "loading" | "ready" | "empty";

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
};

function canAddHolding(market: string): boolean {
  return market === "SH" || market === "SZ" || market === "JD";
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

function formatClock(d: Date): string {
  return d.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatClockShort(d: Date): string {
  return d.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function hitToLeader(hit: SearchHit): LeaderStock {
  return {
    symbol: hit.symbol,
    name: hit.name,
    market: hit.market,
    price: hit.price ?? 0,
    change_pct: hit.change_pct,
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
  if (state === "closed" || state === "weekend") {
    const label = session?.label ?? "已收盘";
    return {
      live: false,
      text: updatedAt ? `${label} · ${formatClockShort(updatedAt)}` : label,
    };
  }
  if (state === "lunch") {
    return {
      live: false,
      text: updatedAt ? `午休 · ${formatClockShort(updatedAt)}` : "午间休市",
    };
  }
  if (state === "pre") {
    return {
      live: false,
      text: updatedAt ? `未开盘 · ${formatClockShort(updatedAt)}` : (session?.label ?? "未开盘"),
    };
  }
  if (state === "trading") {
    return { live: true, text: updatedAt ? formatClock(updatedAt) : "交易中" };
  }
  return { live: false, text: updatedAt ? formatClock(updatedAt) : "—" };
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
    return {
      live: false,
      text: updatedAt ? `参考 · ${formatClockShort(updatedAt)}` : "金价参考",
    };
  }

  const live = isGoldItemLive(item, aShareSession);
  const status = goldKickerStatus(item, aShareSession, live);
  if (live) {
    return { live: true, text: updatedAt ? formatClock(updatedAt) : status };
  }
  return {
    live: false,
    text: updatedAt ? `${status} · ${formatClockShort(updatedAt)}` : status,
  };
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
    if (state === "closed" || state === "weekend") return "金ETF收盘";
    if (state === "lunch") return "午休";
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
    return aShare?.state === "trading";
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
  const [indices, setIndices] = useState<IndexQuote[]>(
    () => cachePeek<IndexQuote[]>(PrefetchKeys.indices) ?? [],
  );
  const [selectedKey, setSelectedKey] = useState("sh-composite");
  const [boardKind, setBoardKind] = useState<BoardKind>("up");
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

  const [searchQuery, setSearchQuery] = useState("");
  const [searchHits, setSearchHits] = useState<SearchHit[] | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const searchSeq = useRef(0);

  const [detail, setDetail] = useState<LeaderStock | null>(null);
  const [detailIntra, setDetailIntra] = useState<IntradaySeries | null>(null);
  const [detailChart, setDetailChart] = useState<DetailChartStatus>("idle");
  const [shares, setShares] = useState("1000");
  const [cost, setCost] = useState("");
  const [boughtAt, setBoughtAt] = useState(() => shanghaiTodayIso());
  const [saving, setSaving] = useState(false);
  const [marketScope, setMarketScope] = useState<MarketScope>("stock");
  const [goldBoard, setGoldBoard] = useState<GoldBoard | null>(null);
  const [goldSection, setGoldSection] = useState<GoldSectionId>("domestic");
  const [selectedGoldId, setSelectedGoldId] = useState<string>("");
  const [selectedLeaderKey, setSelectedLeaderKey] = useState<string>("");
  const [goldIntraday, setGoldIntraday] = useState<IntradaySeries | null>(null);
  const [goldBiasByKey, setGoldBiasByKey] = useState<Record<string, ShortBias>>({});

  const searching = searchQuery.trim().length > 0;
  const selected = indices.find((i) => i.key === selectedKey) ?? null;
  const goldSectionData =
    goldBoard?.sections.find((s) => s.id === goldSection) ?? goldBoard?.sections[0] ?? null;
  const selectedGoldItem =
    goldSectionData?.items.find((i) => i.id === selectedGoldId) ??
    goldSectionData?.items[0] ??
    null;
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
  const useEtfChart =
    marketScope === "gold" &&
    Boolean(selectedGoldItem?.holdable && selectedGoldItem.symbol) &&
    goldChartPoints.length < 2;
  const heroQuote =
    marketScope === "gold"
      ? {
          name: selectedGoldItem?.name ?? "黄金",
          price: selectedGoldItem?.price ?? undefined,
          change_pct: selectedGoldItem?.change_pct,
          prev_close: selectedGoldItem?.prev ?? undefined,
          market: selectedGoldItem?.market || "SH",
          unit: selectedGoldItem?.unit,
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
    marketScope === "gold"
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
      : intraday;
  const toneClass = pnlTone(heroQuote.change_pct, heroQuote.price, heroQuote.prev_close);
  const heroTone =
    toneClass === "text-up" ? "up" : toneClass === "text-down" ? "down" : "flat";
  const kicker =
    marketScope === "gold"
      ? goldHeroKicker(selectedGoldItem, updatedAt, pollFailed, session)
      : heroKicker(session, updatedAt, pollFailed);
  const levelBubbles = useMemo(
    () =>
      getIntradayLevelBubbles(
        activeIntraday?.points ?? [],
        activeIntraday?.prev_close ?? heroQuote.prev_close,
      ),
    [activeIntraday?.points, activeIntraday?.prev_close, heroQuote.prev_close],
  );

  const loadIndices = useCallback(async () => {
    const data = await api.getIndices();
    cacheSet(PrefetchKeys.indices, data);
    setIndices(data);
  }, []);

  const loadIntraday = useCallback(async (key: string) => {
    const data = await api.getIntraday(key);
    cacheSet(PrefetchKeys.intraday(key), data);
    setIntraday(data);
  }, []);

  const loadLeaders = useCallback(async (key: string, kind: BoardKind) => {
    const data = await api.getLeaders(key, kind);
    cacheSet(PrefetchKeys.leaders(key, kind), data);
    setLeaders(data);
  }, []);

  const loadSession = useCallback(async (key: string) => {
    const data = await api.getSession(key);
    cacheSet(PrefetchKeys.session(key), data);
    setSession(data);
  }, []);

  const loadGold = useCallback(async () => {
    const board = await api.getGoldBoard();
    setGoldBoard(board);
    setSelectedGoldId((prev) => {
      const sec = board.sections.find((s) => s.id === goldSection) ?? board.sections[0];
      if (prev && sec?.items.some((i) => i.id === prev)) return prev;
      return sec?.items[0]?.id || "";
    });
  }, [goldSection]);

  const loadGoldIntraday = useCallback(async (symbol: string, market: string) => {
    const data = await api.getSymbolIntraday(symbol, market);
    setGoldIntraday(data);
  }, []);

  const refreshMarket = useCallback(
    async (key = selectedKey, kind = boardKind) => {
      try {
        setError(null);
        await Promise.all([
          loadIndices(),
          loadIntraday(key),
          loadLeaders(key, kind),
          loadSession(key),
          loadGold().catch(() => {}),
        ]);
        setUpdatedAt(new Date());
        setPollFailed(false);
      } catch {
        setPollFailed(true);
      }
    },
    [loadIndices, loadIntraday, loadLeaders, loadSession, loadGold, selectedKey, boardKind],
  );

  useEffect(() => {
    void refreshMarket(selectedKey, boardKind);
    const iTimer = setInterval(
      () =>
        void loadIndices()
          .then(() => {
            setUpdatedAt(new Date());
            setPollFailed(false);
          })
          .catch(() => setPollFailed(true)),
      INDEX_POLL_MS,
    );
    const dTimer = setInterval(
      () => void loadIntraday(selectedKey).catch(() => {}),
      INTRADAY_POLL_MS,
    );
    const lTimer = setInterval(
      () =>
        void loadLeaders(selectedKey, boardKind)
          .then(() => {
            setUpdatedAt(new Date());
            setPollFailed(false);
          })
          .catch(() => setPollFailed(true)),
      LEADERS_POLL_MS,
    );
    const sTimer = setInterval(
      () => void loadSession(selectedKey).catch(() => {}),
      SESSION_POLL_MS,
    );
    const gTimer = setInterval(() => void loadGold().catch(() => {}), INTRADAY_POLL_MS);
    return () => {
      clearInterval(iTimer);
      clearInterval(dTimer);
      clearInterval(lTimer);
      clearInterval(sTimer);
      clearInterval(gTimer);
    };
  }, [refreshMarket, loadIndices, loadIntraday, loadLeaders, loadSession, loadGold, selectedKey, boardKind]);

  // Gold hero chart — jicunjin line or holdable ETF intraday
  useEffect(() => {
    if (marketScope !== "gold") return;
    if (!useEtfChart || !selectedGoldItem?.symbol || !selectedGoldItem.market) {
      if (!useEtfChart) setGoldIntraday(null);
      return;
    }
    const symbol = selectedGoldItem.symbol;
    const market = selectedGoldItem.market;
    void loadGoldIntraday(symbol, market).catch(() => setGoldIntraday(null));
    const timer = setInterval(() => {
      void loadGoldIntraday(symbol, market).catch(() => {});
    }, INTRADAY_POLL_MS);
    return () => clearInterval(timer);
  }, [marketScope, useEtfChart, selectedGoldItem?.symbol, selectedGoldItem?.market, loadGoldIntraday]);

  useEffect(() => {
    const q = searchQuery.trim();
    if (!q) {
      searchSeq.current += 1;
      setSearchHits(null);
      setSearchLoading(false);
      return;
    }
    const seq = ++searchSeq.current;
    setSearchLoading(true);
    const timer = setTimeout(() => {
      void api
        .searchSymbols(q)
        .then((res) => {
          if (seq !== searchSeq.current) return;
          setSearchHits(res.items);
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
  }, [searchQuery]);

  useEffect(() => {
    if (!detail) {
      setDetailIntra(null);
      setDetailChart("idle");
      return;
    }
    setDetailIntra(null);
    setCost(String(detail.price || ""));
    setShares(detail.market === "JD" ? "10" : "1000");
    setBoughtAt(shanghaiTodayIso());
    if (!canAddHolding(detail.market)) {
      setDetailChart("idle");
      return;
    }
    if (detail.market === "JD") {
      const item =
        goldBoard?.sections
          .flatMap((s) => s.items)
          .find((i) => i.symbol === detail.symbol || i.id === detail.symbol) ?? null;
      const sess = goldSparkSession(item);
      const pts = chartToPoints(item?.chart, sess, item?.chart_slots, item?.chart_times);
      if (pts.length >= 2) {
        setDetailIntra({
          key: detail.symbol,
          symbol: detail.symbol,
          name: detail.name,
          market: "JD",
          prev_close: item?.prev ?? null,
          session: sess,
          points: pts,
        });
        setDetailChart("ready");
      } else {
        setDetailIntra(null);
        setDetailChart("empty");
      }
      return;
    }
    setDetailChart("loading");
    void api
      .getSymbolIntraday(detail.symbol, detail.market)
      .then((series) => {
        if (series.points.length >= 2) {
          setDetailIntra(series);
          setDetailChart("ready");
        } else {
          setDetailIntra(null);
          setDetailChart("empty");
        }
      })
      .catch(() => {
        setDetailIntra(null);
        setDetailChart("empty");
      });
  }, [detail, goldBoard]);

  function selectIndex(key: string) {
    if (key === selectedKey) return;
    haptics.tap();
    setSelectedKey(key);
    setSelectedLeaderKey("");
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
    setSelectedLeaderKey("");
    setSearchQuery("");
    setSearchHits(null);
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
  }

  useEffect(() => {
    if (marketScope !== "gold" || !goldBoard) return;
    const sec = goldBoard.sections.find((s) => s.id === goldSection) ?? goldBoard.sections[0];
    const keys = [
      ...new Set(
        (sec?.items ?? [])
          .map((it) => goldBiasKey(it))
          .filter((k): k is string => Boolean(k)),
      ),
    ];
    if (keys.length === 0) {
      setGoldBiasByKey({});
      return;
    }
    let cancelled = false;
    void api
      .getShortBias(keys)
      .then((batch) => {
        if (cancelled) return;
        const next: Record<string, ShortBias> = {};
        for (const item of batch.items) {
          next[`${item.market}:${item.symbol}`] = item;
        }
        setGoldBiasByKey(next);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [marketScope, goldSection, goldBoard]);

  function selectBoard(kind: BoardKind) {
    if (kind === boardKind) return;
    haptics.tap();
    setBoardKind(kind);
    setSelectedLeaderKey("");
  }

  function selectLeaderRow(row: LeaderStock) {
    const key = `${row.market}-${row.symbol}`;
    if (key === selectedLeaderKey) {
      openDetail(row);
      return;
    }
    haptics.tap();
    setSelectedLeaderKey(key);
  }

  function clearSearch() {
    haptics.tap();
    setSearchQuery("");
    setSearchHits(null);
  }

  function openDetail(row: LeaderStock) {
    haptics.tap();
    setDetail(row);
  }

  function openSearchHit(hit: SearchHit) {
    openDetail(hitToLeader(hit));
  }

  async function onAddHolding(e: FormEvent) {
    e.preventDefault();
    if (!detail) return;
    if (!canAddHolding(detail.market)) {
      toast("持仓仅支持沪深 A 股 / ETF / 积存金", "warning");
      return;
    }
    setSaving(true);
    try {
      await api.createHolding({
        symbol: detail.symbol,
        name: detail.name,
        market: detail.market as "SH" | "SZ" | "JD",
        shares: Number(shares),
        cost: Number(cost),
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
      <div className="market-page-pin">
        <div className="market-scope-tabs" role="tablist" aria-label="市场范围">
          {(
            [
              { id: "stock" as const, label: "股票", Icon: CandlestickChart },
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
          aria-label={marketScope === "gold" ? "黄金走势" : "市场指数"}
        >
          <div className="market-index-head">
            <div className="market-index-head-main">
              <div className="market-index-head-row">
                <div className="market-index-head-name">{heroQuote.name}</div>
                <div className="market-index-head-kicker" data-live={kicker.live ? "1" : "0"}>
                  {kicker.live && <span className="market-live-dot" aria-hidden />}
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
            <IndexSparkline
              points={activeIntraday?.points ?? []}
              prevClose={activeIntraday?.prev_close ?? heroQuote.prev_close}
              changePct={heroQuote.change_pct}
              session={
                marketScope === "gold"
                  ? useEtfChart
                    ? (activeIntraday?.session ?? "cn")
                    : goldSparkSess
                  : activeIntraday?.session ??
                    (heroQuote.market === "US" ? "us" : heroQuote.market === "HK" ? "hk" : "cn")
              }
              label={`${heroQuote.name}分时走势`}
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
                const pctTone = pnlTone(top?.change_pct, top?.price ?? undefined, top?.prev ?? undefined);
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

        {marketScope === "stock" && (
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
              placeholder="代码或名称，如 510300 / 茅台"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              aria-label="搜索股票或 ETF"
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
              : (leaders?.title ?? "榜单")
        }
      >
        <div className="inset-group-header market-leaders-head">
          <span>
            {searching
              ? "搜索结果"
              : marketScope === "gold"
                ? goldSectionData?.title || "黄金"
                : (leaders?.title ?? BOARD_TABS.find((t) => t.kind === boardKind)?.label)}
          </span>
          <span>
            {searching
              ? `${searchHits?.length ?? 0} 条`
              : marketScope === "gold"
                ? `${goldSectionData?.items.length ?? 0} 只`
                : `${leaders?.items.length ?? 0} 只`}
          </span>
        </div>
        <div className="market-leaders-body">
          {error && !searching && marketScope === "stock" && (
            <p className="text-up" style={{ fontSize: 13, padding: "12px 16px" }}>
              {error}
            </p>
          )}
          {searching ? (
            searchLoading && searchHits == null ? (
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
                const viewOnly = !canAddHolding(hit.market);
                return (
                  <button
                    key={`${hit.market}-${hit.symbol}`}
                    type="button"
                    className="holding-row market-leader-row"
                    onClick={() => openSearchHit(hit)}
                  >
                    <span className="market-search-kind">{KIND_LABEL[hit.kind] ?? "标的"}</span>
                    <div className="market-leader-main">
                      <div className="holding-name">{hit.name}</div>
                      <div className="holding-meta market-leader-code">
                        {hit.market}
                        {hit.symbol}
                        {viewOnly ? " · 仅查阅" : ""}
                      </div>
                    </div>
                    <div className="holding-right market-leader-right">
                      <div className="market-leader-price">{formatMoney(hit.price)}</div>
                      <div className={`market-leader-badge market-leader-badge-solid ${pnlClass(hit.change_pct)}`}>
                        {formatPct(hit.change_pct)}
                      </div>
                    </div>
                  </button>
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
                return (
                  <button
                    key={row.id}
                    type="button"
                    className="holding-row market-leader-row"
                    data-active={row.id === selectedGoldId || row.id === selectedGoldItem?.id ? "1" : "0"}
                    onClick={() => {
                      if (row.id === selectedGoldId) {
                        if (leader) openDetail(leader);
                        return;
                      }
                      selectGoldItem(row.id);
                    }}
                  >
                    <span
                      className="market-leader-rank"
                      data-top={i < 3 ? String(i + 1) : "0"}
                      aria-label={`第 ${i + 1} 名`}
                    >
                      {i + 1}
                    </span>
                    <div className="market-leader-main">
                      <div className="holding-name market-gold-name">
                        <span className="market-gold-name-text">{row.name}</span>
                        {(() => {
                          const key = goldBiasKey(row);
                          const bias = key ? goldBiasByKey[key] : null;
                          if (!bias) return null;
                          if (
                            bias.bias === "na" &&
                            !bias.label.includes("陈旧")
                          ) {
                            return null;
                          }
                          return (
                            <span
                              className={biasChipClass(bias, bias.market, bias.symbol)}
                              title={biasChipTitle(bias, bias.market, bias.symbol)}
                            >
                              {biasChipText(bias, bias.market, bias.symbol)}
                            </span>
                          );
                        })()}
                      </div>
                      <div className="holding-meta market-leader-code">
                        {row.note && goldSection === "shop"
                          ? `${row.note}${row.freshness ? ` · ${row.freshness}` : ""}`
                          : row.freshness || row.unit || ""}
                      </div>
                    </div>
                    <div className="holding-right market-leader-right">
                      <div className="market-leader-price">
                        {formatGoldPrice(row.price, row.unit)}
                      </div>
                      <div
                        className={`market-leader-badge market-leader-badge-solid ${
                          row.change_pct != null ? pnlClass(row.change_pct) : ""
                        }`}
                      >
                        {row.change_pct != null
                          ? formatPct(row.change_pct)
                          : goldSection === "shop"
                            ? "零售"
                            : "—"}
                      </div>
                    </div>
                  </button>
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
              return (
              <button
                key={rowKey}
                type="button"
                className="holding-row market-leader-row"
                data-active={rowKey === selectedLeaderKey ? "1" : "0"}
                onClick={() => selectLeaderRow(row)}
              >
                <span
                  className="market-leader-rank"
                  data-top={i < 3 ? String(i + 1) : "0"}
                  aria-label={`第 ${i + 1} 名`}
                >
                  {i + 1}
                </span>
                <div className="market-leader-main">
                  <div className="holding-name">{row.name}</div>
                  <div className="holding-meta market-leader-code">
                    {row.market}
                    {row.symbol}
                  </div>
                </div>
                <div className="holding-right market-leader-right">
                  <div className="market-leader-price">{formatMoney(row.price)}</div>
                  {boardKind === "amount" ? (
                    <div className="market-leader-metrics">
                      <span className="market-leader-metric-mute">{formatAmount(row.amount)}</span>
                      <span className={`market-leader-badge market-leader-badge-solid ${pnlClass(row.change_pct)}`}>
                        {formatPct(row.change_pct)}
                      </span>
                    </div>
                  ) : boardKind === "turnover" ? (
                    <div className="market-leader-metrics">
                      <span className="market-leader-metric-mute">
                        {row.turnover != null ? `${row.turnover.toFixed(2)}%` : "--"}
                      </span>
                      <span className={`market-leader-badge market-leader-badge-solid ${pnlClass(row.change_pct)}`}>
                        {formatPct(row.change_pct)}
                      </span>
                    </div>
                  ) : (
                    <div className={`market-leader-badge market-leader-badge-solid ${pnlClass(row.change_pct)}`}>
                      {formatPct(row.change_pct)}
                    </div>
                  )}
                </div>
              </button>
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
          detail && canAddHolding(detail.market) ? (
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
              仅沪深与积存金可入仓 · 外盘只查阅
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

            <div className="market-detail-chart">
              {!canAddHolding(detail.market) ? (
                <p className="market-detail-hint">
                  {detail.market === "HK"
                    ? "港股仅查阅报价，分时与入仓未开放"
                    : "外盘仅查阅报价，分时与入仓未开放"}
                </p>
              ) : detailChart === "ready" && detailIntra ? (
                <IndexSparkline
                  points={detailIntra.points}
                  prevClose={detailIntra.prev_close}
                  changePct={detail.change_pct}
                  session={detailIntra.session ?? (detail.market === "JD" ? "day24" : "cn")}
                  label={`${detail.name}分时`}
                  interactive
                  compact
                />
              ) : detailChart === "loading" ? (
                <p className="market-detail-hint">分时加载中…</p>
              ) : (
                <p className="market-detail-hint">暂无分时数据，可稍后再试</p>
              )}
            </div>

            {canAddHolding(detail.market) && (
              <form id="market-add-holding" className="market-detail-form" onSubmit={onAddHolding}>
                <p className="market-detail-form-title">加入仓库</p>
                <label className="market-detail-field">
                  <span className="market-detail-label">
                    {detail.market === "JD" ? "克数" : "份额"}
                  </span>
                  <input
                    value={shares}
                    onChange={(e) => setShares(e.target.value)}
                    inputMode="decimal"
                    placeholder={detail.market === "JD" ? "例如 10" : "例如 1000"}
                    required
                  />
                </label>
                <label className="market-detail-field">
                  <span className="market-detail-label">
                    {detail.market === "JD" ? "成本（元/克）" : "成本价"}
                  </span>
                  <input
                    value={cost}
                    onChange={(e) => setCost(e.target.value)}
                    inputMode="decimal"
                    placeholder="买入成本"
                    required
                  />
                </label>
                <label className="market-detail-field">
                  <span className="market-detail-label">买入日</span>
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
