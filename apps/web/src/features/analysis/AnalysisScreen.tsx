"use client";

import { CenterModal } from "@/components/overlay/CenterModal";
import { Disclaimer } from "@/components/ui/Disclaimer";
import {
  CandlestickChart,
  Check,
  ChevronRight,
  CircleDollarSign,
  Layers,
  RefreshCw,
  Search,
  Sparkles,
  Warehouse,
} from "@/components/ui/icons";
import { api } from "@/lib/api/client";
import {
  ANALYSIS_JOB_EVENT,
  type AnalysisJobEventDetail,
} from "@/lib/analysisEvents";
import { haptics } from "@/lib/haptics";
import { useForegroundEpoch, useTabActive } from "@/hooks/useTabActive";
import { usePullToRefresh } from "@/hooks/usePullToRefresh";
import {
  cacheDelete,
  cacheForceFetch,
  cachePeek,
  cacheSet,
  cacheSWR,
  PrefetchKeys,
  PrefetchTtl,
} from "@/lib/prefetch";
import { OfflineBanner } from "@/components/layout/OfflineBanner";
import type {
  AnalysisAgentStep,
  AnalysisCatalog,
  AnalysisDegree,
  AnalysisJob,
  AnalysisProfile,
  FundSearchHit,
  FundSearchResult,
  GoldBoard,
  Holding,
  SearchHit,
} from "@/lib/types";
import { isGoldBiasKey } from "@/lib/shortBiasChip";
import { useCallback, useEffect, useRef, useState } from "react";
import { AnalysisReportBlocks } from "./AnalysisReportBlocks";

type SubPage = "portfolio" | "symbol";
type SearchScope = "stock" | "fund" | "gold";

const SEARCH_SCOPES: Array<{
  id: SearchScope;
  label: string;
  placeholder: string;
  Icon: typeof CandlestickChart;
}> = [
  { id: "stock", label: "股票", placeholder: "股票代码或名称", Icon: CandlestickChart },
  { id: "fund", label: "基金", placeholder: "基金代码或名称", Icon: Layers },
  { id: "gold", label: "黄金", placeholder: "AU9999 / 积存金 / 黄金ETF", Icon: CircleDollarSign },
];

const TABS: Array<{
  kind: SubPage;
  label: string;
  Icon: typeof Warehouse;
}> = [
  { kind: "portfolio", label: "仓库分析", Icon: Warehouse },
  { kind: "symbol", label: "单条分析", Icon: CandlestickChart },
];

function analysisMarketLabel(market: string, symbol: string, name = ""): string {
  const m = (market || "").toUpperCase();
  if (m === "GDS" || symbol.toUpperCase() === "AU9999") return "上金所现货";
  if (m === "JD") return "黄金积存";
  if (m === "OF") return "场外基金";
  if (
    ["159937", "518660", "518880", "518800", "159934"].includes(symbol) ||
    name.includes("黄金")
  ) {
    return "黄金ETF";
  }
  if (/^(51|56|58|15|16)/.test(symbol)) return "场内ETF";
  if (m === "SZ") return "深市";
  if (m === "SH") return "沪市";
  return m || "标的";
}

function toAnalysisMarket(
  market: string | undefined,
): "SH" | "SZ" | "OF" | "JD" | "GDS" {
  const m = (market || "SH").toUpperCase();
  if (m === "SZ" || m === "OF" || m === "JD" || m === "GDS") return m;
  return "SH";
}

function holdingToHit(h: {
  symbol: string;
  market: string;
  name?: string;
}): SearchHit {
  const m = toAnalysisMarket(h.market);
  return {
    symbol: h.symbol,
    market: m,
    name: h.name || h.symbol,
    kind: m === "OF" ? "fund" : m === "JD" ? "gold" : "stock",
  };
}

function fundHitToSearch(f: {
  symbol: string;
  name: string;
  market: string;
  kind?: string;
  price?: number | null;
  change_pct?: number | null;
}): SearchHit {
  const m = toAnalysisMarket(f.market);
  return {
    symbol: f.symbol,
    name: f.name || f.symbol,
    market: m,
    kind: m === "OF" ? "fund" : f.kind || "etf",
    price: f.price,
    change_pct: f.change_pct,
  };
}

