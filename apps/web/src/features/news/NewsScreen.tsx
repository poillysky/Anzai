"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { CenterModal } from "@/components/overlay/CenterModal";
import { Briefcase, ChevronLeft, ChevronRight, Globe, Inbox, Newspaper, Plus, RefreshCw, Sparkles, Warehouse, X } from "@/components/ui/icons";
import { api } from "@/lib/api";
import { haptics } from "@/lib/haptics";
import { useForegroundEpoch, useTabActive } from "@/hooks/useTabActive";
import { useShellStack } from "@/hooks/useShellStack";
import { ShellBase, ShellLayer, ShellRoot } from "@/components/layout/ShellStack";
import { OfflineBanner } from "@/components/layout/OfflineBanner";
import {
  cacheForceFetch,
  cachePeek,
  cacheSet,
  cacheSWR,
  PrefetchKeys,
  PrefetchTtl,
} from "@/lib/prefetch";
import { usePullToRefresh } from "@/hooks/usePullToRefresh";
import type {
  NewsArticle,
  NewsBoard,
  NewsFeed,
  NewsInterest,
  NewsItem,
  NewsMacroPulse,
  PortfolioSummary,
} from "@/lib/types";

type NewsTab = "market" | "holdings" | "interests";

const TABS: { kind: NewsTab; label: string; Icon: typeof Globe }[] = [
  { kind: "market", label: "市场", Icon: Globe },
  { kind: "holdings", label: "持仓", Icon: Briefcase },
  { kind: "interests", label: "兴趣", Icon: Sparkles },
];

const FALLBACK_BOARDS: NewsBoard[] = [
  { id: "headline", label: "要闻" },
  { id: "hkus", label: "港美" },
  { id: "world", label: "国际" },
  { id: "announce", label: "公告" },
  { id: "tech", label: "科技" },
  { id: "agri", label: "农业" },
  { id: "auto", label: "汽车" },
  { id: "estate", label: "地产" },
  { id: "energy", label: "能源" },
  { id: "industry", label: "产经" },
  { id: "finance", label: "金融" },
  { id: "company", label: "公司" },
];

const INTEREST_SUGGESTIONS = ["半导体", "光伏", "红利", "军工", "新能源", "人工智能"];
const INTEREST_CAP = 8;

function parseNewsDate(raw: string): Date | null {
  const s = (raw || "").trim();
  if (!s) return null;
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/);
  if (m) {
    const [, y, mo, d, hh, mm, ss] = m;
    return new Date(
      Number(y),
      Number(mo) - 1,
      Number(d),
      Number(hh),
      Number(mm),
      Number(ss || 0),
    );
  }
  const t = Date.parse(s.replace(/-/g, "/"));
  return Number.isFinite(t) ? new Date(t) : null;
}

function formatNewsTime(raw: string): { label: string; fresh: boolean } {
  const dt = parseNewsDate(raw);
  if (!dt) {
    const s = (raw || "").trim();
    return { label: s ? s.slice(0, 16) : "", fresh: false };
  }
  const now = Date.now();
  const diff = Math.max(0, now - dt.getTime());
  const mins = Math.floor(diff / 60_000);
  const fresh = mins < 60;
  if (mins < 1) return { label: "刚刚", fresh: true };
  if (mins < 60) return { label: `${mins}分钟前`, fresh: true };
  const hours = Math.floor(mins / 60);
  if (hours < 12 && dt.getDate() === new Date().getDate()) {
    return { label: `${hours}小时前`, fresh };
  }
  const sameDay =
    dt.getFullYear() === new Date().getFullYear() &&
    dt.getMonth() === new Date().getMonth() &&
    dt.getDate() === new Date().getDate();
  const hh = String(dt.getHours()).padStart(2, "0");
  const mm = String(dt.getMinutes()).padStart(2, "0");
  if (sameDay) return { label: `${hh}:${mm}`, fresh };
  const mo = String(dt.getMonth() + 1).padStart(2, "0");
  const d = String(dt.getDate()).padStart(2, "0");
  return { label: `${mo}-${d} ${hh}:${mm}`, fresh: false };
}

