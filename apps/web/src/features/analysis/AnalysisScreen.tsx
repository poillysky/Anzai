"use client";

import { CenterModal } from "@/components/overlay/CenterModal";
import { Disclaimer } from "@/components/ui/Disclaimer";
import {
  CandlestickChart,
  Check,
  RefreshCw,
  Search,
  Sparkles,
  Warehouse,
} from "@/components/ui/icons";
import { api } from "@/lib/api/client";
import { haptics } from "@/lib/haptics";
import { cachePeek, cacheSet, PrefetchKeys } from "@/lib/prefetch";
import type {
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

function TierPicker({
  degrees,
  activeId,
  disabled,
  onSelect,
}: {
  degrees: AnalysisDegree[];
  activeId: string;
  disabled?: boolean;
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
            disabled={disabled}
            onClick={() => onSelect(d.id)}
          >
            {active ? <Check size={12} strokeWidth={2.5} aria-hidden /> : null}
            {d.label}
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
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<SearchHit | null>(null);

  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const searchSeq = useRef(0);

  const degreeId = profile?.degree || "standard";
  const activeDegree = degrees.find((d) => d.id === degreeId) ?? degrees[0];

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
    setBusy("profile");
    setError(null);
    try {
      const next = await api.putAnalysisProfile(degree);
      setProfile(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存档位失败");
    } finally {
      setBusy(null);
    }
  };

  const runPortfolio = async () => {
    setBusy("portfolio");
    setError(null);
    try {
      const job = await api.createAnalysisJob({
        scope: "portfolio",
        degree: degreeId,
      });
      setPortfolioJob(job);
      if (job.status === "failed") setError(job.error || "持仓分析失败");
    } catch (e) {
      setError(e instanceof Error ? e.message : "持仓分析失败");
    } finally {
      setBusy(null);
    }
  };

  const runSymbol = async () => {
    if (!picked) {
      setError("请先选择一只股票");
      return;
    }
    setBusy("symbol");
    setError(null);
    try {
      const job = await api.createAnalysisJob({
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
      setSymbolJob(job);
      if (job.status === "failed") setError(job.error || "个股分析失败");
    } catch (e) {
      setError(e instanceof Error ? e.message : "个股分析失败");
    } finally {
      setBusy(null);
    }
  };

  const switchTab = (kind: SubPage) => {
    if (kind === tab) return;
    haptics.tap();
    setError(null);
    setTab(kind);
  };

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
      </div>

      {error ? (
        <p className="analysis-error" role="alert">
          {error}
        </p>
      ) : null}

      <section className="inset-group" aria-label="分析档位">
        <div className="inset-group-header">分析档位（仓库 / 个股共用）</div>
        <div className="analysis-section-body">
          <TierPicker
            degrees={degrees}
            activeId={degreeId}
            disabled={busy === "profile"}
            onSelect={(id) => void setDegree(id)}
          />
          <p className="analysis-blurb">{activeDegree?.blurb || profile?.blurb || "加载中…"}</p>
        </div>
      </section>

      {tab === "portfolio" ? (
        <div className="analysis-subpage" role="tabpanel" aria-label="仓库分析">
          <button
            type="button"
            className="analysis-primary-btn"
            style={{ marginTop: 12 }}
            disabled={busy != null}
            onClick={() => void runPortfolio()}
          >
            <RefreshCw
              size={14}
              strokeWidth={2}
              className={busy === "portfolio" ? "analysis-spin" : undefined}
              aria-hidden
            />
            {busy === "portfolio" ? "分析中…" : "分析持仓"}
          </button>
          <div className="analysis-report-wrap">
            <AnalysisReportBlocks
              report={portfolioJob?.report ?? null}
              emptyHint={
                busy === "portfolio" ? "分析进行中…" : "选好档位后点「分析持仓」"
              }
            />
          </div>
        </div>
      ) : (
        <div className="analysis-subpage" role="tabpanel" aria-label="个股分析">
          <section className="inset-group" style={{ marginTop: 12 }} aria-label="选择标的">
            <div className="inset-group-header">标的</div>
            <div className="analysis-section-body">
              <button
                type="button"
                className="analysis-pick-btn"
                style={{ marginTop: 0 }}
                onClick={() => {
                  setSearchQuery("");
                  setSearchHits([]);
                  setSearchOpen(true);
                }}
              >
                <Search size={14} strokeWidth={2} aria-hidden />
                {picked
                  ? `${picked.name || picked.symbol} · ${picked.symbol}`
                  : "搜索并选择标的"}
              </button>
              <button
                type="button"
                className="analysis-primary-btn"
                disabled={busy != null || !picked}
                onClick={() => void runSymbol()}
              >
                <Sparkles size={14} strokeWidth={2} aria-hidden />
                {busy === "symbol" ? "分析中…" : "开始分析"}
              </button>
            </div>
          </section>
          <div className="analysis-report-wrap">
            <AnalysisReportBlocks
              report={symbolJob?.report ?? null}
              emptyHint={
                busy === "symbol" ? "分析进行中…" : "选标的后点「开始分析」（沿用上方档位）"
              }
            />
          </div>
        </div>
      )}

      <Disclaimer />

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
