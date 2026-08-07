/**
 * Prefetch fund / gold board + default hero series so scope switches paint from cache.
 */
import { api } from "@/lib/api";
import type {
  FundBoard,
  FundBoardItem,
  FundNavHistory,
  GoldBoard,
  GoldBoardItem,
  IntradaySeries,
  ShortBias,
  ShortBiasBatch,
} from "@/lib/types";
import { cacheFetch, cachePeek } from "./memoryCache";
import { PrefetchKeys, PrefetchTtl } from "./warmTabs";

function goldBiasKey(item: { id?: string; market?: string; symbol?: string }): string | null {
  const m = (item.market || "").trim().toUpperCase();
  const s = (item.symbol || "").trim();
  if (m && s) return `${m}:${s}`;
  if (item.id === "au9999") return "GDS:AU9999";
  return null;
}

export function fundNavToSeries(
  hist: FundNavHistory,
  item: Pick<FundBoardItem, "symbol" | "name" | "market">,
): IntradaySeries | null {
  const pts = hist.points || [];
  const code = (item.symbol || "").trim();
  if (!pts.length || !code) return null;
  const market = item.market || "OF";
  return {
    key: `${market}:${code}`,
    symbol: code,
    name: hist.name || item.name || code,
    market,
    prev_close: pts.length >= 2 ? pts[pts.length - 2]?.price : pts[0]?.price,
    session: "daily",
    points: pts,
  };
}

export function defaultFundItem(board: FundBoard | null | undefined): FundBoardItem | null {
  if (!board?.sections?.length) return null;
  const sec = board.sections.find((s) => s.id === "broad") ?? board.sections[0];
  return sec?.items?.[0] ?? null;
}

export function defaultGoldItem(board: GoldBoard | null | undefined): GoldBoardItem | null {
  if (!board?.sections?.length) return null;
  const sec = board.sections.find((s) => s.id === "domestic") ?? board.sections[0];
  return sec?.items?.[0] ?? null;
}

export function goldSectionBiasKeys(
  board: GoldBoard | null | undefined,
  sectionId = "domestic",
): string[] {
  if (!board) return [];
  const sec = board.sections.find((s) => s.id === sectionId) ?? board.sections[0];
  return [
    ...new Set(
      (sec?.items ?? [])
        .map((it) => goldBiasKey(it))
        .filter((k): k is string => Boolean(k))
        .filter((k) => {
          const m = (k.split(":")[0] || "").toUpperCase();
          return m !== "SH" && m !== "SZ";
        }),
    ),
  ];
}

export function shortBiasMap(batch: ShortBiasBatch): Record<string, ShortBias> {
  const next: Record<string, ShortBias> = {};
  for (const item of batch.items) {
    next[`${item.market}:${item.symbol}`] = item;
  }
  return next;
}

/** Warm boards + default fund/gold hero payloads (idle-friendly). */
export async function warmMarketFundGold(): Promise<void> {
  const [gold, fund] = await Promise.all([
    cacheFetch(PrefetchKeys.goldBoard, () => api.getGoldBoard(), PrefetchTtl.gold),
    cacheFetch(PrefetchKeys.fundBoard, () => api.getFundBoard(), PrefetchTtl.fund),
  ]);
  await Promise.allSettled([warmFundHero(fund), warmGoldHero(gold)]);
}

export async function warmFundHero(board?: FundBoard | null): Promise<void> {
  const b = board ?? cachePeek<FundBoard>(PrefetchKeys.fundBoard);
  const item = defaultFundItem(b);
  if (!item) return;
  const symbol = item.symbol?.trim();
  if (!symbol) return;
  const market = item.market || "OF";
  const tasks: Promise<unknown>[] = [
    cacheFetch(
      PrefetchKeys.fundNav(market, symbol),
      () => api.getFundNavHistory(symbol, 30, market),
      PrefetchTtl.fundNav,
    ),
  ];
  if (item.kind !== "otc" && item.market) {
    const mkt = item.market;
    tasks.push(
      cacheFetch(
        PrefetchKeys.symbolIntraday(mkt, symbol),
        () => api.getSymbolIntraday(symbol, mkt),
        PrefetchTtl.symbolIntraday,
      ),
    );
  }
  await Promise.allSettled(tasks);
}

export async function warmGoldHero(
  board?: GoldBoard | null,
  sectionId = "domestic",
): Promise<void> {
  const b = board ?? cachePeek<GoldBoard>(PrefetchKeys.goldBoard);
  const item = defaultGoldItem(b);
  const tasks: Promise<unknown>[] = [];

  if (item?.holdable && item.symbol && item.market) {
    tasks.push(
      cacheFetch(
        PrefetchKeys.symbolIntraday(item.market, item.symbol),
        () => api.getSymbolIntraday(item.symbol!, item.market!),
        PrefetchTtl.symbolIntraday,
      ),
    );
  }

  const keys = goldSectionBiasKeys(b, sectionId);
  if (keys.length) {
    const cacheKey = PrefetchKeys.shortBias(keys);
    tasks.push(
      cacheFetch(cacheKey, () => api.getShortBias(keys), PrefetchTtl.shortBias),
    );
  }

  await Promise.allSettled(tasks);
}

/** Pointerdown / scope switch: refresh that scope's hero if stale. */
export function scheduleWarmMarketScope(scope: "fund" | "gold"): void {
  if (typeof window === "undefined") return;
  const run = () => {
    if (scope === "fund") void warmFundHero().catch(() => {});
    else void warmGoldHero().catch(() => {});
  };
  if (typeof window.requestIdleCallback === "function") {
    window.requestIdleCallback(run, { timeout: 500 });
  } else {
    window.setTimeout(run, 0);
  }
}