function goldBoardToHits(board: GoldBoard | null | undefined, q: string): SearchHit[] {
  if (!board?.sections?.length) return [];
  const raw = q.trim();
  const ql = raw.toLowerCase();
  const wantAllGold =
    raw === "黄金" ||
    raw === "金" ||
    raw === "积存金" ||
    raw === "金价" ||
    raw === "上金所" ||
    raw.includes("积存") ||
    ql.includes("au9999") ||
    ql === "au";
  const out: SearchHit[] = [];
  for (const sec of board.sections) {
    for (const it of sec.items || []) {
      if (!it.symbol || !it.market) continue;
      const mRaw = (it.market || "").toUpperCase();
      const isSpot =
        mRaw === "GDS" ||
        (it.id || "").toLowerCase() === "au9999" ||
        it.symbol.toUpperCase() === "AU9999";
      // 可入仓积存/金ETF + 上金所 AU9999（仅查阅，可单条分析）
      if (!it.holdable && !isSpot) continue;
      const m = isSpot ? "GDS" : toAnalysisMarket(it.market);
      if (m !== "JD" && m !== "SH" && m !== "SZ" && m !== "GDS") continue;
      const blob = `${it.name} ${it.symbol} ${it.id} ${it.note || ""}`;
      const matched =
        wantAllGold ||
        blob.toLowerCase().includes(ql) ||
        it.name.includes(raw) ||
        it.symbol.toUpperCase().includes(raw.toUpperCase()) ||
        (it.id || "").toLowerCase().includes(ql);
      if (!matched) continue;
      out.push({
        symbol: isSpot ? "AU9999" : it.symbol,
        name: it.name || (isSpot ? "AU9999" : it.symbol),
        market: m,
        kind: m === "JD" || m === "GDS" ? "gold" : "etf",
        note: it.note || (isSpot ? "上金所现货，仅查阅·可分析" : null),
      });
    }
  }
  return out;
}

