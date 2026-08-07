/**
 * Warm main-tab data + JS chunks after login so Tab switches feel instant.
 * Lightweight stand-in until React Query is warranted (docs/架构.md).
 */
import { api } from "@/lib/api";
import { getAccessToken } from "@/lib/auth";
import { cacheClear, cacheFetch, cacheForceFetch } from "./memoryCache";

function warmGet<T>(
  force: boolean,
  key: string,
  fetcher: () => Promise<T>,
  ttlMs: number,
): Promise<T> {
  return force ? cacheForceFetch(key, fetcher) : cacheFetch(key, fetcher, ttlMs);
}

export const PrefetchKeys = {
  portfolio: "portfolio",
  /** dim + ref (empty = server「当前」时段) */
  portfolioReturns: (dim: string, ref = "") => `portfolio:returns:${dim}:${ref || "cur"}`,
  indices: "market:indices",
  session: (key: string) => `market:session:${key}`,
  intraday: (key: string) => `market:intraday:${key}`,
  leaders: (key: string, kind: string) => `market:leaders:${key}:${kind}`,
  goldBoard: "market:gold-board",
  fundBoard: "market:fund-board",
  fundNav: (market: string, code: string) =>
    `market:fund-nav:${(market || "OF").toUpperCase()}:${code}`,
  symbolIntraday: (market: string, symbol: string) =>
    `market:sym-intra:${market.toUpperCase()}:${symbol}`,
  shortBias: (keys: string[]) => `market:short-bias:${[...keys].sort().join(",")}`,
  newsBoards: "news:boards",
  newsMacroPulse: "news:macro-pulse",
  newsMarket: (board: string) => `news:market:${board}`,
  newsHoldings: "news:holdings",
  analysisCatalog: "analysis:catalog",
  analysisProfile: "analysis:profile",
  analysisLatest: (scope: string) => `analysis:latest:${scope}`,
  agentSession: "agent:session",
} as const;

export const PrefetchTtl = {
  portfolio: 45_000,
  portfolioReturns: 45_000,
  indices: 20_000,
  session: 60_000,
  intraday: 30_000,
  leaders: 25_000,
  gold: 45_000,
  fund: 45_000,
  fundNav: 60_000,
  symbolIntraday: 30_000,
  shortBias: 30_000,
  news: 60_000,
  analysis: 120_000,
  analysisLatest: 60_000,
  agentSession: 45_000,
} as const;

const DEFAULT_INDEX = "sh-composite";
const SECONDARY_INDICES = ["sz-component", "chinext"] as const;
const DEFAULT_BOARD = "headline";

let warming = false;
let warmedAt = 0;
const WARM_COOLDOWN_MS = 20_000;
const pathWarmAt = new Map<string, number>();
const PATH_WARM_COOLDOWN_MS = 8_000;

export function clearPrefetchCache(): void {
  cacheClear();
  warmedAt = 0;
  pathWarmAt.clear();
}

/** Allow immediate re-warm after PWA/background resume (cooldown would otherwise block). */
export function resetWarmCooldowns(): void {
  warmedAt = 0;
  pathWarmAt.clear();
  warming = false;
}

/** Prefetch Next route payloads (App Router). */
export function warmTabRoutes(prefetch: (href: string) => void): void {
  for (const href of ["/", "/market", "/news", "/analysis", "/agent"]) {
    try {
      prefetch(href);
    } catch {
      /* ignore */
    }
  }
}

/** Pull JS for inactive tabs into the module cache (idle). */
export function warmTabChunks(): void {
  void import("@/features/portfolio/PortfolioScreen");
  void import("@/features/market/MarketScreen");
  void import("@/features/news/NewsScreen");
  void import("@/features/analysis/AnalysisScreen");
  void import("@/features/agent/AgentScreen");
}

