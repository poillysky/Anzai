"use client";

import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Activity,
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
  pnlArrow,
  pnlClass,
} from "@/lib/format";
import { haptics } from "@/lib/haptics";
import { cachePeek, cacheSet, PrefetchKeys } from "@/lib/prefetch";
import type {
  IndexQuote,
  IntradaySeries,
  LeaderStock,
  LeadersBoard,
  MarketSession,
  SearchHit,
} from "@/lib/types";

const INDEX_POLL_MS = 15000;
const INTRADAY_POLL_MS = 30000;
const LEADERS_POLL_MS = 20000;
const SESSION_POLL_MS = 60000;
const SEARCH_DEBOUNCE_MS = 280;
const DEFAULT_INDEX = "sh-composite";

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
  return market === "SH" || market === "SZ";
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
  const [saving, setSaving] = useState(false);

  const searching = searchQuery.trim().length > 0;
  const selected = indices.find((i) => i.key === selectedKey) ?? null;
  /** Keep 5 slots always — filtering empties collapsed the grid and shoved hero down on load */
  const grid = INDEX_ORDER.map((key) => ({
    key,
    quote: indices.find((i) => i.key === key) ?? null,
  }));
  const toneClass = pnlClass(selected?.change_pct);
  const heroTone =
    toneClass === "text-up" ? "up" : toneClass === "text-down" ? "down" : "flat";
  const kicker = heroKicker(session, updatedAt, pollFailed);
  const levelBubbles = useMemo(
    () =>
      getIntradayLevelBubbles(
        intraday?.points ?? [],
        intraday?.prev_close ?? selected?.prev_close,
      ),
    [intraday?.points, intraday?.prev_close, selected?.prev_close],
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

  const refreshMarket = useCallback(
    async (key = selectedKey, kind = boardKind) => {
      try {
        setError(null);
        await Promise.all([
          loadIndices(),
          loadIntraday(key),
          loadLeaders(key, kind),
          loadSession(key),
        ]);
        setUpdatedAt(new Date());
        setPollFailed(false);
      } catch {
        setPollFailed(true);
      }
    },
    [loadIndices, loadIntraday, loadLeaders, loadSession, selectedKey, boardKind],
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
    return () => {
      clearInterval(iTimer);
      clearInterval(dTimer);
      clearInterval(lTimer);
      clearInterval(sTimer);
    };
  }, [refreshMarket, loadIndices, loadIntraday, loadLeaders, loadSession, selectedKey, boardKind]);

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
    setShares("1000");
    if (!canAddHolding(detail.market)) {
      setDetailChart("idle");
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
  }, [detail]);

  function selectIndex(key: string) {
    if (key === selectedKey) return;
    haptics.tap();
    setSelectedKey(key);
    // Keep board tab sticky — only refresh quote/chart/list for the new index.
  }

  function selectBoard(kind: BoardKind) {
    if (kind === boardKind) return;
    haptics.tap();
    setBoardKind(kind);
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
      toast("持仓仅支持沪深 A 股 / ETF", "warning");
      return;
    }
    setSaving(true);
    try {
      await api.createHolding({
        symbol: detail.symbol,
        name: detail.name,
        market: detail.market as "SH" | "SZ",
        shares: Number(shares),
        cost: Number(cost),
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
        {/* Always mount session row — async insert was shoving the hero down on open */}
        <div
          className="market-session"
          data-state={session?.state ?? "closed"}
          data-pending={session ? "0" : "1"}
        >
          <span className="market-session-dot" aria-hidden />
          <span className="market-session-label">{session?.label ?? "行情"}</span>
          <span className="market-session-detail">
            {session?.detail ?? (pollFailed ? "网络异常" : "加载中…")}
          </span>
        </div>

        <section
          className="market-index-hero"
          data-tone={heroTone}
          data-live={kicker.live ? "1" : "0"}
          aria-label="市场指数"
        >
          <div className="market-index-head">
            <div className="market-index-head-main">
              <div className="market-index-head-row">
                <div className="market-index-head-name">
                  {selected?.name ?? INDEX_META[selectedKey]?.short ?? "上证指数"}
                </div>
                <div className="market-index-head-kicker" data-live={kicker.live ? "1" : "0"}>
                  {kicker.live && <span className="market-live-dot" aria-hidden />}
                  {kicker.text}
                </div>
              </div>
              <div className={`market-index-head-quote ${toneClass}`}>
                <span className="market-index-head-price">{formatMoney(selected?.price)}</span>
                <div className="market-index-head-deltas">
                  <span className="market-index-delta">
                    <span className="pnl-arrow" aria-hidden>
                      {pnlArrow(selected?.change_pct)}
                    </span>
                    {formatChange(selected?.price, selected?.prev_close)}
                  </span>
                  <span className="market-index-delta market-index-delta-pct">
                    {formatPct(selected?.change_pct)}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="market-spark-wrap">
            <IndexSparkline
              points={intraday?.points ?? []}
              prevClose={intraday?.prev_close ?? selected?.prev_close}
              changePct={selected?.change_pct}
              session={
                intraday?.session ??
                (selected?.market === "US" ? "us" : selected?.market === "HK" ? "hk" : "cn")
              }
              label={`${selected?.name ?? "指数"}分时走势`}
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

          <div className="market-index-grid" role="tablist" aria-label="切换指数">
            {grid.map(({ key, quote }) => {
              const meta = INDEX_META[key] ?? {
                short: quote?.name ?? key,
                tone: "sh",
              };
              const active = key === selectedKey;
              const pctTone = pnlClass(quote?.change_pct);
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
        </section>

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
            placeholder="代码或名称"
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

        {!searching && (
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
        aria-label={searching ? "搜索结果" : (leaders?.title ?? "榜单")}
      >
        <div className="inset-group-header market-leaders-head">
          <span>
            {searching
              ? "搜索结果"
              : (leaders?.title ?? BOARD_TABS.find((t) => t.kind === boardKind)?.label)}
          </span>
          <span>
            {searching
              ? `${searchHits?.length ?? 0} 条`
              : `${leaders?.items.length ?? 0} 只`}
          </span>
        </div>
        <div className="market-leaders-body">
          {error && !searching && (
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
            leaders.items.map((row, i) => (
              <button
                key={`${row.market}-${row.symbol}`}
                type="button"
                className="holding-row market-leader-row"
                onClick={() => openDetail(row)}
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
            ))
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
              仅沪深可入仓 · 外盘只查阅
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
              <div className={`market-detail-price ${pnlClass(detail.change_pct)}`}>
                {formatMoney(detail.price)}
              </div>
              <div className={`market-detail-chg ${pnlClass(detail.change_pct)}`}>
                <span className="pnl-arrow">{pnlArrow(detail.change_pct)}</span>
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
                  session="cn"
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
                  <span className="market-detail-label">份额</span>
                  <input
                    value={shares}
                    onChange={(e) => setShares(e.target.value)}
                    inputMode="decimal"
                    placeholder="例如 1000"
                    required
                  />
                </label>
                <label className="market-detail-field">
                  <span className="market-detail-label">成本价</span>
                  <input
                    value={cost}
                    onChange={(e) => setCost(e.target.value)}
                    inputMode="decimal"
                    placeholder="买入成本"
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