function mergeAnalysisSearchHits(parts: SearchHit[][]): SearchHit[] {
  const seen = new Set<string>();
  const out: SearchHit[] = [];
  for (const batch of parts) {
    for (const h of batch) {
      const m = (h.market || "").toUpperCase();
      if (m === "HK" || m === "US") continue;
      if (h.kind === "ipo") continue;
      const key = `${m}:${h.symbol}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ ...h, market: toAnalysisMarket(h.market) });
    }
  }
  return out;
}

async function searchAnalysisTargets(
  q: string,
  scope: SearchScope,
): Promise<SearchHit[]> {
  const query = q.trim();
  if (!query) return [];

  if (scope === "stock") {
    const stockRes = await api
      .searchSymbols(query, 12)
      .catch(() => ({ items: [] as SearchHit[] }));
    // 股票栏：排除港美/新股；金 ETF 留给黄金栏
    const stocks = (stockRes.items || []).filter((h) => {
      const m = (h.market || "").toUpperCase();
      if (m === "HK" || m === "US" || m === "OF" || m === "JD") return false;
      if (h.kind === "ipo") return false;
      if (isGoldBiasKey(h.market, h.symbol)) return false;
      return true;
    });
    return mergeAnalysisSearchHits([stocks]);
  }

  if (scope === "fund") {
    const fundRes = await api
      .searchFunds(query, 16)
      .catch(() => ({ items: [] as FundSearchHit[] }));
    const funds = ((fundRes as FundSearchResult).items || [])
      .map(fundHitToSearch)
      .filter((h) => !isGoldBiasKey(h.market, h.symbol));
    return mergeAnalysisSearchHits([funds]);
  }

  // gold
  let goldBoard: GoldBoard | null = null;
  try {
    goldBoard = await cacheSWR(
      PrefetchKeys.goldBoard,
      () => api.getGoldBoard(),
      PrefetchTtl.gold,
    );
  } catch {
    goldBoard = cachePeek<GoldBoard>(PrefetchKeys.goldBoard);
  }
  const golds = goldBoardToHits(goldBoard, query);
  // 名称搜金 ETF 时，基金/股票接口作补充
  const [stockRes, fundRes] = await Promise.all([
    api.searchSymbols(query, 8).catch(() => ({ items: [] as SearchHit[] })),
    api.searchFunds(query, 8).catch(() => ({ items: [] as FundSearchHit[] })),
  ]);
  const goldEtfs = [
    ...(stockRes.items || []),
    ...((fundRes as FundSearchResult).items || []).map(fundHitToSearch),
  ].filter((h) => isGoldBiasKey(h.market, h.symbol));
  return mergeAnalysisSearchHits([golds, goldEtfs]);
}

function holdingMatchesScope(h: Holding, scope: SearchScope): boolean {
  const m = (h.market || "").toUpperCase();
  if (scope === "fund") return m === "OF";
  if (scope === "gold") {
    return isGoldBiasKey(h.market, h.symbol) || m === "JD" || m === "GDS";
  }
  // stock：沪深且非金 ETF
  if (m === "OF" || m === "JD" || m === "GDS" || m === "HK") return false;
  return !isGoldBiasKey(h.market, h.symbol);
}

function AnalysisProgressBar({
  pct,
  label,
}: {
  pct: number;
  label: string | null;
}) {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div className="analysis-progress" aria-live="polite" aria-label="分析进度">
      <div className="analysis-progress-track">
        <div className="analysis-progress-fill" style={{ width: `${clamped}%` }} />
      </div>
      <div className="analysis-progress-meta">
        <span className="analysis-progress-label">{label || "分析中…"}</span>
        <span className="analysis-progress-pct">{Math.round(clamped)}%</span>
      </div>
    </div>
  );
}

/** Blue CTA doubles as live status: 正在分析中（本页）/ 安崽分析中. */
function AnalysisPrimaryBtn({
  idleLabel,
  IdleIcon,
  inFlight,
  fromAgent,
  disabled,
  onClick,
}: {
  idleLabel: string;
  IdleIcon: typeof Sparkles;
  inFlight: boolean;
  fromAgent: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="analysis-primary-btn"
      data-running={inFlight ? "1" : "0"}
      data-source={inFlight ? (fromAgent ? "agent" : "page") : undefined}
      disabled={inFlight ? true : disabled}
      aria-busy={inFlight || undefined}
      aria-live={inFlight ? "polite" : undefined}
      onClick={onClick}
    >
      {inFlight ? (
        <span className="analysis-primary-running">
          <span className="analysis-running-orbit" aria-hidden>
            <span className="analysis-running-orbit-core" />
          </span>
          <span className="analysis-primary-running-label">
            {fromAgent ? "安崽分析中" : "正在分析中"}
          </span>
        </span>
      ) : (
        <>
          <IdleIcon size={15} strokeWidth={2} aria-hidden />
          {idleLabel}
        </>
      )}
    </button>
  );
}

function TierPicker({
  degrees,
  activeId,
  onSelect,
}: {
  degrees: AnalysisDegree[];
  activeId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="analysis-chip-row" role="radiogroup" aria-label="分析档位">
      {degrees.map((d) => {
        const active = activeId === d.id;
        return (
          <button
            key={d.id}
            type="button"
            className="analysis-chip"
            data-active={active ? "1" : "0"}
            role="radio"
            aria-checked={active}
            onClick={() => onSelect(d.id)}
          >
            {active ? <Check size={12} strokeWidth={2.5} aria-hidden /> : null}
            <span>{d.label}</span>
          </button>
        );
      })}
    </div>
  );
}

export default function AnalysisScreen() {
  const tabActive = useTabActive("/analysis");
  const fgEpoch = useForegroundEpoch();
  const [tab, setTab] = useState<SubPage>("portfolio");
  const [profile, setProfile] = useState<AnalysisProfile | null>(
    () => cachePeek<AnalysisProfile>(PrefetchKeys.analysisProfile),
  );
  const [degrees, setDegrees] = useState<AnalysisDegree[]>(
    () => cachePeek<AnalysisCatalog>(PrefetchKeys.analysisCatalog)?.degrees ?? [],
  );
  const [portfolioJob, setPortfolioJob] = useState<AnalysisJob | null>(
    () => cachePeek<AnalysisJob | null>(PrefetchKeys.analysisLatest("portfolio")),
  );
  const [symbolJob, setSymbolJob] = useState<AnalysisJob | null>(
    () => cachePeek<AnalysisJob | null>(PrefetchKeys.analysisLatest("symbol")),
  );
  const [busy, setBusy] = useState<"portfolio" | "symbol" | "profile" | null>(null);
  const [remoteRunning, setRemoteRunning] = useState<AnalysisJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<SearchHit | null>(null);
  const [progressLabel, setProgressLabel] = useState<string | null>(null);
  const [progressPct, setProgressPct] = useState(0);
  const [liveAgents, setLiveAgents] = useState<AnalysisAgentStep[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const remoteJobIdRef = useRef<number | null>(null);
  const busyRef = useRef<"portfolio" | "symbol" | "profile" | null>(null);
  const followRemoteJobRef = useRef<((job: AnalysisJob) => Promise<void> | void) | null>(
    null,
  );

  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchScope, setSearchScope] = useState<SearchScope>("stock");
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const searchSeq = useRef(0);

  const degreeId = profile?.degree || "standard";

  busyRef.current = busy;

  const bootstrapGen = useRef(0);
  const loadBootstrap = useCallback(async (opts?: { force?: boolean }) => {
    const force = opts?.force === true;
    const gen = ++bootstrapGen.current;
    setError(null);
    try {
      // Prefer live / running job — never let stale「latest done」overwrite in-flight UI
      let running: AnalysisJob | null = null;
      try {
        running = await api.getRunningAnalysis();
      } catch {
        running = null;
      }
      if (gen !== bootstrapGen.current) return;

      const runningScope =
        running && (running.scope === "portfolio" || running.scope === "symbol")
          ? (running.scope as SubPage)
          : null;

      const liveBusy = busyRef.current;
      const attaching = remoteJobIdRef.current != null;

      if (
        running &&
        runningScope &&
        remoteJobIdRef.current !== running.id &&
        liveBusy !== "portfolio" &&
        liveBusy !== "symbol"
      ) {
        void followRemoteJobRef.current?.(running);
      }

      const skipPortLatest =
        runningScope === "portfolio" || liveBusy === "portfolio" || attaching;
      const skipSymLatest =
        runningScope === "symbol" || liveBusy === "symbol" || attaching;

      const [catalog, prof, latestPort, latestSym] = await Promise.all([
        force
          ? cacheForceFetch(PrefetchKeys.analysisCatalog, () => api.getAnalysisCatalog())
          : cacheSWR(
              PrefetchKeys.analysisCatalog,
              () => api.getAnalysisCatalog(),
              PrefetchTtl.analysis,
              (c) => {
                if (gen !== bootstrapGen.current) return;
                setDegrees(c.degrees);
              },
            ),
        force
          ? cacheForceFetch(PrefetchKeys.analysisProfile, () => api.getAnalysisProfile())
          : cacheSWR(
              PrefetchKeys.analysisProfile,
              () => api.getAnalysisProfile(),
              PrefetchTtl.analysis,
              (p) => {
                if (gen !== bootstrapGen.current) return;
                setProfile(p);
              },
            ),
        skipPortLatest
          ? Promise.resolve(undefined)
          : (force
              ? cacheForceFetch(PrefetchKeys.analysisLatest("portfolio"), () =>
                  api.getLatestAnalysis("portfolio"),
                )
              : api
                  .getLatestAnalysis("portfolio")
                  .then((j) => {
                    cacheSet(PrefetchKeys.analysisLatest("portfolio"), j);
                    return j;
                  })
            ).catch(() => null),
        skipSymLatest
          ? Promise.resolve(undefined)
          : (force
              ? cacheForceFetch(PrefetchKeys.analysisLatest("symbol"), () =>
                  api.getLatestAnalysis("symbol"),
                )
              : api
                  .getLatestAnalysis("symbol")
                  .then((j) => {
                    cacheSet(PrefetchKeys.analysisLatest("symbol"), j);
                    return j;
                  })
            ).catch(() => null),
      ]);
      if (gen !== bootstrapGen.current) return;
      cacheSet(PrefetchKeys.analysisCatalog, catalog);
      cacheSet(PrefetchKeys.analysisProfile, prof);
      setDegrees(catalog.degrees);
      setProfile(prof);
      // Don't clobber if we attached / are busy after awaits
      if (
        latestPort !== undefined &&
        busyRef.current !== "portfolio" &&
        remoteJobIdRef.current == null
      ) {
        setPortfolioJob(latestPort);
      }
      if (
        latestSym !== undefined &&
        busyRef.current !== "symbol" &&
        remoteJobIdRef.current == null
      ) {
        setSymbolJob(latestSym);
      }
    } catch (e) {
      if (gen !== bootstrapGen.current) return;
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }, []);

  /** Leave / resume: abort hung SSE like Agent — frozen busy otherwise blocks buttons + bootstrap. */
  useEffect(() => {
    const clearLive = () => {
      abortRef.current?.abort();
      abortRef.current = null;
      remoteJobIdRef.current = null;
      busyRef.current = null;
      setBusy(null);
      setRemoteRunning(null);
      setProgressLabel(null);
      setProgressPct(0);
      setLiveAgents([]);
    };

    if (!tabActive) {
      clearLive();
      return;
    }

    clearLive();
    void loadBootstrap({ force: true });
    void api
      .getPortfolio()
      .then((pf) => setHoldings(pf.holdings || []))
      .catch(() => setHoldings([]));
  }, [tabActive, fgEpoch, loadBootstrap]);

  const analysisScrollRef = useRef<HTMLDivElement>(null);
  const ptrBarRef = useRef<HTMLDivElement>(null);
  const {
    refreshing: ptrRefreshing,
    ready: ptrReady,
  } = usePullToRefresh(analysisScrollRef, ptrBarRef, {
    onRefresh: async () => {
      await loadBootstrap({ force: true });
      const pf = await api.getPortfolio().catch(() => null);
      if (pf) setHoldings(pf.holdings || []);
    },
    disabled: searchOpen,
    onArmed: () => haptics.selection(),
  });

  useEffect(() => {
    if (!searchOpen) return;
    const q = searchQuery.trim();
    if (q.length < 1) {
      setSearchHits([]);
      return;
    }
    const seq = ++searchSeq.current;
    const t = window.setTimeout(() => {
      setSearchLoading(true);
      void searchAnalysisTargets(q, searchScope)
        .then((items) => {
          if (seq !== searchSeq.current) return;
          setSearchHits(items);
        })
        .catch(() => {
          if (seq !== searchSeq.current) return;
          setSearchHits([]);
        })
        .finally(() => {
          if (seq === searchSeq.current) setSearchLoading(false);
        });
    }, 280);
    return () => window.clearTimeout(t);
  }, [searchQuery, searchOpen, searchScope]);

  const setDegree = async (degree: string) => {
    if (degree === degreeId || busy === "profile") return;
    const prev = profile;
    const label = degrees.find((d) => d.id === degree)?.label ?? degree;
    setError(null);
    // Optimistic: keep chips interactive — no disabled opacity flash
    setProfile((p) =>
      p
        ? { ...p, degree, degree_label: label }
        : {
            degree,
            degree_label: label,
            blurb: "",
            default_recipe: degree,
          },
    );
    setBusy("profile");
    try {
      const next = await api.putAnalysisProfile(degree);
      setProfile(next);
    } catch (e) {
      setProfile(prev);
      setError(e instanceof Error ? e.message : "保存档位失败");
    } finally {
      setBusy(null);
    }
  };

  const applyStreamEvent = useCallback(
    (
      kind: SubPage,
      ev: {
        type: string;
        label?: string;
        pct?: number;
        message?: string;
        job_id?: number;
        job?: AnalysisJob;
        report?: AnalysisJob["report"];
        agent?: AnalysisAgentStep;
        symbols?: AnalysisJob["symbols"];
        degree?: string;
      },
      fallbackSymbols?: AnalysisJob["symbols"],
      fallbackDegree?: string,
    ) => {
      if (ev.type === "progress" || ev.type === "stage" || ev.type === "agent_start") {
        if (typeof ev.pct === "number") setProgressPct(Math.max(0, Math.min(100, ev.pct)));
        if (ev.label) setProgressLabel(String(ev.label));
      }
      if (ev.type === "agent_done" && ev.agent) {
        const step = ev.agent;
        setLiveAgents((prev) => {
          const rest = prev.filter((a) => a.id !== step.id || step.id === "dialectic");
          return [...rest, step];
        });
      }
      if (ev.type === "report" && ev.report) {
        const report = ev.report;
        const patch = (j: AnalysisJob | null): AnalysisJob =>
          j
            ? { ...j, report, status: "done" }
            : {
                id: Number(ev.job_id) || 0,
                scope: kind,
                symbols: fallbackSymbols || [],
                recipe_id: fallbackDegree || degreeId,
                degree: fallbackDegree || degreeId,
                status: "done",
                report,
              };
        if (kind === "portfolio") setPortfolioJob(patch);
        else setSymbolJob(patch);
      }
      if (ev.type === "done" && ev.job) {
        const job = ev.job;
        cacheSet(PrefetchKeys.analysisLatest(kind), job);
        if (kind === "portfolio") setPortfolioJob(job);
        else setSymbolJob(job);
      }
      if (ev.type === "error") {
        setError(String(ev.message || "分析失败"));
      }
    },
    [degreeId],
  );

  const beginLiveUi = (
    kind: SubPage,
    opts: { fromAgent: boolean; job?: AnalysisJob | null },
  ) => {
    setBusy(kind);
    setError(null);
    setProgressLabel(opts.fromAgent ? "安崽已启动，准备分析…" : "准备分析…");
    setProgressPct(2);
    setLiveAgents([]);
    // Drop stale done-report cache so bootstrap/SWR cannot restore old content
    cacheDelete(PrefetchKeys.analysisLatest(kind));
    // Immediately clear stale report (updater form — prev job kept as shell)
    const runningStub = (j: AnalysisJob | null): AnalysisJob => {
      const src = opts.job || j;
      return src
        ? {
            ...src,
            id: opts.job?.id || src.id,
            report: null,
            status: "running",
          }
        : {
            id: opts.job?.id || 0,
            scope: kind,
            symbols: opts.job?.symbols || [],
            recipe_id: opts.job?.degree || degreeId,
            degree: opts.job?.degree || degreeId,
            status: "running",
            report: null,
          };
    };
    if (kind === "portfolio") setPortfolioJob(runningStub);
    else {
      setSymbolJob(runningStub);
      const s0 = opts.job?.symbols?.[0];
      if (s0?.symbol) {
        setPicked({
          symbol: s0.symbol,
          market: toAnalysisMarket(s0.market),
          name: s0.name || s0.symbol,
          kind: "stock",
        });
      }
    }
  };

  const runStream = async (
    kind: "portfolio" | "symbol",
    body: Parameters<typeof api.streamAnalysisJob>[0],
  ) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    remoteJobIdRef.current = null;
    setRemoteRunning(null);
    beginLiveUi(kind, { fromAgent: false });

    try {
      await api.streamAnalysisJob(
        body,
        (ev) => applyStreamEvent(kind, ev, body.symbols, body.degree || degreeId),
        ac.signal,
      );
    } catch (e) {
      if ((e as Error)?.name === "AbortError") return;
      setError(e instanceof Error ? e.message : "分析失败");
    } finally {
      setBusy(null);
      setProgressLabel(null);
      setProgressPct(0);
      setRemoteRunning(null);
      if (abortRef.current === ac) abortRef.current = null;
    }
  };

  /** Attach to agent/background job — clear old report, real progress + seat sync. */
  const followRemoteJob = useCallback(
    async (job: AnalysisJob) => {
      if (!(job.scope === "portfolio" || job.scope === "symbol")) return;
      if (remoteJobIdRef.current === job.id && busy != null) return;

      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      const kind = job.scope as SubPage;
      remoteJobIdRef.current = job.id;
      setRemoteRunning(job);
      setTab(kind);
      beginLiveUi(kind, { fromAgent: true, job });

      try {
        await api.streamAnalysisJobAttach(
          job.id,
          (ev) => applyStreamEvent(kind, ev, job.symbols, job.degree),
          ac.signal,
        );
      } catch (e) {
        if ((e as Error)?.name === "AbortError") return;
        setError(e instanceof Error ? e.message : "附着分析进度失败");
      } finally {
        if (abortRef.current === ac) abortRef.current = null;
        if (remoteJobIdRef.current === job.id) {
          remoteJobIdRef.current = null;
          setRemoteRunning(null);
        }
        setBusy(null);
        setProgressLabel(null);
        setProgressPct(0);
        void loadBootstrap({ force: true });
      }
    },
    [applyStreamEvent, busy, loadBootstrap],
  );

  followRemoteJobRef.current = followRemoteJob;

  /** Agent tab: analysis started/done → invalidate cache + attach ASAP. */
  useEffect(() => {
    const onJob = (ev: Event) => {
      const detail = (ev as CustomEvent<AnalysisJobEventDetail>).detail;
      if (!detail) return;
      const scope = detail.scope === "symbol" ? "symbol" : "portfolio";
      cacheDelete(PrefetchKeys.analysisLatest(scope));
      if (detail.phase === "done") {
        cacheDelete(PrefetchKeys.analysisLatest("portfolio"));
        cacheDelete(PrefetchKeys.analysisLatest("symbol"));
        // Warm cache even when Analysis tab is inactive — first paint won't show stale report
        void (async () => {
          try {
            const [port, sym] = await Promise.all([
              api.getLatestAnalysis("portfolio").catch(() => null),
              api.getLatestAnalysis("symbol").catch(() => null),
            ]);
            if (port) {
              cacheSet(PrefetchKeys.analysisLatest("portfolio"), port);
              if (!tabActive) setPortfolioJob(port);
            }
            if (sym) {
              cacheSet(PrefetchKeys.analysisLatest("symbol"), sym);
              if (!tabActive) setSymbolJob(sym);
            }
          } catch {
            /* ignore */
          }
        })();
        if (tabActive) void loadBootstrap();
        return;
      }
      if (!tabActive) return;
      if (busy === "portfolio" || busy === "symbol") return;
      void (async () => {
        try {
          const job = await api.getRunningAnalysis(
            detail.scope === "symbol" || detail.scope === "portfolio"
              ? detail.scope
              : undefined,
          );
          if (job && (job.scope === "portfolio" || job.scope === "symbol")) {
            void followRemoteJob(job);
          }
        } catch {
          /* ignore */
        }
      })();
    };
    window.addEventListener(ANALYSIS_JOB_EVENT, onJob);
    return () => window.removeEventListener(ANALYSIS_JOB_EVENT, onJob);
  }, [tabActive, busy, followRemoteJob, loadBootstrap]);

  /** Discover agent-started jobs and attach live SSE (same UX as manual). */
  useEffect(() => {
    if (!tabActive) return;
    if (busy === "portfolio" || busy === "symbol") return;
    let cancelled = false;
    const poll = async () => {
      if (cancelled) return;
      if (busyRef.current === "portfolio" || busyRef.current === "symbol") return;
      if (remoteJobIdRef.current != null) return;
      try {
        const job = await api.getRunningAnalysis();
        if (cancelled || !job) return;
        if (job.scope === "portfolio" || job.scope === "symbol") {
          void followRemoteJob(job);
        }
      } catch {
        /* ignore */
      }
    };
    void poll();
    const id = window.setInterval(poll, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [tabActive, fgEpoch, busy, followRemoteJob]);

  const runPortfolio = () =>
    void runStream("portfolio", { scope: "portfolio", degree: "standard" });

  const runSymbol = () => {
    if (!picked) {
      setError("请先选择一条（股票 / 基金 / 黄金均可）");
      return;
    }
    void runStream("symbol", {
      scope: "symbol",
      degree: degreeId,
      symbols: [
        {
          symbol: picked.symbol,
          market: toAnalysisMarket(picked.market),
          name: picked.name,
        },
      ],
    });
  };

  const switchTab = (kind: SubPage) => {
    if (kind === tab) return;
    haptics.tap();
    setError(null);
    setTab(kind);
  };

  const portInFlight =
    busy === "portfolio" || remoteRunning?.scope === "portfolio";
  const symInFlight = busy === "symbol" || remoteRunning?.scope === "symbol";
  const anyJobBusy = busy === "portfolio" || busy === "symbol" || remoteRunning != null;
  const runningScope: SubPage | null = remoteRunning
    ? (remoteRunning.scope as SubPage)
    : busy === "portfolio" || busy === "symbol"
      ? busy
      : null;
  const runningFromAgent = remoteRunning != null;

  return (
    <div className="analysis-page" data-kind={tab} data-running={anyJobBusy ? "1" : "0"}>
      <OfflineBanner />
      <div className="analysis-page-pin">
        <div className="news-seg" role="tablist" aria-label="分析子页">
          {TABS.map(({ kind, label, Icon }) => (
            <button
              key={kind}
              type="button"
              role="tab"
              className="news-seg-tab"
              aria-selected={tab === kind}
              data-active={tab === kind ? "1" : "0"}
              data-kind={kind}
              data-running={runningScope === kind ? "1" : "0"}
              onClick={() => switchTab(kind)}
            >
              <Icon size={13} strokeWidth={2.2} absoluteStrokeWidth aria-hidden />
              {label}
              {runningScope === kind ? (
                <span className="analysis-tab-live" aria-label="分析中">
                  ·进行中
                </span>
              ) : null}
            </button>
          ))}
        </div>

        {error ? (
          <p className="analysis-error" role="alert">
            {error}
          </p>
        ) : null}

        {tab === "portfolio" ? (
          <section className="inset-group analysis-setup-card" aria-label="仓库巡检">
            <div className="inset-group-header">仓库巡检</div>
            <div className="analysis-section-body">
              {!portInFlight ? (
                <p className="analysis-setup-hint">
                  覆盖仓库全部持仓：股票、场内 ETF、场外基金、黄金积存
                </p>
              ) : null}
              <AnalysisPrimaryBtn
                idleLabel={
                  anyJobBusy && !portInFlight ? "另有分析进行中…" : "分析持仓"
                }
                IdleIcon={RefreshCw}
                inFlight={portInFlight}
                fromAgent={runningFromAgent}
                disabled={anyJobBusy || busy === "profile"}
                onClick={() => runPortfolio()}
              />
              {portInFlight ? (
                <AnalysisProgressBar pct={progressPct} label={progressLabel} />
              ) : null}
            </div>
          </section>
        ) : (
          <section className="inset-group analysis-setup-card" aria-label="单条设置">
            <div className="inset-group-header">单条设置</div>
            <div className="analysis-section-body">
              <TierPicker
                degrees={degrees}
                activeId={degreeId}
                onSelect={(id) => void setDegree(id)}
              />
              <button
                type="button"
                className="analysis-pick-btn"
                data-picked={picked ? "1" : "0"}
                disabled={anyJobBusy}
                onClick={() => {
                  setSearchQuery("");
                  setSearchHits([]);
                  setSearchScope("stock");
                  setSearchOpen(true);
                }}
              >
                <Search size={15} strokeWidth={2} aria-hidden />
                <span className="analysis-pick-text">
                  {picked
                    ? `${picked.name || picked.symbol} · ${analysisMarketLabel(picked.market, picked.symbol, picked.name)}`
                    : "选品种后搜索，或从仓库选"}
                </span>
                <ChevronRight size={16} strokeWidth={2} aria-hidden />
              </button>
              <AnalysisPrimaryBtn
                idleLabel={
                  anyJobBusy && !symInFlight
                    ? "另有分析进行中…"
                    : "开始分析"
                }
                IdleIcon={Sparkles}
                inFlight={symInFlight}
                fromAgent={runningFromAgent}
                disabled={anyJobBusy || busy === "profile" || !picked}
                onClick={() => runSymbol()}
              />
              {symInFlight ? (
                <AnalysisProgressBar pct={progressPct} label={progressLabel} />
              ) : null}
            </div>
          </section>
        )}
      </div>

      <div
        className="analysis-scroll"
        ref={analysisScrollRef}
        data-ptr={ptrRefreshing ? "1" : "0"}
        role="tabpanel"
        aria-label={tab === "portfolio" ? "仓库分析" : "单条分析"}
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
        <div className="analysis-report-wrap">
          {tab === "portfolio" ? (
            <AnalysisReportBlocks
              report={portfolioJob?.report ?? null}
              progressLabel={portInFlight ? progressLabel : null}
              liveAgents={portInFlight ? liveAgents : undefined}
              emptyHint={
                portInFlight
                  ? runningFromAgent
                    ? "安崽已启动仓库分析，委员会开会中…"
                    : "委员会召开中…"
                  : "点「分析持仓」巡检当前仓库"
              }
            />
          ) : (
            <AnalysisReportBlocks
              report={symbolJob?.report ?? null}
              progressLabel={symInFlight ? progressLabel : null}
              liveAgents={symInFlight ? liveAgents : undefined}
              emptyHint={
                symInFlight
                  ? runningFromAgent
                    ? "安崽已启动分析，委员会开会中…"
                    : "委员会召开中…"
                  : "搜股票/基金/黄金后点「开始分析」"
              }
            />
          )}
        </div>
        <Disclaimer />
      </div>

      <CenterModal
        open={searchOpen}
        title="选择分析标的"
        onClose={() => setSearchOpen(false)}
      >
        <div className="analysis-search-modal">
          <div
            className="analysis-search-scope-tabs"
            role="tablist"
            aria-label="搜索品种"
          >
            {SEARCH_SCOPES.map(({ id, label, Icon }) => (
              <button
                key={id}
                type="button"
                role="tab"
                className="analysis-search-scope-tab"
                data-scope={id}
                data-active={searchScope === id ? "1" : "0"}
                aria-selected={searchScope === id}
                onClick={() => {
                  if (searchScope === id) return;
                  setSearchScope(id);
                  setSearchHits([]);
                }}
              >
                <Icon
                  size={13}
                  strokeWidth={2.2}
                  className="analysis-search-scope-tab-icon"
                  aria-hidden
                />
                <span>{label}</span>
              </button>
            ))}
          </div>
          <div className="analysis-search-field">
            <Search
              size={16}
              strokeWidth={2}
              className="analysis-search-field-icon"
              aria-hidden
            />
            <input
              className="analysis-search-field-input"
              data-autofocus
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={
                SEARCH_SCOPES.find((s) => s.id === searchScope)?.placeholder ||
                "代码或名称"
              }
              enterKeyHint="search"
              autoCapitalize="off"
              autoCorrect="off"
            />
          </div>
          {!searchQuery.trim() &&
          holdings.some((h) => holdingMatchesScope(h, searchScope)) ? (
            <div className="analysis-holding-picks" aria-label="仓库持仓">
              <div className="analysis-holding-picks-head">
                <Warehouse size={13} strokeWidth={2.2} aria-hidden />
                <span>
                  仓库
                  {SEARCH_SCOPES.find((s) => s.id === searchScope)?.label || ""}
                </span>
              </div>
              <ul className="analysis-search-hits">
                {holdings
                  .filter((h) => holdingMatchesScope(h, searchScope))
                  .map((h) => (
                    <li key={`${h.market}:${h.symbol}`}>
                      <button
                        type="button"
                        className="analysis-search-hit"
                        onClick={() => {
                          setPicked(holdingToHit(h));
                          setSearchOpen(false);
                        }}
                      >
                        <span className="analysis-search-hit-main">
                          <span className="analysis-search-hit-name">
                            {h.name || h.symbol}
                          </span>
                          <span className="analysis-search-hit-meta">
                            {h.symbol} ·{" "}
                            {analysisMarketLabel(h.market, h.symbol, h.name)}
                          </span>
                        </span>
                        {h.weight != null ? (
                          <span className="analysis-search-hit-weight">
                            {h.weight.toFixed(1)}%
                          </span>
                        ) : null}
                        <ChevronRight
                          size={16}
                          strokeWidth={2}
                          className="analysis-search-hit-chevron"
                          aria-hidden
                        />
                      </button>
                    </li>
                  ))}
              </ul>
            </div>
          ) : null}
          {searchLoading ? (
            <p className="analysis-search-status">搜索中…</p>
          ) : searchQuery.trim() ? (
            searchHits.length === 0 ? (
              <p className="analysis-search-status">
                「{SEARCH_SCOPES.find((s) => s.id === searchScope)?.label}
                」暂无结果，换个词试试
              </p>
            ) : (
              <ul className="analysis-search-hits analysis-search-hits--results">
                {searchHits.map((h) => (
                  <li key={`${h.market}:${h.symbol}`}>
                    <button
                      type="button"
                      className="analysis-search-hit"
                      onClick={() => {
                        setPicked({
                          ...h,
                          market: toAnalysisMarket(h.market),
                        });
                        setSearchOpen(false);
                      }}
                    >
                      <span className="analysis-search-hit-main">
                        <span className="analysis-search-hit-name">
                          {h.name || h.symbol}
                        </span>
                        <span className="analysis-search-hit-meta">
                          {h.symbol} ·{" "}
                          {analysisMarketLabel(h.market, h.symbol, h.name || "")}
                        </span>
                      </span>
                      <span
                        className="analysis-search-hit-tag"
                        data-scope={searchScope}
                      >
                        {SEARCH_SCOPES.find((s) => s.id === searchScope)?.label}
                      </span>
                      <ChevronRight
                        size={16}
                        strokeWidth={2}
                        className="analysis-search-hit-chevron"
                        aria-hidden
                      />
                    </button>
                  </li>
                ))}
              </ul>
            )
          ) : null}
        </div>
      </CenterModal>
    </div>
  );
}