/** Strip EM scrape leftovers like a lone trailing fullwidth "（" */
function cleanReaderBody(raw: string): string {
  return raw
    .replace(/[\s\n\u3000]*[\(\uFF08\[【〈《]+[\s\n\u3000]*$/u, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export default function NewsScreen() {
  const tabActive = useTabActive("/news");
  const fgEpoch = useForegroundEpoch();
  const resumeEpochRef = useRef(0);
  const { page: shellPage, overlayOpen, push, pop, popSoft } = useShellStack<"list" | "reader">({
    root: "list",
  });
  const [tab, setTab] = useState<NewsTab>("market");
  const [board, setBoard] = useState("headline");
  const [boards, setBoards] = useState<NewsBoard[]>(() => {
    const cached = cachePeek<{ items: NewsBoard[] }>(PrefetchKeys.newsBoards);
    return cached?.items?.length ? cached.items : FALLBACK_BOARDS;
  });
  /** Per-board feeds — list always follows selected chip, never leave previous board's rows */
  const [marketByBoard, setMarketByBoard] = useState<Record<string, NewsFeed>>(() => {
    const headline = cachePeek<NewsFeed>(PrefetchKeys.newsMarket("headline"));
    return headline ? { headline } : ({} as Record<string, NewsFeed>);
  });
  const [holdings, setHoldings] = useState<NewsFeed | null>(
    () => cachePeek<NewsFeed>(PrefetchKeys.newsHoldings),
  );
  const [macroPulse, setMacroPulse] = useState<NewsMacroPulse | null>(
    () => cachePeek<NewsMacroPulse>(PrefetchKeys.newsMacroPulse),
  );
  const [interestsFeed, setInterestsFeed] = useState<NewsFeed | null>(null);
  const [interests, setInterests] = useState<NewsInterest[]>([]);
  const [holdingCount, setHoldingCount] = useState<number | null>(() => {
    const p = cachePeek<PortfolioSummary>(PrefetchKeys.portfolio);
    return p ? p.holdings.length : null;
  });
  const [loading, setLoading] = useState(
    () => !cachePeek<NewsFeed>(PrefetchKeys.newsMarket("headline")),
  );
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<NewsItem | null>(null);
  const [article, setArticle] = useState<NewsArticle | null>(null);
  const [articleError, setArticleError] = useState<string | null>(null);
  const [openingKey, setOpeningKey] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [draftKeyword, setDraftKeyword] = useState("");
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const openGen = useRef(0);
  const feedBodyRef = useRef<HTMLDivElement>(null);
  const marketByBoardRef = useRef(marketByBoard);
  marketByBoardRef.current = marketByBoard;

  const market = marketByBoard[board] ?? null;

  useEffect(() => {
    void cacheSWR(
      PrefetchKeys.newsBoards,
      () => api.getNewsBoards(),
      PrefetchTtl.news,
      (res) => {
        if (res.items?.length) setBoards(res.items);
      },
    ).catch(() => {
      /* keep fallback chips */
    });
  }, []);

  const loadMacroPulse = useCallback(async (force = false) => {
    const apply = (pulse: NewsMacroPulse) => {
      cacheSet(PrefetchKeys.newsMacroPulse, pulse);
      setMacroPulse(pulse);
    };
    try {
      if (force) {
        apply(
          await cacheForceFetch(PrefetchKeys.newsMacroPulse, () =>
            api.getNewsMacroPulse(),
          ),
        );
        return;
      }
      await cacheSWR(
        PrefetchKeys.newsMacroPulse,
        () => api.getNewsMacroPulse(),
        PrefetchTtl.news,
        apply,
      );
    } catch {
      apply({
        as_of: "",
        weekday: "",
        session_hint: "",
        calendar: "宏观速览暂不可用",
        items: [],
        note: "行情源暂时拉不到，下拉刷新重试",
      });
    }
  }, []);

  const loadInterestsList = useCallback(async () => {
    const res = await api.getNewsInterests();
    setInterests(res.items ?? []);
    return res.items ?? [];
  }, []);

  const loadMarket = useCallback(async (boardId: string, force = false) => {
    const apply = (feed: NewsFeed) => {
      cacheSet(PrefetchKeys.newsMarket(boardId), feed);
      setMarketByBoard((prev) => ({ ...prev, [boardId]: feed }));
    };
    if (force) {
      apply(
        await cacheForceFetch(PrefetchKeys.newsMarket(boardId), () =>
          api.getMarketNews(100, boardId),
        ),
      );
      return;
    }
    await cacheSWR(
      PrefetchKeys.newsMarket(boardId),
      () => api.getMarketNews(100, boardId),
      PrefetchTtl.news,
      apply,
    );
  }, []);

  const loadHoldings = useCallback(async (force = false) => {
    if (force) {
      const [feed, portfolio] = await Promise.all([
        cacheForceFetch(PrefetchKeys.newsHoldings, () => api.getHoldingsNews(100)),
        cacheForceFetch(PrefetchKeys.portfolio, () => api.getPortfolio()).catch(
          () => null,
        ),
      ]);
      setHoldings(feed);
      setHoldingCount(portfolio?.holdings?.length ?? 0);
      return;
    }
    await cacheSWR(
      PrefetchKeys.newsHoldings,
      () => api.getHoldingsNews(100),
      PrefetchTtl.news,
      (feed) => {
        setHoldings(feed);
      },
    );
    void cacheSWR(
      PrefetchKeys.portfolio,
      () => api.getPortfolio(),
      PrefetchTtl.portfolio,
      (portfolio) => {
        setHoldingCount(portfolio.holdings?.length ?? 0);
      },
    ).catch(() => {});
  }, []);

  const loadInterests = useCallback(async () => {
    const [list, feed] = await Promise.all([
      loadInterestsList(),
      api.getInterestsNews(100),
    ]);
    setInterests(list);
    setInterestsFeed(feed);
  }, [loadInterestsList]);

  const refreshGen = useRef(0);
  const refresh = useCallback(
    async (kind: NewsTab, boardId: string, soft = false, force = false) => {
      const gen = ++refreshGen.current;
      if (!soft) setLoading(true);
      setError(null);
      try {
        const netForce = force || !soft;
        if (kind === "market") {
          await Promise.all([
            loadMarket(boardId, netForce),
            loadMacroPulse(netForce),
          ]);
        } else if (kind === "holdings") await loadHoldings(netForce);
        else await loadInterests();
        if (gen !== refreshGen.current) return;
      } catch (e) {
        if (gen !== refreshGen.current) return;
        setError(e instanceof Error ? e.message : "加载失败");
        if (!soft && kind === "interests") setInterestsFeed(null);
      } finally {
        if (!soft && gen === refreshGen.current) setLoading(false);
      }
    },
    [loadMarket, loadMacroPulse, loadHoldings, loadInterests],
  );

  /** Soft when we already have rows (or warm cache); hard only on true empty first paint */
  const bootedRef = useRef(false);
  const boardRef = useRef(board);
  boardRef.current = board;

  useEffect(() => {
    if (!tabActive) return;
    const fromResume = fgEpoch !== resumeEpochRef.current;
    if (fromResume) resumeEpochRef.current = fgEpoch;
    if (fromResume) {
      void cacheForceFetch(PrefetchKeys.newsBoards, () => api.getNewsBoards())
        .then((res) => {
          if (res.items?.length) setBoards(res.items);
        })
        .catch(() => {});
    }
    const hasBoardFeed =
      tab !== "market" ||
      Boolean(marketByBoardRef.current[board] || cachePeek(PrefetchKeys.newsMarket(board)));
    const hasHoldings =
      tab !== "holdings" || Boolean(cachePeek(PrefetchKeys.newsHoldings));
    const soft =
      !fromResume &&
      (bootedRef.current ||
        (tab === "market" ? hasBoardFeed : tab === "holdings" ? hasHoldings : false));
    bootedRef.current = true;
    void refresh(tab, board, soft, fromResume);
  }, [tabActive, fgEpoch, tab, board, refresh]);

  /** Warm other board feeds so chip + list switch together next time */
  useEffect(() => {
    if (!tabActive || tab !== "market") return;
    let cancelled = false;
    const warm = async () => {
      for (const b of boards) {
        if (cancelled) return;
        if (b.id === boardRef.current) continue;
        if (marketByBoardRef.current[b.id]) continue;
        const cached = cachePeek<NewsFeed>(PrefetchKeys.newsMarket(b.id));
        if (cached) {
          setMarketByBoard((prev) => (prev[b.id] ? prev : { ...prev, [b.id]: cached }));
          continue;
        }
        try {
          const feed = await api.getMarketNews(100, b.id);
          if (cancelled) return;
          cacheSet(PrefetchKeys.newsMarket(b.id), feed);
          setMarketByBoard((prev) => ({ ...prev, [b.id]: feed }));
        } catch {
          /* skip */
        }
      }
    };
    const idle =
      typeof window.requestIdleCallback === "function"
        ? window.requestIdleCallback(() => void warm(), { timeout: 2500 })
        : window.setTimeout(() => void warm(), 400);
    return () => {
      cancelled = true;
      if (typeof window.cancelIdleCallback === "function") {
        window.cancelIdleCallback(idle as number);
      } else {
        window.clearTimeout(idle as number);
      }
    };
  }, [tabActive, tab, boards]);

  const selectTab = useCallback((kind: NewsTab) => {
    setTab((prev) => {
      if (prev === kind) return prev;
      haptics.tap();
      return kind;
    });
  }, []);

  const selectBoard = useCallback((id: string) => {
    if (boardRef.current === id) return;
    haptics.tap();
    boardRef.current = id;
    // Swap list with this board's feed in the same gesture as the chip
    setMarketByBoard((prev) => {
      if (prev[id]) return prev;
      const cached = cachePeek<NewsFeed>(PrefetchKeys.newsMarket(id));
      return cached ? { ...prev, [id]: cached } : prev;
    });
    setBoard(id);
  }, []);

  const pullRefresh = useCallback(async () => {
    void cacheForceFetch(PrefetchKeys.newsBoards, () => api.getNewsBoards())
      .then((res) => {
        if (res.items?.length) setBoards(res.items);
      })
      .catch(() => {});
    await refresh(tab, board, true, true);
  }, [refresh, tab, board]);

  const ptrBarRef = useRef<HTMLDivElement>(null);
  const {
    refreshing: ptrRefreshing,
    ready: ptrReady,
  } = usePullToRefresh(feedBodyRef, ptrBarRef, {
    onRefresh: pullRefresh,
    disabled: overlayOpen || editOpen,
    onArmed: () => haptics.selection(),
  });

  /** Prefetch body before open so card height matches final content (no mid-open grow). */
  const openReader = useCallback(async (row: NewsItem) => {
    // Prefer URL so 同花顺/新浪 short ids are not mistaken for东财 codes
    const key = (row.url || row.id || "").trim();
    if (!key) return;
    haptics.tap();
    const gen = ++openGen.current;
    setOpeningKey(key);
    let nextArticle: NewsArticle | null = null;
    let nextError: string | null = null;
    try {
      nextArticle = await Promise.race([
        api.getNewsArticle(key),
        new Promise<never>((_, reject) => {
          window.setTimeout(() => reject(new Error("timeout")), 12_000);
        }),
      ]);
    } catch {
      nextError = "正文暂不可用，已显示摘要";
    }
    if (gen !== openGen.current) return;
    setArticle(nextArticle);
    setArticleError(nextError);
    setActive(row);
    setOpeningKey(null);
    push("reader");
  }, [push]);

  // Resume / leave: never leave a row stuck on「打开中」
  useEffect(() => {
    openGen.current += 1;
    setOpeningKey(null);
  }, [fgEpoch, tabActive]);

  const closeReader = useCallback(() => {
    openGen.current += 1;
    setOpeningKey(null);
    setActive(null);
    setArticle(null);
    setArticleError(null);
    if (shellPage === "reader") pop();
  }, [pop, shellPage]);

  useEffect(() => {
    if (shellPage === "list" && active) {
      openGen.current += 1;
      setOpeningKey(null);
      setActive(null);
      setArticle(null);
      setArticleError(null);
    }
  }, [shellPage, active]);

  const openEditor = useCallback(() => {
    haptics.tap();
    setEditError(null);
    setDraftKeyword("");
    setEditOpen(true);
    void loadInterestsList().catch(() => {
      /* keep current chips */
    });
  }, [loadInterestsList]);

  const addInterest = useCallback(
    async (raw: string) => {
      const kw = raw.trim();
      if (!kw || editBusy) return;
      if (interests.length >= INTEREST_CAP) {
        setEditError(`最多 ${INTEREST_CAP} 个兴趣词`);
        return;
      }
      if (interests.some((i) => i.keyword === kw)) {
        setEditError("已添加");
        return;
      }
      setEditBusy(true);
      setEditError(null);
      try {
        await api.addNewsInterest(kw);
        haptics.success();
        setDraftKeyword("");
        await loadInterestsList();
      } catch (e) {
        setEditError(e instanceof Error ? e.message : "添加失败");
      } finally {
        setEditBusy(false);
      }
    },
    [editBusy, interests, loadInterestsList],
  );

  const removeInterest = useCallback(
    async (id: number) => {
      if (editBusy) return;
      setEditBusy(true);
      setEditError(null);
      try {
        await api.removeNewsInterest(id);
        haptics.tap();
        await loadInterestsList();
      } catch (e) {
        setEditError(e instanceof Error ? e.message : "删除失败");
      } finally {
        setEditBusy(false);
      }
    },
    [editBusy, loadInterestsList],
  );

  const closeEditor = useCallback(() => {
    setEditOpen(false);
    setDraftKeyword("");
    setEditError(null);
    if (tab === "interests") void refresh("interests", board, true);
  }, [tab, board, refresh]);

  const boardLabel = boards.find((b) => b.id === board)?.label ?? "要闻";
  const feed =
    tab === "market" ? market : tab === "holdings" ? holdings : interestsFeed;
  const items = feed?.items ?? [];
  const feedNote = (feed?.note || "").trim();
  const emptyHoldings = tab === "holdings" && holdingCount === 0;
  const emptyInterests = tab === "interests" && interests.length === 0;
  const feedTitle =
    tab === "market"
      ? feed?.title || boardLabel
      : tab === "holdings"
        ? (feed?.title ?? "持仓相关")
        : (feed?.title ?? "我的兴趣");
  const showSkel = loading && items.length === 0 && !emptyInterests && !emptyHoldings;

  const readerTitle = active?.title || "资讯";
  const summaryBody = cleanReaderBody((active?.summary || "").trim());
  const articleBody = cleanReaderBody((article?.body || "").trim());
  const readerBody = articleBody || summaryBody;
  const readerSource = active?.source || article?.source || "";
  const readerTime = formatNewsTime(active?.published_at || "").label;

  const suggestLeft = INTEREST_SUGGESTIONS.filter(
    (s) => !interests.some((i) => i.keyword === s),
  );

  const statusLabel =
    tab === "market" ? "多源聚合" : tab === "holdings" ? "持仓相关" : "兴趣定制";

  return (
    <ShellRoot className={`news-page${overlayOpen ? " news-page--push" : ""}`} pushed={overlayOpen}>
      <ShellBase className="news-shell-base" behind={overlayOpen}>
        <div className="news-page-inner" data-kind={tab}>
          <OfflineBanner />
          <div className="news-page-pin">
        <div className="news-seg news-seg-3" role="tablist" aria-label="新闻分类">
          {TABS.map(({ kind, label, Icon }) => (
            <button
              key={kind}
              type="button"
              role="tab"
              className="news-seg-tab"
              aria-selected={tab === kind}
              data-active={tab === kind ? "1" : "0"}
              data-kind={kind}
              onPointerDown={(e) => {
                if (e.button !== 0) return;
                selectTab(kind);
              }}
            >
              <Icon size={13} strokeWidth={2.2} absoluteStrokeWidth aria-hidden />
              {label}
            </button>
          ))}
        </div>

        {tab === "market" ? (
          <>
            {macroPulse && (macroPulse.calendar || macroPulse.items.length > 0 || macroPulse.note) ? (
              <div className="news-macro-strip" aria-label="宏观速览">
                <div className="news-macro-strip-meta">
                  <span className="news-macro-strip-cal">
                    {macroPulse.calendar || "宏观速览"}
                  </span>
                  {macroPulse.session_hint ? (
                    <span className="news-macro-strip-session">{macroPulse.session_hint}</span>
                  ) : null}
                </div>
                {macroPulse.items.length > 0 ? (
                  <div className="news-macro-strip-scroller" role="list">
                    {macroPulse.items.map((it) => {
                      const chg = it.change_pct;
                      const tone =
                        chg == null ? "flat" : chg > 0 ? "up" : chg < 0 ? "down" : "flat";
                      const chgLabel =
                        chg == null
                          ? "—"
                          : `${chg > 0 ? "+" : ""}${chg.toFixed(2)}%`;
                      const priceLabel =
                        it.price >= 100
                          ? it.price.toFixed(2)
                          : it.price >= 10
                            ? it.price.toFixed(3)
                            : it.price.toFixed(4);
                      return (
                        <div
                          key={it.key}
                          className="news-macro-chip"
                          data-tone={tone}
                          role="listitem"
                        >
                          <span className="news-macro-chip-name">{it.name}</span>
                          <span className="news-macro-chip-price">
                            {priceLabel}
                            {it.unit ? (
                              <span className="news-macro-chip-unit">{it.unit}</span>
                            ) : null}
                          </span>
                          <span className="news-macro-chip-chg">{chgLabel}</span>
                        </div>
                      );
                    })}
                  </div>
                ) : macroPulse.note ? (
                  <p className="news-macro-strip-note">{macroPulse.note}</p>
                ) : null}
              </div>
            ) : null}
            <div className="news-boards" role="tablist" aria-label="市场板块">
              {boards.map((b) => (
                <button
                  key={b.id}
                  type="button"
                  role="tab"
                  className="news-board-chip"
                  aria-selected={board === b.id}
                  data-active={board === b.id ? "1" : "0"}
                  data-board={b.id}
                  onPointerDown={(e) => {
                    if (e.button !== 0) return;
                    selectBoard(b.id);
                  }}
                >
                  {b.label}
                </button>
              ))}
            </div>
          </>
        ) : null}

        {tab === "interests" ? (
          <div className="news-interest-bar">
            <div className="news-boards news-interest-chips" role="list" aria-label="兴趣词">
              {interests.length === 0 ? (
                <span className="news-interest-placeholder">添加关键词定制资讯</span>
              ) : (
                interests.map((item) => (
                  <span key={item.id} className="news-board-chip" data-active="1" role="listitem">
                    {item.keyword}
                  </span>
                ))
              )}
            </div>
            <button type="button" className="news-interest-edit" onClick={openEditor}>
              <Plus size={14} strokeWidth={2.4} absoluteStrokeWidth aria-hidden />
              编辑
            </button>
          </div>
        ) : null}
      </div>

      <section className="inset-group news-feed" aria-label={feedTitle}>
        <div className="inset-group-header news-feed-head">
          <span className="news-feed-head-title">
            <span className="news-feed-head-icon" aria-hidden>
              <Newspaper size={12} strokeWidth={2.2} absoluteStrokeWidth />
            </span>
            <span className="news-feed-head-text">{feedTitle}</span>
            <span className="news-feed-head-kicker">{statusLabel}</span>
          </span>
          <span className="news-feed-count">
            {showSkel ? "…" : `${items.length}`}
            <span className="news-feed-count-unit">条</span>
          </span>
        </div>

        <div className="news-feed-body" ref={feedBodyRef} data-ptr={ptrRefreshing ? "1" : "0"}>
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
                size={16}
                strokeWidth={2.2}
                absoluteStrokeWidth
              />
              <span className="news-ptr-label">
                {ptrRefreshing ? "刷新中" : ptrReady ? "松开刷新" : "下拉刷新"}
              </span>
            </div>
          </div>
          {error ? (
            <div className="news-empty">
              <Inbox size={22} strokeWidth={1.75} absoluteStrokeWidth aria-hidden />
              <p>{error}</p>
            </div>
          ) : showSkel ? (
            <div className="news-skel" aria-label="加载中">
              {Array.from({ length: 7 }, (_, i) => (
                <div key={i} className="news-skel-row" />
              ))}
            </div>
          ) : emptyHoldings ? (
            <div className="news-empty">
              <Warehouse size={22} strokeWidth={1.75} absoluteStrokeWidth aria-hidden />
              <p>仓库暂无持仓</p>
              <p className="news-empty-sub">添加持仓后自动聚合相关资讯</p>
              <Link href="/" className="news-empty-link" replace scroll={false}>
                去仓库添加
              </Link>
            </div>
          ) : emptyInterests ? (
            <div className="news-empty">
              <Sparkles size={22} strokeWidth={1.75} absoluteStrokeWidth aria-hidden />
              <p>还没有兴趣词</p>
              <p className="news-empty-sub">添加如「半导体」「红利」等关键词</p>
              <button type="button" className="news-empty-link" onClick={openEditor}>
                添加兴趣
              </button>
            </div>
          ) : items.length === 0 ? (
            <div className="news-empty">
              <Inbox size={22} strokeWidth={1.75} absoluteStrokeWidth aria-hidden />
              <p>{feedNote || "暂无资讯"}</p>
              {feedNote ? (
                <p className="news-empty-sub">可切换板块或稍后下拉刷新</p>
              ) : null}
            </div>
          ) : (
            <ul className="news-list" key={`news-${tab}-${board}`}>
              {items.map((row, idx) => (
                <NewsRow
                  key={`${row.id || "n"}-${row.url || "u"}-${idx}`}
                  item={row}
                  index={idx}
                  showSymbols={tab === "holdings" || tab === "interests"}
                  busy={openingKey === (row.url || row.id)}
                  onOpen={() => void openReader(row)}
                />
              ))}
            </ul>
          )}
        </div>
      </section>

<CenterModal
        open={editOpen}
        title="编辑兴趣"
        onClose={closeEditor}
        footer={
          <button type="button" className="btn btn-block" onClick={closeEditor}>
            完成
          </button>
        }
      >
        <div className="news-interest-edit-modal">
          <label className="news-interest-input-wrap">
            <input
              data-autofocus
              value={draftKeyword}
              onChange={(e) => setDraftKeyword(e.target.value.slice(0, 16))}
              placeholder="输入关键词"
              enterKeyHint="done"
              autoComplete="off"
              autoCorrect="off"
              spellCheck={false}
              maxLength={16}
              disabled={editBusy}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void addInterest(draftKeyword);
                }
              }}
            />
            <button
              type="button"
              className="btn"
              disabled={editBusy || !draftKeyword.trim()}
              onClick={() => void addInterest(draftKeyword)}
            >
              添加
            </button>
          </label>

          {editError ? <p className="news-interest-edit-error">{editError}</p> : null}

          <div className="news-interest-edit-section">
            <div className="news-interest-edit-label">
              已选 · {interests.length}/{INTEREST_CAP}
            </div>
            {interests.length === 0 ? (
              <p className="news-interest-edit-empty">暂无，可从下方推荐添加</p>
            ) : (
              <div className="news-interest-edit-chips">
                {interests.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    className="news-interest-chip-rem"
                    disabled={editBusy}
                    onClick={() => void removeInterest(item.id)}
                    aria-label={`移除 ${item.keyword}`}
                  >
                    {item.keyword}
                    <X size={12} strokeWidth={2.4} absoluteStrokeWidth aria-hidden />
                  </button>
                ))}
              </div>
            )}
          </div>

          {suggestLeft.length > 0 && interests.length < INTEREST_CAP ? (
            <div className="news-interest-edit-section">
              <div className="news-interest-edit-label">推荐</div>
              <div className="news-interest-edit-chips">
                {suggestLeft.map((s) => (
                  <button
                    key={s}
                    type="button"
                    className="news-board-chip"
                    disabled={editBusy}
                    onClick={() => void addInterest(s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </CenterModal>
        </div>
      </ShellBase>

      {overlayOpen && active ? (
        <ShellLayer className="news-reader-layer" onEdgeBack={popSoft}>
          <header className="news-reader-nav">
            <button
              type="button"
              className="news-reader-nav-back"
              onClick={closeReader}
              aria-label="返回"
            >
              <ChevronLeft size={22} strokeWidth={2.25} absoluteStrokeWidth aria-hidden />
            </button>
            <h1 className="news-reader-nav-title">资讯</h1>
            <span className="news-reader-nav-spacer" aria-hidden />
          </header>
          <div className="news-reader news-reader--page">
            <article className="news-reader-article">
              <header className="news-reader-head">
                <h2 className="news-reader-title">{readerTitle}</h2>
                <div className="news-reader-meta">
                  {active.symbols?.length ? (
                    <span className="news-reader-meta-syms">
                      {active.symbols.slice(0, 3).join(" · ")}
                    </span>
                  ) : null}
                  {readerSource ? (
                    <span className="news-reader-meta-source">{readerSource}</span>
                  ) : null}
                  {readerTime ? (
                    <span className="news-reader-meta-time">{readerTime}</span>
                  ) : null}
                </div>
              </header>
              <div className="news-reader-body">
                {(() => {
                  const paras = (readerBody || "")
                    .split(/\n+/)
                    .map((p) => p.trim())
                    .filter(Boolean);
                  const imgs = article?.images ?? [];
                  if (paras.length === 0 && imgs.length === 0) {
                    return <p className="news-reader-empty">暂无正文</p>;
                  }
                  return (
                    <>
                      {paras.map((para, i) => (
                        <p key={i}>{para}</p>
                      ))}
                      {imgs.map((src) => (
                        <img
                          key={src}
                          className="news-reader-img"
                          src={src}
                          alt=""
                          loading="lazy"
                          referrerPolicy="no-referrer"
                        />
                      ))}
                    </>
                  );
                })()}
              </div>
              {articleError ? <p className="news-reader-hint">{articleError}</p> : null}
            </article>
          </div>
        </ShellLayer>
      ) : null}
    </ShellRoot>
  );
}

function NewsRow({
  item,
  index,
  showSymbols,
  busy,
  onOpen,
}: {
  item: NewsItem;
  index: number;
  showSymbols: boolean;
  busy?: boolean;
  onOpen: () => void;
}) {
  const { label: time, fresh } = formatNewsTime(item.published_at);
  const source = (item.source || "").trim();
  return (
    <li>
      <button
        type="button"
        className="news-row"
        data-fresh={fresh ? "1" : "0"}
        data-odd={index % 2 === 1 ? "1" : "0"}
        data-top={index < 3 ? String(index + 1) : undefined}
        onClick={onOpen}
        disabled={busy || (!item.id && !item.url)}
        aria-busy={busy || undefined}
      >
        <div className="news-row-main">
          <div className="news-row-title-line">
            <span className="news-row-index" aria-hidden>
              {index + 1}
            </span>
            <div className="news-row-title">{item.title}</div>
          </div>
          <div className="news-row-meta">
            {fresh ? <span className="news-row-live" aria-label="新近">新</span> : null}
            {source ? <span className="news-row-source">{source}</span> : null}
            {showSymbols && item.symbols?.length ? (
              <span className="news-row-syms">{item.symbols.slice(0, 2).join(" · ")}</span>
            ) : null}
          </div>
        </div>
        <div className="news-row-aside">
          {time ? <span className="news-row-time">{time}</span> : null}
          <ChevronRight
            className="news-row-chevron"
            size={14}
            strokeWidth={2.2}
            absoluteStrokeWidth
            aria-hidden
          />
        </div>
      </button>
    </li>
  );
}
