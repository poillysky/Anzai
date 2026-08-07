"use client";

import { CenterModal } from "@/components/overlay/CenterModal";
import { Disclaimer } from "@/components/ui/Disclaimer";
import {
  CandlestickChart,
  Check,
  ChevronRight,
  RefreshCw,
  Search,
  Sparkles,
  Warehouse,
} from "@/components/ui/icons";
import { api } from "@/lib/api/client";
import { haptics } from "@/lib/haptics";
import { cachePeek, cacheSet, PrefetchKeys } from "@/lib/prefetch";
import type {
  AnalysisAgentStep,
  AnalysisCatalog,
  AnalysisDegree,
  AnalysisJob,
  AnalysisProfile,
  SearchHit,
} from "@/lib/types";
import { useCallback, useEffect, useRef, useState } from "react";
import { AnalysisReportBlocks } from "./AnalysisReportBlocks";

type SubPage = "portfolio" | "symbol";

const TABS: Array<{
  kind: SubPage;
  label: string;
  Icon: typeof Warehouse;
}> = [
  { kind: "portfolio", label: "仓库分析", Icon: Warehouse },
  { kind: "symbol", label: "个股分析", Icon: CandlestickChart },
];

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
  const [tab, setTab] = useState<SubPage>("portfolio");
  const [profile, setProfile] = useState<AnalysisProfile | null>(
    () => cachePeek<AnalysisProfile>(PrefetchKeys.analysisProfile),
  );
  const [degrees, setDegrees] = useState<AnalysisDegree[]>(
    () => cachePeek<AnalysisCatalog>(PrefetchKeys.analysisCatalog)?.degrees ?? [],
  );
  const [portfolioJob, setPortfolioJob] = useState<AnalysisJob | null>(null);
  const [symbolJob, setSymbolJob] = useState<AnalysisJob | null>(null);
  const [busy, setBusy] = useState<"portfolio" | "symbol" | "profile" | null>(null);
  const [remoteRunning, setRemoteRunning] = useState<AnalysisJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<SearchHit | null>(null);
  const [progressLabel, setProgressLabel] = useState<string | null>(null);
  const [progressPct, setProgressPct] = useState(0);
  const [liveAgents, setLiveAgents] = useState<AnalysisAgentStep[]>([]);
  const abortRef = useRef<AbortController | null>(null);
  const remoteJobIdRef = useRef<number | null>(null);

  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const searchSeq = useRef(0);

  const degreeId = profile?.degree || "standard";

  const loadBootstrap = useCallback(async () => {
    setError(null);
    try {
      const [catalog, prof, latestPort, latestSym] = await Promise.all([
        api.getAnalysisCatalog(),
        api.getAnalysisProfile(),
        api.getLatestAnalysis("portfolio").catch(() => null),
        api.getLatestAnalysis("symbol").catch(() => null),
      ]);
      cacheSet(PrefetchKeys.analysisCatalog, catalog);
      cacheSet(PrefetchKeys.analysisProfile, prof);
      setDegrees(catalog.degrees);
      setProfile(prof);
      setPortfolioJob(latestPort);
      setSymbolJob(latestSym);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    void loadBootstrap();
  }, [loadBootstrap]);

  /** Poll agent-started (or other) background jobs so progress shows without local SSE. */
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      if (busy === "portfolio" || busy === "symbol") return;
      try {
        const job = await api.getRunningAnalysis();
        if (cancelled) return;
        if (job && (job.scope === "portfolio" || job.scope === "symbol")) {
          const scope = job.scope as SubPage;
          if (remoteJobIdRef.current !== job.id) {
            remoteJobIdRef.current = job.id;
            setTab(scope);
            const s0 = job.symbols?.[0];
            if (scope === "symbol" && s0?.symbol) {
              setPicked({
                symbol: s0.symbol,
                market: s0.market || "SH",
                name: s0.name || s0.symbol,
                kind: "stock",
              });
            }
          }
          setRemoteRunning(job);
          const created = job.created_at ? Date.parse(job.created_at) : Date.now();
          const elapsed = Math.max(0, (Date.now() - created) / 1000);
          setProgressPct(Math.min(92, 6 + elapsed * 1.4));
          const nm = job.symbols?.[0]?.name || job.symbols?.[0]?.symbol;
          setProgressLabel(
            scope === "portfolio"
              ? "安崽已启动仓库分析，委员会进行中…"
              : `安崽已启动个股分析${nm ? `「${nm}」` : ""}，进行中…`,
          );
        } else if (remoteJobIdRef.current != null) {
          remoteJobIdRef.current = null;
          setRemoteRunning(null);
          setProgressLabel(null);
          setProgressPct(0);
          void loadBootstrap();
        }
      } catch {
        /* ignore transient poll errors */
      }
    };
    void poll();
    const id = window.setInterval(poll, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [busy, loadBootstrap]);

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
      void api
        .searchSymbols(q, 12)
        .then((res) => {
          if (seq !== searchSeq.current) return;
          setSearchHits(res.items);
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
  }, [searchQuery, searchOpen]);

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

  const runStream = async (
    kind: "portfolio" | "symbol",
    body: Parameters<typeof api.streamAnalysisJob>[0],
  ) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setBusy(kind);
    setError(null);
    setProgressLabel("准备分析…");
    setProgressPct(2);
    setLiveAgents([]);
    if (kind === "portfolio") setPortfolioJob((j) => (j ? { ...j, report: null } : j));
    else setSymbolJob((j) => (j ? { ...j, report: null } : j));

    try {
      await api.streamAnalysisJob(
        body,
        (ev) => {
          if (ev.type === "progress") {
            if (typeof ev.pct === "number") setProgressPct(Math.max(0, Math.min(100, ev.pct)));
            if (ev.label) setProgressLabel(String(ev.label));
          }
          if (ev.type === "stage") {
            if (ev.label) setProgressLabel(String(ev.label));
            if (typeof ev.pct === "number") setProgressPct(Math.max(0, Math.min(100, ev.pct)));
          }
          if (ev.type === "agent_start") {
            if (ev.label) setProgressLabel(String(ev.label));
            if (typeof ev.pct === "number") setProgressPct(Math.max(0, Math.min(100, ev.pct)));
          }
          if (ev.type === "agent_done" && ev.agent) {
            const step = ev.agent as AnalysisAgentStep;
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
                    symbols: body.symbols || [],
                    recipe_id: degreeId,
                    degree: degreeId,
                    status: "done",
                    report,
                  };
            if (kind === "portfolio") setPortfolioJob(patch);
            else setSymbolJob(patch);
          }
          if (ev.type === "done" && ev.job) {
            const job = ev.job as AnalysisJob;
            if (kind === "portfolio") setPortfolioJob(job);
            else setSymbolJob(job);
          }
          if (ev.type === "error") {
            setError(String(ev.message || "分析失败"));
          }
        },
        ac.signal,
      );
    } catch (e) {
      if ((e as Error)?.name === "AbortError") return;
      setError(e instanceof Error ? e.message : "分析失败");
    } finally {
      setBusy(null);
      setProgressLabel(null);
      setProgressPct(0);
      if (abortRef.current === ac) abortRef.current = null;
    }
  };

  const runPortfolio = () =>
    void runStream("portfolio", { scope: "portfolio", degree: "standard" });

  const runSymbol = () => {
    if (!picked) {
      setError("请先选择一只股票");
      return;
    }
    void runStream("symbol", {
      scope: "symbol",
      degree: degreeId,
      symbols: [
        {
          symbol: picked.symbol,
          market: picked.market === "SZ" ? "SZ" : "SH",
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

  return (
    <div className="analysis-page" data-kind={tab}>
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
              onClick={() => switchTab(kind)}
            >
              <Icon size={13} strokeWidth={2.2} absoluteStrokeWidth aria-hidden />
              {label}
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
              <button
                type="button"
                className="analysis-primary-btn"
                disabled={anyJobBusy || busy === "profile"}
                onClick={() => runPortfolio()}
              >
                <RefreshCw
                  size={15}
                  strokeWidth={2}
                  className={portInFlight ? "analysis-spin" : undefined}
                  aria-hidden
                />
                {portInFlight ? "委员会分析中…" : "分析持仓"}
              </button>
              {portInFlight ? (
                <AnalysisProgressBar pct={progressPct} label={progressLabel} />
              ) : null}
            </div>
          </section>
        ) : (
          <section className="inset-group analysis-setup-card" aria-label="个股设置">
            <div className="inset-group-header">个股设置</div>
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
                onClick={() => {
                  setSearchQuery("");
                  setSearchHits([]);
                  setSearchOpen(true);
                }}
              >
                <Search size={15} strokeWidth={2} aria-hidden />
                <span className="analysis-pick-text">
                  {picked
                    ? `${picked.name || picked.symbol} · ${picked.symbol}`
                    : "搜索并选择标的"}
                </span>
                <ChevronRight size={16} strokeWidth={2} aria-hidden />
              </button>
              <button
                type="button"
                className="analysis-primary-btn"
                disabled={anyJobBusy || busy === "profile" || !picked}
                onClick={() => runSymbol()}
              >
                <Sparkles size={15} strokeWidth={2} aria-hidden />
                {symInFlight ? "委员会分析中…" : "开始分析"}
              </button>
              {symInFlight ? (
                <AnalysisProgressBar pct={progressPct} label={progressLabel} />
              ) : null}
            </div>
          </section>
        )}
      </div>

      <div
        className="analysis-scroll"
        role="tabpanel"
        aria-label={tab === "portfolio" ? "仓库分析" : "个股分析"}
      >
        <div className="analysis-report-wrap">
          {tab === "portfolio" ? (
            <AnalysisReportBlocks
              report={portfolioJob?.report ?? null}
              progressLabel={portInFlight ? progressLabel : null}
              liveAgents={busy === "portfolio" ? liveAgents : undefined}
              emptyHint={
                portInFlight ? "委员会召开中…" : "点「分析持仓」巡检当前仓库"
              }
            />
          ) : (
            <AnalysisReportBlocks
              report={symbolJob?.report ?? null}
              progressLabel={symInFlight ? progressLabel : null}
              liveAgents={busy === "symbol" ? liveAgents : undefined}
              emptyHint={
                symInFlight ? "委员会召开中…" : "选标的与档位后点「开始分析」"
              }
            />
          )}
        </div>
        <Disclaimer />
      </div>

      <CenterModal
        open={searchOpen}
        title="选择标的"
        onClose={() => setSearchOpen(false)}
      >
        <div className="analysis-search-modal">
          <input
            className="ios-input"
            data-autofocus
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="代码或名称"
            enterKeyHint="search"
            autoCapitalize="off"
            autoCorrect="off"
          />
          {searchLoading ? (
            <p className="skeleton-placeholder">搜索中…</p>
          ) : (
            <ul className="analysis-search-hits">
              {searchHits.map((h) => (
                <li key={`${h.market}:${h.symbol}`}>
                  <button
                    type="button"
                    className="analysis-search-hit"
                    onClick={() => {
                      setPicked(h);
                      setSearchOpen(false);
                    }}
                  >
                    <span className="analysis-search-hit-name">{h.name || h.symbol}</span>
                    <span className="analysis-search-hit-meta">
                      {h.symbol} · {h.market}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </CenterModal>
    </div>
  );
}