async function warmPhaseA(force = false): Promise<void> {
  // Keep Phase A light: portfolio + default index + news/analysis shells.
  // Gold/fund boards deferred to Phase B / /market warm.
  await Promise.allSettled([
    warmGet(force, PrefetchKeys.portfolio, () => api.getPortfolio(), PrefetchTtl.portfolio),
    warmGet(
      force,
      PrefetchKeys.portfolioReturns("day"),
      () => api.getPortfolioReturns("day"),
      PrefetchTtl.portfolioReturns,
    ),
    warmGet(force, PrefetchKeys.indices, () => api.getIndices(), PrefetchTtl.indices),
    warmGet(
      force,
      PrefetchKeys.session(DEFAULT_INDEX),
      () => api.getSession(DEFAULT_INDEX),
      PrefetchTtl.session,
    ),
    warmGet(
      force,
      PrefetchKeys.intraday(DEFAULT_INDEX),
      () => api.getIntraday(DEFAULT_INDEX),
      PrefetchTtl.intraday,
    ),
    warmGet(
      force,
      PrefetchKeys.leaders(DEFAULT_INDEX, "up"),
      () => api.getLeaders(DEFAULT_INDEX, "up"),
      PrefetchTtl.leaders,
    ),
    warmGet(force, PrefetchKeys.newsBoards, () => api.getNewsBoards(), PrefetchTtl.news),
    warmGet(
      force,
      PrefetchKeys.newsMacroPulse,
      () => api.getNewsMacroPulse(),
      PrefetchTtl.news,
    ),
    warmGet(
      force,
      PrefetchKeys.newsMarket(DEFAULT_BOARD),
      () => api.getMarketNews(100, DEFAULT_BOARD),
      PrefetchTtl.news,
    ),
    warmGet(
      force,
      PrefetchKeys.analysisCatalog,
      () => api.getAnalysisCatalog(),
      PrefetchTtl.analysis,
    ),
    warmGet(
      force,
      PrefetchKeys.analysisProfile,
      () => api.getAnalysisProfile(),
      PrefetchTtl.analysis,
    ),
  ]);
}

async function warmPhaseB(force = false): Promise<void> {
  await Promise.allSettled([
    warmGet(
      force,
      PrefetchKeys.portfolioReturns("month"),
      () => api.getPortfolioReturns("month"),
      PrefetchTtl.portfolioReturns,
    ),
    warmGet(
      force,
      PrefetchKeys.portfolioReturns("year"),
      () => api.getPortfolioReturns("year"),
      PrefetchTtl.portfolioReturns,
    ),
    warmGet(force, PrefetchKeys.goldBoard, () => api.getGoldBoard(), PrefetchTtl.gold),
    warmGet(force, PrefetchKeys.fundBoard, () => api.getFundBoard(), PrefetchTtl.fund),
    warmGet(
      force,
      PrefetchKeys.newsHoldings,
      () => api.getHoldingsNews(100),
      PrefetchTtl.news,
    ),
    warmGet(
      force,
      PrefetchKeys.analysisLatest("portfolio"),
      () => api.getLatestAnalysis("portfolio"),
      PrefetchTtl.analysisLatest,
    ),
    warmGet(
      force,
      PrefetchKeys.analysisLatest("symbol"),
      () => api.getLatestAnalysis("symbol"),
      PrefetchTtl.analysisLatest,
    ),
    warmGet(
      force,
      PrefetchKeys.agentSession,
      () => api.getAgentSession(),
      PrefetchTtl.agentSession,
    ),
    ...SECONDARY_INDICES.flatMap((key) => [
      warmGet(
        force,
        PrefetchKeys.session(key),
        () => api.getSession(key),
        PrefetchTtl.session,
      ),
      warmGet(
        force,
        PrefetchKeys.intraday(key),
        () => api.getIntraday(key),
        PrefetchTtl.intraday,
      ),
    ]),
  ]);
}

/** Parallel API warm for the screens users hit first (staged A → B). */
export async function warmTabData(opts?: { force?: boolean }): Promise<void> {
  if (!getAccessToken()) return;
  const force = opts?.force === true;
  const now = Date.now();
  if (warming || (!force && now - warmedAt < WARM_COOLDOWN_MS)) return;
  warming = true;
  try {
    await warmPhaseA(force);
    warmedAt = Date.now();
    await warmPhaseB(force);
  } finally {
    warming = false;
  }
}

/** Target-tab warm on TabBar pointerdown (cooldown per path). */
export async function warmTabDataFor(path: string): Promise<void> {
  if (!getAccessToken()) return;
  const now = Date.now();
  const last = pathWarmAt.get(path) ?? 0;
  if (now - last < PATH_WARM_COOLDOWN_MS) return;
  pathWarmAt.set(path, now);

  if (path === "/" || path === "") {
    await Promise.allSettled([
      cacheFetch(PrefetchKeys.portfolio, () => api.getPortfolio(), PrefetchTtl.portfolio),
      cacheFetch(
        PrefetchKeys.portfolioReturns("day"),
        () => api.getPortfolioReturns("day"),
        PrefetchTtl.portfolioReturns,
      ),
    ]);
    return;
  }
  if (path.startsWith("/market")) {
    const { warmMarketFundGold } = await import("./marketWarm");
    await Promise.allSettled([
      cacheFetch(PrefetchKeys.indices, () => api.getIndices(), PrefetchTtl.indices),
      cacheFetch(
        PrefetchKeys.intraday(DEFAULT_INDEX),
        () => api.getIntraday(DEFAULT_INDEX),
        PrefetchTtl.intraday,
      ),
      cacheFetch(
        PrefetchKeys.leaders(DEFAULT_INDEX, "up"),
        () => api.getLeaders(DEFAULT_INDEX, "up"),
        PrefetchTtl.leaders,
      ),
      cacheFetch(
        PrefetchKeys.session(DEFAULT_INDEX),
        () => api.getSession(DEFAULT_INDEX),
        PrefetchTtl.session,
      ),
      warmMarketFundGold(),
    ]);
    return;
  }
  if (path.startsWith("/news")) {
    await Promise.allSettled([
      cacheFetch(PrefetchKeys.newsBoards, () => api.getNewsBoards(), PrefetchTtl.news),
      cacheFetch(PrefetchKeys.newsMacroPulse, () => api.getNewsMacroPulse(), PrefetchTtl.news),
      cacheFetch(
        PrefetchKeys.newsMarket(DEFAULT_BOARD),
        () => api.getMarketNews(100, DEFAULT_BOARD),
        PrefetchTtl.news,
      ),
      cacheFetch(PrefetchKeys.newsHoldings, () => api.getHoldingsNews(100), PrefetchTtl.news),
    ]);
    return;
  }
  if (path.startsWith("/analysis")) {
    await Promise.allSettled([
      cacheFetch(
        PrefetchKeys.analysisCatalog,
        () => api.getAnalysisCatalog(),
        PrefetchTtl.analysis,
      ),
      cacheFetch(
        PrefetchKeys.analysisProfile,
        () => api.getAnalysisProfile(),
        PrefetchTtl.analysis,
      ),
      cacheFetch(
        PrefetchKeys.analysisLatest("portfolio"),
        () => api.getLatestAnalysis("portfolio"),
        PrefetchTtl.analysisLatest,
      ),
      cacheFetch(
        PrefetchKeys.analysisLatest("symbol"),
        () => api.getLatestAnalysis("symbol"),
        PrefetchTtl.analysisLatest,
      ),
    ]);
    return;
  }
  if (path.startsWith("/agent")) {
    await cacheFetch(
      PrefetchKeys.agentSession,
      () => api.getAgentSession(),
      PrefetchTtl.agentSession,
    ).catch(() => {});
  }
}

/** Boot entry: routes + chunks immediately, data on idle. */
export function scheduleTabWarm(prefetch?: (href: string) => void): () => void {
  if (typeof window === "undefined" || !getAccessToken()) {
    return () => undefined;
  }

  if (prefetch) warmTabRoutes(prefetch);

  const chunkTimer = window.setTimeout(() => warmTabChunks(), 50);

  let idleId = 0;
  let dataTimer = 0;
  const runData = () => {
    void warmTabData();
  };

  if (typeof window.requestIdleCallback === "function") {
    idleId = window.requestIdleCallback(runData, { timeout: 1200 });
  } else {
    dataTimer = window.setTimeout(runData, 200);
  }

  // iOS PWA: after background, polls may have stopped — bust cooldown and warm again.
  // Also run on pageshow/focus: visibilityState sometimes stays "visible" while timers freeze.
  let lastResume = 0;
  let wasHidden = typeof document !== "undefined" && document.visibilityState === "hidden";
  const onResume = () => {
    if (typeof document !== "undefined" && document.visibilityState === "hidden") {
      wasHidden = true;
      return;
    }
    const now = Date.now();
    if (now - lastResume < 1500) return;
    lastResume = now;
    wasHidden = false;
    resetWarmCooldowns();
    // Force: TTL cacheFetch would re-serve frozen-before-background data to other tabs
    void warmTabData({ force: true });
  };
  const onVis = () => {
    if (document.visibilityState === "hidden") {
      wasHidden = true;
      return;
    }
    if (wasHidden) onResume();
  };
  document.addEventListener("visibilitychange", onVis);
  window.addEventListener("pageshow", onResume);
  window.addEventListener("focus", onResume);

  return () => {
    window.clearTimeout(chunkTimer);
    window.clearTimeout(dataTimer);
    if (idleId && typeof window.cancelIdleCallback === "function") {
      window.cancelIdleCallback(idleId);
    }
    document.removeEventListener("visibilitychange", onVis);
    window.removeEventListener("pageshow", onResume);
    window.removeEventListener("focus", onResume);
  };
}
